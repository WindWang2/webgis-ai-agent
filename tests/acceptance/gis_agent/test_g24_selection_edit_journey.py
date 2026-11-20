"""G24 验收旅程：选中（权威 handle）→ 核实 → 编辑预览/授权 → 新 revision。

旅程语义（A25/A26/A19/A22 的服务端侧链路）：
- Turn 1 用户框选大图层（服务端句柄；前端 IR 补发 selection_handle 的
  客户端半边在 frontend/lib/selection/brush-handle.test.ts 钉住）；
- Turn 2 Agent 用 webgis_selection_inspect 核实全集（count/completeness/
  revision；不偷传 fixture 内部 id —— 工具只接受服务端 handle）；
- Turn 3 对选中对象发起真实 feature_edit（preview→approve 全链），编辑
  成功后 data revision 前进；
- Turn 4 旧 handle 消费 → typed stale（数据刷新后不可静默复用，A22）；
  split/merge 血缘 wfids 进入回执（A19 服务端半边）。

（浏览器端到端在 e2e/ 前端层另有覆盖；本层锁定服务契约与授权语义。）
"""
from __future__ import annotations

import uuid

import pytest

from app.services.feature_edit import executor as fe_executor
from app.services.feature_edit.service import build_and_store_plan
from app.services.selection_handles import create_selection
from app.services.session_data import session_data_manager
from app.tools.registry import ToolRegistry
from app.tools.selection_tools import register_selection_tools


def _sid(prefix: str) -> str:
    return f"g24j-{prefix}-{uuid.uuid4().hex[:8]}"


def _campus_fc(n: int = 8) -> dict:
    return {"geojson": {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": f"b-{i}",
         "geometry": {"type": "Polygon", "coordinates": [[
             [104.0 + i * 0.02, 30.0], [104.015 + i * 0.02, 30.0],
             [104.015 + i * 0.02, 30.02], [104.0 + i * 0.02, 30.02],
             [104.0 + i * 0.02, 30.0]]]},
         "properties": {"name": f"block-{i}", "floor": 3 + (i % 5)}}
        for i in range(1, n + 1)
    ]}}


@pytest.fixture
def registry():
    reg = ToolRegistry()
    register_selection_tools(reg)
    return reg


@pytest.mark.asyncio
async def test_journey_select_inspect_edit_stale(registry):
    sid = _sid("main")
    payload = _campus_fc()
    ref = await session_data_manager.store(sid, payload, prefix="data")
    desc = await session_data_manager.get_ref_descriptor(sid, ref)
    revision0 = desc["content_revision"]
    features = payload["geojson"]["features"]

    # ── Turn 1：用户框选（服务端句柄 = 权威全集）──
    entry = await create_selection(
        sid, ref, revision0,
        {"kind": "where", "where": {"field": "floor", "op": "gte", "value": 5}},
        features)
    assert entry.count_exact and entry.count == 5  # floor≥5：i=2,3,4,7,8

    # ── Turn 2：Agent 核实（只认 handle，不透传内部 id）──
    out = await registry.dispatch(
        "webgis_selection_inspect",
        {"ref_id": ref, "handle": entry.handle, "page_size": 2},
        session_id=sid)
    assert out["count"] == 5 and out["completeness"] == "exact"
    assert out["ids_page"] == ["b-2", "b-3"]
    # 续页枚举全集（不是前 N）
    ids, cursor = list(out["ids_page"]), out["page"]["next_cursor"]
    while cursor:
        page2 = await registry.dispatch(
            "webgis_selection_inspect",
            {"ref_id": ref, "handle": entry.handle, "page_size": 2, "cursor": cursor},
            session_id=sid)
        ids.extend(page2["ids_page"])
        cursor = page2["page"]["next_cursor"]
    assert ids == ["b-2", "b-3", "b-4", "b-7", "b-8"]

    # 拾取核实单个对象（refs-only：属性可见，几何只有摘要）
    detail = await registry.dispatch(
        "webgis_pick_detail", {"ref_id": ref, "feature_id": "b-3"}, session_id=sid)
    assert detail["found"] and detail["properties"]["floor"] == 6
    assert "coordinates" not in detail["geometry_summary"]

    # ── Turn 3：对选中对象真实编辑（preview → owner approve）──
    plan, findings = await build_and_store_plan(
        sid, ref_id=ref,
        feature_ids=["b-2", "b-3", "b-4"],
        ops=[{"op": "set_fields", "fields": {"floor": 9}}],
    )
    assert findings == [], findings
    plan_dict = await fe_executor.get_plan(sid, plan["plan_id"])
    receipt = await fe_executor.commit_plan(sid, plan_dict, actor="user", origin="rest")
    revision1 = receipt["content_revision_after"]
    assert revision1 > revision0
    desc1 = await session_data_manager.get_ref_descriptor(sid, ref)
    assert desc1["content_revision"] == revision1

    # ── Turn 4：旧 handle 失效（A22：数据刷新不可静默复用）──
    stale = await registry.dispatch(
        "webgis_selection_inspect",
        {"ref_id": ref, "handle": entry.handle},
        session_id=sid)
    assert stale["error"] == "selection_stale"
    assert stale["current_revision"] == revision1
    assert stale["handle_revision"] == revision0

    # 编辑后的数据仍可按权威链寻址（_wfid 物化后链内一致）
    detail2 = await registry.dispatch(
        "webgis_pick_detail", {"ref_id": ref, "feature_id": "b-3"}, session_id=sid)
    assert detail2["found"] and detail2["properties"]["floor"] == 9


@pytest.mark.asyncio
async def test_journey_split_lineage_receipt(registry):
    """A19（服务端半边）：split 产生新 wfid，回执血缘可查（不按位置猜）。"""
    sid = _sid("split")
    SQUARE = [[104.0, 30.0], [104.2, 30.0], [104.2, 30.2], [104.0, 30.2], [104.0, 30.0]]
    # 空数据集 → create（首个编辑 = 物化 + 服务端分稳定 id）→ split 该 id
    from app.services.feature_edit.datasets import create_empty_dataset

    created_ds = await create_empty_dataset(sid, name="split-target", geometry_type="Polygon")
    assert created_ds.get("ok") is True, created_ds
    ref = created_ds["ref_id"]
    plan0, findings0 = await build_and_store_plan(
        sid, ref_id=ref, feature_ids=None,
        ops=[{"op": "create_features", "features": [
            {"geometry": {"type": "Polygon", "coordinates": [SQUARE]},
             "properties": {"name": "big"}}]}],
    )
    assert findings0 == [], findings0
    plan_dict0 = await fe_executor.get_plan(sid, plan0["plan_id"])
    receipt0 = await fe_executor.commit_plan(sid, plan_dict0, actor="user", origin="rest")
    target_wfid = receipt0["created_wfids"][0]
    plan, findings = await build_and_store_plan(
        sid, ref_id=ref, feature_ids=[target_wfid],
        ops=[{"op": "split_feature", "wfid": target_wfid, "line": {
            "type": "LineString", "coordinates": [[104.1, 29.9], [104.1, 30.3]]}}],
    )
    assert findings == [], findings
    plan_dict2 = await fe_executor.get_plan(sid, plan["plan_id"])
    receipt2 = await fe_executor.commit_plan(sid, plan_dict2, actor="user", origin="rest")
    created = receipt2.get("created_wfids") or []
    retired = receipt2.get("retired_wfids") or []
    assert len(created) == 2 and retired  # split：2 子嗣 + 父退役（血缘事实）
    # 新对象可经权威链寻址（前端 selection lineage 消费的输入）
    for child in created:
        detail = await registry.dispatch(
            "webgis_pick_detail", {"ref_id": ref, "feature_id": child}, session_id=sid)
        assert detail["found"], child
    journals = await fe_executor._load_journals(sid)
    entries = (journals.get(ref) or {}).get("entries") or []
    assert any(e.get("lineage") for e in entries)
