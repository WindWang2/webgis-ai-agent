import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import subprocess

from app.services.session_data import session_data_manager

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BASE_STORAGE_DIR = PROJECT_ROOT / ".webgis-agent"


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
        "view": view or {"center": [0.0, 0.0], "zoom": 2.0},
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
    if isinstance(geojson_data, dict):
      mapspec["sources"][source_id]["inlineData"] = geojson_data
    elif isinstance(geojson_data, str) and (geojson_data.startswith("http") or geojson_data.startswith("/")):
      mapspec["sources"][source_id]["url"] = geojson_data

    await self.save_mapspec(session_id, mapspec)
    return profile

  async def layer_upsert(
      self,
      session_id: str,
      layer: Dict[str, Any],
      source_data: Optional[Any] = None,
  ) -> Dict[str, Any]:
    from app.services.spatial_meta_profiler import profile_geojson_source

    mapspec = await self.get_mapspec(session_id)
    if not mapspec:
      res = await self.init_project(session_id)
      mapspec = res["mapspec"]

    source_id = layer.get("source", "default_source")
    if "sources" not in mapspec:
      mapspec["sources"] = {}
    if source_id not in mapspec["sources"]:
      mapspec["sources"][source_id] = {"type": "geojson"}

    source_entry = mapspec["sources"][source_id]

    # Auto-profiling & auto-view injection (User Stories 12 & 13)
    data_to_profile = source_data or source_entry.get("inlineData") or source_entry.get("url") or source_entry.get("dataPath")
    if data_to_profile and "profile" not in source_entry:
      try:
        profile = profile_geojson_source(data_to_profile)
        source_entry["profile"] = profile

        # First dissectable layer auto-writes view when view is unset/default
        curr_center = mapspec.get("view", {}).get("center", [0.0, 0.0])
        if (curr_center == [0.0, 0.0] or curr_center == [0, 0]) and "suggestedView" in profile:
          mapspec["view"]["center"] = profile["suggestedView"]["center"]
          mapspec["view"]["zoom"] = profile["suggestedView"]["zoom"]
      except Exception as e:
        logger.warning(f"Auto-profiling failed for layer {layer.get('id')}: {e}")

    # Upsert layer into mapspec["layers"]
    layers = mapspec.setdefault("layers", [])
    updated = False
    for i, l in enumerate(layers):
      if l.get("id") == layer.get("id"):
        layers[i] = layer
        updated = True
        break
    if not updated:
      layers.append(layer)

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
    filtered_layers = [l for l in layers if l.get("id") != layer_id and l.get("id") != f"{layer_id}-label"]
    mapspec["layers"] = filtered_layers

    res = await self.save_mapspec(session_id, mapspec)
    await session_data_manager.remove_layer_from_state(session_id, layer_id)

    return {
        "success": True,
        "mapspec": res["mapspec"],
        "removed_id": layer_id,
    }


  async def compile_mapspec_cli(self, session_id: str, out_dir: Optional[Path] = None) -> Dict[str, Any]:
    mapspec = await self.get_mapspec(session_id)
    if not mapspec:
      return {"success": False, "message": "MapSpec not found"}

    session_dir = self.get_session_dir(session_id)
    target_out_dir = out_dir or (session_dir / "compiled")
    target_out_dir.mkdir(parents=True, exist_ok=True)

    mapspec_file = session_dir / "mapspec.json"
    cli_path = PROJECT_ROOT / "frontend" / "lib" / "mapspec-compiler" / "cli.ts"

    cmd = [
        "npx",
        "tsx",
        str(cli_path),
        "--input",
        str(mapspec_file),
        "--out-dir",
        str(target_out_dir),
    ]

    try:
      proc = subprocess.run(
          cmd,
          cwd=str(PROJECT_ROOT / "frontend"),
          capture_output=True,
          text=True,
          timeout=15,
      )
      report_file = target_out_dir / "compile-report.json"
      if report_file.exists():
        with open(report_file, "r", encoding="utf-8") as f:
          report = json.load(f)
      else:
        report = {
            "success": proc.returncode == 0,
            "errors": [{"code": "CLI_ERROR", "message": proc.stderr or proc.stdout}],
            "warnings": [],
            "stats": {"sourceCount": 0, "layerCount": 0, "compiledLayerCount": 0, "labelLayerCount": 0},
        }
    except Exception as e:
      logger.warning(f"Node CLI compilation failed: {e}")
      report = {
          "success": False,
          "errors": [{"code": "CLI_UNAVAILABLE", "message": str(e)}],
          "warnings": [],
          "stats": {
              "sourceCount": len(mapspec.get("sources", {})),
              "layerCount": len(mapspec.get("layers", [])),
              "compiledLayerCount": 0,
              "labelLayerCount": 0,
          },
      }

    # The TS CLI is the sole compiler; it writes style.json itself. Do NOT
    # overwrite it — read it back only to populate the return value. Earlier
    # code ran a second (stale, divergent) Python compiler here and clobbered
    # the authoritative TS output.
    style = {}
    style_file = target_out_dir / "style.json"
    if style_file.exists():
      try:
        with open(style_file, "r", encoding="utf-8") as f:
          style = json.load(f)
      except Exception as e:
        logger.warning(f"Could not read back compiled style.json: {e}")

    return {
        "success": report.get("success", False),
        "report": report,
        "out_dir": str(target_out_dir),
        "style": style,
    }

  async def validate_mapspec(self, session_id: str) -> Dict[str, Any]:
    mapspec = await self.get_mapspec(session_id)
    if not mapspec:
      return {"success": False, "message": "MapSpec not found", "errors": ["MapSpec not initialized"]}

    errors: List[Dict[str, Any]] = []
    warnings: List[str] = []

    sources = mapspec.get("sources", {})
    layers = mapspec.get("layers", [])

    if not sources:
      errors.append({"code": "MISSING_SOURCES", "message": "No sources defined in MapSpec"})

    source_keys = set(sources.keys())
    for layer in layers:
      l_source = layer.get("source")
      if l_source not in source_keys:
        errors.append({"code": "INVALID_SOURCE_REF", "message": f"Layer '{layer.get('id')}' references missing source '{l_source}'"})

      paint = layer.get("paint", {})
      for prop, method in paint.items():
        if isinstance(method, dict):
          m_type = method.get("method")
          if m_type in ("interpolate", "step"):
            stops = method.get("stops", [])
            if len(stops) < 2:
              errors.append({"code": "INVALID_STOPS_COUNT", "message": f"Property '{prop}' in layer '{layer.get('id')}' requires at least 2 stops"})
            else:
              for i in range(len(stops) - 1):
                if stops[i][0] >= stops[i + 1][0]:
                  errors.append({"code": "NON_INCREASING_STOPS", "message": f"Property '{prop}' stops must be strictly increasing: {stops[i][0]} >= {stops[i+1][0]}"})
                  break

    return {
        "success": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "summary": "Validation passed" if len(errors) == 0 else f"Validation failed with {len(errors)} errors",
    }

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
    mapspec = await self.get_mapspec(session_id)
    if not mapspec:
      return {"success": False, "message": "MapSpec not found"}

    ckpt_id = checkpoint_id or f"ckpt_{int(time.time() * 1000)}"
    session_dir = self.get_session_dir(session_id)
    ckpt_dir = session_dir / "checkpoints" / ckpt_id
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    # 1. Copy mapspec.json
    with open(ckpt_dir / "mapspec.json", "w", encoding="utf-8") as f:
      json.dump(mapspec, f, ensure_ascii=False, indent=2)

    # 2. Materialize payloads behind referenced ref_ids (Decision 3 & Story 31)
    materialized_refs: Dict[str, Any] = {}
    for s_id, source in mapspec.get("sources", {}).items():
      ref_candidate = source.get("url") or source.get("dataPath") or ""
      if isinstance(ref_candidate, str) and ref_candidate.startswith("ref:"):
        ref_data = await session_data_manager.get(session_id, ref_candidate)
        if ref_data is not None:
          materialized_refs[ref_candidate] = ref_data

    with open(ckpt_dir / "materialized_refs.json", "w", encoding="utf-8") as f:
      json.dump(materialized_refs, f, ensure_ascii=False, indent=2)

    meta = {
        "checkpoint_id": ckpt_id,
        "timestamp": time.time(),
        "ref_count": len(materialized_refs),
    }
    with open(ckpt_dir / "checkpoint_meta.json", "w", encoding="utf-8") as f:
      json.dump(meta, f, ensure_ascii=False, indent=2)

    return {
        "success": True,
        "checkpoint_id": ckpt_id,
        "checkpoint_dir": str(ckpt_dir),
        "ref_count": len(materialized_refs),
        "summary": f"Checkpoint '{ckpt_id}' created with {len(materialized_refs)} materialized refs",
    }

  async def rollback(self, session_id: str, checkpoint_id: str) -> Dict[str, Any]:
    session_dir = self.get_session_dir(session_id)
    ckpt_dir = session_dir / "checkpoints" / checkpoint_id
    if not ckpt_dir.exists():
      return {"success": False, "message": f"Checkpoint '{checkpoint_id}' not found"}

    # 1. Restore mapspec.json
    mapspec_file = ckpt_dir / "mapspec.json"
    with open(mapspec_file, "r", encoding="utf-8") as f:
      mapspec = json.load(f)

    # 2. Restore materialized ref_ids into session_data_manager
    refs_file = ckpt_dir / "materialized_refs.json"
    if refs_file.exists():
      with open(refs_file, "r", encoding="utf-8") as f:
        refs_data = json.load(f)
        for ref_id, payload in refs_data.items():
          await session_data_manager.overwrite(session_id, ref_id, payload)

    # 3. Dual-write restored MapSpec to map_state
    await self.save_mapspec(session_id, mapspec)

    return {
        "success": True,
        "checkpoint_id": checkpoint_id,
        "mapspec": mapspec,
        "summary": f"Rolled back to checkpoint '{checkpoint_id}'",
    }


mapspec_store = MapSpecStore()
