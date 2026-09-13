"""Benchmark runner 库（B9/B10，ADR-0183 决策六）：scripts/replay_bench.py 的主体。

资源纪律：
- 顺序执行（并发 = 1，天然有界；视觉裁判 env 注入是进程级状态，禁并发）；
- 离线优先：``--offline`` 经 tests.data.offline_guard 装全局 socket 阻断；
- perf profile 只做**合成描述符放大**（语义不变），large 档显式手动运行。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.lib.harness.replay.replayer import OfflineReplayer, Scenario

SUITES = ("core", "multi-turn", "faults", "all")
_PROFILES = ("small", "medium", "large")
_PROFILE_BLOB_BYTES = {"small": 0, "medium": 64 * 1024, "large": 512 * 1024}


def select_scenarios(corpus: List[Scenario], suite: str = "all",
                     *, only: Optional[str] = None,
                     limit: Optional[int] = None) -> List[Scenario]:
    if suite not in SUITES:
        raise ValueError(f"unknown suite {suite!r} (choose from {SUITES})")
    if only:
        wanted = {sid.strip() for sid in only.split(",") if sid.strip()}
        return [s for s in corpus if s.scenario_id in wanted]
    if suite == "core":
        picked = [s for s in corpus if len(s.turns) < 3]
    elif suite == "multi-turn":
        picked = [s for s in corpus if len(s.turns) >= 3]
    elif suite == "faults":
        picked = [s for s in corpus if s.faults]
    else:
        picked = list(corpus)
    return picked[:limit] if limit else picked


def apply_profile(scenario: Scenario, profile: str) -> Scenario:
    """perf profile：放大合成描述符（确定性 filler），语义不变、体积有界。"""
    import copy

    if profile not in _PROFILES:
        raise ValueError(f"unknown profile {profile!r}")
    target = _PROFILE_BLOB_BYTES[profile]
    if not target:
        return scenario
    scaled = copy.deepcopy(scenario)
    for turn in scaled.turns:
        for index, op in enumerate(turn.ops):
            filler = (f"blob-{scenario.scenario_id}-{index}-"
                      + ("x" * (target // 8)))
            op.arguments["_perf_blob"] = filler
    return scaled


async def run_one(replayer: OfflineReplayer, scenario: Scenario,
                  *, record_perf: bool = True) -> Dict[str, Any]:
    started = time.perf_counter()
    result = await replayer.replay_scenario(scenario)
    duration_ms = (time.perf_counter() - started) * 1000.0
    entry: Dict[str, Any] = {
        "scenario_id": scenario.scenario_id,
        "category": scenario.category,
        "ok": result.ok,
        "replay_digest": result.replay_digest,
        "levels_run": result.levels_run,
        "not_run": result.not_run,
        "turns": len(result.turns),
        "exact_diff_count": sum(len(t.exact_diffs) for t in result.turns),
        "metric_row_count": len(result.metrics_rows),
    }
    if record_perf:
        entry["duration_ms"] = round(duration_ms, 1)
        entry["context_bytes_proxy"] = sum(
            len(str(op.arguments)) for t in scenario.turns for op in t.ops)
    return entry


async def run_suite(
    scenarios: List[Scenario], *, seed: int = 0,
    profile: str = "small", resume_path: Optional[str] = None,
) -> Dict[str, Any]:
    """跑一个 suite；resume 状态文件跳过已完成场景（最终合并全量报告）。"""
    replayer = OfflineReplayer(seed=seed)
    completed: Dict[str, Any] = {}
    if resume_path and Path(resume_path).exists():
        completed = json.loads(Path(resume_path).read_text(encoding="utf-8"))
        if completed.get("seed") != seed or completed.get("profile") != profile:
            completed = {}  # 基线参数变化 → 重跑（不混账）
    entry_map: Dict[str, Any] = completed.get("entries") or {}
    if not isinstance(entry_map, dict):
        entry_map = {}
    entries: List[Dict[str, Any]] = list(entry_map.values())
    done_ids = set(entry_map)
    for scenario in scenarios:
        if scenario.scenario_id in done_ids:
            continue
        entry = await run_one(replayer, apply_profile(scenario, profile))
        entries.append(entry)
        if resume_path:
            Path(resume_path).write_text(json.dumps({
                "seed": seed, "profile": profile,
                "entries": {e["scenario_id"]: e for e in entries},
            }, ensure_ascii=False), encoding="utf-8")
    entries.sort(key=lambda e: e["scenario_id"])
    return {
        "suite_seed": seed,
        "profile": profile,
        "scenarios": len(entries),
        "green": sum(1 for e in entries if e["ok"]),
        "red": sum(1 for e in entries if not e["ok"]),
        "entries": entries,
    }


def compare_results(report: Dict[str, Any], baseline_path: str) -> Dict[str, Any]:
    """对照基线：digest 漂移 + 绿/red 计数漂移。"""
    baseline = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    base_entries = {e["scenario_id"]: e for e in baseline.get("entries") or []}
    drifts = []
    for entry in report.get("entries") or []:
        base = base_entries.get(entry["scenario_id"])
        if base is None:
            drifts.append({"scenario_id": entry["scenario_id"],
                           "kind": "new"})
        elif base.get("replay_digest") != entry.get("replay_digest"):
            drifts.append({"scenario_id": entry["scenario_id"],
                           "kind": "digest_drift",
                           "baseline_ok": base.get("ok"),
                           "current_ok": entry.get("ok")})
    for sid, base in base_entries.items():
        if not any(e["scenario_id"] == sid
                   for e in report.get("entries") or []):
            drifts.append({"scenario_id": sid, "kind": "missing"})
    return {"baseline": baseline_path, "drifts": drifts,
            "green_baseline": baseline.get("green"),
            "green_current": report.get("green")}


def write_report(report: Dict[str, Any], fmt: str, output: str) -> None:
    if fmt == "json":
        text = json.dumps(report, ensure_ascii=False, indent=1)
    elif fmt == "csv":
        import csv
        import io

        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer, fieldnames=["scenario_id", "category", "ok",
                                "replay_digest", "duration_ms",
                                "exact_diff_count"])
        writer.writeheader()
        for entry in report.get("entries") or []:
            writer.writerow({k: entry.get(k) for k in writer.fieldnames})
        text = buffer.getvalue()
    elif fmt == "md":
        lines = [
            "# Replay Benchmark Report", "",
            f"- scenarios: {report.get('scenarios')}",
            f"- green: {report.get('green')}  red: {report.get('red')}",
            f"- seed: {report.get('suite_seed')}  profile: {report.get('profile')}",
            "", "| scenario | category | ok | duration_ms | diffs |",
            "|---|---|---|---|---|",
        ]
        for entry in report.get("entries") or []:
            mark = "✅" if entry["ok"] else "❌"
            lines.append(
                f"| {entry['scenario_id']} | {entry['category']} | {mark} "
                f"| {entry.get('duration_ms')} | {entry.get('exact_diff_count')} |")
        text = "\n".join(lines) + "\n"
    else:
        raise ValueError(f"unknown format {fmt!r}")
    if output == "-":
        print(text, end="")
    else:
        Path(output).write_text(text, encoding="utf-8")
