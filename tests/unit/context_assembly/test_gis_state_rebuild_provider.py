"""ADR-0288（W09）：GIS 状态重建块 —— compaction/重启后的权威事实投影。"""
from __future__ import annotations

import asyncio

from app.services.context_assembly.contract import ContextDomain
from app.services.context_assembly.providers import (
    GisStateRebuildProvider,
    build_default_providers,
    render_gis_state_rebuild_block,
)


class _Req:
    session_id = "sess-rebuild"


class _Facts:
    def __init__(self, mapspec, plan=None):
        self.mapspec = mapspec
        self.plan = plan
        self.source_session_id = "sess-rebuild"


class _Plan:
    def __init__(self, capabilities=(), refs=()):
        self.capabilities = capabilities
        self.recent_evidence_refs = refs


def test_rebuild_block_projects_authoritative_state():
    maps = {
        "revision": "rev-7",
        "layers": [
            {"id": "poi-layer", "visible": True},
            {"id": "hidden-heatmap", "visible": False},
        ],
    }
    text = render_gis_state_rebuild_block(maps, _Plan(("capability_x",), ("ref://r1",)))
    assert "rev-7" in text
    assert "可见图层: poi-layer" in text
    # 隐藏状态是用户约束 —— 重建后必须保留（A11）
    assert "隐藏图层（保持隐藏）: hidden-heatmap" in text
    assert "capability_x" in text and "ref://r1" in text


def test_rebuild_block_bounded_and_honest():
    # 有界：>24 层截断披露；空 mapspec 形状不虚构
    maps = {"revision": "r", "layers": [{"id": f"l{i}", "visible": True} for i in range(40)]}
    text = render_gis_state_rebuild_block(maps, None)
    assert "仅列前 24" in text
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
