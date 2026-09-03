"""
Uncertainty Quantification and Vectorized Monte Carlo Engine for Spatial Decision Intelligence V3.
Provides deterministic, reproducible probabilistic simulation with bounded sample sizes.
Supports fixed, uniform interval, triangular, normal, and empirical distributions.
"""
import math
import logging
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from app.services.spatial_decision.models_v3 import (
    DistributionType,
    OutcomeDistribution,
    UncertainParameter,
)

logger = logging.getLogger(__name__)


def sample_parameter_distribution(
    param: UncertainParameter,
    n_samples: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """
    Generates n_samples draws from the declared probability distribution of a parameter.
    """
    dist = param.distribution
    p = param.params

    if dist == DistributionType.FIXED:
        val = p.get("value", p.get("expected", 0.0))
        return np.full(n_samples, val, dtype=float)

    elif dist == DistributionType.INTERVAL:
        min_v = p.get("min", 0.0)
        max_v = p.get("max", min_v + 1.0)
        if max_v < min_v:
            min_v, max_v = max_v, min_v
        return rng.uniform(min_v, max_v, size=n_samples)

    elif dist == DistributionType.TRIANGULAR:
        min_v = p.get("min", 0.0)
        max_v = p.get("max", min_v + 1.0)
        mode_v = p.get("mode", (min_v + max_v) / 2.0)
        if not (min_v <= mode_v <= max_v):
            mode_v = (min_v + max_v) / 2.0
        return rng.triangular(min_v, mode_v, max_v, size=n_samples)

    elif dist == DistributionType.NORMAL:
        mean_v = p.get("mean", 0.0)
        std_v = max(1e-6, p.get("std", 1.0))
        samples = rng.normal(mean_v, std_v, size=n_samples)
        # Optional clipping if min/max provided
        if "min" in p or "max" in p:
            low = p.get("min", -np.inf)
            high = p.get("max", np.inf)
            samples = np.clip(samples, low, high)
        return samples

    elif dist == DistributionType.EMPIRICAL:
        values = p.get("values")
        if isinstance(values, (list, tuple)) and len(values) > 0:
            arr = np.array(values, dtype=float)
            return rng.choice(arr, size=n_samples, replace=True)
        return np.zeros(n_samples, dtype=float)

    # Fallback
    return np.zeros(n_samples, dtype=float)


def compute_distribution_summary(
    samples: np.ndarray,
    metric_key: str,
    threshold: Optional[float] = None,
    operator: str = "<=",
) -> OutcomeDistribution:
    """
    Computes key summary percentiles (mean, median, p05, p25, p75, p95, std) from samples.
    """
    if len(samples) == 0:
        return OutcomeDistribution(
            metric_key=metric_key,
            mean=0.0,
            median=0.0,
            std=0.0,
            p05=0.0,
            p25=0.0,
            p75=0.0,
            p95=0.0,
        )

    clean_samples = samples[np.isfinite(samples)]
    if len(clean_samples) == 0:
        clean_samples = np.array([0.0])

    mean_v = float(np.mean(clean_samples))
    med_v = float(np.median(clean_samples))
    std_v = float(np.std(clean_samples))
    p05_v = float(np.percentile(clean_samples, 5))
    p25_v = float(np.percentile(clean_samples, 25))
    p75_v = float(np.percentile(clean_samples, 75))
    p95_v = float(np.percentile(clean_samples, 95))

    prob_met = None
    if threshold is not None:
        if operator == "<=":
            prob_met = float(np.mean(clean_samples <= threshold))
        elif operator == ">=":
            prob_met = float(np.mean(clean_samples >= threshold))
        elif operator == "<":
            prob_met = float(np.mean(clean_samples < threshold))
        elif operator == ">":
            prob_met = float(np.mean(clean_samples > threshold))

    return OutcomeDistribution(
        metric_key=metric_key,
        mean=round(mean_v, 4),
        median=round(med_v, 4),
        std=round(std_v, 4),
        p05=round(p05_v, 4),
        p25=round(p25_v, 4),
        p75=round(p75_v, 4),
        p95=round(p95_v, 4),
        prob_constraint_met=round(prob_met, 4) if prob_met is not None else None,
    )
