"""GIS-101 regression: spatial decision V3 hard constraints must fail closed.

Deep-review finding: INTERSECTS/DISJOINT/BUFFER_EXCLUSION/SERVICE_COVERAGE/
OVERLAP_RATIO fell through to ``passed=True`` in spatial_constraints.py, and
LOGICAL constraints were always satisfied, so infeasible alternatives were
recommended as "satisfying all hard constraints".
"""
import pytest
from shapely.geometry import box, mapping

from app.services.spatial_decision.constraints import (
    evaluate_alternative_constraints,
)
from app.services.spatial_decision.models_v3 import (
    Alternative,
    Constraint,
    ConstraintCategory,
    ConstraintType,
    SpatialPredicate,
)
from app.services.spatial_decision.spatial_constraints import (
    IMPLEMENTED_SPATIAL_PREDICATES,
    evaluate_spatial_constraint,
)
from app.tools.spatial_decision_tools import _parse_spatial_predicate


UNIMPLEMENTED = [
    SpatialPredicate.INTERSECTS,
    SpatialPredicate.DISJOINT,
    SpatialPredicate.BUFFER_EXCLUSION,
    SpatialPredicate.SERVICE_COVERAGE,
    SpatialPredicate.OVERLAP_RATIO,
]


def _buffer_exclusion_constraint() -> Constraint:
    return Constraint(
        id="buffer_excl",
        name="No overlap with existing service buffer",
        constraint_type=ConstraintType.HARD,
        category=ConstraintCategory.SPATIAL,
        spatial_predicate=SpatialPredicate.BUFFER_EXCLUSION,
        buffer_distance_m=500.0,
        reference_geometry=mapping(box(116.30, 39.90, 116.40, 40.00)),
    )


def test_unimplemented_predicates_raise_instead_of_passing():
    alt = mapping(box(116.35, 39.95, 116.36, 39.96))  # overlaps the reference
    for predicate in UNIMPLEMENTED:
        constraint = _buffer_exclusion_constraint().model_copy(
            update={"spatial_predicate": predicate}
        )
        with pytest.raises(ValueError, match=predicate.value):
            evaluate_spatial_constraint("A", alt, constraint)


def test_hard_buffer_exclusion_overlap_never_feasible():
    """The report's counterexample must not yield feasible=True anymore."""
    alt = Alternative(
        id="A",
        name="Overlapping site",
        geometry=mapping(box(116.35, 39.95, 116.36, 39.96)),
    )
    with pytest.raises(ValueError):
        evaluate_alternative_constraints(
            alternative=alt,
            constraints=[_buffer_exclusion_constraint()],
            metric_values={},
        )


def test_logical_constraint_without_evaluator_is_a_violation():
    alt = Alternative(id="A", name="A", attributes={"kind": "x"})
    hard = Constraint(
        id="logic_hard",
        name="Composite logic gate",
        constraint_type=ConstraintType.HARD,
        category=ConstraintCategory.LOGICAL,
        metric_key="kind",
        threshold=["x"],
    )
    soft = hard.model_copy(update={"id": "logic_soft", "constraint_type": ConstraintType.SOFT})

    feasible, hard_v, soft_v = evaluate_alternative_constraints(alt, [hard], {})
    assert feasible is False
    assert len(hard_v) == 1
    assert hard_v[0].observed_value == "no_evaluator"
    assert hard_v[0].penalty == 0.0

    feasible2, hard_v2, soft_v2 = evaluate_alternative_constraints(alt, [soft], {})
    assert feasible2 is True
    assert not hard_v2 and len(soft_v2) == 1
    assert soft_v2[0].penalty == soft.penalty_weight


def test_only_four_predicates_have_evaluators():
    assert IMPLEMENTED_SPATIAL_PREDICATES == frozenset({
        SpatialPredicate.OUTSIDE,
        SpatialPredicate.WITHIN,
        SpatialPredicate.MIN_DISTANCE,
        SpatialPredicate.MAX_DISTANCE,
    })


def test_tool_predicate_parser_rejects_unknown_and_unimplemented():
    with pytest.raises(ValueError, match="unknown spatial_predicate"):
        _parse_spatial_predicate("buffer_zone")
    for predicate in UNIMPLEMENTED:
        with pytest.raises(ValueError, match="not implemented"):
            _parse_spatial_predicate(predicate.value)
    # implemented names still map correctly, and a missing value defaults to
    # outside (documented tool default)
    assert _parse_spatial_predicate(None) == SpatialPredicate.OUTSIDE
    assert _parse_spatial_predicate("MIN_DISTANCE") == SpatialPredicate.MIN_DISTANCE


def test_categorical_constraint_unknown_operator_fails_closed():
    """A misspelling like "==" must not fall through to the "not in" branch.

    ``Constraint`` validation normally rejects unknown categorical operators,
    but an unvalidated copy (``model_copy``) can still reach the evaluator. The
    value below is NOT in the allowed set, so the old default branch
    (``val not in thresh``) wrongly reported the hard constraint as satisfied.
    """
    alt = Alternative(id="A", name="A", attributes={"kind": "warehouse"})
    constraint = Constraint(
        id="c_cat",
        name="Only schools allowed",
        constraint_type=ConstraintType.HARD,
        category=ConstraintCategory.CATEGORICAL,
        metric_key="kind",
        operator="in",
        threshold=["school"],
    ).model_copy(update={"operator": "=="})

    feasible, hard_violations, _ = evaluate_alternative_constraints(alt, [constraint], {})
    assert feasible is False
    assert len(hard_violations) == 1
    assert hard_violations[0].passed is False


def test_within_constraint_without_boundary_fails_closed():
    """WITHIN without a reference boundary must not pass by default (GIS-101)."""
    alt = Alternative(
        id="A",
        name="A",
        geometry=mapping(box(116.35, 39.95, 116.36, 39.96)),
    )
    constraint = Constraint(
        id="c_within",
        name="Inside permitted boundary",
        constraint_type=ConstraintType.HARD,
        category=ConstraintCategory.SPATIAL,
        spatial_predicate=SpatialPredicate.WITHIN,
    )

    evaluation = evaluate_spatial_constraint("A", alt.geometry, constraint)
    assert evaluation.passed is False
    assert evaluation.margin == -1.0

    feasible, hard_violations, _ = evaluate_alternative_constraints(alt, [constraint], {})
    assert feasible is False
    assert len(hard_violations) == 1
