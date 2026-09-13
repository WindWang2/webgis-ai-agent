"""ads-v1 semantic dimension parsing (DS6, ADR-0176): time / granularity /
field roles — the acquisition-intent side of the intent split.

Boundary (task book §8.1.4): ``intent_semantic`` (V11 W1) owns *cartography*
intent (what map, what palette); THIS package owns *acquisition* intent (what
data, what range, what granularity). The two domains integrate exclusively
through the frozen slot contract (``slots.to_intent_slots``) — neither module
imports the other.
"""
from app.services.data_fabric.semantic.granularity import (
    GRANULARITY_ORDER,
    normalize_granularity,
    parse_granularity,
)
from app.services.data_fabric.semantic.time_parser import TimeRange, parse_time_expr
from app.services.data_fabric.semantic.field_roles import infer_field_roles
from app.services.data_fabric.semantic.slots import to_intent_slots

__all__ = [
    "TimeRange",
    "parse_time_expr",
    "parse_granularity",
    "normalize_granularity",
    "GRANULARITY_ORDER",
    "infer_field_roles",
    "to_intent_slots",
]
