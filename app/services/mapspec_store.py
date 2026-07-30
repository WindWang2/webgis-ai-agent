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


def compile_mapspec_python(mapspec: Dict[str, Any]) -> Dict[str, Any]:
  """
  Python implementation of MapSpec compilation to MapLibre style JSON.
  Used as a fast path and fallback when Node CLI is unavailable.
  """
  sources: Dict[str, Any] = {}
  for k, s in mapspec.get("sources", {}).items():
    if "inlineData" in s:
      sources[k] = {"type": "geojson", "data": s["inlineData"]}
    elif "url" in s or "dataPath" in s:
      sources[k] = {"type": "geojson", "data": s.get("url") or s.get("dataPath")}
    else:
      sources[k] = {"type": "geojson", "data": {"type": "FeatureCollection", "features": []}}

  compiled_layers: List[Dict[str, Any]] = []
  for layer in mapspec.get("layers", []):
    l_type = layer.get("type", "circle")
    maplibre_layer: Dict[str, Any] = {
        "id": layer["id"],
        "type": l_type,
        "source": layer["source"],
        "layout": {},
        "paint": {},
    }

    paint = layer.get("paint", {})
    if l_type == "circle":
      if "color" in paint:
        maplibre_layer["paint"]["circle-color"] = _compile_style_method(paint["color"])
      if "radius" in paint:
        maplibre_layer["paint"]["circle-radius"] = _compile_style_method(paint["radius"])
      if "opacity" in paint:
        maplibre_layer["paint"]["circle-opacity"] = _compile_style_method(paint["opacity"])
    elif l_type == "line":
      if "color" in paint:
        maplibre_layer["paint"]["line-color"] = _compile_style_method(paint["color"])
      if "width" in paint:
        maplibre_layer["paint"]["line-width"] = _compile_style_method(paint["width"])
    elif l_type == "fill":
      if "color" in paint:
        maplibre_layer["paint"]["fill-color"] = _compile_style_method(paint["color"])
      if "opacity" in paint:
        maplibre_layer["paint"]["fill-opacity"] = _compile_style_method(paint["opacity"])

    compiled_layers.append(maplibre_layer)

    # Label split
    label_spec = layer.get("label") or (
        {"field": layer["layout"]["labelField"]} if layer.get("layout", {}).get("labelField") else None
    )
    if label_spec and label_spec.get("field"):
      label_layer = {
          "id": f"{layer['id']}-label",
          "type": "symbol",
          "source": layer["source"],
          "layout": {
              "text-field": ["get", label_spec["field"]],
              "text-size": _compile_style_method(label_spec.get("size", 12)),
          },
          "paint": {
              "text-color": _compile_style_method(label_spec.get("color", "#000000")),
          },
      }
      compiled_layers.append(label_layer)

  view = mapspec.get("view", {})
  style = {
      "version": 8,
      "name": "MapSpec Compiled Style",
      "center": view.get("center", [0.0, 0.0]),
      "zoom": view.get("zoom", 2.0),
      "sources": sources,
      "layers": compiled_layers,
  }

  return style


def _compile_style_method(method: Any) -> Any:
  if not isinstance(method, dict) or "type" not in method:
    return method

  m_type = method["type"]
  if m_type == "constant":
    return method.get("value")
  elif m_type == "field":
    return ["get", method.get("field")]
  elif m_type == "interpolate":
    field_expr = ["to-number", ["get", method.get("field")]]
    stops = []
    for s in method.get("stops", []):
      stops.extend(s)
    return ["interpolate", ["linear"], field_expr, *stops]
  elif m_type == "step":
    field_expr = ["to-number", ["get", method.get("field")]]
    raw_stops = method.get("stops", [])
    if not raw_stops:
      return method.get("default", 0)
    init_val = method.get("default", raw_stops[0][1])
    stops = []
    start_idx = 0 if method.get("default") is not None else 1
    for s in raw_stops[start_idx:]:
      stops.extend(s)
    return ["step", field_expr, init_val, *stops]
  elif m_type == "match":
    field_expr = ["get", method.get("field")]
    cases = []
    for c in method.get("cases", []):
      cases.extend(c)
    default_val = method.get("default", "")
    return ["match", field_expr, *cases, default_val]

  return method.get("value")


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

    # 2. Compile MapSpec to style JSON
    compiled_style = compile_mapspec_python(mapspec)

    # 3. Dual-write to map_state in SessionStore
    await session_data_manager.set_map_state(session_id, "mapspec", mapspec)
    await session_data_manager.set_map_state(session_id, "layers", compiled_style.get("layers", []))
    if mapspec.get("view"):
      await session_data_manager.set_map_state(session_id, "view", mapspec["view"])

    return {
        "mapspec": mapspec,
        "style": compiled_style,
    }

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


mapspec_store = MapSpecStore()
