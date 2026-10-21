"""G03 引擎确定性/有界性测试（WP-D.12）。

指纹跨调用稳定、报告排序确定、MAX_REPORTS 截断披露、单工具审计缺陷
不中断全量报告、to_dict 有界。
"""
from app.lib.gis.tool_capability_conformance import (
    MAX_DETAIL_CHARS,
    MAX_REPORTS,
    STATUS_FAIL,
    ConformanceFacts,
    audit_registry_conformance,
    audit_tool_conformance,
)
from app.tools.registry import ToolRegistry


def _registry_with(n: int = 8) -> ToolRegistry:
    r = ToolRegistry()
    for i in range(n):
        r.register(
            f"tool_{i:03d}", f"t{i}", func=lambda **_: {"i": i},
            side_effect="pure", network=False, deterministic=True,
            result_size_policy="bounded", output_semantic_type="object")
    return r


def _facts(r: ToolRegistry) -> ConformanceFacts:
    return ConformanceFacts(
        registry=r, capability_ids=("cap_a",),
        domain_keywords={"analysis": ["x"]})


def test_fingerprint_stable_across_calls():
    r = _registry_with()
    a = audit_tool_conformance("tool_000", _facts(r))
    b = audit_tool_conformance("tool_000", _facts(_registry_with()))
    assert a.fingerprint == b.fingerprint
    assert a.to_dict() == b.to_dict()


def test_fingerprint_changes_with_verdict_change():
    r = _registry_with()
    before = audit_tool_conformance("tool_000", _facts(r))
    r.register(
        "tool_000", "t0", func=lambda **_: {},
        side_effect="pure", network=False, deterministic=True,
        result_size_policy="bounded", output_semantic_type="object",
        fallback_tool="ghost")  # 引入 fail 面
    after = audit_tool_conformance("tool_000", _facts(r))
    assert before.fingerprint != after.fingerprint
    assert after.face("registration").status == STATUS_FAIL


def test_registry_audit_sorted_and_bounded():
    r = _registry_with(16)
    s = audit_registry_conformance(_facts(r))
    names = [rep.tool for rep in s.reports]
    assert names == sorted(names)
    assert s.tool_count == 16 and not s.truncated


def test_registry_audit_truncation_disclosed():
    r = _registry_with(4)
    s = audit_registry_conformance(_facts(r), tool_names=[f"tool_{i:03d}" for i in range(4)])
    capped = audit_registry_conformance(
        _facts(r),
        tool_names=[f"tool_{i:03d}" for i in range(MAX_REPORTS + 3)])
    assert not s.truncated and capped.truncated
    assert capped.tool_count == MAX_REPORTS


def test_single_tool_audit_failure_does_not_break_summary():
    """registry.descriptor 抛错（例如审计期被并发注销）→ 降级为 audit_error
    报告，全量继续。"""
    class FlakyRegistry(ToolRegistry):
        def descriptor(self, name):
            if name == "tool_001":
                raise RuntimeError("boom")
            return super().descriptor(name)

    r = FlakyRegistry()
    for i in range(4):
        r.register(
            f"tool_{i:03d}", f"t{i}", func=lambda **_: {},
            side_effect="pure", network=False, deterministic=True,
            result_size_policy="bounded", output_semantic_type="object")
    s = audit_registry_conformance(_facts(r))
    by_tool = {rep.tool: rep for rep in s.reports}
    assert by_tool["tool_001"].overall == STATUS_FAIL
    assert by_tool["tool_001"].faces[0].reason == "audit_error"
    # 其余工具不受单点审计缺陷影响：保持 pass 且无 audit_error 面
    for name in ("tool_000", "tool_002", "tool_003"):
        assert by_tool[name].overall != STATUS_FAIL, name
        assert all(v.reason != "audit_error" for v in by_tool[name].faces), name


def test_to_dict_detail_bounded():
    r = ToolRegistry()
    r.register(
        "long_detail", "x", func=lambda **_: {},
        fallback_tool="ghost")
    rep = audit_tool_conformance("long_detail", _facts(r))
    d = rep.to_dict()
    for face in d["faces"]:
        assert len(face["detail"]) <= MAX_DETAIL_CHARS
    assert len(d["fingerprint"]) == 16


def test_summary_counts_consistent():
    r = _registry_with(6)
    s = audit_registry_conformance(_facts(r))
    assert sum(s.by_overall.values()) == s.tool_count == 6
    payload = s.to_dict(include_reports=False)
    assert "reports" not in payload and payload["tool_count"] == 6


def test_reason_counts_match_faces():
    r = _registry_with(4)
    s = audit_registry_conformance(_facts(r))
    counted = 0
    for rep in s.reports:
        for v in rep.faces:
            if v.status in ("warning", "fail") and v.reason:
                counted += 1
    assert sum(n for _, n in s.top_reasons) == counted
    assert s.top_reasons == tuple(
        sorted(s.top_reasons, key=lambda kv: (-kv[1], kv[0])))
