#!/usr/bin/env python
"""AC-10 P0 头照场景计时测量（只测时长与稳定性，不写仓库产物）。

对 tests/fixtures/runtime/ 下 9 个场景逐个（顺序、零并发）跑 3 次
runtime_validator.validate_runtime，记录 wall-clock 时长与 valid/失败原因，
输出 JSON 到指定路径。结果用于：ADR-0065 晋升判定（连续 10 次绿 + <30s）
与 P3 golden 基线的场景分档。

用法：
    python scripts/measure_scenario_timing.py --out /tmp/ac10-scenario-timing.json \
        [--repeats 3] [--scenarios heatmap-basic,step-fill]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "runtime"


def _discover() -> list[str]:
    return sorted(
        d.name for d in FIXTURE_ROOT.iterdir()
        if d.is_dir() and (d / "mapspec.json").is_file()
    )


def _windows_cli_shim() -> None:
    """Windows 本地跑工具链的进程内 shim（只影响本脚本进程，不动仓库代码）：
    - ``node_modules/.bin/jiti`` 是无扩展名 sh 脚本，CreateProcess 无法执行
      （WinError 193）→ 把 coordinator 的 CLI 指针改指同目录 ``jiti.cmd``；
    - ``npx`` 同理必须走 ``npx.cmd``（WinError 2）→ 包一层 subprocess.run
      把 npx 解析成可执行 shim。"""
    import os
    import shutil
    import subprocess

    if os.name != "nt":
        return
    from app.services.mapspec import coordinator

    cli = getattr(coordinator, "_CLI_LOCAL_BIN", None)
    if cli is not None and cli.suffix == "" and cli.with_suffix(".cmd").exists():
        coordinator._CLI_LOCAL_BIN = cli.with_suffix(".cmd")

    if getattr(_windows_cli_shim, "_npx_patched", False):
        return
    npx = shutil.which("npx.cmd") or shutil.which("npx")
    if npx:
        orig_run = subprocess.run

        def _run_shim(cmd, *args, **kwargs):
            if isinstance(cmd, list) and cmd and str(cmd[0]) == "npx":
                cmd = [npx] + list(cmd[1:])
            return orig_run(cmd, *args, **kwargs)

        subprocess.run = _run_shim
        _windows_cli_shim._npx_patched = True


async def _run_one(scenario: str, repeat: int) -> dict:
    from app.services.mapspec_store import mapspec_store
    from app.services.runtime_validator import runtime_validator
    from app.services.session_data import session_data_manager

    _windows_cli_shim()

    sid = f"ac10-timing-{scenario}-{repeat}-{uuid.uuid4().hex[:6]}"
    fixture_dir = FIXTURE_ROOT / scenario
    mapspec = json.loads((fixture_dir / "mapspec.json").read_text(encoding="utf-8"))
    probes_path = fixture_dir / "probes.json"
    entry: dict = {"scenario": scenario, "repeat": repeat, "session_id": sid}
    t0 = time.perf_counter()
    try:
        await mapspec_store.save_mapspec(sid, mapspec)
        res = await runtime_validator.validate_runtime(sid, probes_path=probes_path)
        elapsed = time.perf_counter() - t0
        report = res.get("report") or {}
        entry.update({
            "seconds": round(elapsed, 2),
            "valid": bool(res.get("valid")),
            "score": res.get("score"),
            "map_png": (Path(res["runtime_dir"]) / "map.png").exists()
            if res.get("runtime_dir") else False,
            "fatal_error": report.get("fatalError"),
            "probe_fail": [
                r for r in (report.get("probeResults") or []) if not r.get("pass")
            ][:3],
            "page_errors": len(report.get("pageErrors") or []),
            "console_errors": len(report.get("consoleErrors") or []),
            "failed_requests": len(report.get("failedRequests") or []),
            "canvas_blank": (report.get("canvas") or {}).get("blank"),
            "map_loaded": bool(report.get("mapLoaded")),
            "map_idle": bool(report.get("mapIdle")),
        })
    except Exception as exc:  # noqa: BLE001 — 测量脚本必须吞错继续
        entry.update({
            "seconds": round(time.perf_counter() - t0, 2),
            "valid": False,
            "error": f"{type(exc).__name__}: {exc}"[:300],
        })
    finally:
        try:
            await session_data_manager.clear_session(sid)
        except Exception:  # noqa: BLE001
            pass
    return entry


async def main_async(scenarios: list[str], repeats: int) -> list[dict]:
    results: list[dict] = []
    for scenario in scenarios:
        for repeat in range(1, repeats + 1):
            entry = await _run_one(scenario, repeat)
            print(json.dumps(entry, ensure_ascii=False), flush=True)
            results.append(entry)
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--scenarios", default="")
    args = parser.parse_args()
    scenarios = (
        [s for s in args.scenarios.split(",") if s] if args.scenarios
        else _discover()
    )
    print(f"scenarios={scenarios} repeats={args.repeats}", flush=True)
    results = asyncio.run(main_async(scenarios, args.repeats))
    Path(args.out).write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
