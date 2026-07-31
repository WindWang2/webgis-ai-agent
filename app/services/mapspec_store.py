import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.session_data import session_data_manager

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BASE_STORAGE_DIR = PROJECT_ROOT / ".webgis-agent"


# Companion label layer convention emitted by the MapLibre compiler
LABEL_LAYER_SUFFIX = "-label"


def _should_remove_layer(layer: Dict[str, Any], target_layer_id: str) -> bool:
  """Check if a layer matches the target layer ID or its associated companion label layer."""
  lid = layer.get("id")
  if not lid:
    return False
  return lid == target_layer_id or lid == f"{target_layer_id}{LABEL_LAYER_SUFFIX}"


def view_has_center(mapspec: Dict[str, Any]) -> bool:
  """Predicate over a raw MapSpec dict: has a center been explicitly set?

  This is the replacement for the old `center == [0.0, 0.0]` heuristic. It
  treats only an *absent* center as unset; an explicitly-set [0.0, 0.0] counts
  as a real value and must not be clobbered by auto-view injection.
  """
  view = mapspec.get("view") or {}
  center = view.get("center", None)
  return "center" in view and center is not None


class MapSpecStore:
  """Manages MapSpec intent storage and dual-writing into runtime map_state."""

  def get_session_dir(self, session_id: str) -> Path:
    session_dir = BASE_STORAGE_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir

  async def get_mapspec(self, session_id: str) -> Optional[Dict[str, Any]]:
    # Check map_state cache first
    map_state = await session_data_manager.get_map_state(session_id)
    if "mapspec" in map_state:
      return map_state["mapspec"]

    # Fallback to file
    mapspec_file = self.get_session_dir(session_id) / "mapspec.json"
    if mapspec_file.exists():
      try:
        with open(mapspec_file, "r", encoding="utf-8") as f:
          mapspec = json.load(f)
          await session_data_manager.set_map_state(session_id, "mapspec", mapspec)
          return mapspec
      except Exception as e:
        logger.error(f"Error reading mapspec file for session {session_id}: {e}")

    return None

  async def save_mapspec(self, session_id: str, mapspec: Dict[str, Any]) -> Dict[str, Any]:
    session_dir = self.get_session_dir(session_id)
    rev_dir = session_dir / "revisions"
    rev_dir.mkdir(parents=True, exist_ok=True)

    # 1. Write file & revision
    mapspec_path = session_dir / "mapspec.json"
    with open(mapspec_path, "w", encoding="utf-8") as f:
      json.dump(mapspec, f, ensure_ascii=False, indent=2)

    rev_filename = f"mapspec_rev_{int(time.time() * 1000)}.json"
    with open(rev_dir / rev_filename, "w", encoding="utf-8") as f:
      json.dump(mapspec, f, ensure_ascii=False, indent=2)

    # 2. Cache the MapSpec intent in runtime map_state. The compiled MapLibre
    # style is produced on demand by the TS compiler (compile_mapspec_cli /
    # the Runtime Validator); it is not a dual-write concern here. Earlier
    # writes of map_state["layers"] / ["view"] were removed: nothing reads
    # them (readers use "viewport" and the SSE/HudState layer model).
    await session_data_manager.set_map_state(session_id, "mapspec", mapspec)

    return {"mapspec": mapspec}

  async def init_project(
      self,
      session_id: str,
      view: Optional[Dict[str, Any]] = None,
      thresholds: Optional[Dict[str, Any]] = None,
  ) -> Dict[str, Any]:
    mapspec = {
        "version": "1.0",
        "view": view or {},
        "sources": {},
        "layers": [],
        "layout": {
            "legend": {"visible": True, "position": "top-right"},
            "controls": [{"type": "navigation", "position": "top-right"}],
        },
        "thresholds": thresholds or {"maxFeatures": 50000, "timeoutMs": 30000},
    }
    return await self.save_mapspec(session_id, mapspec)

  async def set_view(
      self,
      session_id: str,
      center: Optional[List[float]] = None,
      zoom: Optional[float] = None,
      pitch: Optional[float] = None,
      bearing: Optional[float] = None,
  ) -> Dict[str, Any]:
    mapspec = await self.get_mapspec(session_id)
    if not mapspec:
      res = await self.init_project(session_id)
      mapspec = res["mapspec"]

    if "view" not in mapspec:
      mapspec["view"] = {}

    if center is not None:
      mapspec["view"]["center"] = center
    if zoom is not None:
      mapspec["view"]["zoom"] = zoom
    if pitch is not None:
      mapspec["view"]["pitch"] = pitch
    if bearing is not None:
      mapspec["view"]["bearing"] = bearing

    return await self.save_mapspec(session_id, mapspec)

  async def source_profile(
      self,
      session_id: str,
      source_id: str,
      geojson_data: Any,
  ) -> Dict[str, Any]:
    from app.services.spatial_meta_profiler import profile_geojson_source

    mapspec = await self.get_mapspec(session_id)
    if not mapspec:
      res = await self.init_project(session_id)
      mapspec = res["mapspec"]

    profile = profile_geojson_source(geojson_data)

    if "sources" not in mapspec:
      mapspec["sources"] = {}
    if source_id not in mapspec["sources"]:
      mapspec["sources"][source_id] = {"type": "geojson"}

    mapspec["sources"][source_id]["profile"] = profile
    # source-shape classification routes through mapspec_source (ADR-0008).
    # This site's policy: a dict → inlineData unconditionally; a string is
    # only stored as `url` if it looks like a real location (http/abs-path).
    # Non-locational strings (e.g. bare ref: cursors) are dropped here —
    # intentionally stricter than layer_upsert.
    from app.services.mapspec_source import store_data

    if isinstance(geojson_data, dict):
      store_data(mapspec["sources"][source_id], geojson_data)
    elif isinstance(geojson_data, str) and (geojson_data.startswith("http") or geojson_data.startswith("/")):
      store_data(mapspec["sources"][source_id], geojson_data)

    await self.save_mapspec(session_id, mapspec)
    return profile

  async def layer_upsert(
      self,
      session_id: str,
      layer: Dict[str, Any],
      source_data: Optional[Any] = None,
  ) -> Dict[str, Any]:
    from app.services.mapspec_layer_pipeline import process_layer_ingestion

    mapspec = await self.get_mapspec(session_id)
    if not mapspec:
      res = await self.init_project(session_id)
      mapspec = res["mapspec"]

    session_dir = self.get_session_dir(session_id)
    processed_layer, source_entry, suggested_view = process_layer_ingestion(
        mapspec, layer, source_data, session_dir
    )

    source_id = processed_layer.get("source", "default_source")
    mapspec.setdefault("sources", {})[source_id] = source_entry

    if suggested_view:
      mapspec.setdefault("view", {})
      mapspec["view"]["center"] = suggested_view["center"]
      mapspec["view"]["zoom"] = suggested_view["zoom"]

    layers = mapspec.setdefault("layers", [])
    updated = False
    for i, l in enumerate(layers):
      if l.get("id") == processed_layer.get("id"):
        layers[i] = processed_layer
        updated = True
        break
    if not updated:
      layers.append(processed_layer)

    res = await self.save_mapspec(session_id, mapspec)
    return {
        "success": True,
        "mapspec": res["mapspec"],
        "layer": layer,
    }

  async def layer_remove(self, session_id: str, layer_id: str) -> Dict[str, Any]:
    mapspec = await self.get_mapspec(session_id)
    if not mapspec:
      return {"success": False, "message": "MapSpec not found"}

    layers = mapspec.get("layers", [])
    filtered_layers = [l for l in layers if not _should_remove_layer(l, layer_id)]
    mapspec["layers"] = filtered_layers

    res = await self.save_mapspec(session_id, mapspec)
    await session_data_manager.remove_layer_from_state(session_id, layer_id)

    return {
        "success": True,
        "mapspec": res["mapspec"],
        "removed_id": layer_id,
    }


  async def compile_mapspec_cli(self, session_id: str, out_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Compile the session's MapSpec to MapLibre style via the TS CLI.

    Façade (Candidate #2): loads the MapSpec, hands its file path to the
    CompileCoordinator, returns the result. The coordinator owns the CLI
    subprocess; this store stays the data authority.
    """
    mapspec = await self.get_mapspec(session_id)
    if not mapspec:
      return {"success": False, "message": "MapSpec not found"}

    session_dir = self.get_session_dir(session_id)
    target_out_dir = out_dir or (session_dir / "compiled")
    mapspec_file = session_dir / "mapspec.json"
    from app.services import mapspec_compile_coordinator
    return await mapspec_compile_coordinator.compile_via_cli(mapspec_file, target_out_dir)

  async def validate_mapspec(self, session_id: str) -> Dict[str, Any]:
    """Validate the session's MapSpec structure pre-compile.

    Façade (Candidate #2): loads the MapSpec and delegates to the pure
    CompileCoordinator.validate. No session/storage access in the validator.
    """
    mapspec = await self.get_mapspec(session_id)
    if not mapspec:
      return {"success": False, "message": "MapSpec not found", "errors": ["MapSpec not initialized"]}
    from app.services import mapspec_compile_coordinator
    return mapspec_compile_coordinator.validate(mapspec)

  async def layout_set(
      self,
      session_id: str,
      legend: Optional[Dict[str, Any]] = None,
      controls: Optional[List[Dict[str, Any]]] = None,
      margins: Optional[Dict[str, Any]] = None,
  ) -> Dict[str, Any]:
    mapspec = await self.get_mapspec(session_id)
    if not mapspec:
      res = await self.init_project(session_id)
      mapspec = res["mapspec"]

    layout = mapspec.setdefault("layout", {})
    if legend is not None:
      layout["legend"] = legend
    if controls is not None:
      layout["controls"] = controls
    if margins is not None:
      layout["margins"] = margins

    res = await self.save_mapspec(session_id, mapspec)
    return {
        "success": True,
        "layout": res["mapspec"]["layout"],
        "mapspec": res["mapspec"],
    }

  async def checkpoint(self, session_id: str, checkpoint_id: Optional[str] = None) -> Dict[str, Any]:
    """Snapshot the session's MapSpec into a self-contained checkpoint.

    Façade (Candidate #2): loads the MapSpec and delegates to CheckpointStore.
    The coordinator owns the snapshot bytes + ref materialization.
    """
    mapspec = await self.get_mapspec(session_id)
    if not mapspec:
      return {"success": False, "message": "MapSpec not found"}
    session_dir = self.get_session_dir(session_id)
    from app.services import mapspec_checkpoint_store
    return await mapspec_checkpoint_store.snapshot(
        mapspec, session_dir, session_data_manager, checkpoint_id
    )

  async def rollback(self, session_id: str, checkpoint_id: str) -> Dict[str, Any]:
    """Roll back to a checkpoint. The store owns the post-rollback save.

    Façade (Candidate #2, decision p): CheckpointStore.rollback recovers the
    snapshot (files + ref payloads) and RETURNS the restored MapSpec; this
    store persists it as the sole write authority.
    """
    session_dir = self.get_session_dir(session_id)
    from app.services import mapspec_checkpoint_store
    recovered = await mapspec_checkpoint_store.rollback(
        session_dir, checkpoint_id, session_data_manager
    )
    if not recovered.get("success"):
      return recovered
    restored_mapspec = recovered["mapspec"]
    await self.save_mapspec(session_id, restored_mapspec)
    return {
        "success": True,
        "checkpoint_id": checkpoint_id,
        "mapspec": restored_mapspec,
        "summary": f"Rolled back to checkpoint '{checkpoint_id}'",
    }


mapspec_store = MapSpecStore()
