"""
Unified Constraint Evaluation Engine for Spatial Decision Intelligence V3.
Evaluates Hard and Soft constraints across numeric, spatial, categorical, and logical dimensions.
Enforces the inviolable principle: Infeasible alternatives CANNOT be recommended.
"""
import logging
from typing import Dict, List, Optional, Tuple

from app.services.spatial_decision.models_v3 import (
    Alternative,
    Constraint,
    ConstraintCategory,
    ConstraintEvaluation,
    ConstraintType,
)
from app.services.spatial_decision.spatial_constraints import evaluate_spatial_constraint

logger = logging.getLogger(__name__)


def evaluate_numeric_constraint(
    alt_id: str,
    val: Optional[float],
    constraint: Constraint,
) -> ConstraintEvaluation:
    """Evaluates a numeric constraint against an observed or derived metric."""
    if val is None:
        return ConstraintEvaluation(
            constraint_id=constraint.id,
            alternative_id=alt_id,
            passed=False,
            observed_value=None,
            threshold=constraint.threshold,
            margin=None,
            penalty=constraint.penalty_weight if constraint.constraint_type == ConstraintType.SOFT else 0.0,
            evidence_statement=f"Metric '{constraint.metric_key}' missing for alternative '{alt_id}', failing constraint '{constraint.name}'.",
        )

    op = constraint.operator or "<="
    thresh = float(constraint.threshold)
    passed = False
    margin = 0.0

    if op == "<=":
        passed = val <= thresh
        margin = thresh - val
    elif op == ">=":
        passed = val >= thresh
        margin = val - thresh
    elif op == "<":
        passed = val < thresh
        margin = thresh - val
    elif op == ">":
        passed = val > thresh
        margin = val - thresh
    elif op == "==":
        passed = abs(val - thresh) < 1e-6
        margin = -abs(val - thresh)
    elif op == "!=":
        passed = abs(val - thresh) >= 1e-6
        margin = abs(val - thresh)
    else:
        passed = val <= thresh
        margin = thresh - val

    penalty = 0.0 if passed else (constraint.penalty_weight if constraint.constraint_type == ConstraintType.SOFT else 0.0)
    status_str = "satisfied" if passed else "VIOLATED"
    stmt = (
        f"Constraint '{constraint.name}' ({constraint.metric_key} {op} {thresh}): "
        f"observed {round(val, 2)} ({status_str}, margin: {round(margin, 2)})."
    )

    return ConstraintEvaluation(
        constraint_id=constraint.id,
        alternative_id=alt_id,
        passed=passed,
        observed_value=round(val, 4),
        threshold=thresh,
        margin=round(margin, 4),
        penalty=penalty,
        evidence_statement=stmt,
    )


def evaluate_alternative_constraints(
    alternative: Alternative,
    constraints: List[Constraint],
    metric_values: Dict[str, Optional[float]],
) -> Tuple[bool, List[ConstraintEvaluation], List[ConstraintEvaluation]]:
    """
    Evaluates all constraints for a single candidate alternative.

    Args:
        alternative: Alternative to evaluate.
        constraints: List of Hard and Soft constraints.
        metric_values: Evaluated metric values for this alternative (metric_key -> val).

    Returns:
        Tuple of (is_feasible, hard_violations, soft_violations).
    """
    hard_violations: List[ConstraintEvaluation] = []
    soft_violations: List[ConstraintEvaluation] = []

    for c in constraints:
        evaluation: ConstraintEvaluation

        if c.category == ConstraintCategory.SPATIAL:
            evaluation = evaluate_spatial_constraint(
                alt_id=alternative.id,
                alt_geometry_dict=alternative.geometry,
                constraint=c,
            )
        elif c.category == ConstraintCategory.NUMERIC:
            # Check attribute in alternative.attributes first, then in metric_values
            val = None
            if c.metric_key in alternative.attributes:
                val = alternative.attributes[c.metric_key]
            elif c.metric_key in metric_values:
                val = metric_values[c.metric_key]
            evaluation = evaluate_numeric_constraint(
                alt_id=alternative.id,
                val=float(val) if val is not None else None,
                constraint=c,
            )
        elif c.category == ConstraintCategory.CATEGORICAL:
            val = alternative.attributes.get(c.metric_key)
            thresh = c.threshold
            op = c.operator or "in"
            passed = (val in thresh) if op == "in" else (val not in thresh)
            penalty = 0.0 if passed else (c.penalty_weight if c.constraint_type == ConstraintType.SOFT else 0.0)
            evaluation = ConstraintEvaluation(
                constraint_id=c.id,
                alternative_id=alternative.id,
                passed=passed,
                observed_value=val,
                threshold=thresh,
                margin=0.0 if passed else -1.0,
                penalty=penalty,
                evidence_statement=f"Categorical constraint '{c.name}': {val} {op} {thresh}.",
            )
        else:
            evaluation = ConstraintEvaluation(
                constraint_id=c.id,
                alternative_id=alternative.id,
                passed=True,
                observed_value=None,
                threshold=c.threshold,
                margin=0.0,
                penalty=0.0,
                evidence_statement="Logical constraint satisfied.",
            )

        if not evaluation.passed:
            if c.constraint_type == ConstraintType.HARD:
                hard_violations.append(evaluation)
            else:
                soft_violations.append(evaluation)

    is_feasible = len(hard_violations) == 0
    return is_feasible, hard_violations, soft_violations
