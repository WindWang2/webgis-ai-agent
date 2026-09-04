"""
Modular Impact Model Abstraction and Registry for Spatial Decision Intelligence V3.
Replaces monolithic hardcoded if/elif branches with declarative, evidence-grounded
impact models supporting transparent assumptions, required inputs, and causal honesty.
"""
from abc import ABC, abstractmethod
import logging
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from app.services.spatial_decision.models import (
    TargetAreaSpec,
    MetricDeltaV2,
    MetricRange,
    DomainRule,
    EvidenceItem,
    SpatialImpactZone,
)
from app.services.spatial_decision.models_v3 import Assumption

logger = logging.getLogger(__name__)


class ImpactModelResult(BaseModel):
    """Standardized output from an impact model simulation."""
    model_name: str = Field(..., description="Impact model name")
    model_version: str = Field(default="v3.0", description="Model version")
    causal_level: str = Field(
        default="rule_based_projected_range",
        description="Causal honesty level: 'observed_effect', 'empirical_relationship', 'rule_based_projected_range', 'scenario_estimate'",
    )
    metrics: Dict[str, MetricDeltaV2] = Field(default_factory=dict, description="Simulated metrics")
    spatial_impacts: List[SpatialImpactZone] = Field(default_factory=list, description="Spatial impact zones")
    simulation_geojson: Dict[str, Any] = Field(default_factory=dict, description="Generated impact GeoJSON")
    assumptions: List[Assumption] = Field(default_factory=list, description="Explicit scenario assumptions")
    evidence: List[EvidenceItem] = Field(default_factory=list, description="Evidence items used")
    warnings: List[str] = Field(default_factory=list, description="Model warnings or limitations")


class ImpactModel(ABC):
    """Abstract Base Class for domain-specific spatial impact models."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable model name."""
        pass

    @property
    @abstractmethod
    def version(self) -> str:
        """Model version string."""
        pass

    @abstractmethod
    def supports(self, scenario_type: str, context: Optional[Dict[str, Any]] = None) -> bool:
        """Returns True if this model can simulate the given scenario type."""
        pass

    @abstractmethod
    def required_inputs(self) -> List[str]:
        """List of required baseline metric keys or parameters."""
        pass

    @abstractmethod
    def simulate(
        self,
        target_area: TargetAreaSpec,
        parameters: Dict[str, Any],
        baseline_metrics: Dict[str, MetricDeltaV2],
        rules: List[DomainRule],
    ) -> ImpactModelResult:
        """Executes the simulation and produces structured impact outcomes."""
        pass

    def describe_assumptions(self) -> List[str]:
        """Declarative description of the model's theoretical or empirical assumptions."""
        return []
