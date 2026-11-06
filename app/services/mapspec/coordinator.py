"""CompileCoordinator (app/services/mapspec/coordinator.py).

拥有 MapSpec → MapLibre 编译 (TS CLI) 与 Pre-compile 结构校验纯函数。
"""
from __future__ import annotations

import asyncio
import os
import json
import logging
import math
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
    await asyncio.to_thread(target_out_dir.mkdir, parents=True, exist_ok=True)
    executable = _CLI_LOCAL_BIN.with_suffix(".cmd") if os.name == "nt" else _CLI_LOCAL_BIN
    cmd = [str(executable)]  # Missing pinned dependency fails closed; never download at runtime.
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
        if await asyncio.to_thread(report_file.exists):
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
    if await asyncio.to_thread(style_file.exists):
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
    """Malformed caller structures produce validation errors, never TypeError."""
    try:
        return _validate_structure(mapspec)
    except (TypeError, ValueError, IndexError, KeyError, AttributeError, OverflowError) as exc:
        logger.debug("MapSpec structure invalid: %s", type(exc).__name__)
        return {"success": False, "errors": [{"code": "INVALID_MAPSPEC_SHAPE", "message": "Invalid MapSpec structure"}], "warnings": []}


def _validate_structure(mapspec: Dict[str, Any]) -> dict[str, object]:
    """MapSpec 结构 Pre-compile 校验纯函数"""
    errors: List[Dict[str, Any]] = []
    warnings: List[str] = []
    if not isinstance(mapspec, dict) or not isinstance(mapspec.get("sources", {}), dict) or not isinstance(mapspec.get("layers", []), list):
        return {"success": False, "errors": [{"code": "INVALID_MAPSPEC_SHAPE", "message": "sources must be an object and layers an array"}], "warnings": []}

    sources = mapspec.get("sources", {})
    layers = mapspec.get("layers", [])

    if not sources:
        errors.append({"code": "MISSING_SOURCES", "message": "No sources defined in MapSpec"})

    source_keys = set(sources.keys())
    for layer in layers:
        if not isinstance(layer, dict):
            errors.append({"code": "INVALID_LAYER_SHAPE", "message": "layer must be an object"})
            continue
        l_source = layer.get("source")
        if l_source not in source_keys:
            errors.append({"code": "INVALID_SOURCE_REF", "message": f"Layer '{layer.get('id')}' references missing source '{l_source}'"})

        paint = layer.get("paint", {})
        if not isinstance(paint, dict):
            errors.append({"code": "INVALID_PAINT_SHAPE", "message": "paint must be an object"})
            continue
        for prop, method in paint.items():
            if isinstance(method, dict):
                m_type = method.get("method")
                if m_type in ("interpolate", "step"):
                    stops = method.get("stops", [])
                    # Method-aware minimum: interpolate needs a range (>=2 stops);
                    # a MapLibre `step` with a single threshold + default is valid,
                    # so step needs >=1. Requiring >=2 for step was a false positive
                    # that rejected legitimate graduated classifications.
                    if not isinstance(stops, list) or any(not isinstance(stop, (list, tuple)) or len(stop) < 2 for stop in stops):
                        errors.append({"code": "INVALID_STOPS_SHAPE", "message": "stops require [number, value] pairs"})
                        continue
                    if any(isinstance(stop[0], bool) or not isinstance(stop[0], (int, float))
                           or not math.isfinite(stop[0]) for stop in stops):
                        errors.append({"code": "INVALID_STOP_VALUE", "message": "stop thresholds must be finite numbers"})
                        continue
                    min_stops = 2 if m_type == "interpolate" else 1
                    if len(stops) < min_stops:
                        errors.append({"code": "INVALID_STOPS_COUNT", "message": f"Property '{prop}' in layer '{layer.get('id')}' requires at least {min_stops} stops for {m_type}"})
                    else:
                        for i in range(len(stops) - 1):
                            left, right = stops[i][0], stops[i + 1][0]
                            if (isinstance(left, bool) or isinstance(right, bool)
                                    or not isinstance(left, (int, float))
                                    or not isinstance(right, (int, float))
                                    or not math.isfinite(left) or not math.isfinite(right)):
                                errors.append({"code": "INVALID_STOP_VALUE", "message": f"Property {prop!r} requires numeric stops"})
                                break
                            if left >= right:
                                errors.append({"code": "NON_INCREASING_STOPS", "message": f"Property '{prop}' stops must be strictly increasing: {stops[i][0]} >= {stops[i+1][0]}"})
                                break

    # ADR-0199：场景协议校验（scene.terrain.source 悬空 / 非 raster-dem =
    # 阻塞，与图层 INVALID_SOURCE_REF 同 fail-closed 口径；两个 code 都在
    # lifecycle 的 BLOCKING_VALIDATION_CODES 内 —— 写路径拒绝引入，冷路径
    # 披露；shape 校验在 SetSceneIntent 引擎分支，冷路径 schema 另行把关）。
    scene = mapspec.get("scene")
    if isinstance(scene, dict) and isinstance(scene.get("terrain"), dict):
        terrain_source = scene["terrain"].get("source")
        if not isinstance(terrain_source, str) or not terrain_source:
            errors.append({
                "code": "SCENE_TERRAIN_SOURCE_REF",
                "message": "scene.terrain.source must be a non-empty source id",
            })
        elif terrain_source not in source_keys:
            errors.append({
                "code": "SCENE_TERRAIN_SOURCE_REF",
                "message": (
                    f"scene.terrain references missing raster-dem source "
                    f"'{terrain_source}'"
                ),
            })
        elif (sources.get(terrain_source) or {}).get("type") != "raster-dem":
            errors.append({
                "code": "SCENE_TERRAIN_SOURCE_TYPE",
                "message": (
                    f"scene.terrain source '{terrain_source}' is "
                    f"'{(sources.get(terrain_source) or {}).get('type')}', "
                    f"expected 'raster-dem'"
                ),
            })

    # v1.7（G3D02）：scene.assets 校验 —— 与 terrain 同 fail-closed 口径：
    # 悬空/错型/无数据指针 = 想象的 3D 证据，写路径拒绝引入。放置域与
    # 证据披露归 scene_quality（质量门），此处只把数据面引用关死。
    if isinstance(scene, dict) and isinstance(scene.get("assets"), list):
        seen_asset_ids = set()
        for asset in scene["assets"]:
            if not isinstance(asset, dict):
                continue
            asset_id = asset.get("id")
            if isinstance(asset_id, str) and asset_id:
                if asset_id in seen_asset_ids:
                    errors.append({
                        "code": "SCENE_ASSET_DUPLICATE_ID",
                        "message": f"duplicate scene asset id '{asset_id}'",
                    })
                seen_asset_ids.add(asset_id)
            asset_source = asset.get("source")
            if not isinstance(asset_source, str) or not asset_source:
                errors.append({
                    "code": "SCENE_ASSET_SOURCE_REF",
                    "message": "scene.assets[].source must be a non-empty source id",
                })
                continue
            if asset_source not in source_keys:
                errors.append({
                    "code": "SCENE_ASSET_SOURCE_REF",
                    "message": (
                        f"scene asset '{asset_id}' references missing source "
                        f"'{asset_source}'"
                    ),
                })
                continue
            src_entry = sources.get(asset_source) or {}
            if src_entry.get("type") != "model3d":
                errors.append({
                    "code": "SCENE_ASSET_SOURCE_TYPE",
                    "message": (
                        f"scene asset '{asset_id}' source '{asset_source}' is "
                        f"'{src_entry.get('type')}', expected 'model3d'"
                    ),
                })
            elif not (src_entry.get("url") or src_entry.get("ref_id")):
                errors.append({
                    "code": "SCENE_ASSET_SOURCE_REF",
                    "message": (
                        f"model3d source '{asset_source}' carries neither url "
                        f"nor ref_id (asset '{asset_id}')"
                    ),
                })

    return {
        "success": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "summary": "Validation passed" if len(errors) == 0 else f"Validation failed with {len(errors)} errors",
    }
