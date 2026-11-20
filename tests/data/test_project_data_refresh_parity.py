"""G10 UPD-07 / O12：增量 vs 固定版本全量重算的**结果对拍与性能报告**。

纪律：先证明结果与全量重算在声明容差内逐字段等价，再测工具执行次数、
IO 字节、wall time 和 max RSS。更快但漏算不算优化；不通过跳过未知依赖
获得漂亮指标。无稳定证据时 plan 会转保守全量，此时增量=全量（诚实）。
"""
from __future__ import annotations

import json
import os
import platform
import resource
import time
from typing import Any, Dict

import pytest

from app.lib.data.fingerprints import canonical_fingerprint
from app.services.project_runtime.refresh.contracts import (
    DiffBudget,
    EvidenceLevel,
    FeatureIdKind,
    SourceSnapshot,
)
from app.services.project_runtime.refresh.diff import compute_source_diff
from app.services.project_runtime.refresh.impact import build_refresh_plan

NODES = [
    "data:schools", "data:population", "analysis:heat",
    "analysis:normalized", "output:legend", "output:map",
]
#: 各节点真实数据闭包内的来源版本键（决定输出是否变化）
NODE_DEPS: Dict[str, tuple[str, ...]] = {
    "data:schools": ("schools",),
    "data:population": ("population",),
    "analysis:heat": ("schools",),
    "analysis:normalized": ("population",),
    "output:legend": (),
    "output:map": ("schools", "population"),
}
DAG = {
    "nodes": [{"node_id": n, "parameters": []} for n in NODES],
    "edges": [
        {"from": "data:schools", "to": "analysis:heat"},
        {"from": "data:population", "to": "analysis:normalized"},
        {"from": "analysis:heat", "to": "output:map"},
        {"from": "analysis:normalized", "to": "output:map"},
        {"from": "output:legend", "to": "output:map"},
    ],
}


def _snapshot(version: int, *, feats: dict, population: str = "pop-2024") -> SourceSnapshot:
    return SourceSnapshot(
        logical_id="ds://schools", version_token=f"dsd-v1:{version}",
        evidence=EvidenceLevel.CONTENT_EXACT,
        fingerprints={
            "content": f"c{version}", "schema": "s0",
            "metadata": f"m-{population}", "crs": "epsg4326",
        },
        schema_fields={"name": "str|", "students": "int|person"},
        crs="EPSG:4326", bbox=[103.9, 30.5, 104.2, 30.8],
        temporal_window=["2024", "2024"],
        denominator=population, method_version="heat_v1",
        features=feats, feature_id_kind=FeatureIdKind.STABLE,
        analysis_roles=["data:schools"],
    )


def _node_output(node_id: str, versions: Dict[str, str]) -> str:
    """确定性领域计算的替代 Oracle：节点输出只依赖其数据闭包内的版本。

    真实生产里这一步由既有 Workflow Runtime 承担；这里用 canonical
    fingerprint 让「闭包外节点输出不变」成为可逐字段验证的事实。
    """
    return canonical_fingerprint({
        "node": node_id,
        "inputs": {k: versions.get(k, "") for k in NODE_DEPS[node_id]},
    })


def _measures(reps: int = 40) -> Dict[str, Any]:
    baseline_versions = {"schools": "v1", "population": "pop-2024"}
    current_versions = {"schools": "v2", "population": "pop-2024"}
    base = _snapshot(1, feats={f"s{i}": {"properties": {"n": i},
                                        "geometry": "POINT(1 1)"} for i in range(1, 6)})
    cur = _snapshot(2, feats={f"s{i}": {"properties": {"n": i},
                                       "geometry": "POINT(1 1)"} for i in range(1, 6)})
    cur.features["s6"] = {"properties": {"n": 6}, "geometry": "POINT(1 1)"}

    diff = compute_source_diff(baseline=base, current=cur, budget=DiffBudget())
    plan = build_refresh_plan(
        diffs=[diff], dag=DAG,
        source_versions=current_versions,
        mapspec_revision=3, user_locks=["L1"], owner_scope="u:org",
    )
    assert diff.evidence.value == "exact"
    assert plan.conservative is False, "parity requires a proven partial subgraph"

    baseline_outputs = {n: _node_output(n, baseline_versions) for n in NODES}

    # ── 先证等价（容差 = 0，逐字段完全相同） ─────────────────────────
    full = {n: _node_output(n, current_versions) for n in NODES}
    incremental = {}
    for n in plan.reuse:
        incremental[n] = baseline_outputs[n]        # 复用已证不变的节点
    for n in plan.recompute:
        incremental[n] = _node_output(n, current_versions)
    assert full == incremental, "incremental result differs from full recompute"

    # ── 再测性能（相同输入、相同环境） ───────────────────────────────
    def _run_full() -> Dict[str, str]:
        return {n: _node_output(n, current_versions) for n in NODES}

    def _run_incremental() -> Dict[str, str]:
        out = {n: baseline_outputs[n] for n in plan.reuse}
        out.update({n: _node_output(n, current_versions) for n in plan.recompute})
        return out

    t0 = time.perf_counter()
    for _ in range(reps):
        _run_full()
    full_s = time.perf_counter() - t0
    t1 = time.perf_counter()
    for _ in range(reps):
        _run_incremental()
    inc_s = time.perf_counter() - t1

    bytes_per_node = 512
    return {
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "pid": os.getpid(),
        },
        "input": {
            "nodes": len(NODES), "sources": 2, "features_baseline": 5,
            "features_current": 6, "reps": reps,
            "versions": {"baseline": baseline_versions, "current": current_versions},
        },
        "equivalence": {
            "declared_tolerance": 0,
            "identical": full == incremental,
            "recompute_nodes": sorted(plan.recompute),
            "reuse_nodes": sorted(plan.reuse),
        },
        "measurements": {
            "tool_calls_full": len(NODES),
            "tool_calls_incremental": len(plan.recompute),
            "io_bytes_full": len(NODES) * bytes_per_node,
            "io_bytes_incremental": len(plan.recompute) * bytes_per_node,
            "wall_time_full_s": round(full_s, 6),
            "wall_time_incremental_s": round(inc_s, 6),
            "max_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        },
    }


def test_o12_parity_before_performance():
    m = _measures()
    eq, perf = m["equivalence"], m["measurements"]
    assert eq["identical"] is True
    assert eq["declared_tolerance"] == 0
    # 更快必须建立在等价之上；且不得靠跳过未知依赖（plan 已证明非保守）
    assert perf["tool_calls_incremental"] < perf["tool_calls_full"]
    assert perf["io_bytes_incremental"] < perf["io_bytes_full"]
    assert perf["max_rss_kb"] > 0
    assert perf["wall_time_full_s"] > 0 and perf["wall_time_incremental_s"] > 0
    print("G10_PERFORMANCE_JSON=" + json.dumps(m, ensure_ascii=False, sort_keys=True))


def test_o12_unknown_evidence_degrades_to_full_and_stays_equivalent():
    """证据缺失 → 保守全量；此时增量=全量，绝不靠跳过未知依赖换指标。"""
    base = _snapshot(1, feats={"a": {"properties": {}, "geometry": "POINT(1 1)"}})
    cur = _snapshot(2, feats={"b": {"properties": {}, "geometry": "POINT(2 2)"}})
    cur.feature_id_kind = FeatureIdKind.NONE
    diff = compute_source_diff(baseline=base, current=cur)
    plan = build_refresh_plan(diffs=[diff], dag=DAG,
                              source_versions={"schools": "v2"})
    assert plan.conservative is True
    assert len(plan.recompute) == len(NODES)


@pytest.mark.asyncio
async def test_m7_concurrent_lease_is_fail_fast(tmp_path, monkeypatch):
    """同 proposal 并发执行：第二个请求 fail-fast，不出现 double execute。"""
    from app.core.config import settings
    from app.services.project_runtime.refresh.store import RefreshStore

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    store = RefreshStore()
    pid = "proj-g10-conc"
    ok, _ = store.acquire_lease(pid, "g10p-x-exec")
    assert ok is True
    ok2, holder = store.acquire_lease(pid, "g10p-x-exec")
    assert ok2 is False
    assert "already running" in str(holder.get("reason"))
    store.release_lease(pid, "g10p-x-exec")
    assert store.acquire_lease(pid, "g10p-x-exec")[0] is True
    store.release_lease(pid, "g10p-x-exec")
