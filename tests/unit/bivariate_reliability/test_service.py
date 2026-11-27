"""G41 服务集成：inspect/preview/commit、强版本、CAS、撤销、恢复、授权。

使用真实 session store + 真实 ``apply_gis_mutation`` / checkpoint / CAS。
"""
from __future__ import annotations

import copy
import json

import pytest

from app.services.bivariate_reliability_authoring import (
    AuthoringError,
    commit_expression,
    inspect_layer,
    parse_plan,
    preview_expression,
)
from app.services.bivariate_reliability_authoring.classify import PAIR_CLASS_FIELD
from app.services.bivariate_reliability_authoring.expression import AUTHORING_KEY
from app.services.bivariate_reliability_authoring.reliability import (
    RELIABILITY_BUCKET_FIELD,
)
from app.services.session_data import session_data_manager
from tests.unit.bivariate_reliability.helpers import (
    bivariate_plan,
    current_revision,
    make_fc,
    read_layer,
    read_source,
    register_geojson_ref,
    reliability_plan,
    seed_layer,
)

ROWS = [{"a": float(i), "b": float(i % 3), "est": float(i),
         "se": 0.0 if i == 0 else float(i % 4) * 0.5,
         "ci_lo": float(i) - 0.4, "ci_hi": float(i) + 0.9}
        for i in range(12)]


async def _preview(session_id, plan_dict):
    plan = parse_plan(plan_dict)
    receipt = await preview_expression(session_id, "L1", plan,
                                       trusted_dispatch=True)
    return plan, receipt


async def _commit(session_id, plan, receipt, *, expected_revision=None):
    rev = (await current_revision(session_id) if expected_revision is None
           else expected_revision)
    return await commit_expression(
        session_id, "L1", plan, expected_revision=rev,
        preview_digest=receipt["candidate_digest"], origin="user",
        actor="test:panel", trusted_dispatch=True)


# ── B01 / 完整纵链 ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_b01_bivariate_full_chain_produces_real_map_and_derived_ref(session_id):
    base_ref = await seed_layer(session_id, make_fc(ROWS))
    ins = await inspect_layer(session_id, "L1", trusted_dispatch=True)
    assert ins["source"]["base_ref"] == base_ref
    assert ins["source"]["content_revision"] >= 1
    assert ins["source"]["feature_count"] == 12
    assert ins["supported"]["bivariate_matrix"] is True

    plan, prev = await _preview(session_id, bivariate_plan(n=2))
    assert prev["committed"] is False and prev["preview_only"] is True
    assert prev["summary"]["valid_pairs"] == 12
    assert len(prev["legend"]["cells"]) == 4
    assert sum(c["count"] for c in prev["legend"]["cells"]) == 12
    assert prev["legend"]["approximate"] is False

    out = await _commit(session_id, plan, prev)
    assert out["success"] and out["mutations"] == 1 and out["can_undo"]
    layer = await read_layer(session_id)
    assert layer["legend_spec"]["type"] == "bivariate"
    assert layer["paint"]["color"]["method"] == "match"
    assert layer["paint"]["color"]["field"] == PAIR_CLASS_FIELD
    assert layer["bivariate"]["class_field"] == PAIR_CLASS_FIELD
    assert layer["bivariate"]["matrix"] == 2
    # 源数据不写回；展示副本有独立身份与血缘
    src = await read_source(session_id)
    derived = src["ref_id"]
    assert derived.startswith("ref:g41display-") and derived != base_ref
    payload = await session_data_manager.get(session_id, derived)
    feats = payload["geojson"]["features"]
    assert len(feats) == 12
    assert all(PAIR_CLASS_FIELD in f["properties"] for f in feats)
    assert payload["geojson"]["metadata"]["base_source_ref"] == base_ref
    base_payload = await session_data_manager.get(session_id, base_ref)
    assert all(PAIR_CLASS_FIELD not in f["properties"]
               for f in base_payload["geojson"]["features"])
    assert layer[AUTHORING_KEY]["base_source_ref"] == base_ref
    assert out["derived"]["source_written_back"] is False


@pytest.mark.asyncio
async def test_b02_two_axes_can_use_different_methods(session_id):
    await seed_layer(session_id, make_fc(ROWS))
    body = bivariate_plan()["bivariate"]
    body["axis_a"]["method"] = "quantiles"
    body["axis_b"]["method"] = "equal_interval"
    plan, prev = await _preview(session_id, {"layer_id": "L1",
                                             "mode": "bivariate_matrix",
                                             "bivariate": body})
    axes = prev["legend"]["axes"]
    assert axes["col"]["method"] == "quantiles"
    assert axes["row"]["method"] == "equal_interval"
    assert axes["col"]["unit"] == "人" and axes["row"]["unit"] == "%"
    assert prev["legend"]["boundary_rule"] == "upper_inclusive"


@pytest.mark.asyncio
async def test_b03_manual_breaks_exact_equality_and_legend_consistency(session_id):
    rows = [{"a": float(v), "b": float(v)} for v in (1, 10, 20, 30, 40)]
    await seed_layer(session_id, make_fc(rows))
    body = bivariate_plan()["bivariate"]
    body["axis_a"] = {"field": "a", "method": "manual", "breaks": [10, 30]}
    body["axis_b"] = {"field": "b", "method": "manual", "breaks": [10, 30]}
    plan, prev = await _preview(session_id, {"layer_id": "L1",
                                             "mode": "bivariate_matrix",
                                             "bivariate": body})
    # upper_inclusive：a=10 恰等断点归上类 → 类 1
    assert prev["legend"]["axes"]["col"]["intervals"][1]["label"].startswith("[10,")
    assert prev["legend"]["boundary_rule"] == "upper_inclusive"
    counts = [c["count"] for c in prev["legend"]["cells"]]
    assert sum(counts) == 5
    # 每个格子的区间文本与 paint 类别逐格一致（同一 canonical 来源）
    for c in prev["legend"]["cells"]:
        assert c["color"] in prev["legend"]["colors"]
        assert c["class_index"] == c["row"] * 3 + c["col"]
        assert isinstance(c["row_interval"], dict)
        assert isinstance(c["col_interval"], dict)


@pytest.mark.asyncio
async def test_b04_legacy_bivariate_layer_is_never_rewritten_by_inspect(session_id):
    """旧 bivariate 图重开且未应用新作者计划 → 旧分类与 paint 完全不变。"""
    from app.lib.cartography.bivariate import (
        bivariate_legend_spec,
        compute_bivariate_classes,
    )
    from app.lib.cartography.thematic_spec import spec_to_paint

    fc = make_fc(ROWS)
    info = compute_bivariate_classes(fc["features"], field_a="a", field_b="b",
                                     n=3, method="quantiles")
    legend = bivariate_legend_spec(
        "BiPurpleOrange", label_a="a", label_b="b",
        breaks_a=info["breaks_a"], breaks_b=info["breaks_b"], n=3)
    paint, _ = spec_to_paint(legend)
    base_ref = await seed_layer(
        session_id, fc, legend=legend, paint={"color": paint})
    before = await read_layer(session_id)
    before_classes = [f["properties"]["__biv_class"]
                      for f in fc["features"]]
    # inspect 是只读；不应用新计划，旧图保持 legacy 端点语义。
    ins = await inspect_layer(session_id, "L1", trusted_dispatch=True)
    assert ins["current"]["legend_type"] == "bivariate"
    after = await read_layer(session_id)
    assert json.dumps(before, sort_keys=True, default=str) == json.dumps(
        after, sort_keys=True, default=str)
    payload = await session_data_manager.get(session_id, base_ref)
    assert [f["properties"]["__biv_class"]
            for f in payload["geojson"]["features"]] == before_classes
    # 新作者计划显式声明规则，且不复用旧 __biv_class 字段
    plan, prev = await _preview(session_id, bivariate_plan())
    assert prev["legend"]["boundary_rule"] == "upper_inclusive"
    assert PAIR_CLASS_FIELD != "__biv_class"


@pytest.mark.asyncio
async def test_b05_constant_axis_and_empty_cells_are_honest(session_id):
    rows = [{"a": 7.0, "b": float(i)} for i in range(9)]
    await seed_layer(session_id, make_fc(rows))
    plan, prev = await _preview(session_id, bivariate_plan())
    assert prev["summary"]["actual_classes_a"] == 1
    assert prev["summary"]["requested_classes"] == 3
    assert any(c["empty"] for c in prev["legend"]["cells"])
    assert any("不伪造" in d or "空格" in d or "实际只有" in d
               for d in prev["disclosures"])


@pytest.mark.asyncio
async def test_b14_source_with_user_biv_class_is_never_overwritten(session_id):
    rows = [dict(r, __biv_class=999) for r in ROWS]
    await seed_layer(session_id, make_fc(rows))
    plan, prev = await _preview(session_id, bivariate_plan())
    assert prev["derived"]["source_has_biv_class"] is True
    out = await _commit(session_id, plan, prev)
    assert out["derived"]["source_has_biv_class"] is True
    layer = await read_layer(session_id)
    assert layer[AUTHORING_KEY]["source_has_biv_class"] is True
    derived = (await read_source(session_id))["ref_id"]
    payload = await session_data_manager.get(session_id, derived)
    assert all(f["properties"]["__biv_class"] == 999
               for f in payload["geojson"]["features"])
    assert all(f["properties"][PAIR_CLASS_FIELD] != 999
               for f in payload["geojson"]["features"])


@pytest.mark.asyncio
async def test_b14_derived_field_collision_is_rejected(session_id):
    rows = [dict(r, **{PAIR_CLASS_FIELD: "user-owned"}) for r in ROWS]
    await seed_layer(session_id, make_fc(rows))
    with pytest.raises(AuthoringError) as exc:
        await _preview(session_id, bivariate_plan())
    assert exc.value.code == "DERIVED_FIELD_COLLISION"


# ── 强版本 / ABA / 陈旧 ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_b15_source_mutation_during_freeze_is_rejected(session_id, monkeypatch):
    await seed_layer(session_id, make_fc(ROWS))

    real = session_data_manager.get_ref_descriptor
    calls = {"n": 0}

    async def flip(session, ref):
        calls["n"] += 1
        d = await real(session, ref)
        if isinstance(d, dict) and calls["n"] == 2:
            d = dict(d)
            d["content_revision"] = int(d["content_revision"]) + 1
        return d

    monkeypatch.setattr(session_data_manager, "get_ref_descriptor", flip)
    with pytest.raises(AuthoringError) as exc:
        await inspect_layer(session_id, "L1", trusted_dispatch=True)
    assert exc.value.code == "SOURCE_MUTATION_DURING_READ"


@pytest.mark.asyncio
async def test_b38_missing_descriptor_is_typed_rejection(session_id, monkeypatch):
    await seed_layer(session_id, make_fc(ROWS))
    monkeypatch.setattr(session_data_manager, "get_ref_descriptor",
                        lambda session, ref: _none())
    with pytest.raises(AuthoringError) as exc:
        await inspect_layer(session_id, "L1", trusted_dispatch=True)
    assert exc.value.code == "NO_STRONG_REVISION"


async def _none():
    return None


@pytest.mark.asyncio
async def test_b37_aba_restore_is_detected_by_monotonic_generation(session_id):
    base_ref = await seed_layer(session_id, make_fc(ROWS))
    ins = await inspect_layer(session_id, "L1", trusted_dispatch=True)
    payload = bivariate_plan()
    # 真实 UI 会把 inspect 的强版本锚随计划提交，使源漂移有精确裁决码。
    payload["source_anchor"] = {
        "ref_id": ins["source_anchor"]["ref_id"],
        "content_revision": ins["source_anchor"]["content_revision"],
        "content_fingerprint": ins["source_anchor"]["content_fingerprint"],
    }
    plan, prev = await _preview(session_id, payload)
    original = await session_data_manager.get(session_id, base_ref)
    changed = copy.deepcopy(original)
    changed["geojson"]["features"][0]["properties"]["a"] = 999.0
    assert await session_data_manager.overwrite(session_id, base_ref, changed)
    # 改值后恢复原内容；fingerprint 回到原值，但 generation 已推进两次。
    assert await session_data_manager.overwrite(session_id, base_ref, original)
    with pytest.raises(AuthoringError) as exc:
        await _commit(session_id, plan, prev)
    assert exc.value.code == "STALE_SOURCE"


@pytest.mark.asyncio
async def test_b27_changed_axis_or_error_type_invalidates_preview(session_id):
    await seed_layer(session_id, make_fc(ROWS))
    plan, prev = await _preview(session_id, bivariate_plan())
    body = bivariate_plan()["bivariate"]
    body["boundary_rule"] = "legacy_lower_inclusive"
    changed = parse_plan({"layer_id": "L1", "mode": "bivariate_matrix",
                          "bivariate": body})
    with pytest.raises(AuthoringError) as exc:
        await commit_expression(
            session_id, "L1", changed,
            expected_revision=await current_revision(session_id),
            preview_digest=prev["candidate_digest"], trusted_dispatch=True)
    assert exc.value.code == "STALE_PREVIEW"


@pytest.mark.asyncio
async def test_b28_cas_conflict_preserves_user_decision(session_id):
    await seed_layer(session_id, make_fc(ROWS))
    plan, prev = await _preview(session_id, bivariate_plan())
    with pytest.raises(AuthoringError) as exc:
        await commit_expression(
            session_id, "L1", plan,
            expected_revision=(await current_revision(session_id)) + 7,
            preview_digest=prev["candidate_digest"], trusted_dispatch=True)
    assert exc.value.code == "CAS_SUPERSEDED"
    layer = await read_layer(session_id)
    assert layer["legend_spec"]["type"] == "graduated"
    assert (await read_source(session_id))["ref_id"].startswith("ref:result-")


# ── 可靠性模式 ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_b18_b24_reliability_zero_unknown_and_no_fabrication(session_id):
    rows = [dict(r) for r in ROWS]
    rows[5]["se"] = None
    await seed_layer(session_id, make_fc(rows))
    plan, prev = await _preview(session_id, reliability_plan())
    assert prev["summary"]["zero_measure"] >= 1
    assert prev["summary"]["unknown_measure"] == 1
    assert prev["legend"]["measure"]["author_declared"] is True
    assert prev["legend"]["missing"]["policy"].startswith("误差缺失")
    out = await _commit(session_id, plan, prev)
    assert out["success"]
    layer = await read_layer(session_id)
    assert layer["paint"]["color"]["method"] == "step"
    assert layer["paint"]["opacity"]["method"] == "match"
    assert layer["paint"]["opacity"]["field"] == RELIABILITY_BUCKET_FIELD
    keys = [c[0] for c in layer["paint"]["opacity"]["cases"]]
    assert "unknown" in keys
    assert layer["legend_spec"]["type"] == "reliability_dual_channel"
    assert "bivariate" not in layer


@pytest.mark.asyncio
async def test_b20_ci_pi_distinct_and_level_in_legend(session_id):
    await seed_layer(session_id, make_fc(ROWS))
    body = reliability_plan()["reliability"]
    body["measure"] = {"kind": "prediction_interval", "low_field": "ci_lo",
                       "high_field": "ci_hi", "level": 0.9, "unit": "m",
                       "source": "author_declared",
                       "method_note": "模型输出的 90% 预测区间"}
    plan, prev = await _preview(session_id, {"layer_id": "L1",
                                             "mode": "reliability_dual_channel",
                                             "reliability": body})
    assert prev["legend"]["measure"]["interval_kind"] == "PI"
    assert prev["legend"]["measure"]["level"] == 0.9
    assert "预测区间" in prev["legend"]["measure"]["unit_note"]
    assert any("非对称" in d for d in prev["disclosures"])


@pytest.mark.asyncio
async def test_b21_low_gt_high_is_invalid_not_auto_sorted(session_id):
    rows = [dict(r) for r in ROWS]
    rows[0]["ci_lo"] = 99.0
    rows[0]["ci_hi"] = -99.0
    await seed_layer(session_id, make_fc(rows))
    body = reliability_plan()["reliability"]
    body["measure"] = {"kind": "confidence_interval", "low_field": "ci_lo",
                       "high_field": "ci_hi", "level": 0.95, "unit": "m",
                       "source": "author_declared", "method_note": "测试 CI"}
    with pytest.raises(AuthoringError) as exc:
        await _preview(session_id, {"layer_id": "L1",
                                    "mode": "reliability_dual_channel",
                                    "reliability": body})
    assert exc.value.code == "INVALID_MEASURE_VALUES"


@pytest.mark.asyncio
async def test_f4_allow_noncentered_false_is_enforced(session_id):
    """复审 F4：该开关不是死字段；非居中区间必须被判无效。"""
    rows = [{"a": float(i), "b": float(i), "est": float(i),
             "ci_lo": float(i) - 0.3, "ci_hi": float(i) + 0.9}
            for i in range(8)]
    await seed_layer(session_id, make_fc(rows))
    body = reliability_plan()["reliability"]
    body["measure"] = {
        "kind": "confidence_interval", "low_field": "ci_lo",
        "high_field": "ci_hi", "level": 0.95, "unit": "m",
        "source": "author_declared", "method_note": "来源模型要求对称区间",
        "allow_noncentered": False,
    }
    with pytest.raises(AuthoringError) as exc:
        await _preview(session_id, {"layer_id": "L1",
                                    "mode": "reliability_dual_channel",
                                    "reliability": body})
    assert exc.value.code == "INVALID_MEASURE_VALUES"
    # 默认允许非居中（不机械强制居中），同一数据在 allow=True 下可预览。
    body["measure"]["allow_noncentered"] = True
    _, ok = await _preview(session_id, {"layer_id": "L1",
                                        "mode": "reliability_dual_channel",
                                        "reliability": body})
    assert ok["success"] is True


@pytest.mark.asyncio
async def test_b23_no_uncertainty_evidence_is_blocked_with_next_steps(session_id):
    rows = [{"a": float(i), "b": float(i), "est": float(i),
             "sample_count": 30, "p_value": 0.04} for i in range(8)]
    await seed_layer(session_id, make_fc(rows))
    with pytest.raises(AuthoringError) as exc:
        await _preview(session_id, reliability_plan())
    assert exc.value.code == "NO_UNCERTAINTY_EVIDENCE"
    assert "sample_count" in exc.value.message or "p 值" in exc.value.message
    assert "标准误" in exc.value.correction_hint


@pytest.mark.asyncio
async def test_b26_two_sources_without_stable_join_are_not_merged(session_id):
    """合同只接受同一冻结源同一行；外部误差表按行序拼接无对应证明。"""
    await seed_layer(session_id, make_fc(ROWS))
    plan, prev = await _preview(session_id, reliability_plan())
    assert any("按行序拼接" in d for d in prev["disclosures"])
    assert prev["legend"]["source"]["ref_id"].startswith("ref:")
    assert prev["derived"]["fields"] == [RELIABILITY_BUCKET_FIELD]


@pytest.mark.asyncio
async def test_visual_channel_layer_type_mismatch_rejected(session_id):
    await seed_layer(session_id, make_fc(ROWS), layer_type="line")
    body = reliability_plan()["reliability"]
    body["visual"]["channel"] = "stroke_width"
    with pytest.raises(AuthoringError) as exc:
        await _preview(session_id, {"layer_id": "L1",
                                    "mode": "reliability_dual_channel",
                                    "reliability": body})
    assert exc.value.code == "VISUAL_CHANNEL_LAYER_TYPE_MISMATCH"


@pytest.mark.asyncio
async def test_bivariate_symbol_layer_type_rejected(session_id):
    """symbol 层不支持 bivariate_matrix：inspect 与 preview 口径必须一致。"""
    await seed_layer(session_id, make_fc(ROWS, geom="Point"),
                    layer_type="symbol")
    ins = await inspect_layer(session_id, "L1", trusted_dispatch=True)
    assert ins["supported"]["bivariate_matrix"] is False
    with pytest.raises(AuthoringError) as exc:
        await _preview(session_id, bivariate_plan())
    assert exc.value.code == "UNSUPPORTED_LAYER_TYPE"
    assert exc.value.status_code == 409


# ── B29 / B30 / B31 / B36 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_b29_shared_expression_member_is_refused(session_id):
    await seed_layer(session_id, make_fc(ROWS))
    # 复用产品级 frame_product 形状（G15 shared_expression + frames[].data.layer_id）
    from app.services.mapspec_store import mapspec_store
    spec = await mapspec_store.get_mapspec(session_id)
    spec["frame_product"] = {
        "enabled": True,
        "shared_expression": {"field": "a", "method": "quantiles"},
        "frames": [{"frame_id": "f1", "legend": "shared",
                    "data": {"layer_id": "L1"}}],
    }
    await mapspec_store.save_mapspec(session_id, spec)
    with pytest.raises(AuthoringError) as exc:
        await _preview(session_id, bivariate_plan())
    assert exc.value.code == "SHARED_EXPRESSION_MEMBER"


@pytest.mark.asyncio
async def test_b30_revert_cleans_stale_bivariate_and_second_channel(session_id):
    base_ref = await seed_layer(session_id, make_fc(ROWS))
    p1, r1 = await _preview(session_id, bivariate_plan())
    await _commit(session_id, p1, r1)
    bivariate_display_ref = (await read_source(session_id))["ref_id"]
    # 再切到可靠性模式：bivariate 身份必须被清理，只留新第二通道
    p2, r2 = await _preview(session_id, reliability_plan())
    out2 = await _commit(session_id, p2, r2)
    assert out2["success"]
    layer = await read_layer(session_id)
    assert "bivariate" not in layer
    assert set(layer["paint"]) == {"color", "opacity"}
    assert layer["paint"]["opacity"]["method"] == "match"
    # 显式恢复：不得遗留上一模式的 reliability match / 幽灵 fill-opacity
    rp = parse_plan({"layer_id": "L1", "mode": "revert"})
    rprev = await preview_expression(session_id, "L1", rp, trusted_dispatch=True)
    assert rprev["candidate_digest"]
    rv = await commit_expression(
        session_id, "L1", rp,
        expected_revision=await current_revision(session_id),
        preview_digest=rprev["candidate_digest"], trusted_dispatch=True)
    assert rv["success"] and rv["can_undo"]
    layer = await read_layer(session_id)
    # 单步恢复回到上一笔作者决定（双变量矩阵），且无失效第二通道
    assert layer["paint"]["opacity"] == 1.0
    assert "fill-opacity" not in layer["paint"]
    assert layer["legend_spec"]["type"] == "bivariate"
    assert "bivariate" in layer
    # F2 复审：paint 引用派生字段，恢复的 source 必须是**同一代**展示副本，
    # 否则整层透明空白却挂着双变量图例。
    restored_ref = (await read_source(session_id))["ref_id"]
    assert restored_ref == bivariate_display_ref
    assert restored_ref != base_ref
    payload = await session_data_manager.get(session_id, restored_ref)
    assert all(PAIR_CLASS_FIELD in f["properties"]
               for f in payload["geojson"]["features"])
    assert layer["paint"]["color"]["field"] == PAIR_CLASS_FIELD
    assert layer[AUTHORING_KEY]["base_source_ref"] == base_ref


@pytest.mark.asyncio
async def test_revert_preview_reaches_layer_without_previous_legend(session_id):
    """作者化前本来没有 legend_spec 的层，撤销必须可达（复审 P1-2）。

    预览若把 ``previous_legend_spec is None`` 当作「无快照」拒绝，而提交侧把
    ``None`` 当作「恢复为无图例」支持，二者矛盾会让撤销永远无法提交 ——
    用户只能靠 MapSpec undo 绕过，违反「可撤销」。
    """
    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.intents import UpsertLayerIntent

    ref_id = await register_geojson_ref(session_id, make_fc(ROWS))
    seeded = await apply_gis_mutation(
        session_id,
        UpsertLayerIntent(layer={
            "id": "L1", "type": "fill", "source": "s1",
            "paint": {"color": "#cccccc"}, "data_binding": {"field": "a"},
        }, source_data={"ref_id": ref_id}),
        origin="agent", actor="test:seed")
    assert not seeded.is_error, seeded.error_msg
    assert "legend_spec" not in await read_layer(session_id)

    p, r = await _preview(session_id, bivariate_plan())
    await _commit(session_id, p, r)
    assert (await read_layer(session_id))["legend_spec"]["type"] == "bivariate"

    rp = parse_plan({"layer_id": "L1", "mode": "revert"})
    rprev = await preview_expression(session_id, "L1", rp, trusted_dispatch=True)
    assert rprev["candidate_digest"]
    assert rprev["restores"]["had_legend"] is False
    assert rprev["restores"]["source_ref"] == ref_id

    rv = await commit_expression(
        session_id, "L1", rp,
        expected_revision=await current_revision(session_id),
        preview_digest=rprev["candidate_digest"], trusted_dispatch=True)
    assert rv["success"]
    layer = await read_layer(session_id)
    assert "legend_spec" not in layer
    assert "bivariate" not in layer
    assert layer["paint"]["color"] == "#cccccc"


@pytest.mark.asyncio
async def test_b31_foreign_owner_read_is_rejected(session_id, monkeypatch):
    from app.services.session_data_protocol import SessionRefDataResult

    await seed_layer(session_id, make_fc(ROWS))

    async def denied(session, ref, owner_token=None):
        return SessionRefDataResult(success=False, error="PermissionDenied",
                                    error_type="PermissionDenied")

    monkeypatch.setattr(session_data_manager, "get_ref_data_shared", denied)
    with pytest.raises(AuthoringError) as exc:
        await inspect_layer(session_id, "L1", owner_token="foreign-token",
                            trusted_dispatch=False)
    assert exc.value.code == "SOURCE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_b36_late_callback_cannot_commit_into_wrong_session(session_id):
    other = session_id + "-other"
    await seed_layer(session_id, make_fc(ROWS))
    await seed_layer(other, make_fc(ROWS))
    plan, prev = await _preview(session_id, bivariate_plan())
    # 迟到回调拿着旧 candidate_digest 打到另一个会话 → 源指纹不同 → 拒绝
    with pytest.raises(AuthoringError) as exc:
        await commit_expression(
            other, "L1", plan,
            expected_revision=await current_revision(other),
            preview_digest=prev["candidate_digest"], trusted_dispatch=True)
    assert exc.value.code == "STALE_PREVIEW"
    layer = await read_layer(other)
    assert layer["legend_spec"]["type"] == "graduated"


@pytest.mark.asyncio
async def test_b10_scatter_is_bounded_but_counts_are_exact(session_id):
    rows = [{"a": float(i), "b": float(i % 7)} for i in range(500)]
    await seed_layer(session_id, make_fc(rows))
    body = bivariate_plan()["bivariate"]
    body["scatter_sample_size"] = 50
    plan, prev = await _preview(session_id, {"layer_id": "L1",
                                             "mode": "bivariate_matrix",
                                             "bivariate": body})
    assert prev["legend"]["approximate"] is False
    assert sum(c["count"] for c in prev["legend"]["cells"]) == 500
    assert prev["summary"]["valid_pairs"] == 500
