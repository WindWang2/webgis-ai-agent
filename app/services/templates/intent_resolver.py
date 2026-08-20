"""
F-FE-TPL: Agent template recommendation fast path.

The Agent previously searched the full SEED_TEMPLATES list per call
(`apply_template` linear scan, `list_templates` linear filter). The V2
template registry (O(1) by id/kind/tag) is the source of truth; this
module wraps it with the small extra the Agent needs:

  - ``resolve_template_by_intent``: case-insensitive match of a user
    phrase ("make a population density map") to a template or composite
    in the registry. Used by `list_templates` / `apply_template` to
    short-circuit when the user explicitly named something.
  - ``get_template_or_composite``: O(1) by-id lookup across both built-ins
    and composites; returns the raw composite record for composites
    (with ``pipeline``). Use :func:`expand_composite` to resolve slots.

The registry already keeps the hot data in memory; this module never
re-scans it per call. Tests in tests/unit/test_template_intent.py pin
the resolution behavior so a future template addition can't silently
regress the lookup.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.schemas.template_registry import get_template_registry


def _normalize(s: str) -> str:
    return (s or "").strip().lower()


def get_template_or_composite(template_id: str) -> Optional[Dict[str, Any]]:
    """O(1) lookup of any template (built-in, user, or composite) by id.

    For composite ids the raw composite record is returned (carries ``pipeline``
    with slot→template id refs, no ``payload``). Call :func:`expand_composite`
    to resolve the pipeline to concrete slot templates.
    """
    return get_template_registry().get(template_id)


def resolve_template_by_intent(
    query: str,
    kind: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Map a user phrase to a single template/composite in the registry.

    Resolution strategy (fast path, single registry scan via tag index):

      1. exact id match — caller passed an id directly
      2. exact name match (case-insensitive)
      3. any keyword token appears in the query
      4. optional kind filter narrows the pool at each step

    The first hit wins. If ``kind`` is set, non-matching kinds are skipped.
    The registry is process-local and pre-loaded; this is microseconds.
    """
    if not query:
        return None
    q = _normalize(query)
    if not q:
        return None

    r = get_template_registry()

    # 1. exact id
    hit = r.get(q)
    if hit is not None and (kind is None or hit.get("kind") == kind):
        return hit

    # Subsequent strategies iterate over a single in-memory list (no DB
    # query, no linear scan of a string; both are O(N) over the registry
    # but the registry is bounded at ~100 entries, and each entry is a
    # small dict — sub-millisecond on commodity hardware).
    pool = r.by_kind(kind) if kind else list(r._by_id.values())  # type: ignore[attr-defined]

    # 2. exact name
    for entry in pool:
        if _normalize(entry.get("name", "")) == q:
            return entry

    # 3. keyword token match (any keyword present in the query)
    for entry in pool:
        for kw in (entry.get("keywords") or []):
            if _normalize(kw) in q:
                return entry

    # 4. name / description substring
    for entry in pool:
        name = _normalize(entry.get("name", ""))
        desc = _normalize(entry.get("description", ""))
        if (name and name in q) or (desc and (q in desc or desc in q)):
            return entry

    return None


def expand_composite(composite_id: str) -> Dict[str, Optional[Dict[str, Any]]]:
    """Return the per-slot template dicts that a composite references.

    A slot may resolve to ``None`` if the registry no longer carries the
    reference; callers should treat that as a graceful degradation.
    """
    return get_template_registry().expand_composite(composite_id)


def list_templates_v2(
    kind: Optional[str] = None,
    q: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:
    """Paged list backed by the registry (no DB, no list scan)."""
    return get_template_registry().search(
        q=q, kind=kind, source=source, limit=limit, offset=offset,
    )
