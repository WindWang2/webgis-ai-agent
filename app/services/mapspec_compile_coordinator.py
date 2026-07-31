"""CompileCoordinator — owns MapSpec → MapLibre compilation and validation.

Extracted from MapSpecStore (architecture review Candidate #2). This module
holds the two read-only, non-CRUD concerns that were mixed into the store:
compiling a MapSpec to MapLibre style.json via the TS CLI, and validating a
MapSpec's structure pre-compile.

Pure-ish by design (decision ii): functions take a MapSpec (or its file path)
and an output dir as arguments — no back-reference to the store, no session
storage access. The store remains the sole write authority and calls these
with the data it has already loaded. This makes both operations unit-testable
without any session/storage fixture.
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CLI_PATH = PROJECT_ROOT / "frontend" / "lib" / "mapspec-compiler" / "cli.ts"


async def compile_via_cli(mapspec_file: Path, target_out_dir: Path) -> Dict[str, Any]:
  """Compile a MapSpec file to MapLibre style.json + index.html via the TS CLI.

  The TS CLI is the sole compiler (architecture review #1 deleted the divergent
  Python copy). It writes style.json itself; this function reads it back only to
  populate the return value — it never overwrites the authoritative output.
  """
  target_out_dir.mkdir(parents=True, exist_ok=True)
  cmd = [
      "npx",
      "tsx",
      str(_CLI_PATH),
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
            "sourceCount": 0,
            "layerCount": 0,
            "compiledLayerCount": 0,
            "labelLayerCount": 0,
        },
    }

  # Read back the authoritative TS-produced style.json. Never overwrite it.
  style: Dict[str, Any] = {}
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


def validate(mapspec: Dict[str, Any]) -> Dict[str, Any]:
  """Validate a MapSpec's structure pre-compile (pure function of the dict).

  Checks: sources present, layer→source references valid, stops validity for
  interpolate/step style methods. Returns a structured pass/fail. This is the
  cheap deterministic layer that rejects bad input before the TS compile runs.
  """
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
