"""G14 旗舰旅程（V18/V02/V19 + WP11）：

手画服务区 → 升格项目图层 → Pi 统计区域内学校 → 修改区域边界 →
原统计明确过期（stale verdict）→ 按新边界重算 → MapSpec/导出绑定新数据
版本 → 会话重开后继续编辑。

全部真实链路：ingest 管线、ToolRegistry dispatch（Pi 工具通道）、ref_lifecycle
staleness 传播、mapspec 盖章、journal 持久。无核心实现打桩。
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from unittest.mock import MagicMock

from app.api.routes import feature_edits as fe_routes
from app.services.data_ingest.pipeline import get_ingest_pipeline
from app.services.session_data import session_data_manager
from app.tools.advanced_spatial import register_advanced_spatial_tools
from app.tools.feature_edit_tools import register_feature_edit_tools
from app.tools.registry import ToolRegistry

# 场景：9 所学校网格分布；服务区 v1 覆盖左 2/3 列（6 所），v2 收窄到左 1 列（3 所）
# （x 取 116.01/116.035/116.06，避免学校恰好落在边界上的谓词歧义）
SCHOOLS = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "id": f"sch-{i}",
            "geometry": {"type": "Point", "coordinates": [116.01 + 0.025 * (i % 3), 39.01 + 0.02 * (i // 3)]},
            "properties": {"name": f"学校{i}", "type": "小学"},
        }
        for i in range(9)
    ],
}
AREA_V1 = [[116.00, 39.00], [116.05, 39.00], [116.05, 39.06], [116.00, 39.06], [116.00, 39.00]]
AREA_V2 = [[116.00, 39.00], [116.03, 39.00], [116.03, 39.06], [116.00, 39.06], [116.00, 39.00]]


def sid() -> str:
    return f"sess-flag-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(fe_routes.router, prefix="/api/v1")
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def _auth(app):
    from app.services.auth_history_bridge import require_owned_session

    app.dependency_overrides[require_owned_session] = lambda: MagicMock()


async def _promote(client, s, features, name):
    resp = await client.post(
        f"/api/v1/chat/sessions/{s}/feature-edits/datasets/promote-sketch",
        json={"name": name, "features": features},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _preview(client, s, ref, ops, feature_ids=None):
    return await client.post(
        f"/api/v1/chat/sessions/{s}/feature-edits/preview",
        json={"target": {"mode": "explicit", "ref_id": ref, **({"feature_ids": feature_ids} if feature_ids else {})}, "ops": ops},
    )


async def _approve(client, s, plan_id):
    resp = await client.post(
        f"/api/v1/chat/sessions/{s}/feature-edits/plans/{plan_id}/decision",
        json={"action": "approve"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["receipt"]


async def _committable_preview(client, s, ref, ops, feature_ids=None):
    resp = await _preview(client, s, ref, ops, feature_ids)
    body = resp.json()
    assert resp.status_code == 200, resp.text
    assert body.get("committable") is True, body.get("validation")
    return body


@pytest.mark.asyncio
async def test_service_area_recompute_export_journey(app, client):
    s = sid()
    reg = ToolRegistry()
    register_feature_edit_tools(reg)
    register_advanced_spatial_tools(reg)
    from app.tools.data_discovery import register_data_discovery_tools

    register_data_discovery_tools(reg)

    # ── ① 用户手画服务区（草图）→ 升格持久项目图层 ─────────────────────
    sketch_feature = {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [AREA_V1]},
        "properties": {"kind": "sketch_polygon"},
    }
    promoted = await _promote(client, s, [sketch_feature], "服务区")
    area_ref = promoted["ref_id"]
    assert promoted["feature_count"] == 1 and promoted["origin_kind"] == "sketch"
    assert promoted["layer_id"]  # 已挂层（SSE 镜像消费面）

    # 数据集可见性：Pi 数据工具能发现（catalog 走 artifact ledger）
    listed = await reg.dispatch("list_datasets", {}, session_id=s)
    assert listed["success"], listed
    refs = [d.get("id") for d in listed["datasets"]]
    assert any(area_ref == r for r in refs), refs

    # ── ② Pi 统计服务区内学校（真实分析工具 + ref 通道） ────────────────
    schools_result = await get_ingest_pipeline().ingest(s, SCHOOLS, name="schools")
    assert schools_result.ok
    schools_ref = schools_result.ref_id

    stats1 = await reg.dispatch(
        "spatial_aggregate",
        {"points": schools_ref, "polygons": area_ref, "count_field": "school_count"},
        session_id=s,
    )
    assert stats1.get("success") is True, stats1
    fc1 = stats1.get("data") or stats1.get("geojson")
    assert fc1, stats1
    assert fc1["features"][0]["properties"]["school_count"] == 6  # v1 覆盖左 2 列 × 3 行
    # 结果落 ref + 血缘登记（与生产 dispatch 缝同款调用：result_size_policy
    # ref_offload 的 ref 化 + register_tool_artifact 的 inputs/revision 快照）
    from app.services.artifact_registry import register_tool_artifact

    stats_ref1 = await session_data_manager.store(
        s, {"geojson": fc1}, prefix="analysis"
    )
    area_rev = int((await session_data_manager.get_ref_descriptor(s, area_ref))["content_revision"])
    schools_rev = int((await session_data_manager.get_ref_descriptor(s, schools_ref))["content_revision"])
    registered = await register_tool_artifact(
        s, stats_ref1,
        tool="spatial_aggregate",
        ref_revisions={area_ref: area_rev, schools_ref: schools_rev},
        inputs=[area_ref, schools_ref],
    )
    assert registered is not None

    # ── ③ 修改区域边界（用户手动编辑 = owner REST 事务） ────────────────
    payload = await session_data_manager.get(s, area_ref)
    area_wfid = payload["geojson"]["features"][0]["properties"]["_wfid"]
    desc_before = await session_data_manager.get_ref_descriptor(s, area_ref)
    rev_before = desc_before["content_revision"]

    body3 = await _committable_preview(client, s, area_ref, [
        {"op": "replace_geometry", "geometry": {"type": "Polygon", "coordinates": [AREA_V2]}}
    ], feature_ids=[area_wfid])
    receipt = await _approve(client, s, body3["plan_id"])
    assert receipt["content_revision_after"] == rev_before + 1

    # 数据事实：新边界生效
    payload2 = await session_data_manager.get(s, area_ref)
    assert payload2["geojson"]["features"][0]["geometry"]["coordinates"][0] == AREA_V2

    # ── ④ 原统计明确过期（staleness 传播 + verdict 披露） ───────────────
    from app.services.artifact_registry import get_artifact

    stale_verdict = None
    for _ in range(30):  # staleness 传播经 overwrite 钩子异步调度，有界等待
        rec = await get_artifact(s, stats_ref1)
        if rec is not None:
            md = rec.metadata if isinstance(rec.metadata, dict) else {}
            if md.get("stale_verdict"):
                stale_verdict = md.get("stale_verdict")
                assert md.get("stale_reason") == "ref_overwritten"
                break
        import asyncio

        await asyncio.sleep(0.1)
    assert stale_verdict, "依赖该数据集的分析产物未按数据版本失效"

    # 复用面：产物已 stale，后续同分析必须重算（reuse 快照按 content_revision
    # 拒绝旧结果 —— stale verdict 即第一证据，上面的断言已覆盖）

    # ── ⑤ 按新边界重算 → 新结果绑定新版本 ──────────────────────────────
    stats2 = await reg.dispatch(
        "spatial_aggregate",
        {"points": schools_ref, "polygons": area_ref, "count_field": "school_count"},
        session_id=s,
    )
    assert stats2.get("success") is True
    fc2 = stats2.get("data") or stats2.get("geojson")
    assert fc2["features"][0]["properties"]["school_count"] == 3  # v2 仅左 1 列

    # ── ⑥ MapSpec/导出绑定新数据版本 ───────────────────────────────────
    from app.services.mapspec_store import mapspec_store

    spec = await mapspec_store.get_mapspec(s)
    area_sources = [src for src in spec["sources"].values() if src.get("ref_id") == area_ref]
    assert area_sources, "升格数据集未进 MapSpec 源"
    assert area_sources[0]["content_revision"] == receipt["content_revision_after"]
    # 导出消费面按 content_revision 取数：新版本载荷含新边界
    exported = await session_data_manager.get(s, area_ref)
    assert exported["geojson"]["features"][0]["geometry"]["coordinates"][0] == AREA_V2
    assert (await session_data_manager.get_ref_descriptor(s, area_ref))["content_revision"] == area_sources[0]["content_revision"]

    # ── ⑦ 会话重开后继续编辑（journal/身份持久；V19） ────────────────────
    # 模拟重开：全新 HTTP 客户端（无任何本地缓存）走只读接口
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as fresh:
        hist = await fresh.get(f"/api/v1/chat/sessions/{s}/feature-edits/{area_ref}/history")
        body = hist.json()
        assert body["head_seq"] == 1 and body["entries"][0]["kind"] == "apply"
        # 继续编辑：再改一次边界（回到 v1）
        body7 = await _committable_preview(fresh, s, area_ref, [
            {"op": "replace_geometry", "geometry": {"type": "Polygon", "coordinates": [AREA_V1]}}
        ], feature_ids=[area_wfid])
        r3 = await _approve(fresh, s, body7["plan_id"])
        assert r3["content_revision_after"] == receipt["content_revision_after"] + 1
        payload3 = await session_data_manager.get(s, area_ref)
        # 身份未漂移：同一要素稳定 ID
        assert payload3["geojson"]["features"][0]["properties"]["_wfid"] == area_wfid


@pytest.mark.asyncio
async def test_pi_proposal_vs_manual_edit_interleave(client):
    """V16：Pi 预览与用户手动编辑交错 —— owner/确认与 CAS 纪律。"""
    s = sid()
    reg = ToolRegistry()
    register_feature_edit_tools(reg)

    sketch = [{
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [AREA_V1]},
        "properties": {"name": "Z"},
    }]
    promoted = await _promote(client, s, sketch, "区域")
    ref = promoted["ref_id"]
    payload = await session_data_manager.get(s, ref)
    wfid = payload["geojson"]["features"][0]["properties"]["_wfid"]

    # Pi 预览一个几何变更（replace_geometry）
    preview = await reg.dispatch(
        "vector_feature_edit_preview",
        {"ref_id": ref, "feature_ids": [wfid],
         "ops": [{"op": "replace_geometry", "geometry": {"type": "Polygon", "coordinates": [AREA_V2]}}]},
        session_id=s,
    )
    assert preview["success"] is True, preview
    pi_plan_id = preview["plan_id"]

    # Pi 直接提交 → 几何变更恒需确认（即使 policy allow_safe_attribute）
    await reg.dispatch("vector_feature_edit_commit", {"plan_id": pi_plan_id}, session_id=s)
    from app.services.feature_edit import policy as fe_policy

    await fe_policy.set_policy(s, "allow_safe_attribute")
    blocked = await reg.dispatch("vector_feature_edit_commit", {"plan_id": pi_plan_id}, session_id=s)
    assert blocked["success"] is False and blocked["code"] == "CONFIRMATION_REQUIRED"
    await fe_policy.set_policy(s, "confirm_all")

    # 用户先手动提交自己的编辑（owner REST；同指纹）
    manual = await _committable_preview(client, s, ref, [
        {"op": "replace_geometry", "geometry": {"type": "Polygon", "coordinates": [[
            [116.00, 39.00], [116.06, 39.00], [116.06, 39.06], [116.00, 39.06], [116.00, 39.00]
        ]]}}
    ], feature_ids=[wfid])
    await _approve(client, s, manual["plan_id"])

    # Pi 旧预览此时提交（用户在提案卡 approve 该旧 plan）→ stale 拒绝，用户数据不被覆盖
    resp = await client.post(
        f"/api/v1/chat/sessions/{s}/feature-edits/plans/{pi_plan_id}/decision",
        json={"action": "approve"},
    )
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "stale_revision"
    payload_after = await session_data_manager.get(s, ref)
    # 用户手动边界仍在（116.06），未被旧 Pi 预览静默覆盖
    assert payload_after["geojson"]["features"][0]["geometry"]["coordinates"][0][1][0] == 116.06
