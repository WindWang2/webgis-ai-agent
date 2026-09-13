"""Slot contract between acquisition intent and cartography intent (DS6.4).

Boundary (task book §8.1.4 — frozen at DS6):

- **V11 ``intent_semantic``** owns the *cartography* intent (map type,
  palette, layout …). It never imports this package.
- **ads-v1 semantic/** owns the *acquisition* intent (dataset candidates,
  time range, granularity, measures). It never imports intent_semantic.
- The two meet **only** through ``to_intent_slots``: a plain dict with the
  frozen slot vocabulary below, which a cartography-side consumer may read
  (and which this side may populate from a parsed query).

Slot vocabulary (frozen — additive evolution only):

- ``dataset_candidates``: [{source_id, dataset_id, title, confidence}] —
  the acquisition side's retrieval top-k (DS2);
- ``time_range``: {start, end, granularity} | None — parsed time intent;
- ``granularity``: canonical geographic granularity | None;
- ``measures``: [column names] — measure fields of the candidate datasets;
- ``geometry_scope``: {bbox} | None — spatial scope for the map frame;
- ``clarification``: str — set when acquisition confidence is low.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

SLOT_VOCABULARY = frozenset({
    "dataset_candidates",
    "time_range",
    "granularity",
    "measures",
    "geometry_scope",
    "clarification",
})


def to_intent_slots(
    *,
    dataset_candidates: Optional[List[Dict[str, Any]]] = None,
    time_range: Optional[Any] = None,
    granularity: Optional[str] = None,
    measures: Optional[List[str]] = None,
    geometry_scope: Optional[Dict[str, Any]] = None,
    clarification: str = "",
) -> Dict[str, Any]:
    """Build the frozen slot dict (no extra keys, no domain objects)."""
    slots: Dict[str, Any] = {
        "dataset_candidates": list(dataset_candidates or []),
        "time_range": (
            {"start": time_range.start, "end": time_range.end, "granularity": time_range.granularity}
            if time_range is not None else None
        ),
        "granularity": granularity,
        "measures": list(measures or []),
        "geometry_scope": dict(geometry_scope) if geometry_scope else None,
        "clarification": clarification,
    }
    assert set(slots) <= SLOT_VOCABULARY
    return slots


__all__ = ["SLOT_VOCABULARY", "to_intent_slots"]
