"""Geographic granularity parsing (DS6, ADR-0176).

"省/市/县/乡镇/街道/网格/流域" → the D1 ``granularity`` vocabulary, bilingual.
Deterministic mapping; unknown → None (never a guessed level).
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

#: Canonical granularity vocabulary (D1.granularity values, coarse→fine).
GRANULARITY_ORDER = [
    "country",
    "province",
    "city",
    "county",
    "township",
    "street",
    "grid",
    "basin",
    "point",
]

_ALIASES: list[Tuple[str, str]] = [
    # (pattern, canonical) — first match wins per level set; word-ish matching
    ("国家级|national", "country"),
    ("省市", "city"),  # 「省市」是复合词（跨省的城市层面），先于单「省」判定
    ("省级?|province|state", "province"),
    ("地级市|地市|市级?|city|prefecture", "city"),
    ("县级?|区县|county|district", "county"),
    ("乡镇级?|乡镇|街道级?|township|街道", "township"),
    ("网格|grid|h3|tile", "grid"),
    ("流域|basin|watershed", "basin"),
    ("点位|poi|point", "point"),
]


def parse_granularity(text: str) -> Optional[Tuple[str, float]]:
    """Extract the geographic granularity from a query fragment.

    Returns ``(canonical, confidence)`` or ``None`` when nothing granular is
    expressed. Confidence is higher when the term is a dedicated word
    (e.g. "县级") than a bare substring hit.
    """
    if not text:
        return None
    lowered = text.lower()
    best: Optional[Tuple[str, float]] = None
    for pattern, canonical in _ALIASES:
        m = re.search(pattern, lowered)
        if m:
            confidence = 0.9 if m.group(0) else 0.6
            if best is None or confidence > best[1]:
                best = (canonical, confidence)
    return best


def normalize_granularity(value: Optional[str]) -> Optional[str]:
    """Map a free-form level word onto the canonical vocabulary (idempotent)."""
    if not value:
        return None
    v = value.strip().lower()
    if v in GRANULARITY_ORDER:
        return v
    parsed = parse_granularity(v)
    return parsed[0] if parsed else None


__all__ = ["GRANULARITY_ORDER", "parse_granularity", "normalize_granularity"]
