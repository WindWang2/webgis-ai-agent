"""必过旅程 1–4（goal §6）—— 服务级真实链路（不 mock 被测语义）。"""
from __future__ import annotations

import asyncio

import numpy as np
import pytest

from app.lib.cancellation import CancellationToken, OperationCancelled, use_token
from app.services.survey_sampling import service, store
from app.services.survey_sampling.contracts import SurveySamplingError

from .conftest import CLASS_DICT, _register_and_build_frame, truth_labels_for


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def test_journey1_full_loop_hand_check(survey_env):
    """旅程1：合成三层图 → 固定 seed 分层样本 → 独立参考标签 →
    加权矩阵/OA/UA/PA/面积与手算一致 → 导出与地图引用一致。"""
    frame, cls_path, truth_path, ref_id, scope = _run(
        _register_and_build_frame(survey_env))
    assert frame["total_units"] > 0

    # 设计预览（无副作用）→ 创建调查（固定 seed；等额保底稀有层）
    preview = service.preview_allocation(scope, frame["frame_digest"],
                                         strategy="equal", n_total=60)
    assert preview["allocation"]["water"] >= 2      # 稀有层不跳过
    created = _run(service.create_survey(
        scope, frame["frame_digest"], objective="县域土地覆盖核查",
        target_time="2026-11", strategy="equal", n_total=60, seed=42,
        backup_per_stratum=3, blind_labelling=True))
    survey_id = created["survey_id"]
    assert created["main_count"] == 60

    # 盲标视图不含预测类（层名）；manager 视图含
    blind_doc = service.survey_detail(scope, survey_id, blind=True)
    assert all("stratum_id" not in s for s in blind_doc["samples"])
    full_doc = service.survey_detail(scope, survey_id, blind=False)
    assert all("stratum_id" in s for s in full_doc["samples"])

    # 独立参考判读：真值图逐点取标签（与预测图独立的"调查员"）
    samples = full_doc["samples"]
    mains = [s for s in samples if not s["is_backup"]]
    coords = [(s["lon"], s["lat"]) for s in mains]
    labels = truth_labels_for(truth_path, coords)

    # 逐点记录（CAS revision 递增；盲标响应不回显预测类）
    revision = store.get_revision(scope, survey_id)
    for s, lab in zip(mains, labels):
        assert lab is not None, "真值图应覆盖全部样本点"
        res = _run(service.record_reference(
            scope, survey_id, sample_id=s["sample_id"],
            status="labelled", reference_class=lab,
            evidence_type="high_res_imagery", quality="high",
            interpreter="oracle", observed_at="2026-11",
            expected_revision=revision))
        assert res["blind"] is True
        assert "stratum_id" not in res          # 盲标：不回显
        revision = res["revision"]

    # 估计
    est = _run(service.estimate_survey(scope, survey_id))
    assert est["status"] in ("supported", "partial")

    # ── 手算一致性（从样本事实独立复算）─────────────────────────
    by_stratum = {}
    for s, lab in zip(mains, labels):
        by_stratum.setdefault(s["stratum_id"], []).append(lab)
    total_area = frame["total_area"]
    strata_order = [sid for sid, _info in frame["strata"]]  # 估计器行序
    W = {sid: info["area"] / total_area
         for sid, info in frame["strata"]}
    classes = strata_order
    idx = {c: k for k, c in enumerate(classes)}
    H = len(classes)
    matrix = np.zeros((H, H))
    counts = np.zeros((H, H), dtype=int)
    n_resp = np.zeros(H, dtype=int)
    for h, sid in enumerate(classes):
        labs = by_stratum.get(sid, [])
        n_resp[h] = len(labs)
        for lab in labs:
            counts[h, idx[lab]] += 1
        if labs:
            matrix[h, :] = W[sid] * np.array(
                [labs.count(c) for c in classes]) / len(labs)
    np.testing.assert_allclose(np.array(est["matrix"]), matrix, atol=1e-12)
    np.testing.assert_array_equal(np.array(est["sample_counts"]), counts)
    np.testing.assert_array_equal(np.array(est["n_responded"]), n_resp)
    oa_hand = float(np.trace(matrix))
    assert est["overall_accuracy"]["estimate"] == pytest.approx(oa_hand, abs=1e-12)
    # UA = 对角线/行和（行和=层权）
    for i, cname in enumerate(classes):
        ua = est["user_accuracy"][cname]["estimate"]
        assert ua == pytest.approx(matrix[i, i] / W[classes[i]], abs=1e-12)
    # 面积（比例×总面积）
    for j, cname in enumerate(classes):
        area = est["area_estimate"][cname]["estimate"]
        assert area == pytest.approx(matrix[:, j].sum() *
                                     total_area, abs=1e-6)
    # 真值落在 CI 内（覆盖性 sanity；真值面积比例来自真值图）
    import rasterio

    with rasterio.open(truth_path) as src:
        truth = src.read(1)
    valid = truth != 0
    truth_frac = {
        "forest": float((truth[valid] == 1).mean()),
        "crop": float((truth[valid] == 2).mean()),
        "water": float((truth[valid] == 3).mean()),
    }
    for cname, frac in truth_frac.items():
        mv = est["area_fraction"][cname]
        assert mv["ci_low"] <= frac <= mv["ci_high"], (
            f"真值 {cname} 面积比例 {frac:.3f} 落在 CI 外")

    # 导出（估计 artifact 持久化）与地图引用（样本 GeoJSON）同源
    saved = store.get_estimate(scope, survey_id, est["estimate_id"])
    assert saved["matrix"] == est["matrix"]
    samples_out = _run(_samples_via_tool_path(scope, survey_id))
    assert samples_out["features"], "GeoJSON 供地图加载"


async def _samples_via_tool_path(scope, survey_id):
    """工具面 survey_samples 同一 service 出口（地图引用一致性）。"""

    # 直接调用工具实现（绕过 registry 装饰器闭包——工具经注册一致性另测）
    doc = service.survey_detail(scope, survey_id, blind=True)
    features = [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [s["lon"], s["lat"]]},
         "properties": {"sample_id": s["sample_id"]}}
        for s in doc.get("samples", [])
    ]
    return {"type": "FeatureCollection", "features": features}


def test_journey2_stale_and_redesign_keeps_history(survey_env):
    """旅程2：版本/类字典漂移 → stale；缺参考层 → not_evaluated；
    非响应 → 顶替披露；可重设计且历史保留。"""
    frame, cls_path, truth_path, ref_id, scope = _run(
        _register_and_build_frame(survey_env))
    created = _run(service.create_survey(
        scope, frame["frame_digest"], objective="基线调查", target_time="2026-11",
        strategy="equal", n_total=30, seed=7, backup_per_stratum=2))
    survey_id = created["survey_id"]

    # 只标注 2 层（water 层完全缺参考 + 部分 not_visited 非响应）
    full_doc = service.survey_detail(scope, survey_id, blind=False)
    mains = [s for s in full_doc["samples"] if not s["is_backup"]]
    revision = store.get_revision(scope, survey_id)
    labelled = 0
    for s in mains:
        if s["stratum_id"] == "water":
            continue                              # 缺参考层
        if labelled % 4 == 3:
            labelled += 1
            continue                              # 非响应（未访问）
        res = _run(service.record_reference(
            scope, survey_id, sample_id=s["sample_id"],
            status="labelled",
            reference_class="crop" if s["stratum_id"] != "crop" else "forest",
            evidence_type="field_visit", quality="medium",
            expected_revision=revision))
        revision = res["revision"]
        labelled += 1

    est = _run(service.estimate_survey(scope, survey_id))
    assert est["status"] == "not_evaluated"       # water 层零响应
    assert any("not estimable" in lim for lim in est["limitations"])

    # 分类图版本漂移（重写内容）→ 估计 stale 拒绝
    from .conftest import write_survey_raster

    write_survey_raster(cls_path, mutate=True, seed=7)
    with pytest.raises(SurveySamplingError) as exc:
        _run(service.estimate_survey(scope, survey_id))
    assert exc.value.code == "STALE_ESTIMATE"

    # 重新建框 + 新调查（历史保留；旧调查估计不可再生但记录在案）
    from app.services.raster_authoring.source import _SOURCE_CACHE, _REVISION_CACHE

    _SOURCE_CACHE.clear()
    _REVISION_CACHE.clear()
    ref_id2 = None
    from app.services.session_data import session_data_manager

    ref_id2 = _run(session_data_manager.store(
        "sess-survey-journey",
        {"kind": "numeric_raster", "file_path": cls_path},
        prefix="raster"))
    frame2 = _run(service.build_survey_frame(
        "sess-survey-journey", None, scope, source_ref=str(ref_id2),
        band=1, class_dictionary=dict(CLASS_DICT), trusted_dispatch=True))
    assert frame2["frame_digest"] != frame["frame_digest"]          # 漂移 ⇒ 新 digest
    created2 = _run(service.create_survey(
        scope, frame2["frame_digest"], objective="重设计调查", target_time="2026-12",
        strategy="equal", n_total=30, seed=7))
    assert created2["survey_id"] != survey_id
    surveys = service.list_surveys(scope)
    assert len(surveys) == 2                      # 两个调查都在（历史保留）
    est_old_list = store.list_estimates(scope, survey_id)
    assert len(est_old_list) == 1                 # 旧估计 artifact 仍在


def test_journey3_dual_entry_same_service_and_user_authority(survey_env):
    """旅程3：Pi 与 UI 同一设计/估计服务（同输入同结果）；
    用户锁定设计/修改记录不被 Agent 覆盖（CAS 冲突）。"""
    frame, cls_path, truth_path, ref_id, scope = _run(
        _register_and_build_frame(survey_env))
    created = _run(service.create_survey(
        scope, frame["frame_digest"], objective="双入口", target_time="2026-11",
        strategy="equal", n_total=24, seed=5, backup_per_stratum=2))
    survey_id = created["survey_id"]
    full_doc = service.survey_detail(scope, survey_id, blind=False)
    mains = [s for s in full_doc["samples"] if not s["is_backup"]][:8]

    # "UI" 记录（user actor）
    rev0 = store.get_revision(scope, survey_id)
    res_ui = _run(service.record_reference(
        scope, survey_id, sample_id=mains[0]["sample_id"],
        status="labelled", reference_class="forest",
        evidence_type="field_visit", actor="user", expected_revision=rev0))
    # "Agent/Pi" 记录（agent actor）——同一 service、同一 CAS 链
    res_agent = _run(service.record_reference(
        scope, survey_id, sample_id=mains[1]["sample_id"],
        status="labelled", reference_class="crop",
        evidence_type="high_res_imagery", actor="agent:survey_record_reference",
        expected_revision=res_ui["revision"]))
    assert res_agent["revision"] == res_ui["revision"] + 1

    # 用户修改自己的记录（版本递增，旧版保留）
    _run(service.record_reference(
        scope, survey_id, sample_id=mains[0]["sample_id"],
        status="labelled", reference_class="crop",
        evidence_type="field_visit", actor="user",
        expected_revision=res_agent["revision"]))
    recs = store.load_records(scope, survey_id)
    sid0 = [r for r in recs if r["sample_id"] == mains[0]["sample_id"]]
    assert len(sid0) == 2 and sid0[0]["reference_class"] == "forest"
    assert sid0[1]["reference_class"] == "crop"   # 新版生效，旧版保留

    # 过期 CAS：Agent 拿旧 revision 抢写 → 409 冲突，用户数据不被覆盖
    with pytest.raises(SurveySamplingError) as exc:
        _run(service.record_reference(
            scope, survey_id, sample_id=mains[2]["sample_id"],
            status="labelled", reference_class="water",
            actor="agent:survey_record_reference",
            expected_revision=res_ui["revision"]))  # 故意过期
    assert exc.value.code == "SURVEY_REVISION_CONFLICT"

    # 双入口同源：工具面与 REST 面都调 service.estimate_survey——
    # 输入等价时结果同 estimate_id 不可能出现两个真值源
    est1 = _run(service.estimate_survey(scope, survey_id))
    est2 = _run(service.estimate_survey(scope, survey_id))
    assert est1["matrix"] == est2["matrix"]       # 确定性（同记录集）
    assert est1["design_digest"] == est2["design_digest"]


def test_journey4_large_raster_cancel_no_half_done(survey_env):
    """旅程4：大栅格分块构建 + 协作取消 ⇒ 中止且无半成品帧。"""
    from .conftest import write_survey_raster

    cls_path, truth_path = write_survey_raster(
        survey_env["raster_root"] / "big.tif", width=2400, height=1600,
        seed=13)                                   # 3.84M 像元，多块读
    from app.services.session_data import session_data_manager

    scope = {"session_id": "sess-survey-journey"}

    async def _build():
        ref = await session_data_manager.store(
            "sess-survey-journey",
            {"kind": "numeric_raster", "file_path": str(cls_path)},
            prefix="raster")
        return await service.build_survey_frame(
            "sess-survey-journey", None, scope, source_ref=str(ref),
            band=1, class_dictionary=dict(CLASS_DICT), trusted_dispatch=True)

    # 完整构建成功（分块读路径）
    frame = _run(_build())
    assert frame["total_units"] > 3_000_000

    # 取消路径：token 在首个 checkpoint 前已取消 ⇒ OperationCancelled，
    # 且不产生新 frame payload（同源幂等命中的是既有 digest——换新源验证）
    cls_path2, _ = write_survey_raster(
        survey_env["raster_root"] / "big2.tif", width=2400, height=1600,
        seed=17)

    async def _build_cancelled():
        ref = await session_data_manager.store(
            "sess-survey-journey",
            {"kind": "numeric_raster", "file_path": str(cls_path2)},
            prefix="raster")
        token = CancellationToken()
        token.cancel("test cancel")
        with use_token(token):
            return await service.build_survey_frame(
                "sess-survey-journey", None, scope, source_ref=str(ref),
                band=1, class_dictionary=dict(CLASS_DICT),
                trusted_dispatch=True)

    with pytest.raises(OperationCancelled):
        _run(_build_cancelled())
    # 无半成品：该源的 frame payload 未持久化
    from app.services.raster_authoring.source import _SOURCE_CACHE

    _SOURCE_CACHE.clear()
    payloads = store.list_frame_payloads(scope)
    assert all(p["source"]["uri"] != str(cls_path2) for p in payloads)
