"""paper_generalization 服务测试（WP3）—— 真实引擎种子，无 mock。

链路：UpsertLayer(inlineData) 播种 → inspect（尺度证据）→ preview
（零突变）→ apply commit（新 ref 工件 + paper_plans 冻结 + 窗口裁剪
插入 + 回执幂等）→ QA fail-closed（脏数据真实触发）→ CAS superseded。
"""
from __future__ import annotations

import json
import uuid

import pytest

from app.lib.cartography.multiscale.publication_compile import (
    upsert_representation_window,
)
from app.services.gis_harness.paper_generalization import (
    PaperScaleError,
    apply_paper_scale_generalization,
    inspect_paper_scale_generalization,
    preview_paper_scale_generalization,
)
from app.services.mapspec.lifecycle_engine import MapSpecLifecycleEngine
from app.services.mapspec.store import mapspec_store_instance
from app.services.session_data import session_data_manager


def _districts_fc() -> dict:
    """两个共享完整边界的 1° 行政区 + 一个 <0.5km² 小县（应被消除）。"""
    def sq(x0, y0, x1, y1, fid):
        ring = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]
        return {"type": "Feature", "id": fid,
                "geometry": {"type": "Polygon", "coordinates": [ring]},
                "properties": {"name": fid}}

    return {"type": "FeatureCollection", "features": [
        sq(115.0, 38.0, 116.0, 39.0, "district-a"),
        sq(116.0, 38.0, 117.0, 39.0, "district-b"),
        sq(116.0, 39.0, 116.004, 39.004, "tiny-county"),
    ]}


async def _seed(session_id: str, fc: dict | None = None) -> None:
    from app.services.mapspec.intents import UpsertLayerIntent

    engine = MapSpecLifecycleEngine()
    res = await engine.apply_mutation(
        session_id,
        UpsertLayerIntent(
            layer={"id": "districts", "type": "fill",
                   "source": "src-districts",
                   "name": "行政区", "paint": {"fill-color": "#4a90d9"}},
            source_data={"type": "geojson",
                         "inlineData": fc or _districts_fc()},
        ),
    )
    assert not res.is_error, res.error_msg


@pytest.fixture
async def session():
    sid = f"pgen-{uuid.uuid4().hex[:10]}"
    await _seed(sid)
    yield sid
    session_data_manager._store.pop(sid, None)
    session_data_manager._map_state.pop(sid, None)
    await mapspec_store_instance.discard_mapspec(sid)


async def _revision(sid: str):
    return (await session_data_manager.get_map_state(sid) or {}).get(
        "_cartographic_mutation_revision")


class TestInspect:
    async def test_scale_evidence(self, session):
        report = await inspect_paper_scale_generalization(
            session, "districts", denominator_text="1:50万",
            page_text="A4横版", purpose="overview")
        assert report["status"] == "ok"
        assert report["geometry_family"] == "polygon"
        assert report["supported_family"] is True
        assert report["target_denominator"] == 500_000
        assert report["page"]["format_id"] == "A4"
        assert report["page"]["orientation"] == "landscape"
        # 等效 zoom 锚定契约 page.dpi=300（评审 R2-N5 与 apply/QA 同口径）：
        # 1:50万@300dpi → 42.3 m/px → z≈10.5（96dpi 口径下约为 8.86 ——
        # 差 log2(300/96)≈1.64 为 dpi 语义差，非缺陷）
        z = report["equivalent_display_zoom"]
        assert z is not None and 9.0 < z < 12.0
        assert report["scale_variation"]["evaluated"] is True
        assert report["source"]["feature_count"] == 3
        assert report["existing_paper_plans"] == []

    async def test_bad_denominator_text_rejected(self, session):
        with pytest.raises(PaperScaleError) as exc:
            await inspect_paper_scale_generalization(
                session, "districts", denominator_text="很多万")
        assert "无法解析比例尺" in str(exc.value)

    async def test_bad_purpose_rejected(self, session):
        with pytest.raises(PaperScaleError) as exc:
            await inspect_paper_scale_generalization(
                session, "districts", denominator=500_000, purpose="art")
        assert "未知用途" in str(exc.value)


class TestPreview:
    async def test_zero_mutation_with_pipeline_and_qa(self, session):
        spec_before = await mapspec_store_instance.get_mapspec(session)
        rev_before = await _revision(session)
        preview = await preview_paper_scale_generalization(
            session, "districts", denominator=500_000,
            page_text="A4横版", purpose="overview")
        assert preview["status"] == "preview"
        steps = [p["step"] for p in preview["pipeline"]]
        assert steps == ["eliminate_small_polygons", "topology_simplify"]
        assert preview["stats"]["original_count"] == 3
        assert preview["stats"]["artifact_count"] == 2
        assert preview["qa_summary"]["blocking"] == 0
        spec_after = await mapspec_store_instance.get_mapspec(session)
        assert spec_after == spec_before
        assert await _revision(session) == rev_before


class TestApplyCommit:
    async def test_commit_freezes_plan_window_and_artifact(self, session):
        rev_before = await _revision(session)
        receipt = await apply_paper_scale_generalization(
            session, "districts", mode="commit", denominator=500_000,
            page_text="A4横版", purpose="overview",
            operation_id="op-pgen-1", note="1:50万总览")
        assert receipt["status"] == "committed", receipt
        assert receipt["artifact_ref"].startswith("ref:")
        assert receipt["source_untouched"].startswith(("ref:", "inline:"))
        assert receipt["mapspec_revision"] > rev_before
        assert receipt["export_window"]["mode"] == "generalized"
        assert "source" in receipt["export_window"]

        spec = await mapspec_store_instance.get_mapspec(session)
        layer = next(lyr for lyr in spec["layers"] if lyr["id"] == "districts")
        # 冻结计划落 paper_plans[fingerprint]
        plans = layer["scale_profile"]["paper_plans"]
        assert len(plans) == 1
        plan = next(iter(plans.values()))
        assert plan["status"] == "frozen"
        assert plan["artifact_ref"] == receipt["artifact_ref"]
        assert plan["contract"]["target_denominator"] == 500_000
        assert plan["stats"]["classification_basis"] == "original_source"
        # 表达窗口：纸面窗在场且带 source 覆盖；既有窗口语义跨度外不变
        windows = layer["representation"]["windows"]
        paper_wins = [w for w in windows
                      if w.get("reason_codes") == ["PSC.PAPER_FROZEN"]]
        assert len(paper_wins) == 1
        pw = paper_wins[0]
        assert pw["source"] == receipt["artifact_source_id"]
        # 纸面窗 ±0.5 跨度外不得存在被吞掉的窗口（裁剪语义）
        others = [w for w in windows
                  if w.get("reason_codes") != ["PSC.PAPER_FROZEN"]]
        for w in others:
            overlap = not (float(w["max_zoom"]) <= float(pw["min_zoom"])
                           or float(w["min_zoom"]) >= float(pw["max_zoom"]))
            assert not overlap, "既有窗口必须被精确裁剪不与纸面窗重叠"

        # 原始数据字节不动
        src = spec["sources"]["src-districts"]
        assert src.get("inlineData") is not None or src.get("ref_id")

    async def test_operation_id_replay_idempotent(self, session):
        first = await apply_paper_scale_generalization(
            session, "districts", mode="commit", denominator=500_000,
            operation_id="op-pgen-2")
        assert first["status"] == "committed"
        replay = await apply_paper_scale_generalization(
            session, "districts", mode="commit", denominator=500_000,
            operation_id="op-pgen-2")
        assert replay["replayed"] is True
        assert replay["status"] == "committed"
        spec = await mapspec_store_instance.get_mapspec(session)
        layer = next(lyr for lyr in spec["layers"] if lyr["id"] == "districts")
        assert len(layer["scale_profile"]["paper_plans"]) == 1

    async def test_qa_blocked_on_dirty_input(self, session):
        # 真实触发 QA 门：重叠面输入 → seam 审计 fail → 拒绝冻结
        dirty = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "id": "a",
             "geometry": {"type": "Polygon", "coordinates": [
                 [[115, 38], [116.2, 38], [116.2, 39], [115, 39], [115, 38]]]},
             "properties": {}},
            {"type": "Feature", "id": "b",
             "geometry": {"type": "Polygon", "coordinates": [
                 [[116, 38], [117, 38], [117, 39], [116, 39], [116, 38]]]},
             "properties": {}},
        ]}
        sid = f"pgen-dirty-{uuid.uuid4().hex[:8]}"
        await _seed(sid, dirty)
        try:
            receipt = await apply_paper_scale_generalization(
                sid, "districts", mode="commit", denominator=500_000)
            assert receipt["status"] == "qa_blocked", receipt["status"]
            assert receipt["qa_summary"]["blocking"] >= 1
            assert "拒绝冻结" in receipt["error"]
        finally:
            session_data_manager._store.pop(sid, None)
            session_data_manager._map_state.pop(sid, None)
            await mapspec_store_instance.discard_mapspec(sid)

    async def test_superseded_returns_partial(self, session):
        receipt = await apply_paper_scale_generalization(
            session, "districts", mode="commit", denominator=500_000,
            operation_id="op-pgen-3", expected_revision=999_999)
        assert receipt["status"] == "partial"
        # engine superseded/错误消息以实际消息为准（revision 已推进）
        assert receipt.get("error")


class TestMultiScaleJourneys:
    async def test_scale_change_keeps_compatible_plan_and_supersedes_overlap(
            self, session):
        # 旅程 4 缩小版：1:50万 → 1:100万（等效 zoom 相邻不重叠 → 两计划
        # 共存）；旧结果不得覆盖新 revision（CAS），新窗语义以最新为准。
        first = await apply_paper_scale_generalization(
            session, "districts", mode="commit", denominator=500_000,
            page_text="A4横版", purpose="overview", operation_id="op-j4a")
        assert first["status"] == "committed"
        second = await apply_paper_scale_generalization(
            session, "districts", mode="commit", denominator=1_000_000,
            page_text="A4横版", purpose="reference", operation_id="op-j4b")
        assert second["status"] == "committed"
        assert second["mapspec_revision"] > first["mapspec_revision"]
        spec = await mapspec_store_instance.get_mapspec(session)
        layer = next(lyr for lyr in spec["layers"]
                     if lyr["id"] == "districts")
        plans = layer["scale_profile"]["paper_plans"]
        assert len(plans) == 2, "相邻尺度计划共存"
        windows = layer["representation"]["windows"]
        paper_wins = [w for w in windows
                      if w.get("reason_codes") == ["PSC.PAPER_FROZEN"]]
        assert len(paper_wins) == 2
        # 全域覆盖未被破坏（窗外 = 原始数据语义可判定）
        from app.lib.cartography.multiscale.publication_compile import (
            windows_cover_domain,
        )
        assert windows_cover_domain(windows)

    async def test_overlapping_scale_supersedes_old_plan(self, session):
        # 两个等效 zoom 重叠的尺度（1:40万 vs 1:50万 跨度重叠）→ 新冻结
        # 裁掉旧窗 → 旧计划移除且回执披露（计划集 ↔ 窗口恒一致）
        first = await apply_paper_scale_generalization(
            session, "districts", mode="commit", denominator=500_000,
            operation_id="op-j5a")
        assert first["status"] == "committed"
        second = await apply_paper_scale_generalization(
            session, "districts", mode="commit", denominator=400_000,
            operation_id="op-j5b")
        assert second["status"] == "committed"
        spec = await mapspec_store_instance.get_mapspec(session)
        layer = next(lyr for lyr in spec["layers"]
                     if lyr["id"] == "districts")
        plans = layer["scale_profile"]["paper_plans"]
        assert len(plans) == 1, "重叠尺度旧计划被取代移除"
        assert first["plan_fingerprint"] not in plans
        assert second["superseded_plans"] == [first["plan_fingerprint"]]
        # 语义检查不因陈旧计划失败
        from app.lib.cartography.semantic_checks import (
            evaluate_cartography_semantics,
        )
        report = evaluate_cartography_semantics(spec)
        bad = [c for c in report.checks
               if c.rule == "carto.paper_scale_plan"
               and c.status == "fail"]
        assert bad == [], [c.message for c in bad]


class TestHardConstraintEndToEnd:
    async def test_keep_feature_ids_survives_elimination_via_service(
            self, session):
        # 评审 R1-P1-3 回归：用户面「别丢掉小县」经工具入参真实到达引擎
        receipt = await apply_paper_scale_generalization(
            session, "districts",
            mode="commit", denominator=500_000,
            keep_feature_ids=["tiny-county"],
            operation_id="op-hc-1")
        assert receipt["status"] == "committed"
        spec = await mapspec_store_instance.get_mapspec(session)
        layer = next(lyr for lyr in spec["layers"]
                     if lyr["id"] == "districts")
        plan = next(iter(layer["scale_profile"]["paper_plans"].values()))
        assert plan["stats"]["artifact_count"] == 3, \
            "硬约束要素不得被消除"
        hc = plan["contract"]["hard_constraints"]
        assert any(c["kind"] == "keep_feature_ids"
                   and "tiny-county" in c["feature_ids"] for c in hc)

    async def test_keep_values_requires_field(self, session):
        from app.services.gis_harness.paper_generalization import (
            PaperScaleError,
        )
        with pytest.raises(PaperScaleError) as exc:
            await apply_paper_scale_generalization(
                session, "districts", mode="commit",
                denominator=500_000, keep_values=["国道"])
        assert "keep_values_field" in str(exc.value)


class TestResumeAndRegionEndToEnd:
    async def test_partial_resume_reuses_artifact_ref(self, session):
        # 评审 R2-N6：superseded partial → 同 op_id 重放（无 stale revision）
        # 从断点续链，工件 ref 复用（不产孤儿 ref）。
        first = await apply_paper_scale_generalization(
            session, "districts", mode="commit", denominator=500_000,
            operation_id="op-res-1", expected_revision=999_999)
        assert first["status"] == "partial"
        stale_ref = first["artifact_ref"]
        second = await apply_paper_scale_generalization(
            session, "districts", mode="commit", denominator=500_000,
            operation_id="op-res-1")
        assert second["status"] == "committed"
        assert second["artifact_ref"] == stale_ref, \
            "断点续链必须复用已存工件 ref（不产孤儿）"
        spec = await mapspec_store_instance.get_mapspec(session)
        layer = next(lyr for lyr in spec["layers"]
                     if lyr["id"] == "districts")
        assert len(layer["scale_profile"]["paper_plans"]) == 1
        # 工件源真实在 spec.sources（无悬空）
        src_id = second["artifact_source_id"]
        assert isinstance(spec["sources"].get(src_id), dict)

    async def test_keep_region_service_e2e(self, session):
        # 评审 R2-N6：keep_region 硬约束经服务面保护区域内小县
        receipt = await apply_paper_scale_generalization(
            session, "districts", mode="commit", denominator=500_000,
            keep_region=[116.0, 39.0, 116.004, 39.004],
            operation_id="op-region-1")
        assert receipt["status"] == "committed"
        spec = await mapspec_store_instance.get_mapspec(session)
        layer = next(lyr for lyr in spec["layers"]
                     if lyr["id"] == "districts")
        plan = next(iter(layer["scale_profile"]["paper_plans"].values()))
        assert plan["stats"]["artifact_count"] == 3, \
            "区域内的要素不得被消除"


class TestWindowUpsertPure:
    def test_clip_and_split(self):
        windows = [
            {"min_zoom": 0, "max_zoom": 8, "mode": "raw"},
            {"min_zoom": 8, "max_zoom": 24, "mode": "generalized",
             "source": "gen1"},
        ]
        out = upsert_representation_window(
            windows, {"min_zoom": 7, "max_zoom": 9, "mode": "generalized",
                      "source": "paper1"})
        assert out == [
            {"min_zoom": 0, "max_zoom": 7, "mode": "raw"},
            {"min_zoom": 7, "max_zoom": 9, "mode": "generalized",
             "source": "paper1"},
            {"min_zoom": 9, "max_zoom": 24, "mode": "generalized",
             "source": "gen1"},
        ]

    def test_invalid_window_passthrough(self):
        windows = [{"min_zoom": 0, "max_zoom": 8, "mode": "raw"}]
        out = upsert_representation_window(
            windows, {"min_zoom": 9, "max_zoom": 3, "mode": "generalized"})
        assert out == windows   # 非法跨度：原样返回（不猜）
        out2 = upsert_representation_window(windows, {"min_zoom": 5})
        assert out2 == windows  # 缺字段：原样返回

    def test_input_not_mutated(self):
        windows = [{"min_zoom": 0, "max_zoom": 8, "mode": "raw"}]
        snapshot = json.dumps(windows)
        upsert_representation_window(
            windows, {"min_zoom": 2, "max_zoom": 4, "mode": "raw"})
        assert json.dumps(windows) == snapshot
