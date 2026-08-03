"""Headless Runtime Validator & Eval Evidence Collector.

Drives the TS/Playwright validator over the compiler's static output via a Node
subprocess (same pattern as `mapspec_store.compile_mapspec_cli` invoking the
compiler CLI). The browser work — MapLibre load/idle waits, console/page/network
error capture, canvas decode + blank-detection, control overflow/collision —
lives in `frontend/lib/mapspec-compiler/runtime-validate.ts` (Seam C). This
module owns the Python side: invoke it, parse its JSON report, compute the
5-dimension eval score, and persist report.json.
"""
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.mapspec_store import mapspec_store, PROJECT_ROOT

logger = logging.getLogger(__name__)

# How long to let Chromium run before giving up. Browser-driven validation is
# far slower than a static check, so this is deliberately generous.
RUNTIME_TIMEOUT_S = 90.0

# Path to the Seam C Node script, mirroring how compile_mapspec_cli resolves cli.ts.
RUNTIME_VALIDATE_SCRIPT = (
    PROJECT_ROOT / "frontend" / "lib" / "mapspec-compiler" / "runtime-validate.ts"
)


def compute_eval_scores(report: Dict[str, Any], mapspec: Dict[str, Any]) -> Dict[str, Any]:
  """
  Computes the 5-dimension evaluation score (100% max, incorporating the visual judge
  segment for cartographic quality).
  """
  # Browser health now reflects the *actual* headless run, not a hardcoded True.
  map_loaded = bool(report.get("mapLoaded"))
  map_idle = bool(report.get("mapIdle"))
  no_errors = (
      len(report.get("pageErrors", [])) == 0
      and len(report.get("consoleErrors", [])) == 0
      and len(report.get("failedRequests", [])) == 0
      and report.get("fatalError") is None
  )
  browser_ok = map_loaded and map_idle and no_errors

  has_layers = len(mapspec.get("layers", [])) > 0
  has_sources = len(mapspec.get("sources", {})) > 0

  spatial_data_score = 25.0 if (has_sources and has_layers) else 10.0
  task_completion_score = 20.0 if (has_layers and browser_ok) else 10.0
  browser_runtime_score = 15.0 if browser_ok else 5.0
  # Traceability: rewarded when a full evidence trail (report + screenshot) exists.
  traceability_score = 10.0 if report.get("_evidenceComplete") else 6.0
  # Efficiency: penalise fatal failures (the run cost something for no result).
  efficiency_score = 10.0 if report.get("fatalError") is None else 4.0

  total_score_80 = round(
      spatial_data_score
      + task_completion_score
      + browser_runtime_score
      + traceability_score
      + efficiency_score,
      2,
  )

  # ── Visual-Judge Cartographic Aesthetic Quality Segment (20.0 pts max) ──
  canvas_stats = report.get("canvas") or {}
  ctrl_stats = report.get("controls") or {}

  # 1. Visual Contrast & Luminance Variance (8.0 pts max)
  lum_stddev = float(canvas_stats.get("luminanceStdDev", 0.0))
  dominant_ratio = float(canvas_stats.get("dominantRatio", 1.0))
  if lum_stddev >= 15.0 and dominant_ratio < 0.90:
      visual_contrast_score = 8.0
  elif lum_stddev >= 5.0:
      visual_contrast_score = round(min(8.0, 8.0 * (lum_stddev / 15.0)), 2)
  else:
      visual_contrast_score = 2.0

  # 2. Control & Label Collision Score (6.0 pts max)
  overflow_count = len(ctrl_stats.get("overflow", []))
  collision_count = len(ctrl_stats.get("collisions", []))
  total_issues = overflow_count + collision_count
  label_collision_score = max(0.0, round(6.0 - (total_issues * 2.0), 2))

  # 3. Layout Balance & Composition Score (6.0 pts max)
  transparent_ratio = float(canvas_stats.get("transparentRatio", 1.0))
  if transparent_ratio < 0.80 and not canvas_stats.get("blank", False):
      layout_balance_score = 6.0
  else:
      layout_balance_score = 3.0

  cartographic_quality_score = round(
      visual_contrast_score + label_collision_score + layout_balance_score, 2
  )
  total_score_100 = round(total_score_80 + cartographic_quality_score, 2)

  return {
      "spatial_data_score": spatial_data_score,
      "task_completion_score": task_completion_score,
      "browser_runtime_score": browser_runtime_score,
      "traceability_score": traceability_score,
      "efficiency_score": efficiency_score,
      "total_score_80_max": total_score_80,
      "cartographic_quality_score": cartographic_quality_score,
      "total_score_100_max": total_score_100,
      "cartographic_quality_status": "evaluated_by_visual_judge",
      "visual_judge_details": {
          "visual_contrast_score": visual_contrast_score,
          "label_collision_score": label_collision_score,
          "layout_balance_score": layout_balance_score,
      },
  }


class RuntimeValidator:
  """Headless Runtime Validator & Eval Evidence Collector."""

  async def validate_runtime(self, session_id: str) -> Dict[str, Any]:
    mapspec = await mapspec_store.get_mapspec(session_id)
    if not mapspec:
      return {"success": False, "message": "MapSpec not found for session"}

    # 1. Recompile MapSpec to static output (index.html + style.json).
    comp_res = await mapspec_store.compile_mapspec_cli(session_id)
    if not comp_res.get("success"):
      return {
          "success": False,
          "message": "Compilation failed; cannot run runtime validation",
          "compile_report": comp_res.get("report"),
      }
    out_dir = Path(comp_res["out_dir"])
    runtime_dir = out_dir.parent / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    # 2. Drive the headless Playwright validator over the compiled output.
    report = self._run_headless_validator(out_dir, runtime_dir)

    # 3. Enrich + compute the 5-dimension eval score.
    report["timestamp"] = time.time()
    report["stats"] = comp_res.get("report", {}).get("stats", {})
    report["_evidenceComplete"] = self._evidence_complete(runtime_dir)
    scores = compute_eval_scores(report, mapspec)
    report["eval_scores"] = scores

    # 4. Persist the report alongside the screenshot/trace.
    with open(runtime_dir / "report.json", "w", encoding="utf-8") as f:
      json.dump(report, f, ensure_ascii=False, indent=2)

    # valid = the browser contract held AND the canvas is not blank. Note the
    # canvas-blank signal is a *risk* flag: a legitimate minimal map can trip
    # it, so a 'valid=False' here still merits human cartographic review.
    valid = (
        report.get("mapLoaded")
        and report.get("mapIdle")
        and report.get("fatalError") is None
        and len(report.get("pageErrors", [])) == 0
        and len(report.get("consoleErrors", [])) == 0
        and not (report.get("canvas") or {}).get("blank", True)
    )

    return {
        "valid": valid,
        "score": scores["total_score_80_max"],
        "report": report,
        "eval_scores": scores,
        "runtime_dir": str(runtime_dir),
        "summary": (
            f"Runtime validation {'passed' if valid else 'failed'} "
            f"(score: {scores['total_score_80_max']}/80.0)"
        ),
    }

  def _run_headless_validator(
      self, input_dir: Path, out_dir: Path
  ) -> Dict[str, Any]:
    """Invoke the Node Playwright validator and parse its JSON report.

    The script always prints one JSON object to stdout and exits 0 on pass / 1
    on failure. We treat a non-zero exit as a failed validation (not a crash):
    the report itself carries the reason via `fatalError` / error arrays.
    """
    if not RUNTIME_VALIDATE_SCRIPT.exists():
      logger.error("Runtime validator script missing: %s", RUNTIME_VALIDATE_SCRIPT)
      return {
          "mapLoaded": False,
          "mapIdle": False,
          "consoleErrors": [],
          "pageErrors": [f"validator script missing: {RUNTIME_VALIDATE_SCRIPT}"],
          "failedRequests": [],
          "canvas": None,
          "controls": {"overflow": [], "collisions": []},
          "fatalError": f"validator script missing: {RUNTIME_VALIDATE_SCRIPT}",
      }

    cmd = [
        "npx",
        "tsx",
        str(RUNTIME_VALIDATE_SCRIPT),
        "--input-dir",
        str(input_dir),
        "--out-dir",
        str(out_dir),
        "--timeout",
        "45000",
    ]
    try:
      proc = subprocess.run(
          cmd,
          cwd=str(PROJECT_ROOT / "frontend"),
          capture_output=True,
          text=True,
          timeout=RUNTIME_TIMEOUT_S,
      )
      # The script writes the report to stdout regardless of exit code.
      if proc.stdout.strip():
        return json.loads(proc.stdout)
      # No stdout → the script itself failed to start (e.g. missing chromium).
      return {
          "mapLoaded": False,
          "mapIdle": False,
          "consoleErrors": [],
          "pageErrors": [],
          "failedRequests": [],
          "canvas": None,
          "controls": {"overflow": [], "collisions": []},
          "fatalError": (
              proc.stderr.strip()
              or f"validator exited {proc.returncode} with no output"
          ),
      }
    except subprocess.TimeoutExpired:
      return {
          "mapLoaded": False,
          "mapIdle": False,
          "consoleErrors": [],
          "pageErrors": [],
          "failedRequests": [],
          "canvas": None,
          "controls": {"overflow": [], "collisions": []},
          "fatalError": f"validator exceeded {RUNTIME_TIMEOUT_S}s timeout",
      }
    except Exception as e:  # noqa: BLE001 - surface any failure in the report
      logger.exception("Runtime validator subprocess failed")
      return {
          "mapLoaded": False,
          "mapIdle": False,
          "consoleErrors": [],
          "pageErrors": [str(e)],
          "failedRequests": [],
          "canvas": None,
          "controls": {"overflow": [], "collisions": []},
          "fatalError": str(e),
      }

  @staticmethod
  def _evidence_complete(runtime_dir: Path) -> bool:
    """True when the full evidence trail (screenshot + trace + report) exists."""
    return (
        (runtime_dir / "map.png").exists()
        and (runtime_dir / "trace.zip").exists()
    )


runtime_validator = RuntimeValidator()
