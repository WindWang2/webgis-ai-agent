"""WP2 · 覆盖矩阵 oracle 测试（独立于生产实现的手算期望值）。

科学验收矩阵（GOAL-20261126-02 WP2）：
- 四层分离：声明范围 / 元数据相交 / 实际可核验覆盖 / 要素完备性；
- bbox 相交 ≠ 全覆盖：bbox 全重叠但实际 footprint 有洞 → PARTIAL（手算
  期望比值 0.75）；只有 bbox（无 footprint）→ DECLARED_ONLY，绝不 FULL；
- 实际几何不相交 → NONE；无任何范围证据 → UNKNOWN（未知 ≠ 不存在）；
- 跨日期线：任务 [170,-10,-170,10]（=170..190 环连续）× footprint
  175..185 → 手算比值 0.25；
- 栅格 nodata：几何全覆盖但 nodata=0.5 → 有效覆盖 0.5 → PARTIAL；
- 时间口径：任务 2024 年 × 候选 2010-2015 → temporal/stale_version 缺口；
  候选 2020-2025 包含任务窗 → 通过；
- 属性/粒度：required_fields 缺失 → 逐字段缺口码；分辨率粗于需求 →
  粒度缺口码；
- 科学适用性：display_only 来源 × numeric_stats 需求 → 不得进入数值
  统计资格（map_display 仍可用）；率需求无分母 → 科学前置缺口；
- 完备性诚实：POI 候选面积比 100% 也不得推断记录完备（completeness
  保持 unknown + 显式说明）；
- 许可诚实：redistribution_required 且许可空 → 只披露不伪造授权；
- 排序按适用性（FULL > DECLARED_ONLY > PARTIAL > ...），绝不按价格。
"""

from app.services.task_data_supply.coverage import (
    COVER_DECLARED_ONLY,
    COVER_FULL,
    COVER_NONE,
    COVER_PARTIAL,
    COVER_UNKNOWN,
    CoverageCandidate,
    evaluate_task_coverage,
)
from app.services.task_data_supply.requirement import (
    TaskScope,
    task_requirement,
)

TOL = 0.01


# ── fixture 工厂 ─────────────────────────────────────────────────────────

def _square(x0, y0, x1, y1):
    return {"type": "Polygon", "coordinates": [[
        [x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


def _req(demands=None, scope=None):
    return task_requirement(
        task_scope=scope or TaskScope(
            bbox=[104.0, 30.0, 105.0, 31.0], time_range=[
                "2024-01-01", "2024-12-31"]),
        demands=domands if (domands := demands) is not None else [
            {"role": "subject", "geometry_kind": "point",
             "consumption_methods": ["numeric_stats"],
             "required_fields": ["name"]},
        ],
        provenance=[{"source": "user_message"}],
    )


def _cand(**kw):
    base = dict(
        candidate_id="src/pois", channel="data_fabric_catalog",
        crs="EPSG:4326", serves_roles=("subject",),
        declared_bbox=[104.0, 30.0, 105.0, 31.0],
        temporal_coverage=["2020-01-01", "2025-12-31"],
        fields=("name", "category"), geometry_kind="point",
        role_class="vector_features",
    )
    base.update(kw)
    return CoverageCandidate(**base)


def _row(matrix, role="subject", cid="src/pois"):
    for r in matrix.roles:
        if r.role != role:
            continue
        for e in r.evaluations:
            if e.candidate_id == cid:
                return e
    raise AssertionError(f"no evaluation row role={role} cid={cid}")


# ── 空间覆盖 ─────────────────────────────────────────────────────────────

class TestSpatialCoverage:
    def test_bbox_overlap_with_footprint_hole_is_partial(self):
        # 任务 (0,0)-(1,1)；footprint 全 bbox 但挖掉 (0.5,0.5)-(1,1) 四分之一的洞
        ring = [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]
        hole = [[0.5, 0.5], [1.0, 0.5], [1.0, 1.0], [0.5, 1.0], [0.5, 0.5]]
        footprint = {"type": "Polygon",
                     "coordinates": [ring, hole]}
        ev = _row(evaluate_task_coverage(
            _req(scope=TaskScope(bbox=[0.0, 0.0, 1.0, 1.0])),
            [_cand(declared_bbox=[0.0, 0.0, 1.0, 1.0],
                   footprint_geometry=footprint)]))
        assert ev.verdict == COVER_PARTIAL
        assert ev.spatial_ratio is not None
        assert abs(ev.spatial_ratio - 0.75) < TOL
        assert any("hole" in g["dimension"] or g["dimension"] == "spatial"
                   for g in ev.gaps)

    def test_bbox_overlap_without_footprint_is_declared_only(self):
        ev = _row(evaluate_task_coverage(_req(), [_cand()]))
        assert ev.verdict == COVER_DECLARED_ONLY
        assert ev.spatial_ratio is None
        # bbox 粗筛不得宣称全覆盖
        assert any("未核验" in d or "full" not in d.lower()
                   for d in ev.disclosures)

    def test_actual_disjoint_is_none_not_unknown(self):
        ev = _row(evaluate_task_coverage(
            _req(), [_cand(footprint_geometry=_square(
                110.0, 30.0, 111.0, 31.0))]))
        assert ev.verdict == COVER_NONE

    def test_no_extent_evidence_is_unknown(self):
        ev = _row(evaluate_task_coverage(
            _req(), [_cand(declared_bbox=None)]))
        assert ev.verdict == COVER_UNKNOWN
        # 未知 ≠ 不存在：不产出 not_available 判决
        assert ev.verdict != COVER_NONE

    def test_dateline_task_hand_computed_ratio(self):
        # 任务 [170,-10]..[190,10]（跨线记法 [170,-10,-170,10]）
        scope = TaskScope(bbox=[170.0, -10.0, -170.0, 10.0])
        footprint = _square(175.0, -5.0, 185.0, 5.0)  # 185 → -175 表示
        footprint = {"type": "Polygon", "coordinates": [[
            [175.0, -5.0], [-175.0, -5.0], [-175.0, 5.0],
            [175.0, 5.0], [175.0, -5.0]]]}
        ev = _row(evaluate_task_coverage(
            _req(scope=scope), [_cand(
                declared_bbox=[170.0, -10.0, -170.0, 10.0],
                footprint_geometry=footprint)]))
        assert ev.verdict == COVER_PARTIAL
        assert abs(ev.spatial_ratio - 0.25) < TOL

    def test_full_footprint_is_verifiable_full(self):
        ev = _row(evaluate_task_coverage(
            _req(), [_cand(footprint_geometry=_square(
                103.5, 29.5, 105.5, 31.5))]))
        assert ev.verdict == COVER_FULL
        assert ev.spatial_ratio is not None
        assert ev.spatial_ratio > 0.999

    def test_task_geometry_provided_used_as_task_polygon(self):
        scope = TaskScope(bbox=[104.0, 30.0, 105.0, 31.0],
                          geometry=_square(104.2, 30.2, 104.8, 30.8))
        ev = _row(evaluate_task_coverage(
            _req(scope=scope), [_cand(footprint_geometry=_square(
                104.0, 30.0, 105.0, 31.0))]))
        assert ev.verdict == COVER_FULL

    def test_nodata_reduces_effective_coverage(self):
        ev = _row(evaluate_task_coverage(
            _req(), [_cand(geometry_kind="raster", role_class="numeric_raster",
                           nodata_fraction=0.5,
                           footprint_geometry=_square(
                               104.0, 30.0, 105.0, 31.0))]))
        assert ev.verdict == COVER_PARTIAL
        assert abs(ev.spatial_ratio - 0.5) < TOL
        assert any("nodata" in g.get("detail", "") for g in ev.gaps)


# ── 时间口径 ─────────────────────────────────────────────────────────────

class TestTemporalCoverage:
    def test_period_mismatch_is_temporal_gap(self):
        ev = _row(evaluate_task_coverage(
            _req(), [_cand(temporal_coverage=["2010-01-01", "2015-12-31"])]))
        dims = {g["dimension"] for g in ev.gaps}
        assert "temporal" in dims
        # 不相交 = 该候选对任务时间窗无覆盖（版本/期次不匹配的诚实判定）
        assert ev.temporal_verdict == "none"
        assert any(g.get("code") == "COVERAGE_TEMPORAL_DISJOINT"
                   for g in ev.gaps)

    def test_window_contained_passes(self):
        ev = _row(evaluate_task_coverage(
            _req(), [_cand()]))  # 2020-2025 ⊇ 2024
        assert ev.temporal_verdict == "ok"

    def test_unknown_temporal_disclosed(self):
        ev = _row(evaluate_task_coverage(
            _req(), [_cand(temporal_coverage=None)]))
        assert ev.temporal_verdict == "unknown"
        assert any(g["dimension"] == "temporal" for g in ev.gaps)


# ── 属性 / 粒度 / 科学适用性 ─────────────────────────────────────────────

class TestAttributesAndFitness:
    def test_missing_fields_listed_per_field(self):
        ev = _row(evaluate_task_coverage(
            _req(demands=[{"role": "subject",
                           "consumption_methods": ["numeric_stats"],
                           "required_fields": ["name", "grade", "capacity"]}]),
            [_cand(fields=("name", "category"))]))
        attr_gaps = [g for g in ev.gaps if g["dimension"] == "attribute"]
        assert {g["field"] for g in attr_gaps} == {"grade", "capacity"}

    def test_coarser_resolution_is_granularity_gap(self):
        ev = _row(evaluate_task_coverage(
            _req(demands=[{"role": "subject", "resolution_meters": 10,
                           "consumption_methods": ["numeric_stats"]}]),
            [_cand(resolution_meters=500)]))
        assert any(g["dimension"] == "granularity" for g in ev.gaps)

    def test_display_only_cannot_enter_numeric_stats(self):
        ev = _row(evaluate_task_coverage(
            _req(), [_cand(role_class="display_only",
                           channel="provider_service")]))
        assert ev.numeric_stats_qualified is False
        assert any("展示" in d for d in ev.disclosures)

    def test_display_only_still_usable_for_map_display(self):
        ev = _row(evaluate_task_coverage(
            _req(demands=[{"role": "subject",
                           "consumption_methods": ["map_display"]}]),
            [_cand(role_class="display_only")]))
        assert ev.fit_for_methods.get("map_display") is True
        assert ev.fit_for_methods.get("numeric_stats") is False

    def test_rate_without_denominator_is_scientific_gap(self):
        matrix = evaluate_task_coverage(
            _req(demands=[{"role": "subject",
                           "consumption_methods": ["rate"],
                           "needs_denominator": True}]),
            [_cand()])
        ev = _row(matrix)
        assert any(g["dimension"] == "denominator" for g in ev.gaps)

    def test_license_missing_disclosed_not_fabricated(self):
        ev = _row(evaluate_task_coverage(
            _req(demands=[{"role": "subject",
                           "consumption_methods": ["numeric_stats"],
                           "license_redistribution_required": True}]),
            [_cand(license="")]))
        assert any("许可" in d and "未声明" in d for d in ev.disclosures)
        assert ev.license_disclosed is True


# ── 完备性诚实与矩阵行为 ─────────────────────────────────────────────────

class TestCompletenessAndMatrix:
    def test_area_ratio_never_implies_poi_completeness(self):
        ev = _row(evaluate_task_coverage(
            _req(), [_cand(feature_count=42,
                           footprint_geometry=_square(
                               104.0, 30.0, 105.0, 31.0))]))
        assert ev.completeness == "unknown"
        assert any("完整" in d for d in ev.disclosures)

    def test_explicit_completeness_evidence_honored(self):
        ev = _row(evaluate_task_coverage(
            _req(), [_cand(completeness_evidence=
                           "来源方声明：收录全部注册学校（2024 名册）")]))
        assert ev.completeness == "declared"

    def test_ranking_by_applicability_not_price(self):
        matrix = evaluate_task_coverage(
            _req(), [
                _cand(candidate_id="cheap/partial",
                      footprint_geometry=_square(104.0, 30.0, 104.5, 31.0)),
                _cand(candidate_id="pricier/full",
                      footprint_geometry=_square(
                          103.9, 29.9, 105.1, 31.1)),
            ])
        role = matrix.roles[0]
        assert role.evaluations[0].candidate_id == "pricier/full"
        assert role.evaluations[0].verdict == COVER_FULL

    def test_candidate_not_serving_role_ignored(self):
        matrix = evaluate_task_coverage(
            _req(), [_cand(serves_roles=("boundary",))])
        assert matrix.roles[0].evaluations == []
        assert matrix.roles[0].unknown_reasons  # 有解释，不静默

    def test_bounded_output(self):
        cands = [_cand(candidate_id=f"src/pois-{i}") for i in range(20)]
        bounded = evaluate_task_coverage(_req(), cands).to_bounded_dict()
        assert len(bounded["roles"][0]["evaluations"]) <= 7

    def test_closed_verdict_vocabulary(self):
        assert {COVER_FULL, COVER_PARTIAL, COVER_DECLARED_ONLY,
                COVER_NONE, COVER_UNKNOWN} == {
            "full", "partial", "declared_only", "none", "unknown"}
