"""V8 资格引擎数据质量面回归锁（DQH v1，additive）。

不变式：
- QualificationContext 未提供质量面（默认 ""/[]）→ qualify_node 行为与
  既有完全一致（feature-off）；
- quality_gate="blocked" → INELIGIBLE + 结构化 reason（check=quality_gate，
  hint 携带阻断码）—— 不可修复质量问题在 capability 层早期失格；
- quality_gate="degraded" → 仅在节点原本 eligible 时降级为 DEGRADED；
  节点已有硬失格 reason 时不掩盖（保持 INELIGIBLE）；
- quality_gate="ready"/"unknown" → 零增量（unknown ≠ 不满足红线）；
- to_dict 投影有界且包含质量面。
"""
from __future__ import annotations

from app.services.gis_harness.capability_graph import (
    KIND_MODEL,
    KIND_TOOL,
    GraphNode,
)
from app.services.gis_harness.qualification_v8 import (
    QualificationContext,
    QualificationStatus,
    qualify_model_for_input,
    qualify_node,
)


def _plain_tool() -> GraphNode:
    return GraphNode(
        id="test.quality.plain_tool",
        kind=KIND_TOOL,
        source_registry="test",
        label="plain tool",
        extras={"tier": 1},
    )


class TestQualityGateFace:
    def test_feature_off_unchanged(self):
        ctx = QualificationContext()
        result = qualify_node(_plain_tool(), ctx)
        assert result.status == QualificationStatus.ELIGIBLE

    def test_blocked_gate_ineligible_with_structured_reason(self):
        ctx = QualificationContext(
            quality_gate="blocked",
            blocking_issue_codes=["empty_payload", "profile_incomplete"])
        result = qualify_node(_plain_tool(), ctx)
        assert result.status == QualificationStatus.INELIGIBLE
        qr = [r for r in result.reasons if r.check == "quality_gate"]
        assert qr, result.to_dict()
        assert "empty_payload" in qr[0].hint
        assert qr[0].expected

    def test_degraded_gate_degrades_eligible_node(self):
        ctx = QualificationContext(
            quality_gate="degraded",
            blocking_issue_codes=["crs_missing"])
        result = qualify_node(_plain_tool(), ctx)
        assert result.status == QualificationStatus.DEGRADED
        assert any(r.check == "quality_gate" for r in result.reasons)

    def test_degraded_gate_does_not_mask_hard_ineligibility(self):
        # 节点已有硬失格（tier>=3 confirm_required → degraded 面；
        # 这里用 offline+network 构造硬失格）→ 保持 INELIGIBLE。
        node = GraphNode(
            id="test.quality.net_tool",
            kind=KIND_TOOL,
            source_registry="test",
            extras={"tier": 1, "network": True},
        )
        ctx = QualificationContext(offline=True, quality_gate="degraded")
        result = qualify_node(node, ctx)
        assert result.status == QualificationStatus.INELIGIBLE
        assert any(r.check == "offline_network_required" for r in result.reasons)

    def test_ready_and_unknown_gates_zero_delta(self):
        for gate in ("ready", "unknown"):
            ctx = QualificationContext(quality_gate=gate)
            result = qualify_node(_plain_tool(), ctx)
            assert result.status == QualificationStatus.ELIGIBLE, gate
            assert not any(r.check == "quality_gate" for r in result.reasons)

    def test_context_projection_includes_quality(self):
        ctx = QualificationContext(
            quality_gate="degraded", blocking_issue_codes=["crs_missing"])
        d = ctx.to_dict()
        assert d["quality_gate"] == "degraded"
        assert d["blocking_issue_codes_count"] == 1


class TestBuildSituationPassthrough:
    def test_quality_face_flows_through(self):
        from app.services.gis_harness.capability_resolution import build_situation

        ctx = build_situation(
            quality_gate="blocked",
            blocking_issue_codes=["empty_payload"])
        assert ctx.quality_gate == "blocked"
        assert ctx.blocking_issue_codes == ["empty_payload"]
        result = qualify_node(_plain_tool(), ctx)
        assert result.status == QualificationStatus.INELIGIBLE

    def test_default_zero_delta(self):
        from app.services.gis_harness.capability_resolution import build_situation

        ctx = build_situation()
        assert ctx.quality_gate == ""
        assert ctx.blocking_issue_codes == []
        assert qualify_node(_plain_tool(), ctx).status == QualificationStatus.ELIGIBLE


class TestReviewP2ConditionalProjection:
    """review P2-2：质量面键仅在启用时进投影（默认参数逐字节兼容）。"""

    def test_default_to_dict_has_no_quality_keys(self):
        d = QualificationContext().to_dict()
        assert "quality_gate" not in d
        assert "blocking_issue_codes_count" not in d

    def test_set_gate_projection_includes_keys(self):
        d = QualificationContext(quality_gate="degraded").to_dict()
        assert d["quality_gate"] == "degraded"
        assert d["blocking_issue_codes_count"] == 0


# ── review P2：extras 脏值不得炸纯函数；无声明不得误报 DEGRADED ────────


def _model(**extras) -> GraphNode:
    base = {"task_types": ["segmentation"], "input_bands": 3,
            "provider_ref": "p", "owner_scope_key": "global-builtin"}
    base.update(extras)
    return GraphNode(
        id="test.quality.model", kind=KIND_MODEL, source_registry="test",
        label="model", extras=base)


def test_tier_none_or_garbage_does_not_escape():
    """反证（review P2）：int(node.extras.get("tier", 1)) 无保护 —— tier 为
    None 或非数字串时 TypeError/ValueError 逃逸纯函数（同文件 248-251 对
    同一值有 try/except，前后不一致）。"""
    ctx = QualificationContext()
    for bad in (None, "abc", "", [], {}):
        result = qualify_node(_plain_tool_with_extras({"tier": bad}), ctx)
        assert result.status == QualificationStatus.ELIGIBLE
    # tier>=3 仍照常披露（行为不回退）
    high = qualify_node(_plain_tool_with_extras({"tier": 3}), ctx)
    assert high.status == QualificationStatus.DEGRADED
    assert any(r.check == "confirm_required" for r in high.reasons)


def _plain_tool_with_extras(extras) -> GraphNode:
    merged = {"tier": 1}
    merged.update(extras)
    return GraphNode(
        id="test.quality.tier_tool", kind=KIND_TOOL, source_registry="test",
        label="tier tool", extras=merged)


def test_no_declared_resolution_range_is_not_degraded():
    """反证（review P2）：模型无 min/max_m_per_px 且输入为地理 CRS(res=0)
    时不得误报 DEGRADED —— reason 文本会变成 "[None, None] m/px"。"""
    ctx = QualificationContext(raster_bands=3, resolution_m_per_px=0.0)
    result = qualify_model_for_input(_model(), ctx)
    assert result.status == QualificationStatus.ELIGIBLE, result.to_dict()
    assert not any(r.check == "resolution_unknown" for r in result.reasons)


def test_geographic_crs_with_declared_range_still_degraded():
    """对照：确实声明了分辨率范围时，地理 CRS 仍是 degraded（不硬拒）。"""
    ctx = QualificationContext(raster_bands=3, resolution_m_per_px=0.0)
    result = qualify_model_for_input(
        _model(min_m_per_px=0.1, max_m_per_px=10.0), ctx)
    assert result.status == QualificationStatus.DEGRADED
    assert any(r.check == "resolution_unknown" for r in result.reasons)


def test_partially_declared_resolution_range_is_checked():
    """反证（review P2）：res>0 只声明 lo/hi 之一时不得静默跳过校验。"""
    ctx = QualificationContext(raster_bands=3, resolution_m_per_px=5.0)
    # 只声明下限 10 → 5 m/px 低于下限 → INELIGIBLE（此前静默放行）
    only_lo = qualify_model_for_input(_model(min_m_per_px=10.0), ctx)
    assert only_lo.status == QualificationStatus.INELIGIBLE
    assert any(r.check == "resolution" for r in only_lo.reasons)
    # 只声明上限 10 → 5 m/px 在界内 → 合格
    only_hi = qualify_model_for_input(_model(max_m_per_px=10.0), ctx)
    assert only_hi.status == QualificationStatus.ELIGIBLE, only_hi.to_dict()
