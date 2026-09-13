#!/usr/bin/env python
"""头照场景像素级 golden 基线管理（任务书 10 线 P3，ADR-0159）。

用法（单场景串行，禁止并发 —— 头照/浏览器场景极耗资源）::

    # 生成/刷新 golden（跑真实 headless 渲染并落 tests/fixtures/runtime/<s>/golden/）
    python scripts/golden_baseline.py generate --scenarios heatmap-basic

    # 校验：当前渲染 vs golden（像素 diff + 取色点可分性）
    python scripts/quality_gate_local.sh 调用 verify；单跑：
    python scripts/golden_baseline.py verify

    # 场景分档（ADR-0065 晋升语义）：pr-blocking / nightly-only / quarantine
    python scripts/golden_baseline.py status --scenarios step-fill \
        --promotion pr-blocking --note "3x green locally, 9.9-10.2s"

verify 退出码语义：pr-blocking 场景失败 → 1（拦截）；nightly-only /
quarantine 场景失败 → 报告但不拦截（quarantine 必须附失败样本路径，
禁直接删除）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
for p in (str(REPO_ROOT), str(REPO_ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "runtime"
PROMOTIONS = ("pr-blocking", "nightly-only", "quarantine")


def _is_blocking(promotion: str) -> bool:
    """分档 → 是否 PR 阻断（ADR-0065 语义：quarantine / nightly-only 只报告）。"""
    return str(promotion) == "pr-blocking"


def _windows_cli_shim() -> None:
    """Windows 进程内 shim（只影响本进程；见 measure_scenario_timing.py）。"""
    import os
    import shutil as _shutil
    import subprocess

    if os.name != "nt":
        return
    from app.services.mapspec import coordinator

    cli = getattr(coordinator, "_CLI_LOCAL_BIN", None)
    if cli is not None and cli.suffix == "" and cli.with_suffix(".cmd").exists():
        coordinator._CLI_LOCAL_BIN = cli.with_suffix(".cmd")
    if getattr(_windows_cli_shim, "_npx_patched", False):
        return
    npx = _shutil.which("npx.cmd") or _shutil.which("npx")
    if npx:
        orig_run = subprocess.run

        def _run_shim(cmd, *args, **kwargs):
            if isinstance(cmd, list) and cmd and str(cmd[0]) == "npx":
                cmd = [npx] + list(cmd[1:])
            return orig_run(cmd, *args, **kwargs)

        subprocess.run = _run_shim
        _windows_cli_shim._npx_patched = True


def _discover() -> List[str]:
    return sorted(
        d.name for d in FIXTURE_ROOT.iterdir()
        if d.is_dir() and (d / "mapspec.json").is_file()
    )


def _golden_dir(scenario: str) -> Path:
    return FIXTURE_ROOT / scenario / "golden"


def _run_scenario(scenario: str) -> Dict[str, Any]:
    """跑单场景，返回 {png_bytes|None, report, seconds, session_id}。"""
    from app.services.mapspec_store import mapspec_store
    from app.services.runtime_validator import runtime_validator
    from app.services.session_data import session_data_manager

    sid = f"ac10-golden-{scenario}-{uuid.uuid4().hex[:6]}"
    fixture_dir = FIXTURE_ROOT / scenario
    mapspec = json.loads((fixture_dir / "mapspec.json").read_text(encoding="utf-8"))
    t0 = time.perf_counter()
    png_bytes: Optional[bytes] = None
    result: Dict[str, Any] = {}
    try:
        loop_res = _run_async(_validate(mapspec_store, runtime_validator, sid, mapspec, fixture_dir))
        result = loop_res
        result["seconds"] = round(time.perf_counter() - t0, 2)
        runtime_dir = result.pop("runtime_dir", None)
        if runtime_dir:
            png_path = Path(runtime_dir) / "map.png"
            if png_path.exists():
                png_bytes = png_path.read_bytes()
    finally:
        try:
            _run_async(session_data_manager.clear_session(sid))
        except Exception:  # noqa: BLE001 — 清理失败不影响测量
            pass
    result["png_bytes"] = png_bytes
    result["session_id"] = sid
    return result


async def _validate(mapspec_store, runtime_validator, sid, mapspec, fixture_dir):
    await mapspec_store.save_mapspec(sid, mapspec)
    res = await runtime_validator.validate_runtime(
        sid, probes_path=fixture_dir / "probes.json"
    )
    report = res.get("report") or {}
    return {
        "runtime_dir": res.get("runtime_dir"),
        "map_loaded": bool(report.get("mapLoaded")),
        "map_idle": bool(report.get("mapIdle")),
        "fatal_error": report.get("fatalError"),
        "page_errors": len(report.get("pageErrors") or []),
        "console_errors": len(report.get("consoleErrors") or []),
        "probe_results": [
            {"type": p.get("type"), "pass": bool(p.get("pass"))}
            for p in (report.get("probeResults") or [])
        ],
        "canvas_blank": (report.get("canvas") or {}).get("blank"),
        "score": res.get("score"),
    }


def _run_async(coro):
    return asyncio.run(coro)


def _browser_contract_ok(report: Dict[str, Any], scenario: str) -> bool:
    probes = report.get("probe_results") or []
    probes_spec = json.loads(
        (FIXTURE_ROOT / scenario / "probes.json").read_text(encoding="utf-8")
    )
    expect = probes_spec.get("expect", "pass")
    contract = (
        report.get("map_loaded")
        and report.get("map_idle")
        and report.get("fatal_error") is None
        and report.get("page_errors") == 0
        and report.get("console_errors") == 0
    )
    if expect == "fail":
        # 负路径场景：浏览器契约成立 且 探针确实如预期失败。
        return bool(contract) and any(not p["pass"] for p in probes)
    return bool(contract) and bool(probes) and all(p["pass"] for p in probes)


def cmd_generate(args: argparse.Namespace) -> int:
    from app.services.cartography_metrics_store import record_quality_run_sync

    _windows_cli_shim()
    scenarios = args.scenarios.split(",") if args.scenarios else _discover()
    failures = 0
    for scenario in scenarios:
        print(f"== generate golden: {scenario}", flush=True)
        result = _run_scenario(scenario)
        png = result.pop("png_bytes", None)
        ok = png is not None and _browser_contract_ok(result, scenario)
        result["contract_ok"] = ok
        if not ok:
            failures += 1
            print(f"   ❌ 契约不成立，不落 golden：{json.dumps(result, ensure_ascii=False)}",
                  flush=True)
            continue
        gdir = _golden_dir(scenario)
        gdir.mkdir(parents=True, exist_ok=True)
        (gdir / "map.png").write_bytes(png)
        from app.lib.cartography.golden_diff import ink_ratio

        meta = {
            "scenario": scenario,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "app_version": _app_version(),
            "source_session": result.get("session_id"),
            "seconds": result.get("seconds"),
            "canvas_blank": result.get("canvas_blank"),
            "ink_ratio": ink_ratio(png),
            "score": result.get("score"),
            "probes": result.get("probe_results"),
        }
        (gdir / "golden.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        status_path = gdir / "status.json"
        status: Dict[str, Any] = {}
        if status_path.exists():
            status = json.loads(status_path.read_text(encoding="utf-8"))
        status.setdefault("promotion", "nightly-only")
        status["last_generated"] = meta["generated_at"]
        status_path.write_text(
            json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"   ✅ golden 落库 {gdir}（{result.get('seconds')}s）", flush=True)
        try:
            record_quality_run_sync(
                lane="golden", source="golden_generate",
                scene_id=scenario, passed=True,
                summary={"seconds": result.get("seconds"),
                         "promotion": status["promotion"]},
            )
        except Exception:  # noqa: BLE001 — 账本不阻塞基线生成
            pass
    return 1 if failures else 0


def _app_version() -> str:
    try:
        return (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()[:64]
    except Exception:  # noqa: BLE001
        return ""


def cmd_verify(args: argparse.Namespace) -> int:
    from app.lib.cartography.golden_diff import image_diff, ink_ratio
    from app.services.cartography_metrics_store import record_quality_run_sync

    _windows_cli_shim()
    scenarios = args.scenarios.split(",") if args.scenarios else _discover()
    blocking_failures: List[str] = []
    warnings: List[str] = []
    missing_golden: List[str] = []
    for scenario in scenarios:
        gdir = _golden_dir(scenario)
        golden_png_path = gdir / "map.png"
        golden_meta_path = gdir / "golden.json"
        if not golden_png_path.exists() or not golden_meta_path.exists():
            missing_golden.append(scenario)
            warnings.append(f"{scenario}: 无 golden（先 generate）—— 跳过")
            continue
        status: Dict[str, Any] = {}
        status_path = gdir / "status.json"
        if status_path.exists():
            status = json.loads(status_path.read_text(encoding="utf-8"))
        promotion = status.get("promotion", "nightly-only")
        print(f"== verify golden: {scenario}（{promotion}）", flush=True)
        result = _run_scenario(scenario)
        png = result.pop("png_bytes", None)
        if png is None or not _browser_contract_ok(result, scenario):
            detail = json.dumps(result, ensure_ascii=False)[:300]
            message = f"{scenario}: 浏览器契约不成立 {detail}"
            (gdir / "last_failure.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            if _is_blocking(promotion):
                blocking_failures.append(message)
            else:
                warnings.append(message + "（失败样本已存 last_failure.json）")
            continue
        baseline_png = golden_png_path.read_bytes()
        diff = image_diff(baseline_png, png)
        # 墨量带校验：小要素整块消失不会跌破像素通过线（98% 预算 > 稀疏场景
        # ~0.4% 墨量），用墨量骤降/暴增补位。带内判定：|Δ| ≤ max(0.001, 0.4×ink)。
        # 本机渲染已实证逐像素确定（same-env 下 0.001 ≈ 1280px 容差足够抗
        # 抖动；跨环境漂移走 nightly-only 分档 + 显式 generate 刷新流程）。
        golden_meta = json.loads(golden_meta_path.read_text(encoding="utf-8"))
        ink_golden = float(golden_meta.get("ink_ratio") or 0.0)
        ink_now = ink_ratio(png)
        ink_ok = (
            abs(ink_now - ink_golden) <= max(0.001, 0.4 * abs(ink_golden))
            if ink_golden > 0
            else True
        )
        verdict = {
            "seconds": result.get("seconds"),
            "pixel_diff": diff,
            "ink_golden": ink_golden,
            "ink_now": ink_now,
            "ink_ok": ink_ok,
        }
        print(f"   within_ratio={diff.get('within_ratio')} "
              f"max_channel_diff={diff.get('max_channel_diff')} "
              f"ink={ink_now:.4f}(golden {ink_golden:.4f}) "
              f"pass={diff.get('pass') and ink_ok} ({result.get('seconds')}s)", flush=True)
        (gdir / "last_verify.json").write_text(
            json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        passed = bool(diff["pass"]) and ink_ok
        if not passed:
            if ink_ok:
                message = (f"{scenario}: golden diff 失败 "
                           f"within_ratio={diff['within_ratio']} < {diff['pass_ratio_required']}")
            else:
                message = (f"{scenario}: 墨量带校验失败（要素消失/暴增）"
                           f" ink golden={ink_golden:.4f} now={ink_now:.4f}")
            if _is_blocking(promotion):
                blocking_failures.append(message)
            else:
                warnings.append(message)
        try:
            record_quality_run_sync(
                lane="golden", source="golden_verify",
                scene_id=scenario, passed=passed,
                summary={"promotion": promotion,
                         "within_ratio": diff.get("within_ratio"),
                         "ink_ok": ink_ok,
                         "seconds": result.get("seconds")},
            )
        except Exception:  # noqa: BLE001
            pass
    for warning in warnings:
        print(f"⚠️  {warning}", flush=True)
    if missing_golden:
        # 无证据 ≠ 通过：缺 golden 是配置错误，非阻断告警之外的放行。
        print(f"\n❌ {len(missing_golden)} 个场景缺 golden 基线"
              f"（先跑 generate）：{', '.join(missing_golden)}")
        return 2
    if blocking_failures:
        print(f"\n❌ pr-blocking 场景 golden 校验失败 {len(blocking_failures)} 个：")
        for message in blocking_failures:
            print(f"   {message}")
        return 1
    print("\n✅ golden 校验通过（pr-blocking 集合全绿）。")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    scenarios = args.scenarios.split(",") if args.scenarios else _discover()
    for scenario in scenarios:
        gdir = _golden_dir(scenario)
        gdir.mkdir(parents=True, exist_ok=True)
        status_path = gdir / "status.json"
        status: Dict[str, Any] = {}
        if status_path.exists():
            status = json.loads(status_path.read_text(encoding="utf-8"))
        if args.promotion:
            if args.promotion == "quarantine" and not (args.note or "").strip():
                print("quarantine 必须附 --note 失败样本说明（禁直接删除）",
                      file=sys.stderr)
                return 2
            status["promotion"] = args.promotion
        if args.note:
            status["note"] = args.note
        status["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        status_path.write_text(
            json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"{scenario}: {status.get('promotion')} "
              f"note={status.get('note', '')}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    for scenario in _discover():
        gdir = _golden_dir(scenario)
        status: Dict[str, Any] = {}
        status_path = gdir / "status.json"
        if status_path.exists():
            status = json.loads(status_path.read_text(encoding="utf-8"))
        has_golden = (gdir / "map.png").exists()
        last = ""
        last_path = gdir / "last_verify.json"
        if last_path.exists():
            verdict = json.loads(last_path.read_text(encoding="utf-8"))
            diff = verdict.get("pixel_diff") or {}
            last = f"last_within_ratio={diff.get('within_ratio')}"
        print(f"{scenario:24} golden={'有' if has_golden else '无':2} "
              f"promotion={status.get('promotion', '未分档'):14} {last}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("generate", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--scenarios", default="",
                       help="逗号分隔；缺省为全部 9 场景")
        p.set_defaults(func=cmd_generate if name == "generate" else cmd_verify)

    p_status = sub.add_parser("status", help="设置场景晋升分档")
    p_status.add_argument("--scenarios", default="")
    p_status.add_argument("--promotion", choices=PROMOTIONS)
    p_status.add_argument("--note", default="")
    p_status.set_defaults(func=cmd_status)

    p_show = sub.add_parser("show", help="列出场景分档现状")
    p_show.set_defaults(func=cmd_show)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
