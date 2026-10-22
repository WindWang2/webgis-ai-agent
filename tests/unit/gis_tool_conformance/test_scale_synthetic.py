"""G03 WP-D.12 —— 合成规模下的全 registry 对账：确定性、一致性、有界性、性能。

1000 个合成工具，元数据按索引轮换制造 pass/warning/fail 混合（与任务书同款规则）：

- ``i % 10 == 0`` → ``fallback_tool="ghost_missing"`` 悬空 → registration 面
  fail（``fallback_tool_dangling``），overall=fail；
- 其余 ``i % 7 == 0`` → 不传 ``side_effect``（缺省 unclassified）→ registration
  面 warning（``metadata_incomplete``），overall=warning；
- 其余 → descriptor 完备（pure/offline/deterministic/bounded/output type 全给）
  → overall=pass。

断言面：

1. 全量审计 1000 报告：排序确定、by_overall 与轮换规则逐桶吻合、
   top_reasons 与逐面重数一致、零 audit_error（单工具审计缺陷不发生的
   基线）；
2. 同输入跨 registry 实例的全量报告逐字节一致（to_dict 相等）；
3. 单工具审计 1000 次循环指纹稳定（前 200 次 == 后 200 次）；
4. 全量审计耗时 < 60s（宽松防性能回归闸，不是精确基准）；
5. ``MAX_REPORTS`` 截断路径：``tool_names`` 传 MAX_REPORTS+7 个 →
   ``truncated=True`` 且 ``tool_count == MAX_REPORTS``（截断必须披露）。
"""
from __future__ import annotations

import time

from app.lib.gis.tool_capability_conformance import (
    MAX_REPORTS,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_WARNING,
    ConformanceFacts,
    audit_registry_conformance,
    audit_tool_conformance,
)
from app.tools.registry import ToolRegistry

_N = 1000
_DANGLING_FALLBACK = "ghost_missing"


def _build_synthetic_registry(n: int = _N) -> ToolRegistry:
    """n 个合成工具：i%10 悬空 fallback（fail）/ i%7 元数据缺省（warning）/
    其余完备（pass）。"""
    registry = ToolRegistry()
    for i in range(n):
        name = f"tool_{i:04d}"
        if i % 10 == 0:
            registry.register(
                name, f"synthetic {i}", func=lambda **_: {},
                fallback_tool=_DANGLING_FALLBACK,
                side_effect="pure", network=False, deterministic=True,
                result_size_policy="bounded", output_semantic_type="object")
        elif i % 7 == 0:
            # 不传 side_effect —— registration 面完整度 warning
            registry.register(
                name, f"synthetic {i}", func=lambda **_: {},
                network=False, deterministic=True,
                result_size_policy="bounded", output_semantic_type="object")
        else:
            registry.register(
                name, f"synthetic {i}", func=lambda **_: {},
                side_effect="pure", network=False, deterministic=True,
                result_size_policy="bounded", output_semantic_type="object",
                produced_refs=("data",))
    return registry


def _expected_overall(i: int) -> str:
    if i % 10 == 0:
        return STATUS_FAIL
    if i % 7 == 0:
        return STATUS_WARNING
    return STATUS_PASS


def _facts(registry: ToolRegistry) -> ConformanceFacts:
    return ConformanceFacts(
        registry=registry,
        capability_ids=("cap_synthetic",),
        algorithms=(),
        parameter_contracts={},
        native_tool_names=(),
        domain_keywords={"analysis": ["分析"]},
    )


def test_full_audit_mixed_verdicts_sorted_and_consistent():
    """1000 报告：排序确定 + by_overall 逐桶吻合轮换规则 + 计数自洽。"""
    registry = _build_synthetic_registry()
    summary = audit_registry_conformance(_facts(registry))

    assert summary.tool_count == _N
    assert not summary.truncated
    names = [rep.tool for rep in summary.reports]
    assert names == sorted(names), "全量报告必须按工具名稳定排序"

    expected_buckets: dict[str, int] = {}
    for i in range(_N):
        expected_buckets[_expected_overall(i)] = (
            expected_buckets.get(_expected_overall(i), 0) + 1)
    assert summary.by_overall == expected_buckets
    # 轮换规则保证三类裁决都非空（fail=100 / warning=128 / pass=772）
    assert expected_buckets == {
        STATUS_FAIL: 100, STATUS_WARNING: 128, STATUS_PASS: 772}
    assert sum(summary.by_overall.values()) == summary.tool_count == _N


def test_full_audit_top_reasons_match_face_recount():
    """top_reasons 与逐报告逐面重数一致（warning/fail 面才计数）。"""
    registry = _build_synthetic_registry()
    summary = audit_registry_conformance(_facts(registry))

    recounted: dict[str, int] = {}
    for rep in summary.reports:
        for verdict in rep.faces:
            if verdict.status in (STATUS_WARNING, STATUS_FAIL) and verdict.reason:
                recounted[verdict.reason] = recounted.get(verdict.reason, 0) + 1
    assert sum(n for _, n in summary.top_reasons) == sum(recounted.values())
    assert dict(summary.top_reasons) == recounted
    assert summary.top_reasons == tuple(
        sorted(summary.top_reasons, key=lambda kv: (-kv[1], kv[0]))), (
        "top_reasons 必须按 (次数降序, reason 升序) 确定排序")
    # 轮换规则钉住两个主导 reason code 的精确计数
    assert dict(summary.top_reasons).get("fallback_tool_dangling") == 100
    assert dict(summary.top_reasons).get("metadata_incomplete") == 128


def test_full_audit_zero_audit_error_reports():
    """合成 registry 无缺陷工具 → 1000 份报告零 audit_error（无异常吞噬）。"""
    registry = _build_synthetic_registry()
    summary = audit_registry_conformance(_facts(registry))
    assert not any(
        verdict.reason == "audit_error"
        for rep in summary.reports for verdict in rep.faces)
    assert all(rep.fingerprint for rep in summary.reports), (
        "正常审计路径的每份报告都必须有指纹")


def test_full_audit_deterministic_across_registry_instances():
    """同输入（重建的等价 registry）→ 全量报告 to_dict 逐字节一致。"""
    first = audit_registry_conformance(_facts(_build_synthetic_registry()))
    second = audit_registry_conformance(_facts(_build_synthetic_registry()))
    assert first.to_dict() == second.to_dict()


def test_single_tool_fingerprint_stable_over_1000_audits():
    """单工具审计 1000 次循环：前 200 次与后 200 次指纹完全一致。"""
    registry = _build_synthetic_registry()
    facts = _facts(registry)
    fingerprints = [
        audit_tool_conformance("tool_0500", facts).fingerprint
        for _ in range(1000)
    ]
    assert len(fingerprints) == 1000
    assert len(set(fingerprints)) == 1, "同输入同 facts 的指纹必须恒定"
    assert fingerprints[:200] == fingerprints[-200:]


def test_full_audit_latency_budget():
    """1000 工具全量审计 < 60s（宽松上界：防 O(n²) 级性能回归，非基准）。"""
    registry = _build_synthetic_registry()
    facts = _facts(registry)
    start = time.perf_counter()
    summary = audit_registry_conformance(facts)
    elapsed = time.perf_counter() - start
    assert summary.tool_count == _N
    assert elapsed < 60.0, f"全量审计耗时 {elapsed:.1f}s 超过 60s 防回归闸"


def test_truncation_at_max_reports_disclosed():
    """tool_names 传 MAX_REPORTS+7 个 → 截断披露：truncated=True、条数封顶。"""
    registry = _build_synthetic_registry()
    facts = _facts(registry)
    names = [f"tool_{i:04d}" for i in range(MAX_REPORTS + 7)]
    assert len(names) > MAX_REPORTS
    capped = audit_registry_conformance(facts, tool_names=names)

    assert capped.truncated is True, "超出 MAX_REPORTS 必须显式披露截断"
    assert capped.tool_count == MAX_REPORTS, "报告条数必须封顶在 MAX_REPORTS"
    # 排序确定：保留的是字典序前 MAX_REPORTS 个名字
    kept = [rep.tool for rep in capped.reports]
    assert kept == sorted(names)[:MAX_REPORTS]
    # 尾部 31 个名字越界（tool_1000..tool_1030 未注册）→ 审计缺陷降级报告
    # 不中断全量（既有韧性语义），但截断先于它们发生：截断块内的越界名
    # 只有前 24 个进入报告，其余 7 个被截断丢弃。
    audit_errors = [
        rep for rep in capped.reports
        if rep.faces and rep.faces[0].reason == "audit_error"]
    assert len(audit_errors) == 24
