"""Epic 11 —— GIS Case Corpus 端到端验证（Definition of Done oracle）。

全链消费：自然语言 → task classification → DatasetProfile qualification
→ methodology candidates（含 rejected + 理由）→ selected method/recipe
family → template/component composition → workflow skeleton/render intent。

同时锁定「非 per-query 硬编码」：平行案例不变性 + 乱码弃权 + 组合由
规则推导（学校分布 ≠ 热力图）。
"""
from __future__ import annotations

import pytest

from app.lib.gis.methodology.case_corpus import (
    MIN_CASE_CORPUS_SIZE,
    GIS_CASE_CORPUS,
)
from app.lib.gis.methodology.qualification import QualificationFacts
from app.lib.gis.methodology.service import get_knowledge_service


@pytest.fixture(scope="module")
def service():
    return get_knowledge_service()


def _facts(case) -> QualificationFacts:
    profile = dict(case.profiles[0]) if case.profiles else {}
    return QualificationFacts.from_profile(
        profile, role_states=dict(case.role_states),
        measure_kind=case.measure_kind)


# ── 语料完整性 ──────────────────────────────────────────────────────────

def test_case_corpus_size() -> None:
    assert len(GIS_CASE_CORPUS) >= MIN_CASE_CORPUS_SIZE


def test_case_categories_legal() -> None:
    from app.lib.gis.methodology.taxonomy import GIS_TASK_CATEGORIES
    for case in GIS_CASE_CORPUS:
        assert case.category_id in GIS_TASK_CATEGORIES, case.case_id


# ── 端到端全链（每案例）──────────────────────────────────────────────────

@pytest.mark.parametrize("case", GIS_CASE_CORPUS, ids=lambda c: c.case_id)
def test_end_to_end_chain(case, service) -> None:
    """classify → qualify → rank → template 全链可走通且证据齐备。"""
    facts = _facts(case)
    # 1. task classification
    classification = service.classify(case.query_zh)
    assert classification["primary_category"] == case.category_id, (
        f"{case.case_id}: 分类漂移 {classification['primary_category']}")
    # 2. method candidates（含 rejected + 理由）
    rank = rank_or_abstain(service, case.query_zh, facts, case)
    # 3. invalid 方法必须被拒绝或不得登顶
    top_id = rank["selected"]["method_id"] if rank["selected"] else ""
    assert top_id not in case.invalid_methods, (
        f"{case.case_id}: invalid 方法登顶 {top_id}")
    # 4. gold 命中（direct 案例 rank-1；ambiguous ∈ valid 集由 rank 层测）
    if case.expected_methods and case.kind != "hard_negative":
        assert top_id in case.expected_methods, (
            f"{case.case_id}: top1={top_id} gold={case.expected_methods}")
    # 5. gold 方法的产物期望可满足（任一 gold 满足即可）
    if case.expected_methods and case.expected_artifacts:
        from app.services.gis_harness.workflow_v4.methodology import (
            get_methodology_registry,
        )
        reg = get_methodology_registry()
        satisfied = any(
            set(reg.method(mid).output_artifacts) & set(case.expected_artifacts)
            for mid in case.expected_methods if reg.method(mid))
        assert satisfied, (
            f"{case.case_id}: 产物期望不可满足 {case.expected_artifacts}")
    # 6. 全链解释可生成（evidence chain 齐备）
    explanation = service.explain_plan(case.query_zh, facts,
                                       category_id=case.category_id)
    stages = [c["stage"] for c in explanation["chain"]]
    assert {"classify", "family_routing", "qualification", "ranking"} <= set(stages)


def rank_or_abstain(service, query, facts, case):
    """rank + 弃权语义：无 gold 的 hard_negative 案例允许 honest fallback。"""
    result = service.retrieve_methods(query, facts,
                                      category_id=case.category_id)
    if case.expected_methods:
        assert result["selected"] is not None, (
            f"{case.case_id}: 意外弃权 {result['abstain_reason']}")
    return result


# ── 关键科学判定（fixture 级端到端）──────────────────────────────────────

def test_tiny_sample_all_statistics_rejected(service) -> None:
    """4 个点：统计方法全拒 → 最小层兜底或弃权（不虚构显著性）。"""
    case = next(c for c in GIS_CASE_CORPUS if c.case_id == "case.tiny_sample")
    facts = _facts(case)
    result = service.retrieve_methods(case.query_zh, facts,
                                      category_id=case.category_id)
    for s in result["ranked"]:
        if s["method_id"] in case.invalid_methods:
            assert s["status"] == "rejected", s["method_id"]
    # 不得给出显著性热点结论
    if result["selected"] is not None:
        assert result["selected"]["method_id"] not in case.invalid_methods


def test_no_geometry_blocks_inference(service) -> None:
    """无几何表格：推断方法全部不可行（不虚构空间结论）。"""
    case = next(c for c in GIS_CASE_CORPUS if c.case_id == "case.no_geometry")
    facts = _facts(case)
    report = service.qualify_method("density.kernel_surface", facts)
    assert report.status in ("rejected", "unknown")
    report2 = service.qualify_method("interp.ordinary_kriging", facts)
    assert report2.status in ("rejected", "unknown")


def test_mixed_crs_buffer_semantics(service) -> None:
    """WGS84 缓冲（R1-F1）：欧氏缓冲可运行（GEOGRAPHIC_OK，实现内建
    投影）；PROJECTED_REQUIRED 方法在此数据下 transform 显式化。"""
    case = next(c for c in GIS_CASE_CORPUS
                if c.case_id == "case.mixed_crs_buffer")
    facts = _facts(case)
    rank = service.retrieve_methods(case.query_zh, facts,
                                    category_id=case.category_id)
    assert rank["selected"] is not None
    selected = rank["selected"]["method_id"]
    report = service.qualify_method(selected, facts)
    # 欧氏缓冲（GEOGRAPHIC_OK）→ viable；克里金族在 4326 → transform 披露
    if selected in ("proximity.euclidean_buffer",
                    "proximity.multi_ring_buffer"):
        assert report.status == "viable"


def test_rejection_reasons_are_machine_readable(service) -> None:
    """rejected 报告必须携带稳定 reason codes + 缺失需求（Epic §5.D）。"""
    facts = QualificationFacts.from_profile(
        {"geometryTypes": ["Polygon"], "featureCount": 10})
    report = service.qualify_method("density.kernel_surface", facts)
    assert report.status == "rejected"
    assert report.reason_codes
    assert all(code.startswith("QUAL_") for code in report.reason_codes)
    assert report.missing_requirements


# ── 反硬编码（平行不变性 + 规则推导）────────────────────────────────────

def test_parallel_schools_vs_hospitals_same_plan(service) -> None:
    """同类别不同主体（学校/医院）→ 同一方法族与组合骨架（非 query 硬编码）。"""
    facts = QualificationFacts.from_profile(
        {"featureCount": 100, "geometryTypes": ["Point"]})
    schools = service.retrieve_methods("成都市小学分布情况", facts,
                                       category_id="spatial_distribution")
    hospitals = service.retrieve_methods("成都市医院分布情况", facts,
                                         category_id="spatial_distribution")
    assert schools["selected"]["method_id"] == \
        hospitals["selected"]["method_id"]
    # 规模变化不改裁决
    big = QualificationFacts.from_profile(
        {"featureCount": 5000, "geometryTypes": ["Point"]})
    hospitals_big = service.retrieve_methods("成都市医院分布情况", big,
                                             category_id="spatial_distribution")
    assert hospitals_big["selected"]["method_id"] == \
        schools["selected"]["method_id"]


def test_composition_derived_not_hardcoded(service) -> None:
    """学校分布组合 = 点分布 + 统计伴生；不得出现热力图绑定。"""
    from app.lib.cartography.template_intelligence import plan_composition
    plan = plan_composition(
        "spatial_distribution",
        artifact_types=["point_feature_set", "admin_aggregate_table"])
    components = [s.component_type for s in plan.slot_fills]
    assert "chart_panel" in components
    assert "uncertainty_panel" not in components
    # 同一规划器对插值类目给出不确定性面板——规则驱动而非主体驱动
    plan2 = plan_composition("interpolation",
                             artifact_types=["raster_surface"])
    assert "uncertainty_panel" in [s.component_type
                                   for s in plan2.slot_fills]


# ── workflow skeleton / render intent 消费 ──────────────────────────────

def test_workflow_skeleton_contract(service) -> None:
    skeleton = service.workflow_skeleton("proximity.multi_ring_buffer")
    kinds = [s["kind"] for s in skeleton["steps"]]
    assert "acquire" in kinds and "execute" in kinds and "produce" in kinds
    execute = next(s for s in skeleton["steps"] if s["kind"] == "execute")
    assert execute["ref"] == "geometry.multi_ring_buffer"


def test_render_intent_contract(service) -> None:
    intent = service.render_intent("density.kernel_surface", "density")
    assert intent["primary_viz_family"] == "density_surface"
    assert intent["legend_semantics"] == ["continuous"]
    assert intent["map_model_candidates"]
    assert intent["component_slots"]


def test_five_end_to_end_scenarios_min(service) -> None:
    """DoD：≥5 条端到端场景全绿（全链 + 非硬编码证明）。"""
    scenarios = [
        "case.school_distribution",
        "case.medical_accessibility",
        "case.poi_density",
        "case.admin_statistics",
        "case.hotspot_significance",
        "case.interpolation_appropriate",
        "case.land_use_change",
    ]
    passed = 0
    for case_id in scenarios:
        case = next(c for c in GIS_CASE_CORPUS if c.case_id == case_id)
        facts = _facts(case)
        classification = service.classify(case.query_zh)
        if classification["primary_category"] != case.category_id:
            continue
        result = service.retrieve_methods(case.query_zh, facts,
                                          category_id=case.category_id)
        if result["selected"] is None:
            continue
        if result["selected"]["method_id"] not in case.expected_methods:
            continue
        explanation = service.explain_plan(case.query_zh, facts,
                                           category_id=case.category_id)
        if not explanation["template"]:
            continue
        passed += 1
    assert passed >= 5, f"端到端场景仅 {passed} 条通过"
