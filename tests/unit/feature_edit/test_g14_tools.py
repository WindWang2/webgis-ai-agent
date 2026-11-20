"""G14 Pi 工具面测试：provision / lineage / 结构性 preview-commit 信任边界。"""
from __future__ import annotations

import uuid

import pytest

from app.services.session_data import session_data_manager
from app.tools.feature_edit_tools import register_feature_edit_tools
from app.tools.registry import ToolRegistry

AREA = [[116.00, 39.00], [116.10, 39.00], [116.10, 39.10], [116.00, 39.10], [116.00, 39.00]]
SPLIT_LINE = {"type": "LineString", "coordinates": [[116.05, 38.9], [116.05, 39.2]]}


def sid() -> str:
    return f"sess-t14-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def reg():
    reg = ToolRegistry()
    register_feature_edit_tools(reg)
    return reg


@pytest.mark.asyncio
async def test_provision_empty_dataset_via_tool(reg):
    s = sid()
    out = await reg.dispatch(
        "vector_dataset_provision",
        {"name": "新区学校", "geometry_type": "Point", "topology_rules": ["no_duplicate_points"]},
        session_id=s,
    )
    assert out["success"] is True, out
    ref = out["ref_id"]
    # 真实登记：descriptor + catalog 可见 + 拓扑声明在 envelope
    desc = await session_data_manager.get_ref_descriptor(s, ref)
    assert desc["feature_count"] == 0
    payload = await session_data_manager.get(s, ref)
    assert payload["geometry_type"] == "Point"
    assert payload["topology"]["rules"] == ["no_duplicate_points"]


@pytest.mark.asyncio
async def test_provision_promote_features_via_tool(reg):
    s = sid()
    out = await reg.dispatch(
        "vector_dataset_provision",
        {"name": "手画服务区", "features": [
            {"geometry": {"type": "Polygon", "coordinates": [AREA]}, "properties": {"name": "S"}}
        ]},
        session_id=s,
    )
    assert out["success"] is True, out
    assert out["feature_count"] == 1
    payload = await session_data_manager.get(s, out["ref_id"])
    # 升格即物化稳定身份（首个编辑可寻址）
    assert "_wfid" in payload["geojson"]["features"][0]["properties"]


@pytest.mark.asyncio
async def test_provision_rejects_bad_args(reg):
    s = sid()
    bad = await reg.dispatch(
        "vector_dataset_provision", {"name": "X", "geometry_type": "Hexagon"}, session_id=s
    )
    assert bad["success"] is False and bad["code"] == "INVALID_GEOMETRY_TYPE"
    none = await reg.dispatch("vector_dataset_provision", {"name": "X"}, session_id=s)
    assert none["success"] is False and none["code"] == "INVALID_ARGS"


@pytest.mark.asyncio
async def test_structural_preview_needs_confirmation(reg):
    """Pi 结构性提交恒 CONFIRMATION_REQUIRED（即使 allow_safe_attribute）。"""
    from app.services.feature_edit import policy as fe_policy

    s = sid()
    created = await reg.dispatch(
        "vector_dataset_provision", {"name": "区", "geometry_type": "Polygon"}, session_id=s
    )
    ref = created["ref_id"]
    preview = await reg.dispatch(
        "vector_feature_edit_preview",
        {"ref_id": ref, "ops": [{"op": "create_features", "features": [
            {"geometry": {"type": "Polygon", "coordinates": [AREA]}}
        ]}]},
        session_id=s,
    )
    assert preview["success"] is True and preview["safe"] is False
    plan_id = preview["plan_id"]

    await fe_policy.set_policy(s, "allow_safe_attribute")
    commit = await reg.dispatch("vector_feature_edit_commit", {"plan_id": plan_id}, session_id=s)
    assert commit["success"] is False and commit["code"] == "CONFIRMATION_REQUIRED"
    await fe_policy.set_policy(s, "confirm_all")


@pytest.mark.asyncio
async def test_lineage_tool_projection(reg):
    s = sid()
    created = await reg.dispatch(
        "vector_dataset_provision",
        {"name": "区", "features": [{"geometry": {"type": "Polygon", "coordinates": [AREA]}}]},
        session_id=s,
    )
    ref = created["ref_id"]
    payload = await session_data_manager.get(s, ref)
    wfid = payload["geojson"]["features"][0]["properties"]["_wfid"]

    from app.services.feature_edit import service as fe_service
    from app.services.feature_edit import executor as fe_executor

    plan, findings = await fe_service.build_and_store_plan(
        s, ref_id=ref, feature_ids=[wfid],
        ops=[{"op": "split_feature", "wfid": wfid, "line": SPLIT_LINE}],
    )
    assert findings == [], findings
    await fe_executor.commit_plan(s, plan, actor="user", origin="user")

    out = await reg.dispatch("vector_feature_lineage", {"ref_id": ref}, session_id=s)
    assert out["success"] is True
    assert len(out["events"]) == 1 and out["events"][0]["kind"] == "split"
    assert out["events"][0]["parent_wfids"] == [wfid]
    # 按子身份过滤
    child = out["events"][0]["child_wfids"][0]
    filtered = await reg.dispatch("vector_feature_lineage", {"ref_id": ref, "wfid": child}, session_id=s)
    assert len(filtered["events"]) == 1
    miss = await reg.dispatch("vector_feature_lineage", {"ref_id": ref, "wfid": "wf1-none"}, session_id=s)
    assert miss["events"] == []


@pytest.mark.asyncio
async def test_cross_session_isolation(reg):
    """V13：跨会话 ref/plan 统一拒绝，无存在性泄露。"""
    s1, s2 = sid(), sid()
    created = await reg.dispatch(
        "vector_dataset_provision",
        {"name": "区", "features": [{"geometry": {"type": "Polygon", "coordinates": [AREA]}}]},
        session_id=s1,
    )
    ref = created["ref_id"]
    other = await reg.dispatch("vector_feature_edit_preview",
                               {"ref_id": ref, "ops": [{"op": "delete_features"}]}, session_id=s2)
    assert other["success"] is False and other["code"] == "DATASET_NOT_FOUND"
    lineage = await reg.dispatch("vector_feature_lineage", {"ref_id": ref}, session_id=s2)
    assert lineage["success"] is False and lineage["code"] == "DATASET_NOT_FOUND"
