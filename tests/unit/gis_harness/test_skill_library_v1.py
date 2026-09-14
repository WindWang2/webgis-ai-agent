"""SkillComposition / Library / Catalog / Retrieval / Evidence 测试
（ADR-0182 S5/S15-S19/S21）。"""
import pytest

from app.services.gis_harness.skills.catalog import SkillCatalog
from app.services.gis_harness.skills.composition import (
    CompositionMember,
    SkillComposition,
)
from app.services.gis_harness.skills.evidence import SkillEvidenceRecorder
from app.services.gis_harness.skills.loader import (
    get_skill_library,
    reset_skill_library,
)
from app.services.gis_harness.skills.retrieval import SkillRetriever


@pytest.fixture(scope="module")
def library():
    return get_skill_library()


class TestComposition:
    def test_core_compositions_load(self, library):
        assert len(library.compositions) >= 4

    def test_execution_order_deterministic(self, library):
        comp = library.composition("composition.distribution_report")
        order1 = comp.execution_order()
        order2 = comp.execution_order()
        assert order1 == order2
        # 依赖先行：validate_spatial_data 在 primary 之前
        assert order1.index("validate_spatial_data") < \
            order1.index("point_distribution_analysis")
        assert order1.index("point_distribution_analysis") < \
            order1.index("distribution_map_design")

    def test_cycle_detection(self):
        comp = SkillComposition(
            composition_id="bad", label_zh="x",
            members=[
                CompositionMember(skill_id="a", role="primary",
                                  depends_on=["b"]),
                CompositionMember(skill_id="b", depends_on=["a"]),
            ],
        )
        violations = comp.validate_composition(lambda s: True)
        assert any("环" in v for v in violations)

    def test_exactly_one_primary(self):
        comp = SkillComposition(
            composition_id="bad2", label_zh="x",
            members=[CompositionMember(skill_id="a")])
        violations = comp.validate_composition(lambda s: True)
        assert any("primary" in v for v in violations)

    def test_dangling_member_reference(self):
        comp = SkillComposition(
            composition_id="bad3", label_zh="x",
            members=[
                CompositionMember(skill_id="a", role="primary"),
                CompositionMember(skill_id="b", depends_on=["ghost"]),
            ])
        violations = comp.validate_composition(lambda s: True)
        assert any("未声明成员" in v for v in violations)

    def test_unknown_role(self):
        comp = SkillComposition(
            composition_id="bad4", label_zh="x",
            members=[
                CompositionMember(skill_id="a", role="chief",
                                  ),
                CompositionMember(skill_id="b", role="primary"),
            ])
        violations = comp.validate_composition(lambda s: True)
        assert any("unknown role" in v for v in violations)


class TestLoaderFailLoud:
    def test_library_loads_clean(self, library):
        assert library.skill_count >= 33
        assert len(library.compositions) >= 4

    def test_singleton_identity(self, library):
        assert get_skill_library() is library

    def test_reset_then_reload(self):
        lib = get_skill_library()
        reset_skill_library()
        lib2 = get_skill_library()
        assert lib is not lib2
        assert lib.fingerprint() == lib2.fingerprint()

    def test_missing_dir_fails_loud(self, tmp_path):
        from app.services.gis_harness.skills.loader import (
            SkillLibraryError,
            load_skill_library,
        )
        with pytest.raises(SkillLibraryError):
            load_skill_library(tmp_path / "nope")

    def test_invalid_skill_fails_loud(self, tmp_path):
        import yaml as _yaml
        from app.services.gis_harness.skills.loader import (
            SkillLibraryError,
            load_skill_library,
        )
        (tmp_path / "core").mkdir()
        (tmp_path / "core" / "bad.yaml").write_text(
            _yaml.dump([{"id": "x", "name": "x"}],
                       allow_unicode=True),
            encoding="utf-8")
        with pytest.raises(SkillLibraryError):
            load_skill_library(tmp_path)


class TestProgressiveDisclosure:
    def test_cards_bounded(self, library):
        catalog = SkillCatalog(library.skills)
        payload = catalog.cards()
        # 预算内全量，或如实标注截断 —— 两者都是合规形态
        assert payload["count"] == payload["total_skills"] or payload["truncated"]
        assert payload["count"] >= 1

    def test_card_minimal_fields(self, library):
        catalog = SkillCatalog(library.skills)
        card = catalog.card("point_distribution_analysis")
        assert card["id"] == "point_distribution_analysis"
        assert "procedure" not in card  # 第一层不含全文

    def test_detail_has_procedure(self, library):
        catalog = SkillCatalog(library.skills)
        detail = catalog.detail("point_distribution_analysis")
        assert "procedure" in detail["skill"]
        assert detail["truncated"] is False

    def test_unknown_skill(self, library):
        catalog = SkillCatalog(library.skills)
        assert "error" in catalog.card("nope")
        assert "error" in catalog.detail("nope")


class TestRetrieval:
    def test_deterministic_retrieval(self, library):
        r1 = SkillRetriever(library.skills).retrieve("小学 分布")
        r2 = SkillRetriever(library.skills).retrieve("小学 分布")
        assert r1 == r2

    def test_zh_and_en(self, library):
        ret = SkillRetriever(library.skills)
        assert ret.retrieve("可达性 分析")[0][0] == "network_accessibility_analysis"
        assert ret.retrieve("choropleth")[0][0] == "choropleth_map_design"

    def test_domain_filter(self, library):
        ret = SkillRetriever(library.skills)
        hits = ret.retrieve("地图 设计", domain="cartography")
        assert hits and all(
            library.get(sid).domain == "cartography" for sid, _ in hits)


class TestEvidence:
    def test_record_and_read(self):
        rec = SkillEvidenceRecorder()
        rec.record_selection("s", "1.0.0", ["kw:分布"], 0.8)
        rec.record_step("s", "step1", ["qc_report"])
        rec.record_skip("s", "step2", "与目标无关")
        rec.record_fallback("s", "insufficient_data", "reduced_output", "披露")
        kinds = rec.evidence_kinds_for("s")
        assert kinds == ["qc_report"]
        assert len(rec.records) == 4

    def test_unknown_event_rejected(self):
        rec = SkillEvidenceRecorder()
        from app.services.gis_harness.skills.evidence import SkillEvidenceRecord
        with pytest.raises(ValueError):
            rec.record(SkillEvidenceRecord(event="thought"))

    def test_bounded_ring(self):
        from app.services.gis_harness.skills.evidence import (
            MAX_RECORDS,
            SkillEvidenceRecord,
        )
        rec = SkillEvidenceRecorder()
        for i in range(MAX_RECORDS + 50):
            rec.record(SkillEvidenceRecord(event="skill_selected",
                                           skill_id=f"s{i}"))
        assert len(rec.records) == MAX_RECORDS
        assert rec.records[0].skill_id == "s50"
