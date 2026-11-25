"""冻结计划测试 —— plan 层面 pin（可序列化 JSON）；无渲染；泄漏纪律。"""
from __future__ import annotations

import json

import pytest

from app.services.map_series.freeze import FREEZE_PLAN_VERSION, build_freeze_plan
from app.services.map_series.store import create_series
from tests.unit.map_series.helpers import series_payload


@pytest.fixture()
def host_spec(db):
    from app.services.design_specs.versioning import create_spec as create_design_spec

    spec = create_design_spec(
        db,
        {"spec_version": "design_spec.v1", "name": "冻结宿主规范"},
        creator_id="series-owner",
    )
    return spec.id


@pytest.mark.asyncio
async def test_freeze_plan_materializes_members(db, session_factory, host_spec):
    await session_factory("sess-frz-a", bounds=[103.0, 30.0, 104.0, 31.0])
    payload = series_payload(
        name="冻结系列",
        design_spec_id=host_spec,
        members=[
            {"member_id": "m1", "title": "主图", "session_id": "sess-frz-a"},
            {"member_id": "m2", "title": "缺席图", "session_id": "sess-frz-missing"},
        ],
        dimension={"axis": "region", "labels": ["区县"]},
        output={"targets": ["png", "pdf"], "dpi": 300},
    )
    series = create_series(db, payload, creator_id="series-owner")
    plan = await build_freeze_plan(db, series.id)

    assert plan["plan_version"] == FREEZE_PLAN_VERSION
    assert plan["series_id"] == series.id
    assert plan["spec_pin"]["design_spec_id"] == host_spec
    assert plan["spec_pin"]["version"] >= 1
    assert plan["unresolvable_member_ids"] == ["m2"]  # 不可解析成员如实单列
    assert len(plan["members"]) == 1
    entry = plan["members"][0]
    assert entry["member_id"] == "m1"
    assert entry["session_id"] == "sess-frz-a"
    assert entry["mapspec_fingerprint"]  # 成员身份物化
    assert entry["data_fingerprints"]["districts"]["kind"] in (
        "inline",
        "fingerprint",
        "unfingered",
    )
    assert entry["design_spec_id"] == host_spec
    assert entry["output_profile"]["dpi"] == 300
    # 整个计划可序列化 JSON（计划层面 pin；无渲染字节）
    assert json.dumps(plan, ensure_ascii=False)
    assert "members" in plan and plan.get("note", "").startswith("plan-level pin")


@pytest.mark.asyncio
async def test_freeze_plan_never_embeds_ref_cursors(db, session_factory, host_spec):
    """ref 绑定只记描述指纹（sha256 短摘要），游标本身绝不进冻结计划。"""
    await session_factory("sess-frz-b")
    # 经真实通道给 source 记一条 ref（模拟延迟绑定源）
    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.intents import UpsertSourceIntent

    res = await apply_gis_mutation(
        "sess-frz-b",
        UpsertSourceIntent(
            source_id="bound",
            source={"type": "geojson", "ref": "ref:dataset/some-cursor-9"},
        ),
        origin="agent", actor="tester",
    )
    assert not res.is_error, res.error_msg

    payload = series_payload(
        name="ref冻结系列",
        design_spec_id=host_spec,
        members=[{"member_id": "m1", "title": "引用图", "session_id": "sess-frz-b"}],
    )
    series = create_series(db, payload, creator_id="series-owner")
    plan = await build_freeze_plan(db, series.id)
    text = json.dumps(plan, ensure_ascii=False)
    assert "ref:dataset/some-cursor-9" not in text  # 游标不进计划
    entry = plan["members"][0]
    assert entry["data_fingerprints"]["bound"]["kind"] == "ref"
    assert entry["data_fingerprints"]["bound"]["ref_digest"].startswith("sha256:")
