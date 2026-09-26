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
        # ADR-0214 D7：结构化投影（diff_reports 的下钻定位面）。
        "projection": _projection_of(result),
    }
    if result.deferred_levels:
        entry["deferred_levels"] = result.deferred_levels
    # ADR-0212：录制 roundtrip 场景的 drift 归因面 —— registry drift
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


def _projection_of(result) -> Dict[str, Any]:
    """场景级结构化投影（ADR-0214 D7）：digest 漂移的下钻定位面。

    只含确定性事实（passed/evaluated/allowed/status/selected/指纹）——
    计时与自由文本不进投影。
    """
    turns = []
    for index, t in enumerate(result.turns):
        turns.append({
            "turn": index,
            "gate": {
                "overall_passed": t.gate_result.get("overall_passed"),
                "checks": {
                    name: {
                        "passed": check.get("passed"),
                        "evaluated": check.get("evaluated"),
                        "score": check.get("score"),
                    }
                    for name, check in (t.gate_result.get("checks") or {}).items()
                },
            },
            "goal": t.goal_satisfaction,
            "mutations": [
                {k: m.get(k) for k in
                 ("op", "success", "spec_fingerprint", "error_code")}
                for m in t.mutation_outcomes
            ],
            "dispatch": [
                {k: entry.get(k) for k in ("call_id", "allowed", "capability")
                 if entry.get(k) is not None}
                for entry in t.dispatch_decisions
            ],
            "receipt": t.receipt_actual,
        })
    return {"turns": turns}


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
    # ADR-0212：suite 级算一次 registry 指纹（录制场景的 drift 归因面）。
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
            # ADR-0212：决策摘要漂移归因（录制场景）—— digest 变了时，
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


# ── differential replay（ADR-0214 D7）：digest 漂移 → step 级结构化 delta ────


def diff_reports(baseline_path: str, current_path: str) -> Dict[str, Any]:
    """文件形态入口：读两份 report JSON → :func:`diff_payloads`。"""
    base_report = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    cur_report = json.loads(Path(current_path).read_text(encoding="utf-8"))
    return diff_payloads(base_report, cur_report,
                         baseline=baseline_path, current=current_path)


def diff_payloads(base_report: Dict[str, Any], cur_report: Dict[str, Any],
                  *, baseline: str = "<memory>",
                  current: str = "<memory>") -> Dict[str, Any]:
    """master 报告 vs feature 报告 → 结构化 delta（可定位到 turn/step）。

    两个输入都是 replay_bench 的 **report**（含 projection 投影）；
    digest_drift 的场景被下钻为：gate_check_flip / goal_status_changed /
    mutation_drift / dispatch_flip / receipt_drift，每条带
    ``scenario / turn / aspect / key`` 定位。输出有界（≤512 条）。
    任意一侧缺 projection（例如喂了 write-baseline 产物）→ 该场景
    delta 标注 ``projection_absent`` 且**不产逐项下钻**（review P1-3：
    缺席侧逐字段 None 会被误报成 gate 翻转 —— 垃圾 delta 禁入）。
    """
    base_entries = {
        e["scenario_id"]: e for e in base_report.get("entries") or []
        if isinstance(e, dict) and e.get("scenario_id")
    }
    deltas: List[Dict[str, Any]] = []
    summary = {"scenarios_compared": 0, "drifted": 0,
               "new": 0, "missing": 0, "unchanged": 0}
    cur_ids = set()
    for entry in cur_report.get("entries") or []:
        sid = entry.get("scenario_id")
        if not sid:
            continue
        cur_ids.add(sid)
        summary["scenarios_compared"] += 1
        base = base_entries.get(sid)
        if base is None:
            summary["new"] += 1
            deltas.append({"scenario_id": sid, "kind": "new"})
            continue
        if base.get("replay_digest") == entry.get("replay_digest") \
                and base.get("ok") == entry.get("ok"):
            summary["unchanged"] += 1
            continue
        summary["drifted"] += 1
        base_proj = base.get("projection")
        cur_proj = entry.get("projection")
        if not isinstance(base_proj, dict) or not isinstance(cur_proj, dict):
            deltas.append({
                "scenario_id": sid,
                "kind": "digest_drift",
                "baseline_ok": base.get("ok"),
                "current_ok": entry.get("ok"),
                "projection_absent": True,
                "delta": [],
                "delta_count": 0,
            })
            continue
        drill = _drill_projection(base_proj, cur_proj)
        deltas.append({
            "scenario_id": sid,
            "kind": "digest_drift",
            "baseline_ok": base.get("ok"),
            "current_ok": entry.get("ok"),
            "delta": drill[:64],
            "delta_count": len(drill),
        })
    for sid in base_entries:
        if sid not in cur_ids:
            summary["missing"] += 1
            deltas.append({"scenario_id": sid, "kind": "missing"})
    return {
        "baseline": baseline,
        "current": current,
        "green_baseline": base_report.get("green"),
        "green_current": cur_report.get("green"),
        "summary": summary,
        "deltas": deltas[:512],
    }


def _drill_projection(base: Dict[str, Any],
                      current: Dict[str, Any]) -> List[Dict[str, Any]]:
    """两份场景投影 → 逐 turn 逐 aspect 的结构化 delta。"""
    deltas: List[Dict[str, Any]] = []
    base_turns = {
        t.get("turn"): t for t in (base.get("turns") or []) if isinstance(t, dict)}
    cur_turns = {
        t.get("turn"): t for t in (current.get("turns") or []) if isinstance(t, dict)}
    for turn_key in sorted(set(base_turns) | set(cur_turns),
                           key=lambda k: (k is None, k)):
        bt = base_turns.get(turn_key) or {}
        ct = cur_turns.get(turn_key) or {}
        loc = {"turn": turn_key if turn_key is not None else "?"}

        # gate checks（passed/evaluated/score —— score-only 漂移也可定位）。
        b_gate = (bt.get("gate") or {}).get("checks") or {}
        c_gate = (ct.get("gate") or {}).get("checks") or {}
        for name in sorted(set(b_gate) | set(c_gate)):
            b_entry = b_gate.get(name) or {}
            c_entry = c_gate.get(name) or {}
            if b_entry != c_entry:
                deltas.append({
                    **loc, "kind": "gate_check_flip", "aspect": "gate",
                    "key": name,
                    "baseline": {"passed": b_entry.get("passed"),
                                 "evaluated": b_entry.get("evaluated"),
                                 "score": b_entry.get("score")},
                    "current": {"passed": c_entry.get("passed"),
                                "evaluated": c_entry.get("evaluated"),
                                "score": c_entry.get("score")},
                })
        # goal。
        if bt.get("goal") != ct.get("goal"):
            deltas.append({
                **loc, "kind": "goal_status_changed", "aspect": "goal",
                "key": "status",
                "baseline": (bt.get("goal") or {}).get("status"),
                "current": (ct.get("goal") or {}).get("status"),
            })
        # mutations（op + success + 指纹）。
        b_muts = bt.get("mutations") or []
        c_muts = ct.get("mutations") or []
        for i in range(max(len(b_muts), len(c_muts))):
            b_m = b_muts[i] if i < len(b_muts) else {}
            c_m = c_muts[i] if i < len(c_muts) else {}
            if b_m.get("op") != c_m.get("op") \
                    or b_m.get("success") != c_m.get("success") \
                    or b_m.get("spec_fingerprint") != c_m.get("spec_fingerprint"):
                deltas.append({
                    **loc, "kind": "mutation_drift", "aspect": "mutations",
                    "key": f"[{i}]{c_m.get('op') or b_m.get('op') or '?'}",
                    "baseline": {k: b_m.get(k) for k in
                                 ("op", "success", "spec_fingerprint")},
                    "current": {k: c_m.get(k) for k in
                                ("op", "success", "spec_fingerprint")},
                })
        # dispatch bind 裁决。
        b_disp = {d.get("call_id"): d
                  for d in (bt.get("dispatch") or []) if isinstance(d, dict)}
        c_disp = {d.get("call_id"): d
                  for d in (ct.get("dispatch") or []) if isinstance(d, dict)}
        for call_id in sorted(set(b_disp) | set(c_disp)):
            if (b_disp.get(call_id) or {}).get("allowed") \
                    != (c_disp.get(call_id) or {}).get("allowed") \
                    or (b_disp.get(call_id) or {}).get("capability") \
                    != (c_disp.get(call_id) or {}).get("capability"):
                deltas.append({
                    **loc, "kind": "dispatch_flip", "aspect": "dispatch",
                    "key": call_id,
                    "baseline": b_disp.get(call_id),
                    "current": c_disp.get(call_id),
                })
        # T4 receipt 合同。
        b_rec = (bt.get("receipt") or {}).get("receipt") or {}
        c_rec = (ct.get("receipt") or {}).get("receipt") or {}
        for call_id in sorted(set(b_rec) | set(c_rec)):
            if b_rec.get(call_id) != c_rec.get(call_id):
                deltas.append({
                    **loc, "kind": "receipt_drift", "aspect": "receipt",
                    "key": call_id,
                    "baseline": b_rec.get(call_id),
                    "current": c_rec.get(call_id),
                })
        b_repeat = (bt.get("receipt") or {}).get("receipt_repeat") or {}
        c_repeat = (ct.get("receipt") or {}).get("receipt_repeat") or {}
        for call_id in sorted(set(b_repeat) | set(c_repeat)):
            if b_repeat.get(call_id) != c_repeat.get(call_id):
                deltas.append({
                    **loc, "kind": "receipt_repeat_drift",
                    "aspect": "receipt_repeat", "key": call_id,
                    "baseline": b_repeat.get(call_id),
                    "current": c_repeat.get(call_id),
                })
    return deltas


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
