"""G24 P6 工具面测试：pick_detail / selection_inspect 的授权与 refs-only 纪律。

关键断言：
- 只接受会话内 ref（跨会话/不存在 → typed not_found，不泄露）；
- pick_detail 返回几何摘要**不含坐标**（大要素不进模型上下文）；
- selection_inspect：handle 三重绑定（跨会话 not_found）、revision 失效
  typed stale、count/completeness 如实、id 枚举有界分页；
- 真工具注册（ToolRegistry dispatch 路径），非 mock 断言自身。
"""
from __future__ import annotations

import uuid

import pytest

from app.services.selection_handles import create_selection, handle_store
from app.services.session_data import session_data_manager
from app.tools.registry import ToolRegistry
from app.tools.selection_tools import register_selection_tools


def sid() -> str:
    return f"sess-t24-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def reg():
    reg = ToolRegistry()
    register_selection_tools(reg)
    return reg


async def _seed(s: str, n: int = 30) -> str:
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": i,
                "geometry": {"type": "Point", "coordinates": [104.0 + i * 0.01, 30.0]},
                "properties": {"i": i, "name": f"f{i}"},
            }
            for i in range(n)
        ],
    }
    return await session_data_manager.store(s, {"geojson": fc}, prefix="data")


@pytest.mark.asyncio
async def test_pick_detail_authoritative_and_refs_only(reg):
    s = sid()
    ref = await _seed(s)
    out = await reg.dispatch(
        "webgis_pick_detail", {"ref_id": ref, "feature_id": "17"}, session_id=s)
    assert out["found"] is True
    assert out["count"] == 1 and out["completeness"] == "exact"
    assert out["properties"]["i"] == 17
    gsum = out["geometry_summary"]
    assert gsum["type"] == "Point" and gsum["vertex_count"] == 1
    assert "coordinates" not in gsum  # refs-only：坐标绝不进模型上下文
    assert isinstance(out["revision"], int)


@pytest.mark.asyncio
async def test_pick_detail_missing_and_cross_session(reg):
    s = sid()
    ref = await _seed(s)
    out = await reg.dispatch(
        "webgis_pick_detail", {"ref_id": ref, "feature_id": "999"}, session_id=s)
    assert out["error"] == "feature_not_found"
    # 跨会话：ref 归属校验（不泄露他人数据）
    out2 = await reg.dispatch(
        "webgis_pick_detail", {"ref_id": ref, "feature_id": "1"}, session_id=sid() + "x")
    assert out2["error"] == "ref_not_found"


@pytest.mark.asyncio
async def test_selection_inspect_enumerates_full_set_paged(reg):
    s = sid()
    ref = await _seed(s)
    payload = await session_data_manager.get(s, ref)
    features = payload["geojson"]["features"]
    entry = await create_selection(
        s, ref, None, {"kind": "where", "where": {"field": "i", "op": "lt", "value": 10}},
        features)
    out = await reg.dispatch(
        "webgis_selection_inspect",
        {"ref_id": ref, "handle": entry.handle, "page_size": 4},
        session_id=s)
    assert out["count"] == 10 and out["count_exact"] is True
    assert out["completeness"] == "exact"
    assert out["ids_page"] == [str(i) for i in range(4)]
    assert out["page"]["has_more"] is True
    # 翻页到尾：枚举 = 全集（A11 —— 不是前 50）
    ids, cursor = [], out["page"]["next_cursor"]
    ids.extend(out["ids_page"])
    while cursor:
        page = await reg.dispatch(
            "webgis_selection_inspect",
            {"ref_id": ref, "handle": entry.handle, "page_size": 4, "cursor": cursor},
            session_id=s)
        ids.extend(page["ids_page"])
        cursor = page["page"]["next_cursor"]
    assert ids == [str(i) for i in range(10)]


@pytest.mark.asyncio
async def test_selection_inspect_cross_session_and_stale(reg):
    s = sid()
    ref = await _seed(s)
    payload = await session_data_manager.get(s, ref)
    desc = await session_data_manager.get_ref_descriptor(s, ref)
    entry = await create_selection(
        s, ref, desc.get("content_revision"),
        {"kind": "ids", "ids": ["1", "2"]}, payload["geojson"]["features"])
    # 跨会话（A16）：不泄露存在性
    out = await reg.dispatch(
        "webgis_selection_inspect",
        {"ref_id": ref, "handle": entry.handle}, session_id=sid() + "y")
    assert out["error"] == "selection_not_found"
    # revision 前进（A22）：typed stale + 双 revision 披露
    await session_data_manager.overwrite(s, ref, {"geojson": {"type": "FeatureCollection", "features": []}})
    out2 = await reg.dispatch(
        "webgis_selection_inspect",
        {"ref_id": ref, "handle": entry.handle}, session_id=s)
    assert out2["error"] == "selection_stale"
    assert out2["current_revision"] > out2["handle_revision"]
    # stale 检查后 handle 已被释放语义（再查仍 stale/not_found，不复活）
    out3 = await reg.dispatch(
        "webgis_selection_inspect",
        {"ref_id": ref, "handle": entry.handle}, session_id=s)
    assert out3["error"] in ("selection_stale", "selection_not_found")


@pytest.mark.asyncio
async def test_selection_inspect_unknown_handle_no_leak(reg):
    s = sid()
    ref = await _seed(s, 3)
    out = await reg.dispatch(
        "webgis_selection_inspect",
        {"ref_id": ref, "handle": "totally-unknown"}, session_id=s)
    assert out["error"] == "selection_not_found"
