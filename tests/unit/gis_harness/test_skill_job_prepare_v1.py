"""G30 — skill job prepare 单元测试（typed 拒绝矩阵 + 幂等 + provenance）。

场景映射（任务验收矩阵）：#5（单位/字段不存在 prepare 期拒绝）、#6（指南
vs 作业不误跑）、#7（弃用版本显示替代）、#8（digest 漂移检测——start 期
覆盖于 runner 测试）、#3（度量语义记录）、#4（率缺分母拒绝）。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.skill_jobs.contracts import SkillJobErrorCode
from app.services.gis_harness.skill_jobs.prepare import (
    extract_binding_facts,
    prepare_skill_job,
)


def _points(n: int = 12):
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [104.0 + i * 0.01, 30.6 + i * 0.005],
                },
                "properties": {"name": f"p{i}", "score": i * 1.5},
            }
            for i in range(n)
        ],
    }


def _polys(n: int = 6):
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [104.0 + i * 0.1, 30.6], [104.1 + i * 0.1, 30.6],
                        [104.1 + i * 0.1, 30.7], [104.0 + i * 0.1, 30.7],
                        [104.0 + i * 0.1, 30.6],
                    ]],
                },
                "properties": {
                    "district": f"d{i}", "pop": 1000 + i * 200, "area_km2": 50.0,
                },
            }
            for i in range(n)
        ],
    }


@pytest.fixture()
def registry():
    """生产注册表（bridge 注入语义）。"""
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    from app.agent_pi_bridge import set_tool_registry

    reg = ToolRegistry()
    init_tools(reg)
    set_tool_registry(reg)
    yield reg


@pytest.mark.asyncio
async def test_binding_facts_extraction():
    facts, err = extract_binding_facts(_points())
    assert err is None
    assert facts.geometry_kind == "point"
    assert facts.feature_count == 12
    assert "score" in facts.fields
    assert facts.field_types["score"] == "numeric"
    assert facts.field_types["name"] == "categorical"
    assert facts.content_digest


@pytest.mark.asyncio
async def test_binding_facts_rejects_non_geojson():
    facts, err = extract_binding_facts({"foo": 1})
    assert err == "DATA_NOT_GEOJSON"
    assert facts.feature_count == 0


@pytest.mark.asyncio
async def test_prepare_validate_report_journey(registry):
    from app.services.session_data import session_data_manager

    ref = await session_data_manager.store("t1", _points(), prefix="geojson")
    result = await prepare_skill_job(
        session_id="t1", skill_id="validate_spatial_data",
        params={}, bindings=[{"role": "subject", "ref": ref}])
    p = result.prepared
    assert p.skill_id == "validate_spatial_data"
    assert p.skill_version == "1.1.0"
    assert p.skill_digest
    # 一次审计覆盖三类证据（dedup 语义见 runner 测试）
    assert [s.tool for s in p.steps] == ["audit_spatial_quality"]
    assert p.idempotency_key.startswith("skj-")
    preview = p.preview
    assert preview["evidence_covered"] == [
        "dataset_profile_report", "geometry_check_report", "quality_verdict"]


@pytest.mark.asyncio
async def test_prepare_point_distribution_map_journey(registry):
    from app.services.session_data import session_data_manager

    ref = await session_data_manager.store("t2", _points(), prefix="geojson")
    result = await prepare_skill_job(
        session_id="t2", skill_id="point_distribution_analysis",
        params={"layer_title": "学校分布"},
        bindings=[{"role": "subject", "ref": ref}])
    p = result.prepared
    tools = [s.tool for s in p.steps]
    assert tools == ["audit_spatial_quality", "webgis_layer_upsert"]
    # express 步骤的 layer 映射：source/id 字面量 + label 参数化
    express = p.steps[1]
    assert express.args["layer"]["id"] == "skill-point-distribution-analysis"
    assert express.args["layer"]["label"] == "学校分布"
    assert express.args["source_data"] == {"$role": "subject"}
    # 参数 provenance：显式=user
    assert {pp.name: pp.source for pp in p.params}["layer_title"] == "user"


@pytest.mark.asyncio
async def test_prepare_choropleth_semantics_gates(registry):
    from app.services.session_data import session_data_manager

    pref = await session_data_manager.store("t3", _polys(), prefix="geojson")
    # count 上色：OK
    r = await prepare_skill_job(
        session_id="t3", skill_id="choropleth_map_design",
        params={"field": "pop", "measure_semantics": "count"},
        bindings=[{"role": "subject", "ref": pref}])
    assert r.prepared.steps[0].tool == "create_thematic_map"
    # rate 缺分母：typed 拒绝（场景 #4）
    with pytest.raises(SkillJobErrorCode) as ei:
        await prepare_skill_job(
            session_id="t3", skill_id="choropleth_map_design",
            params={"field": "pop", "measure_semantics": "rate"},
            bindings=[{"role": "subject", "ref": pref}])
    assert ei.value.code == "PARAM_CONFLICT"
    # rate 带分母：OK
    r2 = await prepare_skill_job(
        session_id="t3", skill_id="choropleth_map_design",
        params={"field": "pop", "measure_semantics": "rate",
                "denominator_field": "area_km2"},
        bindings=[{"role": "subject", "ref": pref}])
    assert any(pp.name == "denominator_field" and pp.source == "user"
               for pp in r2.prepared.params)


@pytest.mark.asyncio
async def test_prepare_typed_rejections(registry):
    from app.services.session_data import session_data_manager

    ref = await session_data_manager.store("t4", _points(), prefix="geojson")
    pref = await session_data_manager.store("t4", _polys(), prefix="geojson")

    async def expect(code, **kwargs):
        with pytest.raises(SkillJobErrorCode) as ei:
            await prepare_skill_job(**kwargs)
        assert ei.value.code == code, f"{ei.value.code} != {code}"

    # 场景 #5：字段不存在 → prepare 期定位（不调用算法）
    await expect("FIELD_NOT_FOUND",
                 session_id="t4", skill_id="choropleth_map_design",
                 params={"field": "nope", "measure_semantics": "count"},
                 bindings=[{"role": "subject", "ref": pref}])
    # 字段类型不符（categorical 上 expects=numeric）
    await expect("FIELD_TYPE_MISMATCH",
                 session_id="t4", skill_id="choropleth_map_design",
                 params={"field": "district", "measure_semantics": "count"},
                 bindings=[{"role": "subject", "ref": pref}])
    # 必填参数缺失
    await expect("PARAM_MISSING",
                 session_id="t4", skill_id="point_distribution_analysis",
                 params={}, bindings=[{"role": "subject", "ref": ref}])
    # 未声明参数不透传
    await expect("PARAM_UNKNOWN",
                 session_id="t4", skill_id="point_distribution_analysis",
                 params={"layer_title": "x", "evil": "rm -rf"},
                 bindings=[{"role": "subject", "ref": ref}])
    # 枚举外取值
    await expect("PARAM_ENUM_INVALID",
                 session_id="t4", skill_id="choropleth_map_design",
                 params={"field": "pop", "measure_semantics": "vibes"},
                 bindings=[{"role": "subject", "ref": pref}])
    # 几何不符（点数据上跑面技能）
    await expect("GEOMETRY_MISMATCH",
                 session_id="t4", skill_id="choropleth_map_design",
                 params={"field": "score", "measure_semantics": "count"},
                 bindings=[{"role": "subject", "ref": ref}])
    # 场景 #6：仅指导技能（无执行映射）typed 拒绝
    await expect("SKILL_GUIDANCE_ONLY",
                 session_id="t4", skill_id="density_map_design",
                 params={}, bindings=[])
    # 场景 #7：版本不匹配（显式选择旧/不存在版本）→ 显示当前版本不回滚
    await expect("SKILL_VERSION_MISMATCH",
                 session_id="t4", skill_id="validate_spatial_data",
                 skill_version="0.9.0",
                 params={}, bindings=[{"role": "subject", "ref": ref}])
    # ref 过期/不存在
    await expect("REF_NOT_FOUND",
                 session_id="t4", skill_id="validate_spatial_data",
                 params={}, bindings=[{"role": "subject", "ref": "ref:geojson-nonexistent"}])
    # 要素数不足（point_distribution min_features=8）
    tiny = await session_data_manager.store(
        "t4", _points(5), prefix="geojson")
    await expect("MIN_FEATURES_NOT_MET",
                 session_id="t4", skill_id="point_distribution_analysis",
                 params={"layer_title": "x"},
                 bindings=[{"role": "subject", "ref": tiny}])
    # 技能不存在
    await expect("SKILL_NOT_FOUND",
                 session_id="t4", skill_id="no_such_skill",
                 params={}, bindings=[])
    # 绑定角色越权（契约外角色）
    await expect("BINDING_UNKNOWN_ROLE",
                 session_id="t4", skill_id="validate_spatial_data",
                 params={}, bindings=[{"role": "boundary", "ref": pref}])


@pytest.mark.asyncio
async def test_prepare_idempotency_and_provenance(registry):
    from app.services.session_data import session_data_manager

    ref = await session_data_manager.store("t5", _points(), prefix="geojson")
    args = dict(session_id="t5", skill_id="point_distribution_analysis",
                params={"layer_title": "T"}, bindings=[{"role": "subject", "ref": ref}])
    r1 = await prepare_skill_job(**args)
    r2 = await prepare_skill_job(**args)
    # 同输入同幂等键（确定性）
    assert r1.prepared.idempotency_key == r2.prepared.idempotency_key
    # 不同 owner → 不同键（归属隔离）
    r3 = await prepare_skill_job(**{**args, "owner_fingerprint": "user-b"})
    assert r3.prepared.idempotency_key != r1.prepared.idempotency_key
    # 数据变化 → 不同键（版本竞争防线）
    ref2 = await session_data_manager.store("t5", _points(13), prefix="geojson")
    r4 = await prepare_skill_job(**{**args, "bindings": [{"role": "subject", "ref": ref2}]})
    assert r4.prepared.idempotency_key != r1.prepared.idempotency_key
    # 缺省参数来源 = skill_default
    r6 = await prepare_skill_job(
        session_id="t5", skill_id="point_distribution_analysis",
        params={"layer_title": "T"},
        bindings=[{"role": "subject", "ref": ref}])
    sources = {pp.name: pp.source for pp in r6.prepared.params}
    assert sources["expression"] == "skill_default"
    assert sources["layer_title"] == "user"
    # 未提供且无缺省的可选参数（method）不进 prepared params
    assert "method" not in {pp.name for pp in r6.prepared.params}
