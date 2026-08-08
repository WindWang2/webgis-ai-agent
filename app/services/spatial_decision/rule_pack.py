"""
Domain Rule Packs & RAG Grounding Integration for Spatial Decision Intelligence V2.
Provides versioned domain rules, registry, applicability matching, RAG search, and evidence chain composition.
"""
import asyncio
import inspect
import logging
import uuid
from typing import Any, Dict, List, Optional

from app.services.spatial_decision.models import DomainRule, EvidenceItem
from app.services.rag.engine import TenantContext, KnowledgeEngine, get_knowledge_engine

logger = logging.getLogger(__name__)


def create_evidence_item(
    id: str,
    type: str,
    domain: str,
    statement: str,
    source: str,
    confidence: float = 1.0,
    parameters: Optional[Dict[str, Any]] = None,
) -> EvidenceItem:
    """
    Factory helper to construct structured EvidenceItem objects.
    Validates type classification: observed_fact, computed_fact, retrieved_rule, assumption, inference.
    """
    valid_types = {"observed_fact", "computed_fact", "retrieved_rule", "assumption", "inference"}
    if type not in valid_types:
        logger.warning(f"Invalid EvidenceItem type '{type}', defaulting to 'observed_fact'")
        type = "observed_fact"

    return EvidenceItem(
        id=id or f"ev_{uuid.uuid4().hex[:8]}",
        type=type,  # type: ignore
        domain=domain,
        statement=statement,
        source=source,
        confidence=min(max(float(confidence), 0.0), 1.0),
        parameters=parameters or {},
    )


def _build_default_domain_rules() -> List[DomainRule]:
    """Construct default versioned domain rules across 5 spatial intelligence domains."""
    rules = [
        # --- 1. Urban Planning ---
        DomainRule(
            id="rule_up_school_001",
            domain="urban_planning",
            name="Primary School Service Radius Rule (中小学服务半径规则)",
            statement="Primary schools should serve residential communities within a 500m walking radius (中小学 500m 服务半径 / 10-minute walk).",
            applicability_conditions={"target_category": ["school", "primary_school", "education"], "distance_m": {"max": 500}},
            parameters={"service_radius_m": 500, "ideal_walk_time_min": 10},
            source="domain_rule_pack",
            version="v2.0",
            confidence=0.95,
            jurisdiction="National Urban Planning Standard GB50180",
        ),
        DomainRule(
            id="rule_up_hospital_001",
            domain="urban_planning",
            name="Hospital Service Radius Rule (医院服务半径规则)",
            statement="General hospitals should maintain a primary service radius of 1500m to 3000m (医院 1500m-3000m 服务半径).",
            applicability_conditions={"target_category": ["hospital", "medical", "healthcare"], "distance_m": {"max": 3000}},
            parameters={"service_radius_min_m": 1500, "service_radius_max_m": 3000},
            source="domain_rule_pack",
            version="v2.0",
            confidence=0.90,
            jurisdiction="Municipal Health & Spatial Plan",
        ),
        DomainRule(
            id="rule_up_park_001",
            domain="urban_planning",
            name="Park & Green Space Radius Rule (公园绿地服务半径规则)",
            statement="Pocket parks and community green space (公园绿地) should provide service coverage within a 500m radius (500m 公园服务圈).",
            applicability_conditions={"target_category": ["park", "green_space", "recreation"], "distance_m": {"max": 500}},
            parameters={"service_radius_m": 500, "min_park_area_m2": 2000},
            source="domain_rule_pack",
            version="v2.0",
            confidence=0.92,
            jurisdiction="National Ecological City Guidelines",
        ),
        DomainRule(
            id="rule_up_15min_circle_001",
            domain="urban_planning",
            name="15-Minute Life Circle Accessibility Rule (15分钟生活圈规则)",
            statement="Essential commercial, educational, and medical services must be reachable within a 15-minute walking radius (15分钟生活圈 / 800-1000m).",
            applicability_conditions={"scenario_type": ["15min_life_circle", "life_circle", "community"], "distance_m": {"max": 1000}},
            parameters={"life_circle_radius_m": 1000, "max_walk_time_min": 15},
            source="domain_rule_pack",
            version="v2.0",
            confidence=0.95,
            jurisdiction="Ministry of Housing and Urban-Rural Development",
        ),
        DomainRule(
            id="rule_up_green_coverage_001",
            domain="urban_planning",
            name="Green Space Coverage Ratio Rule (绿地率覆盖率规则)",
            statement="Urban residential development areas must maintain a green space coverage ratio (绿地率) of at least 35%.",
            applicability_conditions={"metric": ["green_coverage", "vegetation_ratio"], "green_coverage_pct": {"min": 35}},
            parameters={"min_green_space_ratio": 0.35},
            source="domain_rule_pack",
            version="v2.0",
            confidence=0.90,
            jurisdiction="Urban Greening Regulations",
        ),

        # --- 2. Site Selection ---
        DomainRule(
            id="rule_ss_commercial_001",
            domain="site_selection",
            name="Commercial Catchment Radius Rule (商业辐射半径规则)",
            statement="Core retail sites rely on a primary customer catchment zone of 1000m to 3000m radius (商业辐射圈).",
            applicability_conditions={"target_category": ["commercial", "retail", "shopping"], "distance_m": {"max": 3000}},
            parameters={"catchment_radius_m": 3000, "core_catchment_m": 1000},
            source="domain_rule_pack",
            version="v2.0",
            confidence=0.88,
            jurisdiction="Commercial Real Estate Standards",
        ),
        DomainRule(
            id="rule_ss_competition_001",
            domain="site_selection",
            name="Competition Saturation Rule (竞品饱和度规则)",
            statement="High competition saturation (more than 3 competing stores in 1km / 竞品饱和度) reduces site viability score.",
            applicability_conditions={"target_category": ["commercial", "retail"], "competing_stores": {"max": 3}},
            parameters={"max_competing_stores_per_km2": 3, "saturation_penalty": 0.25},
            source="domain_rule_pack",
            version="v2.0",
            confidence=0.85,
            jurisdiction="Retail Economics Guidelines",
        ),
        DomainRule(
            id="rule_ss_school_distance_001",
            domain="site_selection",
            name="School Setback Distance Constraint Rule (学校避让距离约束规则)",
            statement="Gaming, cybercafes, and noise-heavy entertainment facilities must maintain a setback distance of over 200m from schools (学校周边200米避让距离).",
            applicability_conditions={"target_category": ["entertainment", "gaming", "cybercafe"], "school_distance_m": {"min": 200}},
            parameters={"min_school_distance_m": 200},
            source="domain_rule_pack",
            version="v2.0",
            confidence=0.98,
            jurisdiction="Protection of Minors Law & Urban Zoning",
        ),
        DomainRule(
            id="rule_ss_entertainment_001",
            domain="site_selection",
            name="Entertainment Venue Distance Constraint Rule (娱乐场所避让约束规则)",
            statement="Nighttime entertainment venues and bars must be located at least 500m away from quiet residential and educational zones (娱乐场所500米避让).",
            applicability_conditions={"target_category": ["entertainment", "bar", "nightclub"], "residential_distance_m": {"min": 500}},
            parameters={"min_residential_distance_m": 500},
            source="domain_rule_pack",
            version="v2.0",
            confidence=0.92,
            jurisdiction="Noise Pollution Control Ordinance",
        ),

        # --- 3. Transportation ---
        DomainRule(
            id="rule_tr_subway_access_001",
            domain="transportation",
            name="Subway Transit Station Access Rule (地铁站可达性规则)",
            statement="Primary transit-oriented development TOD zones should be located within 800m of a subway station (地铁站800米覆盖率).",
            applicability_conditions={"target_category": ["subway", "transit", "tod"], "subway_distance_m": {"max": 800}},
            parameters={"tod_primary_radius_m": 800, "tod_secondary_radius_m": 1200},
            source="domain_rule_pack",
            version="v2.0",
            confidence=0.96,
            jurisdiction="Metropolitan Transit Planning Standard",
        ),
        DomainRule(
            id="rule_tr_road_saturation_001",
            domain="transportation",
            name="Road Network Saturation Threshold Rule (道路饱和度阈值规则)",
            statement="Peak hour traffic volume to capacity ratio (V/C road saturation / 道路饱和度) should not exceed 0.85 to avoid gridlock.",
            applicability_conditions={"metric": ["road_saturation", "vc_ratio"], "vc_ratio": {"max": 0.85}},
            parameters={"max_vc_ratio": 0.85},
            source="domain_rule_pack",
            version="v2.0",
            confidence=0.90,
            jurisdiction="Highway Capacity & Traffic Management Code",
        ),
        DomainRule(
            id="rule_tr_traffic_restriction_001",
            domain="transportation",
            name="Traffic Restriction Congestion Reduction Rule (限行交通减排缓堵规则)",
            statement="Implementing peak-hour traffic restriction policies (尾号限行) yields an estimated 15% reduction in main arterial congestion.",
            applicability_conditions={"scenario_type": ["traffic_restriction", "restriction", "congestion_control"]},
            parameters={"congestion_reduction_rate": 0.15},
            source="domain_rule_pack",
            version="v2.0",
            confidence=0.87,
            jurisdiction="Traffic Operations Bureau",
        ),
        DomainRule(
            id="rule_tr_commute_time_001",
            domain="transportation",
            name="Commute Time Delta Increase Limit Rule (通勤时间增量限制规则)",
            statement="Spatial alterations or road closures should not increase average community commute time deltas (通勤时间) by more than 10 minutes.",
            applicability_conditions={"metric": ["commute_time", "commute_delta"], "commute_delta_min": {"max": 10}},
            parameters={"max_commute_increase_min": 10.0},
            source="domain_rule_pack",
            version="v2.0",
            confidence=0.88,
            jurisdiction="Urban Mobility & Welfare Index",
        ),

        # --- 4. Environment ---
        DomainRule(
            id="rule_env_flood_risk_001",
            domain="environment",
            name="Flood Risk Buffer & Elevation Rule (防洪淹没风险缓冲规则)",
            statement="Development along rivers must maintain a 200m flood risk buffer zone (防洪缓冲圈) and elevation >= 5.0m above 100-year flood level.",
            applicability_conditions={"scenario_type": ["flood", "river", "flood_plain"], "elevation_m": {"min": 5.0}},
            parameters={"flood_buffer_m": 200, "min_safe_elevation_m": 5.0},
            source="domain_rule_pack",
            version="v2.0",
            confidence=0.97,
            jurisdiction="Flood Control & Water Resources Law",
        ),
        DomainRule(
            id="rule_env_typhoon_warning_001",
            domain="environment",
            name="Typhoon Hazard Warning Radius Rule (台风预警半径规则)",
            statement="Coastal development within a 50km typhoon warning radius (台风预警圈) requires enhanced wind resilience and storm surge buffers.",
            applicability_conditions={"scenario_type": ["typhoon", "coastal", "storm_surge"], "coastal_distance_km": {"max": 50}},
            parameters={"typhoon_warning_radius_km": 50, "storm_surge_buffer_m": 500},
            source="domain_rule_pack",
            version="v2.0",
            confidence=0.94,
            jurisdiction="National Emergency Management Bureau",
        ),
        DomainRule(
            id="rule_env_aqi_threshold_001",
            domain="environment",
            name="Air Quality Index AQI Threshold Rule (AQI空气质量阈值规则)",
            statement="Annual average Air Quality Index (AQI 空气质量指数) for residential zones should be maintained below 100.",
            applicability_conditions={"metric": ["aqi", "air_quality"], "aqi": {"max": 100}},
            parameters={"max_acceptable_aqi": 100, "good_aqi_threshold": 50},
            source="domain_rule_pack",
            version="v2.0",
            confidence=0.93,
            jurisdiction="Environmental Protection Agency Air Quality Standard",
        ),

        # --- 5. Natural Resources ---
        DomainRule(
            id="rule_nr_land_use_001",
            domain="natural_resources",
            name="Land Use & Ecological Redline Protection Rule (土地利用与生态红线保护规则)",
            statement="Prime agricultural land and designated land use ecological redlines (土地利用/耕地红线) permit zero urban non-agricultural construction.",
            applicability_conditions={"scenario_type": ["land_use", "agricultural", "redline"]},
            parameters={"encroachment_allowed": False, "penalty_level": "critical"},
            source="domain_rule_pack",
            version="v2.0",
            confidence=0.99,
            jurisdiction="Ministry of Natural Resources Land Use Redline",
        ),
        DomainRule(
            id="rule_nr_vegetation_index_001",
            domain="natural_resources",
            name="NDVI Vegetation Index Baseline Rule (NDVI 植被指数规则)",
            statement="Ecological preservation areas should maintain a Normalized Difference Vegetation Index (NDVI 植被覆盖指数) >= 0.4.",
            applicability_conditions={"metric": ["ndvi", "vegetation_index"], "ndvi": {"min": 0.4}},
            parameters={"min_ndvi_threshold": 0.4},
            source="domain_rule_pack",
            version="v2.0",
            confidence=0.91,
            jurisdiction="National Remote Sensing Ecological Monitoring Standard",
        ),
        DomainRule(
            id="rule_nr_eco_boundary_001",
            domain="natural_resources",
            name="Ecological Protection Boundary Reserve Rule (生态保护红线边界规则)",
            statement="A strict protective boundary zone (生态保护边界) of at least 1000m must be enforced surrounding core nature reserves.",
            applicability_conditions={"scenario_type": ["eco", "nature_reserve", "protected_area"], "reserve_distance_m": {"min": 1000}},
            parameters={"min_eco_buffer_m": 1000},
            source="domain_rule_pack",
            version="v2.0",
            confidence=0.96,
            jurisdiction="Nature Reserve Management Regulations",
        ),
        # --- 6. Custom & General Decisions ---
        DomainRule(
            id="rule_custom_general_001",
            domain="urban_planning",
            name="Custom Spatial Decision General Rule (自定义空间决策通用评估规则)",
            statement="Custom spatial decision scenarios evaluate spatial metrics, accessibility deltas, and land use impacts based on specified target bounds.",
            applicability_conditions={"scenario_type": ["custom", "general", "other"]},
            parameters={"general_evaluation": True},
            source="domain_rule_pack",
            version="v2.0",
            confidence=0.80,
            jurisdiction="General Spatial Decision Framework",
        ),
    ]
    return rules


class DomainRulePackRegistry:
    """Registry managing versioned domain rule packs, lookups, and applicability evaluation."""

    def __init__(self, load_defaults: bool = True):
        self._rules: Dict[str, DomainRule] = {}
        if load_defaults:
            for rule in _build_default_domain_rules():
                self.register_rule(rule)

    def register_rule(self, rule: DomainRule) -> None:
        """Register a new domain rule or overwrite existing by ID."""
        self._rules[rule.id] = rule
        logger.debug(f"DomainRulePackRegistry: registered rule '{rule.id}' ({rule.name})")

    def get_all_rules(self) -> List[DomainRule]:
        """Return list of all registered domain rules."""
        return list(self._rules.values())

    def get_rule_by_id(self, rule_id: str) -> Optional[DomainRule]:
        """Look up a rule by unique rule ID."""
        return self._rules.get(rule_id)

    def get_rules_by_domain(self, domain: str) -> List[DomainRule]:
        """Filter rules belonging to a specific domain category."""
        return [r for r in self._rules.values() if r.domain.lower() == domain.lower()]

    def get_rules_by_scenario(self, scenario_type: str) -> List[DomainRule]:
        """Look up rules applicable to a specific scenario type (e.g. subway, school, hospital, flood)."""
        st_lower = scenario_type.lower()
        matched = []
        for r in self._rules.values():
            # Check scenario_type in ID, name, or applicability_conditions
            if st_lower in r.id.lower() or st_lower in r.name.lower():
                matched.append(r)
                continue
            cond_scenarios = r.applicability_conditions.get("scenario_type", [])
            if isinstance(cond_scenarios, list) and any(st_lower in str(s).lower() for s in cond_scenarios):
                matched.append(r)
                continue
            cond_target = r.applicability_conditions.get("target_category", [])
            if isinstance(cond_target, list) and any(st_lower in str(t).lower() for t in cond_target):
                matched.append(r)
                continue
            # Keyword matching in statement
            if st_lower in r.statement.lower():
                matched.append(r)

        return matched

    def search_rules(self, query: str, domain: Optional[str] = None) -> List[DomainRule]:
        """Search rules by keyword query across name, statement, ID, domain, and jurisdiction."""
        if not query:
            return self.get_rules_by_domain(domain) if domain else self.get_all_rules()

        q_lower = query.lower()
        rules = self.get_rules_by_domain(domain) if domain else self.get_all_rules()

        matched = []
        for r in rules:
            if (
                q_lower in r.id.lower()
                or q_lower in r.name.lower()
                or q_lower in r.statement.lower()
                or q_lower in r.domain.lower()
                or (r.jurisdiction and q_lower in r.jurisdiction.lower())
            ):
                matched.append(r)

        return matched

    def match_applicable_rules(
        self, context: Dict[str, Any], domain: Optional[str] = None
    ) -> List[DomainRule]:
        """
        Evaluate rule applicability against a runtime context dictionary.
        Returns rules whose applicability_conditions are fully satisfied by context.
        """
        rules = self.get_rules_by_domain(domain) if domain else self.get_all_rules()
        applicable = []

        for r in rules:
            if self._evaluate_applicability(r.applicability_conditions, context):
                applicable.append(r)

        return applicable

    def _evaluate_applicability(
        self, conditions: Dict[str, Any], context: Dict[str, Any]
    ) -> bool:
        """Internal helper evaluating if conditions are satisfied by context."""
        if not conditions:
            return True

        for cond_key, cond_val in conditions.items():
            ctx_val = context.get(cond_key)
            if ctx_val is None:
                # Try finding key without unit suffix or normalized key
                alt_keys = [k for k in context if k.startswith(cond_key.split("_")[0])]
                if alt_keys:
                    ctx_val = context[alt_keys[0]]

            if ctx_val is None:
                continue  # If condition key absent from context, do not disqualify unless mandatory

            # Compare cond_val vs ctx_val
            if not self._check_condition_match(cond_val, ctx_val):
                return False

        return True

    def _check_condition_match(self, cond_val: Any, ctx_val: Any) -> bool:
        """Evaluate a single condition value specification against a context value."""
        if isinstance(cond_val, dict):
            # Numeric range or operator dict, e.g. {"min": 10, "max": 500} or {"<=": 500}
            try:
                num_val = float(ctx_val)
            except (ValueError, TypeError):
                return False

            if "min" in cond_val and num_val < float(cond_val["min"]):
                return False
            if "max" in cond_val and num_val > float(cond_val["max"]):
                return False
            if "<=" in cond_val and num_val > float(cond_val["<="]):
                return False
            if ">=" in cond_val and num_val < float(cond_val[">="]):
                return False
            if "<" in cond_val and num_val >= float(cond_val["<"]):
                return False
            if ">" in cond_val and num_val <= float(cond_val[">"]):
                return False
            if "==" in cond_val and num_val != float(cond_val["=="]):
                return False
            return True

        elif isinstance(cond_val, list):
            # Categorical list check
            if isinstance(ctx_val, list):
                return bool(set(cond_val).intersection(set(ctx_val)))
            return ctx_val in cond_val or str(ctx_val).lower() in [str(c).lower() for c in cond_val]

        else:
            # Direct value match
            if isinstance(cond_val, (int, float)) and isinstance(ctx_val, (int, float)):
                return float(ctx_val) == float(cond_val)
            return str(cond_val).lower() == str(ctx_val).lower()


# Singleton instance container
_registry_instance: Optional[DomainRulePackRegistry] = None


def get_rule_pack_registry() -> DomainRulePackRegistry:
    """Return active global DomainRulePackRegistry singleton."""
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = DomainRulePackRegistry(load_defaults=True)
    return _registry_instance


async def retrieve_evidence_from_rag(
    query: str,
    domain: Optional[str] = None,
    tenant: Optional[TenantContext] = None,
    knowledge_engine: Optional[KnowledgeEngine] = None,
    top_k: int = 5,
) -> List[EvidenceItem]:
    """
    Retrieve semantic knowledge chunks from KnowledgeEngine and map to EvidenceItem objects.
    RAG Fallback: returns empty list if RAG search encounters errors or is uninitialized.
    """
    if not query:
        return []

    engine = knowledge_engine or get_knowledge_engine()
    try:
        res_or_coro = engine.search(query=query, tenant=tenant, top_k=top_k)
        if inspect.isawaitable(res_or_coro):
            results = await asyncio.wait_for(res_or_coro, timeout=2.0)
        else:
            results = res_or_coro
    except Exception as e:
        logger.warning(f"RAG search grounding fallback triggered for query '{query}': {e}")
        return []

    evidence_items = []
    for chunk in results:
        chunk_id = chunk.get("id") or f"chk_{uuid.uuid4().hex[:8]}"
        title = chunk.get("title") or chunk.get("document_id") or "RAG Knowledge Base"
        content = chunk.get("content", "")
        score = chunk.get("score", 0.9)

        # Distinguish between rule chunks and fact chunks
        ev_type = "retrieved_rule" if "rule" in title.lower() or "standard" in title.lower() or "gb" in title.lower() else "observed_fact"

        item = create_evidence_item(
            id=f"ev_rag_{chunk_id}",
            type=ev_type,
            domain=domain or "general",
            statement=content,
            source=title,
            confidence=min(max(float(score), 0.0), 1.0),
            parameters={
                "document_id": chunk.get("document_id"),
                "chunk_id": chunk_id,
                "vector_score": score,
            },
        )
        evidence_items.append(item)

    return evidence_items


async def build_evidence_chain(
    query: str,
    scenario_type: str = "",
    domain: Optional[str] = None,
    context_params: Optional[Dict[str, Any]] = None,
    observed_facts: Optional[List[EvidenceItem]] = None,
    computed_facts: Optional[List[EvidenceItem]] = None,
    assumptions: Optional[List[EvidenceItem]] = None,
    knowledge_engine: Optional[KnowledgeEngine] = None,
    top_k: int = 3,
) -> List[EvidenceItem]:
    """
    Compose a structured, auditable evidence chain combining:
    - User/System Observed Facts (type: observed_fact)
    - Calculated GIS/Metric Deltas (type: computed_fact)
    - Matched Built-in Domain Rules (type: retrieved_rule)
    - Vector Knowledge RAG Chunks (type: retrieved_rule / observed_fact)
    - Declared Assumptions (type: assumption)
    - Derived Inferences (type: inference)
    """
    evidence_chain: List[EvidenceItem] = []
    registry = get_rule_pack_registry()

    # 1. Include explicit Observed Facts
    if observed_facts:
        evidence_chain.extend(observed_facts)

    # 2. Include Computed Facts
    if computed_facts:
        evidence_chain.extend(computed_facts)

    # 3. Include Explicit Assumptions
    if assumptions:
        evidence_chain.extend(assumptions)

    # 4. Lookup and convert matched Domain Rules to EvidenceItems (retrieved_rule)
    matched_rules: List[DomainRule] = []
    if context_params:
        matched_rules.extend(registry.match_applicable_rules(context_params, domain=domain))

    if scenario_type:
        scen_rules = registry.get_rules_by_scenario(scenario_type)
        for r in scen_rules:
            if r not in matched_rules:
                matched_rules.append(r)

    if not matched_rules and domain:
        matched_rules.extend(registry.get_rules_by_domain(domain)[:2])

    for rule in matched_rules:
        ev_rule = create_evidence_item(
            id=f"ev_rule_{rule.id}",
            type="retrieved_rule",
            domain=rule.domain,
            statement=f"[{rule.name}] {rule.statement}",
            source=f"DomainRulePack:{rule.source} ({rule.version})",
            confidence=rule.confidence,
            parameters=rule.parameters,
        )
        evidence_chain.append(ev_rule)

    # 5. Grounding: Retrieve RAG evidence chunks with error fallback
    rag_evidence = await retrieve_evidence_from_rag(
        query=query,
        domain=domain,
        knowledge_engine=knowledge_engine,
        top_k=top_k,
    )
    if rag_evidence:
        evidence_chain.extend(rag_evidence)

    # 6. De-duplicate evidence by ID / statement
    seen_ids = set()
    unique_chain = []
    for item in evidence_chain:
        if item.id not in seen_ids:
            seen_ids.add(item.id)
            unique_chain.append(item)

    return unique_chain
