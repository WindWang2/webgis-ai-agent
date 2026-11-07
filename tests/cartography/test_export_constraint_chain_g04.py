"""G04-CARTOGRAPHY-COMPOSER W1 characterization（#1999–#2003，先红后绿）.

锁定四条出版链断链的**应然**行为：

- #1999：打印档（A4/A3）导出任务必须携带真实 ``solve_layout_v5`` 求解结果
  （不恒 ``not_evaluated``）；屏幕档保持诚实 not_evaluated。
- #2000：求解结果不落第二事实源 —— cartographer 不把 ``constraint_layout``
  写进持久 MapSpec；SVG 编译不再消费全局标记（组件锚点为唯一门，
  历史锚点默认导出字节稳定）。
- #2002：组件族锁（锁 ``legend`` 保护 ``legend__1``）在出版求解中生效。
- #2003：SVG/矢量链指北针随 ``view.bearing`` 旋转（与 live/canvas 的
  ``rotate(-bearing)`` 语义一致；bearing 缺省输出不变）。
"""
from __future__ import annotations

import uuid

import pytest

from app.lib.cartography.publication_constraint_layout import (
    evaluate_constraint_layout,
    prepare_publication_document,
)
from app.services.agent_swarm.specialists.cartographer import CartographerAgent
from app.services.agent_swarm.specialists.ledger import ArtifactLedger
from app.services.mapspec_store import mapspec_store
from app.services.session_data import session_data_manager
from app.tools.cartography import register_cartography_tools
from app.tools.registry import ToolRegistry

pytestmark = pytest.mark.cartography


# ── 公共构造 ──────────────────────────────────────────────────────────────

def _print_components() -> list[dict]:
    """典型出版整饰：标题/图例/比例尺/指北针 + 图例族派生实例。"""
    return [
        {"id": "title-1", "type": "title", "position": "top-center",
         "enabled": True, "optional": False},
        {"id": "legend", "type": "legend", "position": "bottom-left",
         "enabled": True},
        {"id": "legend__1", "type": "legend", "position": "bottom-left",
         "enabled": True},
        {"id": "scale-1", "type": "scale_bar", "position": "bottom-right",
         "enabled": True},
        {"id": "north-1", "type": "north_arrow", "position": "top-right",
         "enabled": True},
    ]


@pytest.fixture
def registry():
    r = ToolRegistry()
    register_cartography_tools(r)
    return r


@pytest.fixture
async def clean_session():
    sid = f"g04-w1-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)


# ── #1999：打印档导出任务携带真实求解结果 ────────────────────────────────


@pytest.mark.asyncio
async def test_a4_export_task_carries_solver_report(registry, clean_session):
    session_id = clean_session
    await mapspec_store.save_mapspec(session_id, {
        "version": "1.6",
        "view": {"center": [104.06, 30.66], "zoom": 10},
        "layers": [],
        "layout": {"components": _print_components()},
    })
    out = await registry.dispatch("export_thematic_map", {
        "title": "成都小学",
        "format": "png",
        "paper_size": "a4",
        "orientation": "landscape",
    }, session_id=session_id)
    # 求解必须真实发生并在任务上如实披露
    assert out["constraint_layout"] == "evaluated", out.get("constraint_layout")
    report = out["constraint_layout_report"]
    assert report["page_profile"] == "a4_landscape"
    zone_by_id = report["placement_zones"]
    assert "title-1" in zone_by_id
    # 任务参数携带求解产物供前端画布链应用
    params = out["params"]
    assert params["paperSize"] == "A4"
    assert params["constraint_layout"]["placement_zones"] == zone_by_id


@pytest.mark.asyncio
async def test_a3_portrait_uses_matching_profile(registry, clean_session):
    session_id = clean_session
    await mapspec_store.save_mapspec(session_id, {
        "version": "1.6",
        "layers": [],
        "layout": {"components": _print_components()},
    })
    out = await registry.dispatch("export_thematic_map", {
        "title": "t", "format": "pdf",
        "paper_size": "a3", "orientation": "portrait",
    }, session_id=session_id)
    assert out["constraint_layout"] == "evaluated"
    assert out["constraint_layout_report"]["page_profile"] == "a3_portrait"


@pytest.mark.asyncio
async def test_screen_export_stays_not_evaluated_with_note(registry, clean_session):
    session_id = clean_session
    await mapspec_store.save_mapspec(session_id, {
        "version": "1.6",
        "layers": [],
        "layout": {"components": _print_components()},
    })
    out = await registry.dispatch("export_thematic_map", {
        "title": "t", "format": "png", "paper_size": "screen",
    }, session_id=session_id)
    # 屏幕档不虚构求解（诚实缺省），NOTE 保留
    assert out["constraint_layout"] == "not_evaluated"
    assert out["publication_note"]


@pytest.mark.asyncio
async def test_print_export_without_session_discloses_not_evaluated(registry):
    """无会话（无 MapSpec）时打印档也不虚构 evaluated。"""
    out = await registry.dispatch("export_thematic_map", {
        "title": "t", "format": "png", "paper_size": "a4",
    })
    assert out["constraint_layout"] == "not_evaluated"
    assert out["constraint_layout_report"]["reason"] in {
        "page_profile_absent", "no_session_mapspec",
    }


@pytest.mark.asyncio
async def test_batch_print_export_carries_report(registry, clean_session):
    session_id = clean_session
    await mapspec_store.save_mapspec(session_id, {
        "version": "1.6",
        "layers": [],
        "layout": {"components": _print_components()},
    })
    out = await registry.dispatch("export_batch_maps", {
        "titles": ["总览", "北部"],
        "paper_size": "a4", "orientation": "landscape",
    }, session_id=session_id)
    assert out["constraint_layout"] == "evaluated"
    assert out["constraint_layout_report"]["page_profile"] == "a4_landscape"


# ── #2000：求解结果不落第二事实源 ────────────────────────────────────────


def _carto_request(**overrides):
    values = [10, 14, 18, 25, 30, 40, 52, 68, 90, 120]
    ring = [
        [104.0, 30.5], [104.02, 30.5], [104.02, 30.52],
        [104.0, 30.52], [104.0, 30.5],
    ]
    features = [
        {"type": "Feature",
         "properties": {"schools": v, "district": f"d{i}"},
         "geometry": {"type": "Polygon", "coordinates": [ring]}}
        for i, v in enumerate(values)
    ]
    req = {
        "geojson": {"type": "FeatureCollection", "features": features},
        "field": "schools",
        "title": "测试专题图",
        "purpose": "a4_landscape",
        "source_id": "districts",
    }
    req.update(overrides)
    return req


def test_cartographer_mapspec_carries_no_constraint_marker():
    ledger = ArtifactLedger()
    agent = CartographerAgent(ledger=ledger)
    ref = agent.compose(_carto_request())
    payload = ledger.get(ref.ref_id)
    layout = payload["layout"]
    # 求解报告不进持久 MapSpec（第二事实源禁止）；报告走 warnings/ref 载荷
    assert "constraint_layout" not in layout
    assert "constraint_layout_report" not in layout
    # 求解事实保留在一等事实（组件 position/anchor）上
    assert layout["components"]


def test_svg_chrome_ignores_persisted_marker():
    """SVG 编译不得因 spec 携带历史 marker 而改变整饰坐标。

    组件锚点（非历史：图例挪到 bottom-center）是唯一事实 —— 全局
    ``constraint_layout`` 标记不得使同一锚点产出不同坐标（#2000：标记
    会改掉随后的默认矢量导出）。
    """
    from app.services.mapspec_to_svg import _render_chrome_groups

    def _spec() -> dict:
        return {
            "layers": [{
                "id": "layer-1", "type": "circle",
                "legend_spec": {"title": "图例", "entries": [
                    {"label": "a", "color": "#111111"},
                    {"label": "b", "color": "#222222"},
                ]},
            }],
            "layout": {"components": [
                {"id": "title-1", "type": "title", "enabled": True,
                 "position": "top-center", "options": {"text": "成都小学"}},
                {"id": "legend-1", "type": "legend", "enabled": True,
                 "position": "bottom-center", "options": {"layerId": "layer-1"}},
            ]},
        }

    clean = _spec()
    polluted = _spec()
    polluted["layout"] = {
        **polluted["layout"],
        "constraint_layout": "evaluated",
        "constraint_layout_report": {"constraint_layout": "evaluated"},
    }
    svg_clean, _ = _render_chrome_groups(
        clean, [104.0, 30.0, 105.0, 31.0], 800.0, 600.0, lambda _pt: (0.0, 0.0),
    )
    svg_polluted, _ = _render_chrome_groups(
        polluted, [104.0, 30.0, 105.0, 31.0], 800.0, 600.0, lambda _pt: (0.0, 0.0),
    )
    assert svg_clean == svg_polluted
    # 鉴别力：非历史锚点确实产生了与历史锚点不同的图例坐标（y 轴下移）
    historic = _spec()
    historic["layout"]["components"][1]["position"] = "bottom-left"
    svg_historic, _ = _render_chrome_groups(
        historic, [104.0, 30.0, 105.0, 31.0], 800.0, 600.0, lambda _pt: (0.0, 0.0),
    )
    assert svg_clean != svg_historic


def test_default_vector_pdf_bytes_stable_after_profile_solve():
    """#2000 场景回放：A4 求解后，默认（无 profile）导出与无前置求解一致。"""
    from app.services.publication_export import _structural_fingerprint

    document = {
        "version": "1.2",
        "layout": {"components": _print_components()},
    }
    # 先做一次带 profile 的求解（克隆路径）
    cloned, report = prepare_publication_document(
        document, page_profile="a4_landscape",
    )
    assert report["constraint_layout"] == "evaluated"
    # 原文档未被污染：默认导出指纹与从未求解过完全一致
    assert _structural_fingerprint(document) == _structural_fingerprint({
        "version": "1.2",
        "layout": {"components": _print_components()},
    })
    assert "constraint_layout" not in document["layout"]


# ── #2002：组件族锁 ──────────────────────────────────────────────────────


def _zone_contention_components() -> list[dict]:
    """bottom-right 过饱和（A4 档容量 4）：字典序在后的族成员会被挪区。

    求解器按（priority, id）排序占位 —— ``zz-*`` 族成员排最后，解锁时
    必被挤出 bottom-right（鉴别力实证：2026-11-08 实测两个 zz-* 都被挪
    到 top-left）。
    """
    return [
        {"id": "title-1", "type": "title", "position": "top-center",
         "enabled": True, "optional": False},
        *[{"id": f"fill-{c}", "type": "chart_panel",
           "position": "bottom-right", "enabled": True}
          for c in "abcd"],
        {"id": "zz-chart", "type": "chart_panel",
         "position": "bottom-right", "enabled": True},
        {"id": "zz-chart__2", "type": "chart_panel",
         "position": "bottom-right", "enabled": True},
    ]


def test_family_lock_protects_derived_instances():
    comps = _zone_contention_components()
    unlocked_clone, _r = prepare_publication_document(
        {"version": "1.2", "layout": {"components": comps}},
        page_profile="a4_landscape",
    )
    unlocked_pos = {
        c["id"]: c.get("position") for c in unlocked_clone["layout"]["components"]
    }
    # 鉴别力：解锁时族成员确实被求解挪离 bottom-right
    assert unlocked_pos["zz-chart"] != "bottom-right"
    assert unlocked_pos["zz-chart__2"] != "bottom-right"

    locked_clone, locked_report = prepare_publication_document(
        {"version": "1.2",
         "workbench": {"lockedComponentIds": ["zz-chart"]},
         "layout": {"components": comps}},
        page_profile="a4_landscape",
    )
    locked_pos = {
        c["id"]: (c.get("position"), c.get("enabled", True))
        for c in locked_clone["layout"]["components"]
    }
    # 锁 zz-chart 必须保护整个族（`__` 派生语义）：基实例与派生实例都不挪
    assert locked_pos["zz-chart"] == ("bottom-right", True)
    assert locked_pos["zz-chart__2"] == ("bottom-right", True)
    kept = locked_report.get("user_lock_kept") or []
    assert "zz-chart" in kept and "zz-chart__2" in kept


# ── #2003：矢量链指北针随 bearing 旋转 ──────────────────────────────────


def test_render_north_arrow_rotates_with_bearing():
    from app.lib.cartography.svg_marginalia import render_north_arrow

    rotated = render_north_arrow(100.0, 100.0, bearing=45.0)
    assert 'transform="rotate(-45' in rotated
    # 缺省 bearing=0：与历史输出字节一致（无 transform）
    plain = render_north_arrow(100.0, 100.0)
    assert 'transform="rotate(' not in plain
    assert 'class="chrome-north-arrow"' in plain


def test_svg_export_north_arrow_follows_view_bearing():
    from app.services.mapspec_to_svg import _render_chrome_groups

    spec = {
        "view": {"center": [104.5, 30.5], "zoom": 10, "bearing": 30},
        "layers": [],
        "layout": {"components": [
            {"id": "north-1", "type": "north_arrow", "enabled": True,
             "position": "top-right"},
        ]},
    }
    svg, coverage = _render_chrome_groups(
        spec, [104.0, 30.0, 105.0, 31.0], 800.0, 600.0, lambda _pt: (0.0, 0.0),
    )
    assert "north_arrow" in (coverage.get("rendered") or [])
    assert 'rotate(-30' in svg
    # 无 bearing：不旋转（字节兼容）
    spec_flat = {**spec, "view": {"center": [104.5, 30.5], "zoom": 10}}
    svg_flat, _ = _render_chrome_groups(
        spec_flat, [104.0, 30.0, 105.0, 31.0], 800.0, 600.0,
        lambda _pt: (0.0, 0.0),
    )
    assert 'rotate(-' not in svg_flat
