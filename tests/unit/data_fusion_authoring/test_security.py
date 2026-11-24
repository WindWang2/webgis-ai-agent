"""WP6 安全对抗测试：注入载荷/巨型字段/键伪造/有界披露。

纪律（不引入新攻击面，只验证域内 fail-closed）：
- 恶意名称/HTML 存储为原始值（输出转义是渲染层职责；后端绝不
  解释、不二次注入日志），预览/队列样本有界截断；
- 巨型字段值：资格卡/预览样本截断披露，不整包进上下文；
- pair_key 伪造（非内容指纹格式）→ typed invalid_plan；
- 计划 JSON 超限 / field_values 超限 / note 超限 → typed 拒绝；
- 跨会话计划/运行/决定访问 → not_owned / run_not_found（既有旅程
  测试覆盖，这里补决定键域校验与载荷边界）。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("db_tables")

from app.services.data_fusion_authoring import service
from app.services.data_fusion_authoring.errors import DataFusionError
from tests.unit.data_fusion_authoring.conftest import (
    chengdu_pois_a,
    chengdu_pois_b,
    fusion_plan,
    seed_session_source,
)


async def test_malicious_names_stored_raw_and_bounded_in_preview():
    """HTML/脚本名称原样存储（诚实保真）；预览样本有界截断。"""
    sid = "sess-fusion-sec"
    payload_a = {
        "type": "FeatureCollection", "crs": "EPSG:4326",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [104.06, 30.65]},
             "properties": {"code": "SEC1",
                            "name": "<script>alert('x')</script>小学",
                            "addr": "\"; DROP TABLE students; --",
                            "cat": "小学", "tel": "028-000"}},
        ],
    }
    payload_b = {
        "type": "FeatureCollection", "crs": "EPSG:4326",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [104.0601, 30.6501]},
             "properties": {"osm_id": "SECB1",
                            "name": "<script>alert('x')</script>小学",
                            "addr": "x", "cat": "primary_school",
                            "checked": "2026-01-01", "tel": "028-001"}},
        ],
    }
    a = await seed_session_source(sid, payload_a, prefix="seca")
    b = await seed_session_source(sid, payload_b, prefix="secb")
    actor = {"trusted_session_id": sid}
    plan = fusion_plan(a, b, sid) | {"commit_policy": "allow_unresolved"}
    view = await service.create_plan(sid, plan, actor=actor)
    run = await service.execute_plan(sid, view["plan_id"], expected_revision=1,
                                     actor=actor, background=False)
    assert run["status"] == "completed"
    # 预览样本有界截断（≤80 字符），不整包
    prev = await service.preview_plan(sid, view["plan_id"],
                                      expected_revision=1, actor=actor)
    for sample in prev["samples"]["matched"] + prev["samples"]["review"]:
        for k in ("name_a", "name_b"):
            assert len(sample[k]) <= 80
    # 成果 provenance 保真（原始值，不经后端"清洗"——渲染层负责转义）
    await service.commit_run(sid, run["run_id"], actor=actor)
    page = await service.run_result_page(sid, run["run_id"], limit=10)
    raw_values = [
        f["properties"]["_fusion"]["fields"]["name"]["values"]
        for f in page["features"]
        if f["properties"]["_fusion"]["origin"] == "merged"
    ]
    assert raw_values, "expected merged pair"
    assert any("<script>" in str(v) or "<img" in str(v)
               for vals in raw_values for v in vals.values())


async def test_pair_key_spoofing_rejected():
    """伪造 pair_key（非 fp: 内容指纹格式）→ typed invalid_plan。"""
    sid = "sess-fusion-sec2"
    a = await seed_session_source(sid, chengdu_pois_a(), prefix="s2a")
    b = await seed_session_source(sid, chengdu_pois_b(), prefix="s2b")
    actor = {"trusted_session_id": sid}
    view = await service.create_plan(sid, fusion_plan(a, b, sid), actor=actor)
    with pytest.raises(DataFusionError) as ei:
        await service.upsert_decision(
            sid, view["plan_id"], pair_key="../../etc/passwd",
            action="accept")
    assert ei.value.code == "invalid_plan"
    with pytest.raises(DataFusionError) as ei:
        await service.upsert_decision(
            sid, view["plan_id"], pair_key="idx:0:0", action="reject")
    assert ei.value.code == "invalid_plan"


async def test_oversized_payloads_typed_rejected():
    sid = "sess-fusion-sec3"
    a = await seed_session_source(sid, chengdu_pois_a(), prefix="s3a")
    b = await seed_session_source(sid, chengdu_pois_b(), prefix="s3b")
    actor = {"trusted_session_id": sid}
    # 巨型计划 JSON（>32KB）
    big_plan = fusion_plan(a, b, sid) | {"purpose": "x" * 40000}
    with pytest.raises(Exception):
        await service.create_plan(sid, big_plan, actor=actor)
    # 正常计划 + 巨型 field_values
    view = await service.create_plan(sid, fusion_plan(a, b, sid), actor=actor)
    run = await service.execute_plan(sid, view["plan_id"], expected_revision=1,
                                     actor=actor, background=False)
    queue = service.review_queue(sid, run["run_id"])
    pair_key = queue["items"][0]["pair_key"]
    with pytest.raises(DataFusionError) as ei:
        await service.upsert_decision(
            sid, view["plan_id"], pair_key=pair_key, action="accept",
            field_values={"address": "y" * 20000})
    assert ei.value.code == "invalid_plan"
    # 超长 note
    with pytest.raises(DataFusionError) as ei:
        await service.upsert_decision(
            sid, view["plan_id"], pair_key=pair_key, action="accept",
            note="n" * 600)
    assert ei.value.code == "invalid_plan"


async def test_decision_action_and_geometry_side_closed():
    sid = "sess-fusion-sec4"
    a = await seed_session_source(sid, chengdu_pois_a(), prefix="s4a")
    b = await seed_session_source(sid, chengdu_pois_b(), prefix="s4b")
    actor = {"trusted_session_id": sid}
    view = await service.create_plan(sid, fusion_plan(a, b, sid), actor=actor)
    run = await service.execute_plan(sid, view["plan_id"], expected_revision=1,
                                     actor=actor, background=False)
    queue = service.review_queue(sid, run["run_id"])
    pair_key = queue["items"][0]["pair_key"]
    with pytest.raises(DataFusionError):
        await service.upsert_decision(
            sid, view["plan_id"], pair_key=pair_key, action="merge_all")
    with pytest.raises(DataFusionError):
        await service.upsert_decision(
            sid, view["plan_id"], pair_key=pair_key, action="accept",
            geometry_side="a; DROP TABLE x")


async def test_giant_field_value_bounded_in_review_queue():
    """巨型属性值：审阅项证据/冲突标记正常，不把整值塞进队列载荷。"""
    sid = "sess-fusion-sec5"
    payload_a = chengdu_pois_a()
    payload_a["features"][0]["properties"]["addr"] = "A" * 100000
    payload_b = chengdu_pois_b()
    payload_b["features"][0]["properties"]["addr"] = "B" * 100000
    a = await seed_session_source(sid, payload_a, prefix="s5a")
    b = await seed_session_source(sid, payload_b, prefix="s5b")
    actor = {"trusted_session_id": sid}
    view = await service.create_plan(sid, fusion_plan(a, b, sid), actor=actor)
    run = await service.execute_plan(sid, view["plan_id"], expected_revision=1,
                                     actor=actor, background=False)
    assert run["status"] == "completed"
    queue = service.review_queue(sid, run["run_id"])
    # 队列项载荷有界（证据为分项数值/短标记；无 100KB 整值）
    import json

    blob = json.dumps(queue, ensure_ascii=False)
    assert len(blob) < 512 * 1024
    assert "A" * 1000 not in blob
