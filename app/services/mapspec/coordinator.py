"""CompileCoordinator (app/services/mapspec/coordinator.py).

拥有 MapSpec → MapLibre 编译 (TS CLI) 与 Pre-compile 结构校验纯函数。
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_CLI_PATH = PROJECT_ROOT / "frontend" / "lib" / "mapspec-compiler" / "cli.ts"
# Prefer the pinned local jiti binary: `npx jiti` hits the npm registry when
# node_modules is absent (cold CI runners blew the compile ceiling there).
_CLI_LOCAL_BIN = PROJECT_ROOT / "frontend" / "node_modules" / ".bin" / "jiti"
_CLI_COMPILE_TIMEOUT_SEC = 45


async def compile_via_cli(mapspec_file: Path, target_out_dir: Path) -> Dict[str, Any]:
    """通过 TS CLI 编译 MapSpec 文件为 MapLibre style.json + index.html"""
    target_out_dir.mkdir(parents=True, exist_ok=True)
    if _CLI_LOCAL_BIN.exists():
        cmd = [str(_CLI_LOCAL_BIN)]
    else:
        cmd = ["npx", "jiti"]
    cmd += [
        str(_CLI_PATH),
        "--input",
        str(mapspec_file),
        "--out-dir",
        str(target_out_dir),
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(PROJECT_ROOT / "frontend"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            # 45s ceiling: `npx tsx` cold-start is ~2.3s locally and the actual
            # compile is fast, but GitHub's 2-core CI runners are markedly slower
            # under load. The old 15s ceiling produced spurious timeouts on
            # test_validate_and_compile (it passed on retry) — 45s keeps a real
            # hang detectable while absorbing cold-start + runner variance.
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=_CLI_COMPILE_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise TimeoutError(
                f"MapSpec CLI compilation timed out after {_CLI_COMPILE_TIMEOUT_SEC}s"
            )

        stdout_text = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr_text = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

        report_file = target_out_dir / "compile-report.json"
        if report_file.exists():
            report = await asyncio.to_thread(
                lambda: json.loads(report_file.read_text(encoding="utf-8"))
            )
        else:
            report = {
                "success": proc.returncode == 0,
                "errors": [{"code": "CLI_ERROR", "message": stderr_text or stdout_text}],
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

    style: Dict[str, Any] = {}
    style_file = target_out_dir / "style.json"
    if style_file.exists():
        try:
            style = await asyncio.to_thread(
                lambda: json.loads(style_file.read_text(encoding="utf-8"))
            )
        except Exception as e:
            logger.warning(f"Could not read back compiled style.json: {e}")

    return {
        "success": report.get("success", False),
        "report": report,
        "out_dir": str(target_out_dir),
        "style": style,
    }


def validate(mapspec: Dict[str, Any]) -> Dict[str, Any]:
    """MapSpec 结构 Pre-compile 校验纯函数"""
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
                    # Method-aware minimum: interpolate needs a range (>=2 stops);
                    # a MapLibre `step` with a single threshold + default is valid,
                    # so step needs >=1. Requiring >=2 for step was a false positive
                    # that rejected legitimate graduated classifications.
                    min_stops = 2 if m_type == "interpolate" else 1
                    if len(stops) < min_stops:
                        errors.append({"code": "INVALID_STOPS_COUNT", "message": f"Property '{prop}' in layer '{layer.get('id')}' requires at least {min_stops} stops for {m_type}"})
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
