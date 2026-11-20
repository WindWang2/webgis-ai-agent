"""G15 frame_products 服务旅程测试 —— bind/plan/update/unbind/status。

锚点（任务书 §5 场景）：
- 成都三年份同一比率指标并排：三个稳定 frame_id、共同 breaks、年份来自真实数据；
- 修改某画幅标题后只该画幅变化（patch 键级）；
- 一帧缺数据：partial + 披露，不借用相邻帧；
- 单期数据诚实拒绝；悬空层拒绝；共享资格拒绝回落独立图例（披露不伪共享）。
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, Dict

import pytest

from app.services.gis_harness.frame_products import service as fps
from app.services.mapspec.lifecycle_engine import MapSpecLifecycleEngine


def _districts_fc(year_value: Dict[int, Dict[str, float]]) -> Dict[str, Any]:
    """区县面 × 年 × 指标（rate 语义：每万人小学学位）。"""
    feats = []
    for y, per in year_value.items():
        for d, v in per.items():
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[
                    [104.0, 30.6], [104.1, 30.6], [104.1, 30.7], [104.0, 30.7],
                    [104.0, 30.6]]]},
                "properties": {"year": y, "district": d, "per_10k": v},
            })
    return {"type": "FeatureCollection", "features": feats}


THREE_YEARS = _districts_fc({
    2020: {"锦江": 21.0, "武侯": 35.0, "高新": 48.0},
    2022: {"锦江": 25.0, "武侯": 33.0, "高新": 52.0},
    2024: {"锦江": 31.0, "武侯": 30.0},  # 高新 2024 缺数据（partial 锚点）
})
SINGLE_YEAR = _districts_fc({2024: {"锦江": 30.0, "武侯": 29.0}})


@pytest.fixture
async def ref_store(monkeypatch, tmp_path):
    fcs: Dict[str, Dict[str, Any]] = {
        "ref:three": THREE_YEARS,
        "ref:single": SINGLE_YEAR,
        "ref:unreadable": None,
    }

    async def _resolve_alias(session_id, ref_or_alias):
        return ref_or_alias

    async def _get_ref_descriptor(session_id, resolved):
        if resolved not in fcs:
            return None
        return {"content_revision": 7,
                "feature_count": len(fcs[resolved]["features"])}

    async def _get_ref_data_shared(session_id, resolved, owner_token=None):
        fc = fcs.get(resolved)
        if fc is None:
            return SimpleNamespace(success=False, error="not found",
                                   error_type="NotFound", data=None)
        return SimpleNamespace(success=True, error=None, error_type=None, data=fc)

    import app.services.session_data as sd

    monkeypatch.setattr(sd.session_data_manager, "resolve_alias", _resolve_alias)
    monkeypatch.setattr(sd.session_data_manager, "get_ref_descriptor", _get_ref_descriptor)
    monkeypatch.setattr(sd.session_data_manager, "get_ref_data_shared", _get_ref_data_shared)
    return fcs


async def _seed_layer(session_id: str, layer_id: str = "lyr-districts") -> int:
    """目标层先落 spec（bind 的诚实义务：不静默挂层）；返回落定 revision。"""
    from app.services.mapspec.intents import UpsertLayerIntent, UpsertSourceIntent
    from app.services.gis_world_state import apply_gis_mutation

    await apply_gis_mutation(
        session_id, UpsertSourceIntent(
            source_id="src-d",
            source={"type": "geojson", "inlineData": THREE_YEARS},
        ), origin="agent", actor="test")
    result = await apply_gis_mutation(
        session_id, UpsertLayerIntent(
            layer={"id": layer_id, "type": "fill", "source": "src-d"}),
        origin="agent", actor="test")
    return int(getattr(result, "mutation_revision", 0) or 0)


async def _spec(session_id: str) -> Dict[str, Any]:
    engine = MapSpecLifecycleEngine()
    return await engine.store.get_mapspec(session_id) or {}


async def test_bind_three_year_product_with_shared_breaks(ref_store):
    sid = f"g15-{uuid.uuid4().hex[:10]}"
    await _seed_layer(sid)
    out = await fps.bind_frame_product(
        sid, "ref:three", product_kind="time_series",
        field="per_10k", title="成都每万人小学学位",
    )
    assert out["status"] == "ok", out
    assert [f["frame_id"] for f in out["frames"]] == [
        "fp-t-2020", "fp-t-2022", "fp-t-2024"]
    shared = out["shared_expression"]
    # 并集分级（跨 8 个有限值）只算一次；三帧均有数据 → complete
    #（高新 2024 缺行是数据覆盖缺口，不是空帧 —— 空帧 partial 语义在
    # test_shared_expression.test_partial_when_frame_missing_data 锚定）。
    assert shared["domain_status"] == "complete"
    assert shared["breaks"] is not None and len(shared["breaks"]) >= 2
    assert shared["sample_size"] == 8

    spec = await _spec(sid)
    product = spec["frame_product"]
    assert product["enabled"] is True
    assert product["product_kind"] == "time_series"
    assert len(product["frames"]) == 3
    # 画幅身份登记在 layout.frames；产品帧引用一致。
    frame_ids = {f["id"] for f in spec["layout"]["frames"]}
    assert {s["frame_id"] for s in product["frames"]} <= frame_ids
    # 每帧绑定记录数据 revision。
    assert all(s["data"]["content_revision"] == 7 for s in product["frames"])
    # 2024 帧 time_window 来自真实桶。
    win24 = next(s for s in product["frames"]
                 if s["frame_id"] == "fp-t-2024")["data"]["time_window"]
    assert win24["start"].startswith("2024-01-01") and win24["end"].startswith("2025-01-01")


async def test_bind_single_period_refused(ref_store):
    sid = f"g15-{uuid.uuid4().hex[:10]}"
    await _seed_layer(sid)
    out = await fps.bind_frame_product(
        sid, "ref:single", product_kind="time_series", field="per_10k")
    assert out["status"] == "refused"
    assert out["code"] == "single_period_unsupported"
    spec = await _spec(sid)
    assert "frame_product" not in spec


async def test_bind_missing_layer_refused(ref_store):
    sid = f"g15-{uuid.uuid4().hex[:10]}"
    out = await fps.bind_frame_product(
        sid, "ref:three", product_kind="time_series", field="per_10k",
        layer_id="ghost-layer")
    assert out["status"] == "refused"
    assert out["code"] == "layer_not_found"


async def test_update_only_touches_patched_keys(ref_store):
    """修改第二幅标题后刷新：只第二幅变化，保存 revision 恢复一致。"""
    sid = f"g15-{uuid.uuid4().hex[:10]}"
    await _seed_layer(sid)
    await fps.bind_frame_product(
        sid, "ref:three", product_kind="time_series", field="per_10k")
    spec_before = await _spec(sid)
    frames_before = spec_before["frame_product"]["frames"]
    frames_after = [dict(f) for f in frames_before]
    frames_after[1] = {**frames_after[1], "title": "2022 年（更新标题）"}
    out = await fps.update_frame_product(
        sid, {"frames": frames_after, "active_frame": "fp-t-2022"})
    assert out["status"] == "ok", out
    spec = await _spec(sid)
    product = spec["frame_product"]
    assert product["frames"][1]["title"] == "2022 年（更新标题）"
    assert product["frames"][0]["title"] == "2020"
    assert product["active_frame"] == "fp-t-2022"
    # frame_id / 数据绑定 / 图例归属保持。
    assert [f["frame_id"] for f in product["frames"]] == [
        "fp-t-2020", "fp-t-2022", "fp-t-2024"]
    assert product["frames"][1]["data"]["field"] == "per_10k"


async def test_rebind_requires_force(ref_store):
    sid = f"g15-{uuid.uuid4().hex[:10]}"
    await _seed_layer(sid)
    await fps.bind_frame_product(
        sid, "ref:three", product_kind="time_series", field="per_10k")
    out = await fps.bind_frame_product(
        sid, "ref:three", product_kind="time_series", field="per_10k")
    assert out["status"] == "refused" and out["code"] == "already_bound"
    out2 = await fps.bind_frame_product(
        sid, "ref:three", product_kind="time_series", field="per_10k",
        force=True)
    assert out2["status"] == "ok"


async def test_status_and_unbind(ref_store):
    sid = f"g15-{uuid.uuid4().hex[:10]}"
    await _seed_layer(sid)
    await fps.bind_frame_product(
        sid, "ref:three", product_kind="time_series", field="per_10k")
    status = await fps.frame_product_status(sid)
    assert status["bound"] is True
    assert len(status["frames"]) == 3
    # WP11 终验块随 status 携带（回归锚：曾因 return 后死代码缺失）。
    assert status["verification"]["status"] == "incomplete"
    assert len(status["verification"]["frames"]) == 3
    assert status["verification"]["summary"]["not_observed"] == 3
    fp2024 = next(f for f in status["frames"] if f["frame_id"] == "fp-t-2024")
    assert fp2024["resolve"]["filtered_feature_count"] == 2
    assert "time_window" in fp2024["resolve"]["applied"] or \
        "where_filter" in fp2024["resolve"]["applied"]

    out = await fps.unbind_frame_product(sid)
    assert out["status"] == "ok"
    spec = await _spec(sid)
    assert "frame_product" not in spec
    # 画幅登记保留（身份不随产品消失）。
    assert spec["layout"]["frames"] and len(spec["layout"]["frames"]) == 3


async def test_variable_compare_refuses_bad_field(ref_store):
    sid = f"g15-{uuid.uuid4().hex[:10]}"
    await _seed_layer(sid)
    out = await fps.bind_frame_product(
        sid, "ref:three", product_kind="variable_compare",
        fields=["per_10k", "district"])
    assert out["status"] == "refused"
    assert out["code"] == "field_not_quantitative"


async def test_category_compare_with_shared(ref_store):
    sid = f"g15-{uuid.uuid4().hex[:10]}"
    await _seed_layer(sid)
    out = await fps.bind_frame_product(
        sid, "ref:three", product_kind="category_compare",
        split_field="district", field="per_10k")
    assert out["status"] == "ok", out
    spec = await _spec(sid)
    slots = spec["frame_product"]["frames"]
    # 非拉丁类别值 → 确定性哈希 id（唯一 + 稳定；可读名在 title）。
    fids = [s["frame_id"] for s in slots]
    assert len(set(fids)) == 3 and all(fid.startswith("fp-c-") for fid in fids)
    assert [s["title"] for s in slots] == ["武侯", "锦江", "高新"]
    # 类别帧 where 过滤互斥且完备（并集 = 全量）。
    assert all(s["data"]["where"]["field"] == "district" for s in slots)


async def test_panel_patch_must_carry_data_bindings(ref_store):
    """review#1（P0）：frames 整表 patch 只覆写被编辑键 —— data 绑定丢失
    会被 schema 契约拒绝（共享图例画幅必须携带 data.field），不静默接受。"""
    sid = f"g15-{uuid.uuid4().hex[:10]}"
    await _seed_layer(sid)
    await fps.bind_frame_product(
        sid, "ref:three", product_kind="time_series", field="per_10k")
    spec = await _spec(sid)
    frames = spec["frame_product"]["frames"]
    # 面板真实负载（raw 携带 data，只改标题）→ 接受。
    ok_frames = [dict(f) for f in frames]
    ok_frames[1]["title"] = "2022（改名）"
    out = await fps.update_frame_product(sid, {"frames": ok_frames})
    assert out["status"] == "ok", out
    # 旧缺陷负载（丢 data）→ 契约拒绝（不静默删除帧过滤）。
    bad_frames = [
        {"frame_id": f["frame_id"], "title": f.get("title"), "legend": f["legend"]}
        for f in frames
    ]
    out2 = await fps.update_frame_product(sid, {"frames": bad_frames})
    assert out2["status"] == "refused"
    assert "data" in out2["message"] or "field" in out2["message"]


async def test_rate_shared_with_denominator_journey(ref_store):
    """review#4：rate 语义经 denominator 声明走共享分级（不再静默降级）。"""
    sid = f"g15-{uuid.uuid4().hex[:10]}"
    await _seed_layer(sid)
    out = await fps.bind_frame_product(
        sid, "ref:three", product_kind="time_series",
        field="per_10k", semantics="other",
        denominator="population", unit="个/万人",
    )
    assert out["status"] == "ok", out
    assert out["shared_expression"]["domain_status"] == "complete"
    spec = await _spec(sid)
    shared = spec["frame_product"]["shared_expression"]
    assert shared["denominator"] == "population"
    assert shared["unit"] == "个/万人"


class TestCasSemantics:
    """review R2-A：CAS 过期（superseded）绝不能上报成功。"""

    async def test_bind_fresh_revision_chains_and_succeeds(self, ref_store):
        sid = f"g15-{uuid.uuid4().hex[:10]}"
        # 真实当前 revision = seed 落定回执的 mutation_revision（>0）；
        # bind 第二笔（产品契约）按链式基线（frames 写落定 revision）CAS。
        rev = await _seed_layer(sid)
        assert rev > 0
        out = await fps.bind_frame_product(
            sid, "ref:three", product_kind="time_series", field="per_10k",
            expected_revision=rev)
        assert out["status"] == "ok", out
        spec = await _spec(sid)
        assert spec.get("frame_product", {}).get("enabled") is True

    async def test_bind_stale_revision_refused_not_fake_success(self, ref_store):
        sid = f"g15-{uuid.uuid4().hex[:10]}"
        await _seed_layer(sid)
        out = await fps.bind_frame_product(
            sid, "ref:three", product_kind="time_series", field="per_10k",
            expected_revision=0)  # seed 已推进 revision → 0 必过期
        assert out["status"] == "refused"
        assert out["code"] == "mutation_superseded"
        spec = await _spec(sid)
        # 不留空壳成功产品。
        assert "frame_product" not in spec

    async def test_update_and_unbind_stale_refused(self, ref_store):
        sid = f"g15-{uuid.uuid4().hex[:10]}"
        await _seed_layer(sid)
        await fps.bind_frame_product(
            sid, "ref:three", product_kind="time_series", field="per_10k")
        stale = 0
        upd = await fps.update_frame_product(
            sid, {"active_frame": "fp-t-2024"}, expected_revision=stale)
        assert upd["status"] == "refused"
        assert upd["code"] == "mutation_superseded"
        un = await fps.unbind_frame_product(sid, expected_revision=stale)
        assert un["status"] == "refused"
        assert un["code"] == "mutation_superseded"
        # spec 未被触碰。
        spec = await _spec(sid)
        assert spec["frame_product"]["active_frame"] == "fp-t-2020"
