"""
Unit tests for Domain Rule Pack and RAG Grounding Integration.
Tests Rule Pack versioning, registry lookups, applicability matching, RAG fallback, and evidence chain composition.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.spatial_decision.models import DomainRule, EvidenceItem
from app.services.spatial_decision.rule_pack import (
    DomainRulePackRegistry,
    get_rule_pack_registry,
    create_evidence_item,
    retrieve_evidence_from_rag,
    build_evidence_chain,
)


class TestDomainRulePackCoverage:
    """Tests that versioned domain rule packs cover all 5 required domains and rules."""

    def test_registry_initialization_contains_five_domains(self):
        registry = DomainRulePackRegistry()
        all_rules = registry.get_all_rules()
        assert len(all_rules) > 0

        domains = {r.domain for r in all_rules}
        expected_domains = {
            "urban_planning",
            "site_selection",
            "transportation",
            "environment",
            "natural_resources",
        }
        assert expected_domains.issubset(domains)

    def test_urban_planning_rules(self):
        registry = get_rule_pack_registry()
        rules = registry.get_rules_by_domain("urban_planning")
        rule_names = [r.name.lower() for r in rules]
        statements = [r.statement.lower() for r in rules]
        combined = " ".join(rule_names + statements)

        assert "school" in combined
        assert "hospital" in combined
        assert "park" in combined
        assert "15-min" in combined or "life circle" in combined or "15分钟" in combined
        assert "green" in combined or "绿地" in combined or "vegetation" in combined

    def test_site_selection_rules(self):
        registry = get_rule_pack_registry()
        rules = registry.get_rules_by_domain("site_selection")
        combined = " ".join([r.name.lower() + " " + r.statement.lower() for r in rules])

        assert "commercial" in combined or "商业" in combined
        assert "competition" in combined or "saturation" in combined or "竞品" in combined
        assert "school" in combined or "学校" in combined
        assert "entertainment" in combined or "娱乐" in combined

    def test_transportation_rules(self):
        registry = get_rule_pack_registry()
        rules = registry.get_rules_by_domain("transportation")
        combined = " ".join([r.name.lower() + " " + r.statement.lower() for r in rules])

        assert "subway" in combined or "地铁" in combined
        assert "saturation" in combined or "road" in combined or "饱和度" in combined
        assert "traffic restriction" in combined or "限行" in combined or "reduction" in combined
        assert "commute" in combined or "通联" in combined or "通勤" in combined

    def test_environment_rules(self):
        registry = get_rule_pack_registry()
        rules = registry.get_rules_by_domain("environment")
        combined = " ".join([r.name.lower() + " " + r.statement.lower() for r in rules])

        assert "flood" in combined or "防洪" in combined or "淹没" in combined
        assert "typhoon" in combined or "台风" in combined
        assert "aqi" in combined or "空气质量" in combined

    def test_natural_resources_rules(self):
        registry = get_rule_pack_registry()
        rules = registry.get_rules_by_domain("natural_resources")
        combined = " ".join([r.name.lower() + " " + r.statement.lower() for r in rules])

        assert "land use" in combined or "用地" in combined or "红线" in combined
        assert "vegetation" in combined or "ndvi" in combined or "植被" in combined
        assert "eco" in combined or "生态" in combined or "protection" in combined

    def test_rules_are_versioned(self):
        registry = get_rule_pack_registry()
        for rule in registry.get_all_rules():
            assert rule.version is not None
            assert rule.version.startswith("v")


class TestDomainRulePackRegistryLookup:
    """Tests rule lookup by domain, scenario, keyword search, and applicability matching."""

    def test_get_rules_by_scenario(self):
        registry = get_rule_pack_registry()
        subway_rules = registry.get_rules_by_scenario("subway")
        assert len(subway_rules) > 0
        assert any("subway" in r.id.lower() or "subway" in r.name.lower() or "地铁" in r.name for r in subway_rules)

        school_rules = registry.get_rules_by_scenario("school")
        assert len(school_rules) > 0

    def test_search_rules(self):
        registry = get_rule_pack_registry()
        matches = registry.search_rules("flood")
        assert len(matches) > 0
        assert any(r.domain == "environment" for r in matches)

        matches_zh = registry.search_rules("绿地")
        assert len(matches_zh) > 0

    def test_match_applicable_rules(self):
        registry = get_rule_pack_registry()
        # Custom rule with specific conditions
        rule = DomainRule(
            id="test_rule_001",
            domain="urban_planning",
            name="Test Radius Rule",
            statement="Service radius within 500m",
            applicability_conditions={"distance_m": {"max": 500}, "target_category": "school"},
            parameters={"radius_m": 500},
            version="v2.0",
        )
        registry.register_rule(rule)

        # Context matching condition
        ctx_match = {"distance_m": 300, "target_category": "school"}
        matched = registry.match_applicable_rules(ctx_match, domain="urban_planning")
        assert rule in matched

        # Context failing distance condition
        ctx_fail_dist = {"distance_m": 800, "target_category": "school"}
        matched_fail = registry.match_applicable_rules(ctx_fail_dist, domain="urban_planning")
        assert rule not in matched_fail


class TestGroundingAndEvidenceChain:
    """Tests Grounding, RAG integration, Evidence classification, and RAG fallback."""

    def test_create_evidence_item_types(self):
        types = ["observed_fact", "computed_fact", "retrieved_rule", "assumption", "inference"]
        for t in types:
            ev = create_evidence_item(
                id=f"ev_{t}",
                type=t,
                domain="urban_planning",
                statement=f"Sample statement for {t}",
                source="unit_test",
                confidence=0.95,
            )
            assert isinstance(ev, EvidenceItem)
            assert ev.type == t

    @pytest.mark.asyncio
    async def test_retrieve_evidence_from_rag(self):
        mock_engine = MagicMock()
        mock_engine.search = AsyncMock(return_value=[
            {
                "id": "chk_101",
                "title": "Urban Planning Guidelines 2025",
                "content": "Primary schools should be located within 500 meters walking distance.",
                "score": 0.88,
                "document_id": "doc_001",
            }
        ])

        evidence_items = await retrieve_evidence_from_rag(
            query="school service radius",
            domain="urban_planning",
            knowledge_engine=mock_engine,
        )

        assert len(evidence_items) == 1
        ev = evidence_items[0]
        assert ev.type in ("retrieved_rule", "observed_fact")
        assert "Primary schools" in ev.statement
        assert ev.domain == "urban_planning"
        assert ev.source == "Urban Planning Guidelines 2025"

    @pytest.mark.asyncio
    async def test_build_evidence_chain_composition(self):
        mock_engine = MagicMock()
        mock_engine.search = AsyncMock(return_value=[
            {
                "id": "chk_201",
                "title": "Traffic Standard GB50180",
                "content": "Subway coverage target: 80% coverage within 800m.",
                "score": 0.90,
                "document_id": "doc_002",
            }
        ])

        observed_facts = [
            create_evidence_item("ev_obs_1", "observed_fact", "transportation", "Current subway distance is 450m", "GIS_Measurement")
        ]
        assumptions = [
            create_evidence_item("ev_asm_1", "assumption", "transportation", "Assuming peak hour commuter volume of 10,000/h", "User_Scenario")
        ]

        chain = await build_evidence_chain(
            query="subway access",
            scenario_type="subway",
            domain="transportation",
            context_params={"distance_m": 450},
            observed_facts=observed_facts,
            assumptions=assumptions,
            knowledge_engine=mock_engine,
        )

        assert len(chain) >= 3
        types_in_chain = {ev.type for ev in chain}
        assert "observed_fact" in types_in_chain
        assert "assumption" in types_in_chain
        assert "retrieved_rule" in types_in_chain

    @pytest.mark.asyncio
    async def test_rag_fallback_when_rag_fails(self):
        mock_engine = MagicMock()
        mock_engine.search = AsyncMock(side_effect=Exception("RAG Vector Index connection failed"))

        chain = await build_evidence_chain(
            query="flood risk level",
            scenario_type="flood",
            domain="environment",
            context_params={"elevation_m": 3.5},
            knowledge_engine=mock_engine,
        )

        # RAG failed but fallback returns built-in matched domain rules cleanly
        assert isinstance(chain, list)
        assert len(chain) > 0
        assert any(ev.domain == "environment" for ev in chain)
