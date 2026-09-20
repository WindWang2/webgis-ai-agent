"""Benchmark runner 库（B9/B10，ADR-0183 决策六）：scripts/replay_bench.py 的主体。

资源纪律：
- 顺序执行（并发 = 1，天然有界；视觉裁判 env 注入是进程级状态，禁并发）；
- 离线优先：``--offline`` 经 tests.data.offline_guard 装全局 socket 阻断；
- perf profile 只做**合成描述符放大**（语义不变），large 档显式手动运行。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.lib.harness.replay.faults import apply_faults, assert_fault_contract
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
                  *, record_perf: bool = True,
                  rows_sink: Optional[List[Dict[str, Any]]] = None,
                  contract_sink: Optional[List[str]] = None,
                  current_registry_digest: Optional[str] = None) -> Dict[str, Any]:
    started = time.perf_counter()
    # 故障编译：声明 faults 的场景在 replayer 环境边界做纯变换（D8）。
    replayed = apply_faults(scenario) if scenario.faults else scenario
    result = await replayer.replay_scenario(replayed)
    if rows_sink is not None:
        rows_sink.extend(result.metrics_rows)
    if contract_sink is not None and scenario.faults:
        violation = assert_fault_contract(
            replayed,
            [t.gate_result for t in result.turns],
            [t.goal_satisfaction for t in result.turns],
            result.ok,
        )
        if violation:
            contract_sink.append(violation)
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
    if result.deferred_levels:
        entry["deferred_levels"] = result.deferred_levels
    # ADR-0204：录制 roundtrip 场景的 drift 归因面 —— registry drift
    # （录制时 vs 当前 registry 指纹）+ 决策重推导 delta（capability
    # resolution 用冻结 inputs 重跑，钉出哪个 provider 裁决变了）。
    if scenario.decisions or scenario.registry_digest:
        entry.update(_drift_attributes(
            scenario, current_registry_digest=current_registry_digest))
    if record_perf:
        entry["duration_ms"] = round(duration_ms, 1)
        entry["context_bytes_proxy"] = sum(
            len(str(op.arguments)) for t in replayed.turns for op in t.ops)
    return entry


def _drift_attributes(scenario: Scenario, *,
                      current_registry_digest: Optional[str]) -> Dict[str, Any]:
    from app.lib.harness.replay.drift import (
        capability_registry_digest,
        decisions_digest,
        diff_decisions,
        rederive_capability_decision,
        registry_drift,
    )

    attributes: Dict[str, Any] = {
        "decision_count": len(scenario.decisions),
    }
    if scenario.decisions:
        attributes["decisions_digest"] = decisions_digest(scenario.decisions)
    if scenario.registry_digest:
        current = current_registry_digest
        if current is None:
            current = capability_registry_digest()
        drift = registry_drift(scenario.registry_digest, current)
        if drift:
            attributes["registry_drift"] = drift
    # 决策重推导：仅 capability_resolution 面可离线重跑（其余种类诚实
    # 缺席）；diff 把「digest 变了」钉到具体决策。
    rederivable = [
        d for d in scenario.decisions
        if d.get("kind") == "capability_resolution"
    ]
    if rederivable:
        rederived = []
        for record in rederivable[:16]:
            rebuilt = rederive_capability_decision(record)
            if rebuilt is not None:
                rederived.append(rebuilt)
        diffs = diff_decisions(rederivable, rederived)
        if diffs:
            attributes["decision_diffs"] = diffs[:16]
    return attributes


async def run_suite(
    scenarios: List[Scenario], *, seed: int = 0,
    profile: str = "small", resume_path: Optional[str] = None,
) -> Dict[str, Any]:
    """跑一个 suite；resume 状态文件跳过已完成场景（最终合并全量报告）。"""
    replayer = OfflineReplayer(seed=seed)
    completed: Dict[str, Any] = {}
    if resume_path and Path(resume_path).exists():
        try:
            completed = json.loads(
                Path(resume_path).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            completed = {}  # 损坏状态文件 → 诚实重跑，不崩 CLI
        if completed.get("seed") != seed or completed.get("profile") != profile:
            completed = {}  # 基线参数变化 → 重跑（不混账）
    entry_map: Dict[str, Any] = completed.get("entries") or {}
    if not isinstance(entry_map, dict):
        entry_map = {}
    wanted_ids = {s.scenario_id for s in scenarios}
    # 只保留本次 suite 的条目（防已删除场景的僵尸记录虚增计数）。
    entries: List[Dict[str, Any]] = [
        e for e in entry_map.values()
        if isinstance(e, dict) and e.get("scenario_id") in wanted_ids]
    done_ids = {e["scenario_id"] for e in entries}

    def _save() -> None:
        if not resume_path:
            return
        payload = json.dumps({
            "seed": seed, "profile": profile,
            "entries": {e["scenario_id"]: e for e in entries},
        }, ensure_ascii=False)
        target = Path(resume_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, target)

    rows: List[Dict[str, Any]] = []
    contract_violations: List[str] = []
    # ADR-0204：suite 级算一次 registry 指纹（录制场景的 drift 归因面）。
    try:
        from app.lib.harness.replay.drift import capability_registry_digest

        current_registry_digest = capability_registry_digest()
    except Exception:  # noqa: BLE001 — 指纹缺席按无 drift 面处理
        current_registry_digest = ""
    for scenario in scenarios:
        if scenario.scenario_id in done_ids:
            continue
        entry = await run_one(replayer, apply_profile(scenario, profile),
                              rows_sink=rows,
                              contract_sink=contract_violations,
                              current_registry_digest=current_registry_digest)
        entries.append(entry)
        _save()
    entries.sort(key=lambda e: e["scenario_id"])
    return {
        "suite_seed": seed,
        "profile": profile,
        "scenarios": len(entries),
        "green": sum(1 for e in entries if e["ok"]),
        "red": sum(1 for e in entries if not e["ok"]),
        "entries": entries,
        "ratchet_rows": rows,
        **({"fault_contract_violations": contract_violations}
           if contract_violations else {}),
    }


def compare_results(report: Dict[str, Any], baseline_path: str) -> Dict[str, Any]:
    """对照基线：digest 漂移 + 绿/red 计数漂移（+ 决策摘要漂移归因）。"""
    baseline = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    base_entries = {e["scenario_id"]: e for e in baseline.get("entries") or []}
    drifts = []
    for entry in report.get("entries") or []:
        base = base_entries.get(entry["scenario_id"])
        if base is None:
            drifts.append({"scenario_id": entry["scenario_id"],
                           "kind": "new"})
        elif base.get("replay_digest") != entry.get("replay_digest"):
            drift = {
                "scenario_id": entry["scenario_id"],
                "kind": "digest_drift",
                "baseline_ok": base.get("ok"),
                "current_ok": entry.get("ok"),
            }
            # ADR-0204：决策摘要漂移归因（录制场景）—— digest 变了时，
            # 决策面是否也变了直接可见。
            base_dd = base.get("decisions_digest") or ""
            cur_dd = entry.get("decisions_digest") or ""
            if base_dd and cur_dd:
                drift["decisions_digest_drift"] = base_dd != cur_dd
            drifts.append(drift)
    for sid, base in base_entries.items():
        if not any(e["scenario_id"] == sid
                   for e in report.get("entries") or []):
            drifts.append({"scenario_id": sid, "kind": "missing"})
    return {"baseline": baseline_path, "drifts": drifts,
            "green_baseline": baseline.get("green"),
            "green_current": report.get("green")}


BASELINE_KIND = "replay_bench_baseline"


def write_baseline(report: Dict[str, Any], output: str, *,
                   corpus_version: Optional[int] = None) -> Dict[str, Any]:
    """基线投影：只含确定性字段（digest/裁决/计数 —— 计时等易变面不进）。"""
    payload: Dict[str, Any] = {
        "kind": BASELINE_KIND,
        "suite_seed": report.get("suite_seed"),
        "profile": report.get("profile"),
        "green": report.get("green"),
        "red": report.get("red"),
        "entries": [
            {
                "scenario_id": e.get("scenario_id"),
                "ok": e.get("ok"),
                "replay_digest": e.get("replay_digest"),
                **({"decisions_digest": e["decisions_digest"]}
                   if e.get("decisions_digest") else {}),
            }
            for e in sorted(
                (x for x in report.get("entries") or []
                 if isinstance(x, dict)),
                key=lambda x: str(x.get("scenario_id") or ""),
            )
        ],
    }
    if corpus_version is not None:
        payload["corpus_version"] = corpus_version
    text = json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if output == "-":
        print(text, end="")
    else:
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return payload


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
