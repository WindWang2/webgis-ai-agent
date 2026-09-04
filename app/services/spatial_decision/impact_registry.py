"""
Concrete Impact Models and Model Registry for Spatial Decision Intelligence V3.
Provides specialized models for transit, schools, hospitals, green space, and generic spatial interventions.
Enforces the fail-closed missing baseline contract: no fabricated forecasts without real evidence.
"""
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.services.spatial_decision.models import (
    TargetAreaSpec,
    MetricDeltaV2,
    MetricRange,
    DomainRule,
    EvidenceItem,
)
from app.services.spatial_decision.models_v3 import Assumption
from app.services.spatial_decision.impact_model import ImpactModel, ImpactModelResult
from app.services.spatial_decision.impact_engine import SpatialImpactEngine

logger = logging.getLogger(__name__)


class BaseDomainImpactModel(ImpactModel):
    """Common implementation helpers for domain impact models."""

    def __init__(self, impact_engine: Optional[SpatialImpactEngine] = None):
        self.impact_engine = impact_engine or SpatialImpactEngine()

    def _eval_or_gap(
        self,
        m_key: str,
        m_name: str,
        unit: str,
        baseline_metrics: Dict[str, MetricDeltaV2],
        pct_range: Tuple[float, float, float],
        assumptions: List[Assumption],
    ) -> MetricDeltaV2:
        """Evaluates metric if real baseline exists, or flags missing baseline honestly."""
        if m_key in baseline_metrics and not baseline_metrics[m_key].missing_baseline:
            bm = baseline_metrics[m_key]
            base_val = float(bm.baseline)
            min_pct, exp_pct, max_pct = pct_range
            sim_val = round(base_val * (1.0 + exp_pct), 4)
            delta_abs = round(sim_val - base_val, 4)
            delta_pct = round(exp_pct * 100.0, 4) if base_val != 0.0 else None

            rng = MetricRange(
                min_val=round(base_val * (1.0 + min_pct), 4),
                expected_val=sim_val,
                max_val=round(base_val * (1.0 + max_pct), 4),
            )
            return MetricDeltaV2(
                metric_key=m_key,
                metric_name=m_name,
                baseline=base_val,
                simulated=sim_val,
                delta_abs=delta_abs,
                delta_pct=delta_pct,
                range=rng,
                unit=unit,
                missing_baseline=False,
            )
        else:
            gap_note = f"指标 [{m_name}] 缺失实测基线数据，未伪造模拟值；需提供真实基线数据才能定量推算。"
            assumptions.append(
                Assumption(
                    key=f"missing_baseline_{m_key}",
                    statement=gap_note,
                    value=None,
                    source="evidence_gap",
                    confidence=1.0,
                )
            )
            return MetricDeltaV2(
                metric_key=m_key,
                metric_name=m_name,
                baseline=None,
                simulated=None,
                delta_abs=None,
                delta_pct=None,
                unit=unit,
                missing_baseline=True,
                evidence_gap_note=gap_note,
            )


class SubwayTransitImpactModel(BaseDomainImpactModel):
    """Simulates accessibility, land-use, and commercial vitality impacts of rail transit."""

    @property
    def name(self) -> str:
        return "SubwayTransitImpactModel"

    @property
    def version(self) -> str:
        return "v3.0"

    def supports(self, scenario_type: str, context: Optional[Dict[str, Any]] = None) -> bool:
        return scenario_type in {"subway", "transit", "metro", "rail"}

    def required_inputs(self) -> List[str]:
        return ["housing_price", "rent", "commute_time", "commercial_vitality"]

    def simulate(
        self,
        target_area: TargetAreaSpec,
        parameters: Dict[str, Any],
        baseline_metrics: Dict[str, MetricDeltaV2],
        rules: List[DomainRule],
    ) -> ImpactModelResult:
        assumptions: List[Assumption] = []
        assumptions.append(
            Assumption(
                key="tod_catchment",
                statement="Transit-Oriented Development impact assumed within 500m core and 1500m secondary radius.",
                value={"core_m": 500, "secondary_m": 1500},
                source="domain_rule_pack",
            )
        )

        # Generate spatial impact zones
        zones, geojson = self.impact_engine.calculate_impacts(
            scenario_type="subway",
            target_area=target_area,
            rules=rules,
            parameters=parameters,
        )

        metrics: Dict[str, MetricDeltaV2] = {
            "housing_price": self._eval_or_gap(
                "housing_price", "Housing Price", "RMB/m2", baseline_metrics, (0.15, 0.20, 0.25), assumptions
            ),
            "rent": self._eval_or_gap(
                "rent", "Rent Price", "RMB/m2/month", baseline_metrics, (0.10, 0.14, 0.18), assumptions
            ),
            "commute_time": self._eval_or_gap(
                "commute_time", "Commute Time", "min", baseline_metrics, (-0.15, -0.10, -0.05), assumptions
            ),
            "commercial_vitality": self._eval_or_gap(
                "commercial_vitality", "Commercial Vitality Index", "pts", baseline_metrics, (0.20, 0.30, 0.40), assumptions
            ),
        }

        return ImpactModelResult(
            model_name=self.name,
            model_version=self.version,
            causal_level="rule_based_projected_range",
            metrics=metrics,
            spatial_impacts=zones,
            simulation_geojson=geojson,
            assumptions=assumptions,
            evidence=[
                EvidenceItem(
                    id="ev_subway_tod",
                    type="retrieved_rule",
                    domain="transportation",
                    statement="TOD rail transit stations exhibit direct property value premium in 500m zone.",
                    source="Transit-Oriented Development Planning Standard",
                    confidence=0.92,
                )
            ],
            warnings=[],
        )


class HospitalFacilityImpactModel(BaseDomainImpactModel):
    """Simulates healthcare accessibility, emergency response radius, and coverage gap relief."""

    @property
    def name(self) -> str:
        return "HospitalFacilityImpactModel"

    @property
    def version(self) -> str:
        return "v3.0"

    def supports(self, scenario_type: str, context: Optional[Dict[str, Any]] = None) -> bool:
        return scenario_type in {"hospital", "medical", "healthcare", "clinic"}

    def required_inputs(self) -> List[str]:
        return ["medical_access", "housing_price"]

    def simulate(
        self,
        target_area: TargetAreaSpec,
        parameters: Dict[str, Any],
        baseline_metrics: Dict[str, MetricDeltaV2],
        rules: List[DomainRule],
    ) -> ImpactModelResult:
        assumptions: List[Assumption] = []
        assumptions.append(
            Assumption(
                key="hospital_service_radius",
                statement="Hospital primary service catchment established at 1500m to 3000m.",
                value={"primary_m": 1500, "secondary_m": 3000},
                source="Municipal Health & Spatial Plan",
            )
        )

        zones, geojson = self.impact_engine.calculate_impacts(
            scenario_type="hospital",
            target_area=target_area,
            rules=rules,
            parameters=parameters,
        )

        metrics: Dict[str, MetricDeltaV2] = {
            "medical_access": self._eval_or_gap(
                "medical_access", "Medical Access Index", "pts", baseline_metrics, (0.40, 0.50, 0.60), assumptions
            ),
            "housing_price": self._eval_or_gap(
                "housing_price", "Housing Price", "RMB/m2", baseline_metrics, (0.05, 0.075, 0.10), assumptions
            ),
        }

        return ImpactModelResult(
            model_name=self.name,
            model_version=self.version,
            causal_level="rule_based_projected_range",
            metrics=metrics,
            spatial_impacts=zones,
            simulation_geojson=geojson,
            assumptions=assumptions,
            evidence=[
                EvidenceItem(
                    id="ev_hospital_standard",
                    type="retrieved_rule",
                    domain="urban_planning",
                    statement="General hospitals maintain 1500m-3000m service catchment with emergency green corridors.",
                    source="GB50180 Urban Planning Standard",
                    confidence=0.90,
                )
            ],
            warnings=[],
        )


class SchoolFacilityImpactModel(BaseDomainImpactModel):
    """Simulates educational facility access, pedestrian commute safety, and school district premium."""

    @property
    def name(self) -> str:
        return "SchoolFacilityImpactModel"

    @property
    def version(self) -> str:
        return "v3.0"

    def supports(self, scenario_type: str, context: Optional[Dict[str, Any]] = None) -> bool:
        return scenario_type in {"school", "education", "primary_school", "middle_school"}

    def required_inputs(self) -> List[str]:
        return ["education_access", "housing_price", "rent"]

    def simulate(
        self,
        target_area: TargetAreaSpec,
        parameters: Dict[str, Any],
        baseline_metrics: Dict[str, MetricDeltaV2],
        rules: List[DomainRule],
    ) -> ImpactModelResult:
        assumptions: List[Assumption] = []
        assumptions.append(
            Assumption(
                key="school_walk_circle",
                statement="Primary educational service radius is 500m pedestrian walking distance (10-min circle).",
                value={"walk_radius_m": 500},
                source="GB50180 National Planning Standard",
            )
        )

        zones, geojson = self.impact_engine.calculate_impacts(
            scenario_type="school",
            target_area=target_area,
            rules=rules,
            parameters=parameters,
        )

        metrics: Dict[str, MetricDeltaV2] = {
            "education_access": self._eval_or_gap(
                "education_access", "Education Access Index", "pts", baseline_metrics, (0.30, 0.40, 0.50), assumptions
            ),
            "housing_price": self._eval_or_gap(
                "housing_price", "Housing Price", "RMB/m2", baseline_metrics, (0.08, 0.115, 0.15), assumptions
            ),
            "rent": self._eval_or_gap(
                "rent", "Rent Price", "RMB/m2/month", baseline_metrics, (0.05, 0.085, 0.12), assumptions
            ),
        }

        return ImpactModelResult(
            model_name=self.name,
            model_version=self.version,
            causal_level="rule_based_projected_range",
            metrics=metrics,
            spatial_impacts=zones,
            simulation_geojson=geojson,
            assumptions=assumptions,
            evidence=[
                EvidenceItem(
                    id="ev_school_walk",
                    type="retrieved_rule",
                    domain="urban_planning",
                    statement="Primary schools must be accessible within 500m pedestrian radius.",
                    source="GB50180 National Standard",
                    confidence=0.95,
                )
            ],
            warnings=[],
        )


class ParkGreenSpaceImpactModel(BaseDomainImpactModel):
    """Simulates ecological coverage, recreational living quality, and cooling island effect."""

    @property
    def name(self) -> str:
        return "ParkGreenSpaceImpactModel"

    @property
    def version(self) -> str:
        return "v3.0"

    def supports(self, scenario_type: str, context: Optional[Dict[str, Any]] = None) -> bool:
        return scenario_type in {"park", "green_space", "ecology", "recreation"}

    def required_inputs(self) -> List[str]:
        return ["living_quality", "housing_price"]

    def simulate(
        self,
        target_area: TargetAreaSpec,
        parameters: Dict[str, Any],
        baseline_metrics: Dict[str, MetricDeltaV2],
        rules: List[DomainRule],
    ) -> ImpactModelResult:
        assumptions: List[Assumption] = []
        zones, geojson = self.impact_engine.calculate_impacts(
            scenario_type="park",
            target_area=target_area,
            rules=rules,
            parameters=parameters,
        )

        metrics: Dict[str, MetricDeltaV2] = {
            "living_quality": self._eval_or_gap(
                "living_quality", "Living Quality Index", "pts", baseline_metrics, (0.15, 0.20, 0.25), assumptions
            ),
            "housing_price": self._eval_or_gap(
                "housing_price", "Housing Price", "RMB/m2", baseline_metrics, (0.05, 0.075, 0.10), assumptions
            ),
        }

        return ImpactModelResult(
            model_name=self.name,
            model_version=self.version,
            causal_level="rule_based_projected_range",
            metrics=metrics,
            spatial_impacts=zones,
            simulation_geojson=geojson,
            assumptions=assumptions,
            evidence=[],
            warnings=[],
        )


class GenericSpatialImpactModel(BaseDomainImpactModel):
    """Fallback general impact model for unspecialized or custom spatial interventions."""

    @property
    def name(self) -> str:
        return "GenericSpatialImpactModel"

    @property
    def version(self) -> str:
        return "v3.0"

    def supports(self, scenario_type: str, context: Optional[Dict[str, Any]] = None) -> bool:
        return True

    def required_inputs(self) -> List[str]:
        return []

    def simulate(
        self,
        target_area: TargetAreaSpec,
        parameters: Dict[str, Any],
        baseline_metrics: Dict[str, MetricDeltaV2],
        rules: List[DomainRule],
    ) -> ImpactModelResult:
        assumptions: List[Assumption] = []
        zones, geojson = self.impact_engine.calculate_impacts(
            scenario_type="custom",
            target_area=target_area,
            rules=rules,
            parameters=parameters,
        )

        metrics: Dict[str, MetricDeltaV2] = {}
        for m_key, bm in baseline_metrics.items():
            if not bm.missing_baseline:
                metrics[m_key] = bm

        return ImpactModelResult(
            model_name=self.name,
            model_version=self.version,
            causal_level="scenario_estimate",
            metrics=metrics,
            spatial_impacts=zones,
            simulation_geojson=geojson,
            assumptions=assumptions,
            evidence=[],
            warnings=["Using generic spatial impact model without specialized domain rules."],
        )


class ImpactModelRegistry:
    """Registry managing domain impact models."""

    def __init__(self):
        self._models: List[ImpactModel] = [
            SubwayTransitImpactModel(),
            HospitalFacilityImpactModel(),
            SchoolFacilityImpactModel(),
            ParkGreenSpaceImpactModel(),
            GenericSpatialImpactModel(),  # Catch-all
        ]

    def register(self, model: ImpactModel):
        """Register a new impact model with highest priority."""
        self._models.insert(0, model)

    def resolve(self, scenario_type: str, context: Optional[Dict[str, Any]] = None) -> ImpactModel:
        """Finds the first registered model that supports the given scenario type."""
        for m in self._models:
            if m.supports(scenario_type, context):
                return m
        return self._models[-1]


_GLOBAL_REGISTRY: Optional[ImpactModelRegistry] = None


def get_impact_model_registry() -> ImpactModelRegistry:
    """Singleton getter for ImpactModelRegistry."""
    global _GLOBAL_REGISTRY
    if _GLOBAL_REGISTRY is None:
        _GLOBAL_REGISTRY = ImpactModelRegistry()
    return _GLOBAL_REGISTRY
