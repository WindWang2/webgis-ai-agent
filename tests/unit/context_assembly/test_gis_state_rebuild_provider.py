"""ADR-0288（W09）：GIS 状态重建块 —— compaction/重启后的权威事实投影。

测试使用**真实** SessionPlan / MapSpec 形状（review-P1 修复锚点）：
- visibility 在 ``layer.layout.visibility = "visible" | "none"``；
- 待完成能力在 ``SessionPlan.progress``（pending/voided/failed）；
- 证据 refs 在 ``PlanStep.evidence[].ref``。
"""
from __future__ import annotations

import asyncio

from app.services.context_assembly.contract import ContextDomain
from app.services.context_assembly.providers import (
    GisStateRebuildProvider,
    build_default_providers,
    render_gis_state_rebuild_block,
)
from app.services.session_plan import CapabilityProgress, SessionPlan


class _Req:
    session_id = "sess-rebuild"


class _Facts:
    def __init__(self, mapspec, plan=None):
        self.mapspec = mapspec
        self.plan = plan
        self.source_session_id = "sess-rebuild"


def _real_plan(capabilities=(), steps=()):
    return SessionPlan(
        envelope_id="env-rebuild",
        session_id="sess-rebuild",
        user_goal="分布图",
        progress=[
            CapabilityProgress(capability=c, status="pending") for c in capabilities
        ],
        steps=list(steps),
    )


def test_rebuild_block_projects_authoritative_state():
    maps = {
        "revision": "rev-7",
        "layers": [
            {"id": "poi-layer", "layout": {"visibility": "visible"}},
            # canonical hidden: layout.visibility = "none"（review-P1 关键形状）
            {"id": "hidden-heatmap", "layout": {"visibility": "none"}},
        ],
    }
    plan = _real_plan(("capability_x",))
    text = render_gis_state_rebuild_block(maps, plan)
    assert "rev-7" in text
    assert "可见图层: poi-layer" in text
    # 隐藏状态是用户约束 —— 重建后必须保留（A11）；canonical layout.visibility=none
    assert "隐藏图层（保持隐藏）: hidden-heatmap" in text
    assert "capability_x" in text


def test_rebuild_block_hidden_layer_not_misreported_visible():
    """review-P1 核心回归：真实隐藏层绝不可被投成可见（防止模型重新显示）。"""
    maps = {"layers": [{"id": "hidden-real", "layout": {"visibility": "none"}}]}
    text = render_gis_state_rebuild_block(maps, None)
    assert "可见图层: hidden-real" not in text
    assert "隐藏图层（保持隐藏）: hidden-real" in text


def test_rebuild_block_projects_step_evidence_refs():
    from app.services.harness_kernel.models import PlanStep, StepEvidence

    step = PlanStep(
        id="step-1",
        capability="capability_x",
        evidence=[StepEvidence(tool="nearest_facility", ref="ref://r1")],
    )
    maps = {"layers": [{"id": "a-layer", "layout": {"visibility": "visible"}}]}
    text = render_gis_state_rebuild_block(maps, _real_plan(steps=[step]))
    assert "ref://r1" in text


def test_rebuild_block_bounded_and_honest():
    # 有界：>24 层截断披露；>12 每类遗漏披露；空 mapspec 形状不虚构
    maps = {
        "revision": "r",
        "layers": [
            {"id": f"l{i}", "layout": {"visibility": "visible"}} for i in range(40)
        ],
    }
    text = render_gis_state_rebuild_block(maps, None)
    assert "仅扫描前 24" in text
    assert "另 12 层未逐一列出" in text  # 24 visible scanned, only 12 listed
    assert render_gis_state_rebuild_block({}, None) == ""
    assert render_gis_state_rebuild_block(None, None) == ""


def test_provider_skips_without_mapspec_and_registered():
    provider = GisStateRebuildProvider()
    report = asyncio.run(provider.collect(_Req(), _Facts(None, None)))
    assert report.skipped_reason == "disabled"
    assert provider.domain is ContextDomain.GIS_STATE_REBUILD
    assert any(
        isinstance(p, GisStateRebuildProvider) for p in build_default_providers()
    )
