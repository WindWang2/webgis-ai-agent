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
    KIND_TOOL,
    GraphNode,
)
from app.services.gis_harness.qualification_v8 import (
    QualificationContext,
    QualificationStatus,
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
