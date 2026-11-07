"""G04 W4(CM-08):Agent 增量制图语义 —— 精确事务边界.

「保留图层只改样式/色带」类用户指令必须编译为**只动目标组件面**的精确
事务;修改无关组件/数据/产物即为失败:

- 组件 variant/options 突变(wegbis_component_update):图层层数组逐字节
  不变、sources 不变、revision 恰好 +1、其余组件逐字段不变;
- 无数据要求的组件面请求(unknown/partial 语义):catalog/update 对不存在
  实体的行为是 typed 列示/拒绝,不生成装饰性组件;
- 组件突变不产生任何 GIS 产物(无新 artifact ref —— 改色不重跑算法)。
"""
from __future__ import annotations

import copy
import shutil
import uuid

import pytest

from app.services.gis_harness.components import build_default_components
from app.services.mapspec.store import BASE_STORAGE_DIR
from app.services.mapspec_store import mapspec_store
from app.services.session_data import session_data_manager

pytestmark = [pytest.mark.asyncio, pytest.mark.cartography]


@pytest.fixture
async def clean_session():
    sid = f"g04-incr-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
async def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    init_tools(reg)
    return reg


def _districts_spec() -> dict:
    ring = [
        [104.0, 30.5], [104.15, 30.5], [104.15, 30.68], [104.0, 30.68],
        [104.0, 30.5],
    ]
    features = [
        {"type": "Feature",
         "properties": {"district": f"d{i}", "schools": float(v)},
         "geometry": {"type": "Polygon", "coordinates": [ring]}}
        for i, v in enumerate([3, 9, 18, 40])
    ]
    return {
        "version": "1.6",
        "view": {"center": [104.07, 30.6], "zoom": 10},
        "sources": {"S": {"type": "geojson", "inlineData": {
            "type": "FeatureCollection", "features": features}}},
        "layers": [{
            "id": "choro", "source": "S", "type": "fill",
            "paint": {"color": {"method": "choropleth", "field": "schools"}},
            "legend_spec": {"title": "小学数量", "unit": "所",
                            "entries": [{"label": "0–10", "color": "#fef0d9"}]},
        }],
        "layout": {"components": [], "component_links": []},
    }


async def _seed(registry, sid: str) -> dict:
    await mapspec_store.save_mapspec(sid, _districts_spec())
    comps = build_default_components(primary_cartography="administrative_choropleth")
    await mapspec_store.layout_set(
        sid, components=[c.to_mapspec() for c in comps], origin="agent",
    )
    spec = await mapspec_store.get_mapspec(sid)
    return spec


async def _revision_of(registry, sid: str) -> int:
    catalog = await registry.dispatch(
        "webgis_component_catalog", {}, session_id=sid)
    return int(catalog.get("mutation_revision") or 0)


async def test_variant_change_touches_only_target_component(registry, clean_session):
    sid = clean_session
    before = await _seed(registry, sid)
    before_layers = copy.deepcopy(before["layers"])
    before_sources = copy.deepcopy(before["sources"])
    before_components = copy.deepcopy(before["layout"]["components"])
    revision_before = await _revision_of(registry, sid)
    target_id = next(
        (c["id"] for c in before_components
         if c.get("type") == "scale_bar"), None,
    ) or before_components[0]["id"]

    out = await registry.dispatch("webgis_component_update", {
        "component_id": target_id,
        "options": {"orientation": "vertical"},
    }, session_id=sid)
    assert out.get("success") is True, out

    after = await mapspec_store.get_mapspec(sid)
    # 1) 数据面零触碰(改样式不重跑算法):图层与源逐字节一致
    assert after["layers"] == before_layers
    assert after["sources"] == before_sources
    # 2) 组件面:只有目标组件变化,其余逐字段不变
    after_components = after["layout"]["components"]
    assert len(after_components) == len(before_components)
    changed = [
        (b, a) for b, a in zip(before_components, after_components) if b != a
    ]
    assert len(changed) == 1, f"恰好一个组件变化,实际 {len(changed)}"
    assert changed[0][0]["id"] == target_id
    # 3) revision 单调推进(精确事务;工具回执/catalog 双口径一致)
    revision_after = await _revision_of(registry, sid)
    assert revision_after > revision_before, (revision_before, revision_after)
    if isinstance(out.get("mutation_revision"), int):
        assert out["mutation_revision"] == revision_after
    # 4) 不产生 GIS 产物引用(无 artifact 面新事实)
    artifact_keys = [
        k for k in after.keys() if "artifact" in str(k).lower()
    ]
    assert artifact_keys == before_artifact_keys(before)


def before_artifact_keys(spec: dict) -> list:
    return [k for k in spec.keys() if "artifact" in str(k).lower()]


async def test_update_unknown_instance_is_typed_not_decorated(registry, clean_session):
    """unknown 实例:typed 失败并列出在场实例 —— 不静默创建装饰性组件。"""
    sid = clean_session
    await _seed(registry, sid)
    spec_before = await mapspec_store.get_mapspec(sid)
    out = await registry.dispatch("webgis_component_update", {
        "component_id": "ghost-panel-42",
        "variant": "default",
    }, session_id=sid)
    # 缺席实例 → typed 拒绝 + correction_hint 列示在场组件(不静默创建)
    assert out.get("success") is False
    assert "未找到组件" in str(out.get("message"))
    hint = str(out.get("correction_hint") or "")
    assert "north" in hint or "north_arrow" in hint
    spec_after = await mapspec_store.get_mapspec(sid)
    assert spec_after["layout"]["components"] == \
        spec_before["layout"]["components"]


async def test_no_data_session_reports_not_decorated(registry, clean_session):
    """空会话(无 MapSpec):catalog 诚实缺省,不生成假组件面。"""
    sid = clean_session
    out = await registry.dispatch("webgis_component_catalog", {}, session_id=sid)
    body = out if isinstance(out, dict) else {}
    assert body.get("success") is False or body.get("components") == []
    spec = await mapspec_store.get_mapspec(sid)
    assert spec is None or not (spec.get("layout") or {}).get("components")
