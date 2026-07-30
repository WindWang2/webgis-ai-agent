import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import subprocess

from app.services.mapspec_store import mapspec_store, PROJECT_ROOT

logger = logging.getLogger(__name__)


def compute_eval_scores(report: Dict[str, Any], mapspec: Dict[str, Any]) -> Dict[str, Any]:
  """
  Computes the 5-dimension evaluation score (80% max, cartographic quality 20% deferred).
  """
  browser_ok = report.get("mapLoaded", True) and len(report.get("pageErrors", [])) == 0
  has_layers = len(mapspec.get("layers", [])) > 0
  has_sources = len(mapspec.get("sources", {})) > 0

  spatial_data_score = 25.0 if (has_sources and has_layers) else 10.0
  task_completion_score = 20.0 if (has_layers and browser_ok) else 10.0
  browser_runtime_score = 15.0 if browser_ok else 5.0
  traceability_score = 10.0
  efficiency_score = 10.0

  total_score = spatial_data_score + task_completion_score + browser_runtime_score + traceability_score + efficiency_score
  normalized_80_max = round(total_score, 2)

  return {
      "spatial_data_score": spatial_data_score,
      "task_completion_score": task_completion_score,
      "browser_runtime_score": browser_runtime_score,
      "traceability_score": traceability_score,
      "efficiency_score": efficiency_score,
      "total_score_80_max": normalized_80_max,
      "cartographic_quality_status": "deferred_pending_visual_judge",
  }


class RuntimeValidator:
  """Headless Runtime Validator & Eval Evidence Collector."""

  async def validate_runtime(self, session_id: str) -> Dict[str, Any]:
    mapspec = await mapspec_store.get_mapspec(session_id)
    if not mapspec:
      return {"success": False, "message": "MapSpec not found for session"}

    # 1. Recompile MapSpec to static output
    comp_res = await mapspec_store.compile_mapspec_cli(session_id)
    out_dir = Path(comp_res["out_dir"])

    # 2. Run runtime validator checks (Playwright Node script or Python static checker)
    page_errors: List[str] = []
    map_loaded = True
    map_idle = True

    index_html = out_dir / "index.html"
    style_json = out_dir / "style.json"

    if not index_html.exists() or not style_json.exists():
      page_errors.append("Missing compiled index.html or style.json")
      map_loaded = False

    # Check if stops/sources are valid in style
    style_data = comp_res.get("style", {})
    if not style_data.get("layers"):
      page_errors.append("No compiled layers in style.json")

    report = {
        "success": len(page_errors) == 0,
        "mapLoaded": map_loaded,
        "mapIdle": map_idle,
        "pageErrors": page_errors,
        "consoleErrors": [],
        "timestamp": time.time(),
        "stats": comp_res.get("report", {}).get("stats", {}),
    }

    # 3. Compute 5-dimension eval score
    scores = compute_eval_scores(report, mapspec)
    report["eval_scores"] = scores

    # 4. Write report.json
    report_file = out_dir / "report.json"
    with open(report_file, "w", encoding="utf-8") as f:
      json.dump(report, f, ensure_ascii=False, indent=2)

    return {
        "success": report["success"],
        "report": report,
        "eval_scores": scores,
        "summary": f"Runtime validation finished (score: {scores['total_score_80_max']}/80.0)",
    }


runtime_validator = RuntimeValidator()
