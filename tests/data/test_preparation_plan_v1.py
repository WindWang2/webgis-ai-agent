"""准备计划与执行器 V1 —— M4 单测（closed union/前置校验/幂等/原件不可变/谱系）。

纯函数面（apply_steps/dry_run）零 IO；执行面走真实 session store + 语义 store
（monkeypatch BASE_STORAGE_DIR + reset 单例，与既有 wiring 测试同纪律）。
"""
from __future__ import annotations

import copy

import pytest

from app.lib.data.profile import DatasetProfileV3, profile_features
from app.lib.gis.dataset_descriptor import SourceRef
from app.services.data_preparation.executor import (
    ExecError,
    apply_steps,
    dry_run,
    execute_plan,
)
from app.services.data_preparation.plan import (
    AxisSwapStep,
    DedupStep,
    FieldMappingStep,
    FilterRowsStep,
    PlanValidationError,
    RenameFieldStep,
    ReprojectStep,
    build_plan,
    compute_plan_hash,
    discard_plan,
    get_plan,
    parse_step,
    save_plan,
)
from app.services.dataset_semantics import (
    derive_descriptor_from_v3,
    get_dataset_semantic_store,
    reset_dataset_semantic_store,
)
from app.services.session_data import session_data_manager


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.mapspec.store.BASE_STORAGE_DIR", tmp_path)
    reset_dataset_semantic_store()
    yield
    reset_dataset_semantic_store()


def _school_fc(n=50, bad_every=0):
    features = []
    for i in range(n):
        lon, lat = 104.0 + (i % 10) * 0.01, 30.0 + (i % 5) * 0.01
        if bad_every and i % bad_every == 0:
            lon, lat = 999.0, 999.0   # impossible coordinates
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"学校编号": f"S{i:03d}", "学校名称": f"第{i}小学",
                           "经度": lon, "纬度": lat},
        })
    return {"type": "FeatureCollection", "features": features}


def _swapped_fc(n=10):
    """已对调数据：几何/属性里经纬互换（[30.x, 104.x]）—— AxisSwap 的真实对象。"""
    features = []
    for i in range(n):
        lon, lat = 104.0 + (i % 10) * 0.01, 30.0 + (i % 5) * 0.01
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lat, lon]},
            "properties": {"学校编号": f"S{i:03d}", "学校名称": f"第{i}小学",
                           "经度": lat, "纬度": lon},
        })
    return {"type": "FeatureCollection", "features": features}


def _payload(fc, crs="EPSG:4326"):
    return {"geojson": fc, "crs": crs}


async def _store_input(session_id, payload):
    ref = await session_data_manager.store(session_id, payload, prefix="test")
    fc = payload["geojson"]
    features = fc["features"]
    vp, _q = profile_features(features, crs=payload.get("crs") or "")
    profile = DatasetProfileV3(target_ref=ref, category="vector",
                               crs=payload.get("crs") or "",
                               extent=vp.extent, vector=vp)
    descriptor = derive_descriptor_from_v3(
        profile, dataset_key=ref, features=features,
        source_refs=[SourceRef(type="ref", ref=ref)])
    put = await get_dataset_semantic_store().put(session_id, ref, descriptor)
    assert put.ok
    return ref, put.fingerprint


# ── 计划构造与校验 ─────────────────────────────────────────────────────────

class TestPlanBuild:
    def test_unknown_step_type_rejected(self):
        with pytest.raises(ValueError, match="unregistered step type"):
            parse_step({"type": "explode", "params": {}})

    def test_swap_requires_receipt_evidence(self):
        with pytest.raises(PlanValidationError) as ei:
            build_plan(session_id="s", owner="u", input_ref="ref:x",
                       input_fingerprint="dsd-v1:" + "a" * 64,
                       steps=[AxisSwapStep(lon_field="经度", lat_field="纬度")])
        assert ei.value.reason_code == "SWAP_WITHOUT_EVIDENCE"

    def test_swap_fields_must_match_evidence(self):
        with pytest.raises(PlanValidationError) as ei:
            build_plan(session_id="s", owner="u", input_ref="ref:x",
                       input_fingerprint="dsd-v1:" + "a" * 64,
                       steps=[AxisSwapStep(lon_field="A", lat_field="B")],
                       axis_suspect_evidence={"lon_field": "经度", "lat_field": "纬度"})
        assert ei.value.reason_code == "SWAP_FIELDS_MISMATCH"

    def test_swap_with_matching_evidence_ok(self):
        plan = build_plan(
            session_id="s", owner="u", input_ref="ref:x",
            input_fingerprint="dsd-v1:" + "a" * 64,
            steps=[AxisSwapStep(lon_field="经度", lat_field="纬度")],
            axis_suspect_evidence={"lon_field": "经度", "lat_field": "纬度"})
        assert plan.state == "proposed"
        assert plan.mutates_data

    def test_reproject_on_assumed_crs_rejected(self):
        with pytest.raises(PlanValidationError) as ei:
            build_plan(session_id="s", owner="u", input_ref="ref:x",
                       input_fingerprint="dsd-v1:" + "a" * 64,
                       steps=[ReprojectStep(target_crs="EPSG:4326")],
                       crs_source="assumed")
        assert ei.value.reason_code == "REPROJECT_ON_ASSUMED_CRS"

    def test_reproject_with_declared_crs_ok(self):
        plan = build_plan(session_id="s", owner="u", input_ref="ref:x",
                          input_fingerprint="dsd-v1:" + "a" * 64,
                          steps=[ReprojectStep(target_crs="EPSG:4326")],
                          crs_source="declared")
        assert plan.steps[0].type == "reproject"

    def test_unknown_field_rejected(self):
        with pytest.raises(PlanValidationError) as ei:
            build_plan(session_id="s", owner="u", input_ref="ref:x",
                       input_fingerprint="dsd-v1:" + "a" * 64,
                       steps=[FieldMappingStep(role="name", field="不存在")],
                       available_fields=["学校名称"])
        assert ei.value.reason_code == "UNKNOWN_FIELD"

    def test_rename_collision_rejected(self):
        with pytest.raises(PlanValidationError) as ei:
            build_plan(session_id="s", owner="u", input_ref="ref:x",
                       input_fingerprint="dsd-v1:" + "a" * 64,
                       steps=[RenameFieldStep(from_field="A", to_field="B")],
                       available_fields=["A", "B"])
        assert ei.value.reason_code == "FIELD_ALREADY_EXISTS"

    def test_filter_frac_bounds(self):
        with pytest.raises(ValueError):
            FilterRowsStep(condition_code="impossible_coordinates",
                           max_drop_frac=0.9)
        with pytest.raises(ValueError):
            FilterRowsStep(condition_code="impossible_coordinates",
                           max_drop_frac=0.0)

    def test_empty_plan_rejected(self):
        with pytest.raises(PlanValidationError) as ei:
            build_plan(session_id="s", owner="u", input_ref="ref:x",
                       input_fingerprint="dsd-v1:" + "a" * 64, steps=[])
        assert ei.value.reason_code == "EMPTY_PLAN"

    def test_too_many_steps_rejected(self):
        steps = [FieldMappingStep(role="name", field=f"f{i}", source="user")
                 for i in range(17)]
        with pytest.raises((PlanValidationError, ValueError)):
            build_plan(session_id="s", owner="u", input_ref="ref:x",
                       input_fingerprint="dsd-v1:" + "a" * 64, steps=steps)

    def test_plan_hash_deterministic_and_sensitive(self):
        s1 = [FieldMappingStep(role="name", field="学校名称")]
        s2 = [FieldMappingStep(role="name", field="学校名称")]
        s3 = [FieldMappingStep(role="name", field="其他名称")]
        fp = "dsd-v1:" + "a" * 64
        assert compute_plan_hash(fp, s1) == compute_plan_hash(fp, s2)
        assert compute_plan_hash(fp, s1) != compute_plan_hash(fp, s3)
        assert compute_plan_hash("dsd-v1:" + "b" * 64, s1) != compute_plan_hash(fp, s1)

    def test_metadata_only_plan_does_not_mutate(self):
        plan = build_plan(session_id="s", owner="u", input_ref="ref:x",
                          input_fingerprint="dsd-v1:" + "a" * 64,
                          steps=[FieldMappingStep(role="name", field="学校名称",
                                                  source="user")],
                          available_fields=["学校名称"])
        assert not plan.mutates_data


# ── 纯变换 ─────────────────────────────────────────────────────────────────

class TestApplySteps:
    def test_input_payload_immutable(self):
        payload = _payload(_swapped_fc(10))
        snapshot = copy.deepcopy(payload)
        steps = [AxisSwapStep(lon_field="经度", lat_field="纬度"),
                 FilterRowsStep(condition_code="impossible_coordinates",
                                max_drop_frac=0.5)]
        _new, _ev, _deg = apply_steps(payload, steps)
        assert payload == snapshot

    def test_axis_swap_point_geometry_and_props(self):
        payload = _payload(_swapped_fc(3))
        new, ev, degraded = apply_steps(
            payload, [AxisSwapStep(lon_field="经度", lat_field="纬度")])
        f0 = new["geojson"]["features"][0]
        assert f0["geometry"]["coordinates"][0] == pytest.approx(104.0, abs=0.05)
        assert f0["geometry"]["coordinates"][1] == pytest.approx(30.0, abs=0.05)
        assert f0["properties"]["经度"] == 104.0 and f0["properties"]["纬度"] == 30.0
        assert ev[0]["features_swapped"] == 3
        assert degraded == []

    def test_filter_rows_drops_impossible(self):
        payload = _payload(_school_fc(20, bad_every=5))   # 4 行坏坐标
        new, ev, _ = apply_steps(
            payload, [FilterRowsStep(condition_code="impossible_coordinates",
                                     max_drop_frac=0.5)])
        assert len(new["geojson"]["features"]) == 16
        assert ev[0]["dropped"] == 4

    def test_filter_fraction_guard(self):
        payload = _payload(_school_fc(10, bad_every=2))   # 5/10 坏 → 50%
        with pytest.raises(ExecError) as ei:
            apply_steps(payload, [FilterRowsStep(
                condition_code="impossible_coordinates", max_drop_frac=0.2)])
        assert ei.value.code == "DROP_FRACTION_EXCEEDED"

    def test_dedup_keep_first(self):
        fc = _school_fc(6)
        fc["features"][2]["properties"]["学校编号"] = "S000"   # 与首行重复
        new, ev, _ = apply_steps(
            _payload(fc), [DedupStep(key_fields=("学校编号",))])
        assert len(new["geojson"]["features"]) == 5
        assert ev[0]["dropped"] == 1

    def test_rename_field(self):
        payload = _payload(_school_fc(3))
        new, ev, _ = apply_steps(
            payload, [RenameFieldStep(from_field="学校名称", to_field="校名")])
        assert "校名" in new["geojson"]["features"][0]["properties"]
        assert "学校名称" not in new["geojson"]["features"][0]["properties"]
        assert ev[0]["renamed"] == 3

    def test_field_mapping_is_metadata_only(self):
        payload = _payload(_school_fc(3))
        new, ev, _ = apply_steps(
            payload, [FieldMappingStep(role="id", field="学校编号",
                                       source="user")])
        assert new == payload
        assert ev[0]["data_changed"] is False


class TestDryRun:
    def test_preview_truncation_and_delta(self):
        payload = _payload(_school_fc(3000, bad_every=100))   # 30 坏行
        plan = build_plan(
            session_id="s", owner="u", input_ref="ref:x",
            input_fingerprint="dsd-v1:" + "a" * 64,
            steps=[FilterRowsStep(condition_code="impossible_coordinates",
                                  max_drop_frac=0.5)])
        preview = dry_run(payload, plan, sample_rows=500)
        assert preview["truncated"] is True
        assert preview["sampled_rows"] == 500
        assert preview["steps"][0]["dropped"] == 5    # 500 行内 5 坏行
        assert payload["geojson"]["features"].__len__() == 3000   # 原件未动


# ── 执行主链（真实 store）──────────────────────────────────────────────────

class TestExecutePlan:
    async def test_execute_mutating_plan_full_chain(self):
        sid = "sess-prep-exec-1"
        payload = _payload(_school_fc(20, bad_every=10))   # 2 坏行
        ref, fp = await _store_input(sid, payload)
        plan = build_plan(
            session_id=sid, owner="user-1", input_ref=ref,
            input_fingerprint=fp,
            steps=[
                FieldMappingStep(role="name", field="学校名称", source="user"),
                FilterRowsStep(condition_code="impossible_coordinates",
                               max_drop_frac=0.2),
            ], available_fields=["学校编号", "学校名称", "经度", "纬度"])
        await save_plan(plan)
        receipt = await execute_plan(plan)
        assert receipt.output_ref != ref                       # 新 ref
        assert receipt.rows_dropped == 2
        # 质量口径：issue 按检查聚合（1 条 issue 携带 count 证据），行级差异
        # 由 rows_dropped 表达 —— 前后断言的是 issue 存在 → 消除。
        assert receipt.quality_before.get("impossible_coordinates", 0) == 1
        assert receipt.quality_after.get("impossible_coordinates", 0) == 0
        assert receipt.user_confirmations == (
            {"field": "学校名称", "role": "name", "source": "user"},)
        # 原件不可变
        original = await session_data_manager.get(sid, ref)
        assert len(original["geojson"]["features"]) == 20
        # 输出 descriptor 带确认角色 + 指纹
        rec = await get_dataset_semantic_store().get(sid, receipt.output_ref)
        assert rec.ok
        entry = {f.name: f for f in rec.descriptor.fields}.get("学校名称")
        assert entry is not None and entry.role_confidence == "user_declared"
        # 计划状态迁移
        stored = await get_plan(sid, plan.plan_id)
        assert stored.state == "executed"

    async def test_execute_idempotent_by_plan_hash(self):
        sid = "sess-prep-exec-2"
        payload = _payload(_school_fc(10))
        ref, fp = await _store_input(sid, payload)
        plan = build_plan(
            session_id=sid, owner="u", input_ref=ref, input_fingerprint=fp,
            steps=[DedupStep(key_fields=("学校编号",))],
            available_fields=["学校编号", "学校名称", "经度", "纬度"])
        await save_plan(plan)
        r1 = await execute_plan(plan)
        # 重新构造同内容计划（新 plan_id、同 hash）→ 直接命中既有 receipt
        plan2 = build_plan(
            session_id=sid, owner="u", input_ref=ref, input_fingerprint=fp,
            steps=[DedupStep(key_fields=("学校编号",))],
            available_fields=["学校编号", "学校名称", "经度", "纬度"])
        await save_plan(plan2)
        r2 = await execute_plan(plan2)
        assert r2.output_ref == r1.output_ref
        assert r2.created_at == r1.created_at

    async def test_metadata_only_plan_reuses_input_ref(self):
        sid = "sess-prep-exec-3"
        payload = _payload(_school_fc(5))
        ref, fp = await _store_input(sid, payload)
        plan = build_plan(
            session_id=sid, owner="u", input_ref=ref, input_fingerprint=fp,
            steps=[FieldMappingStep(role="id", field="学校编号", source="user")],
            available_fields=["学校编号", "学校名称", "经度", "纬度"])
        await save_plan(plan)
        receipt = await execute_plan(plan)
        assert receipt.output_ref == ref                     # 零拷贝
        assert receipt.row_before == receipt.row_after
        # descriptor 版本化更新（同 key 新指纹）
        rec = await get_dataset_semantic_store().get(sid, ref)
        assert rec.ok and rec.descriptor.descriptor_fingerprint != fp

    async def test_stale_input_rejected(self):
        sid = "sess-prep-exec-4"
        payload = _payload(_school_fc(5))
        ref, fp = await _store_input(sid, payload)
        plan = build_plan(
            session_id=sid, owner="u", input_ref=ref, input_fingerprint=fp,
            steps=[DedupStep(key_fields=("学校编号",))],
            available_fields=["学校编号", "学校名称", "经度", "纬度"])
        await save_plan(plan)
        # 数据替换：覆写 ref 内容 + 铸新 descriptor（模拟上游数据替换）
        await session_data_manager.overwrite(sid, ref, _payload(_school_fc(9)))
        new_payload = await session_data_manager.get(sid, ref)
        vp, _q = profile_features(new_payload["geojson"]["features"],
                                  crs=new_payload.get("crs") or "")
        profile = DatasetProfileV3(target_ref=ref, category="vector",
                                   crs="EPSG:4326", extent=vp.extent, vector=vp)
        descriptor = derive_descriptor_from_v3(
            profile, dataset_key=ref,
            features=new_payload["geojson"]["features"],
            source_refs=[SourceRef(type="ref", ref=ref)])
        put = await get_dataset_semantic_store().put(sid, ref, descriptor)
        assert put.ok and put.fingerprint != fp
        with pytest.raises(ExecError) as ei:
            await execute_plan(plan)
        assert ei.value.code == "STALE_INPUT"

    async def test_discarded_plan_rejected(self):
        sid = "sess-prep-exec-5"
        payload = _payload(_school_fc(5))
        ref, fp = await _store_input(sid, payload)
        plan = build_plan(
            session_id=sid, owner="u", input_ref=ref, input_fingerprint=fp,
            steps=[DedupStep(key_fields=("学校编号",))],
            available_fields=["学校编号", "学校名称", "经度", "纬度"])
        await save_plan(plan)
        await discard_plan(sid, plan.plan_id)
        with pytest.raises(ExecError) as ei:
            await execute_plan(plan)
        assert ei.value.code == "PLAN_DISCARDED"

    async def test_discard_executed_plan_rejected(self):
        sid = "sess-prep-exec-6"
        payload = _payload(_school_fc(5))
        ref, fp = await _store_input(sid, payload)
        plan = build_plan(
            session_id=sid, owner="u", input_ref=ref, input_fingerprint=fp,
            steps=[DedupStep(key_fields=("学校编号",))],
            available_fields=["学校编号", "学校名称", "经度", "纬度"])
        await save_plan(plan)
        await execute_plan(plan)
        with pytest.raises(PlanValidationError) as ei:
            await discard_plan(sid, plan.plan_id)
        assert ei.value.reason_code == "DISCARD_INVALID_STATE"

    async def test_missing_plan_record_rejected(self):
        sid = "sess-prep-exec-7"
        payload = _payload(_school_fc(5))
        ref, fp = await _store_input(sid, payload)
        plan = build_plan(
            session_id=sid, owner="u", input_ref=ref, input_fingerprint=fp,
            steps=[DedupStep(key_fields=("学校编号",))],
            available_fields=["学校编号", "学校名称", "经度", "纬度"])
        # 未 save_plan → 执行必须拒绝（台账里没有的计划不存在）
        with pytest.raises(ExecError) as ei:
            await execute_plan(plan)
        assert ei.value.code == "PLAN_NOT_FOUND"

    async def test_missing_input_ref_rejected(self):
        sid = "sess-prep-exec-8"
        plan = build_plan(
            session_id=sid, owner="u", input_ref="ref:nope-123",
            input_fingerprint="dsd-v1:" + "c" * 64,
            steps=[DedupStep(key_fields=("x",))])
        await save_plan(plan)
        with pytest.raises(ExecError) as ei:
            await execute_plan(plan)
        assert ei.value.code == "INPUT_REF_MISSING"
