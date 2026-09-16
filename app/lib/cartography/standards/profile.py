"""Standards profile — purpose × audience × medium resolution (ADR-0200).

The three axes decide which obligations apply and how strictly. Resolution is
explicit-first (caller-provided axes always win), then inferred from existing
MapSpec vocabulary (``cartographic_profile`` rule profile, OUTPUT_PURPOSES
canvas, frame pageSize), defaulting to the *lightest* honest combination —
legacy maps must not gain blocking failures just for lacking new metadata.

Strictness contract: ``strict=True`` only when the profile was explicitly
declared (or the caller demands it). Under a non-strict profile, error-level
obligations are *reported* at warning severity — visible, never fake-passed,
never blocking. Under strict profiles they keep their declared severity and
the pre-compile gate may block on them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from app.lib.cartography.standards.rule import (
    MAP_AUDIENCES,
    MAP_MEDIUMS,
    MAP_PURPOSES,
    StandardsRuleError,
)

#: Profile axes in canonical order (for projections and catalogs).
MAP_PROFILE_AXES: Tuple[str, ...] = ("purpose", "audience", "medium")

#: Rule-profile → purpose mapping (existing `_review_profile` vocabulary).
_RULE_PROFILE_PURPOSE: Dict[str, str] = {
    "general_analysis": "exploration",
    "thematic_map": "analysis",
    "statistical_map": "analysis",
    "raster_result": "exploration",
    "network_result": "analysis",
}

#: OUTPUT_PURPOSES canvas → medium (print canvases are the a4_*/a3_* family).
_PRINT_CANVASES = frozenset({"a4_portrait", "a4_landscape", "a3_portrait", "a3_landscape"})

_PRINT_PAGE_HINTS = frozenset({"a4", "a3", "letter", "legal", "tabloid"})


class ProfileSpecError(StandardsRuleError):
    """Invalid or ambiguous profile resolution."""


@dataclass(frozen=True)
class ProfileSpec:
    """Resolved profile + strictness + provenance (explainable obligation base)."""

    purpose: str
    audience: str
    medium: str
    strict: bool
    inferred: bool
    source: str  # "explicit" | "inferred"

    def __post_init__(self) -> None:
        for axis, value in (
            ("purpose", self.purpose),
            ("audience", self.audience),
            ("medium", self.medium),
        ):
            vocabulary = {
                "purpose": MAP_PURPOSES,
                "audience": MAP_AUDIENCES,
                "medium": MAP_MEDIUMS,
            }[axis]
            if value not in vocabulary:
                raise ProfileSpecError(
                    f"profile.{axis}={value!r} 非法（合法：{', '.join(vocabulary)}）")
        if self.strict and self.inferred:
            raise ProfileSpecError("推断 profile 不得声明 strict（显式声明才可严格）")
        if self.strict != (self.source == "explicit"):
            # strict derives from explicitness; kept as a field for stable
            # serialization, validated here so the two can never diverge.
            object.__setattr__(self, "strict", self.source == "explicit")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "purpose": self.purpose,
            "audience": self.audience,
            "medium": self.medium,
            "strict": self.strict,
            "inferred": self.inferred,
            "source": self.source,
        }


def resolve_profile(
    *,
    purpose: Optional[str] = None,
    audience: Optional[str] = None,
    medium: Optional[str] = None,
    mapspec: Optional[Dict[str, Any]] = None,
) -> ProfileSpec:
    """Explicit axes win; missing axes infer from the MapSpec; then defaults.

    Defaults: purpose=exploration, audience=public, medium=screen — the
    lightest honest combination (back-compat guard, ADR-0200 D4).
    """
    explicit_any = any(v is not None for v in (purpose, audience, medium))
    inferred_purpose, inferred_medium = _infer_from_mapspec(mapspec)
    resolved_purpose = purpose or inferred_purpose or "exploration"
    resolved_audience = audience or "public"
    resolved_medium = medium or inferred_medium or "screen"
    source = "explicit" if explicit_any else "inferred"
    return ProfileSpec(
        purpose=resolved_purpose,
        audience=resolved_audience,
        medium=resolved_medium,
        strict=source == "explicit",
        inferred=source == "inferred",
        source=source,
    )


def infer_profile(mapspec: Dict[str, Any]) -> Tuple[str, str]:
    """(purpose, medium) inference from existing MapSpec vocabulary only."""
    purpose, medium = _infer_from_mapspec(mapspec)
    return (purpose or "exploration", medium or "screen")


def _infer_from_mapspec(
    mapspec: Optional[Dict[str, Any]],
) -> Tuple[Optional[str], Optional[str]]:
    if not isinstance(mapspec, dict):
        return (None, None)
    purpose: Optional[str] = None
    rule_profile = mapspec.get("cartographic_profile")
    if isinstance(rule_profile, str):
        purpose = _RULE_PROFILE_PURPOSE.get(rule_profile)
    medium: Optional[str] = None
    layout = mapspec.get("layout") if isinstance(mapspec.get("layout"), dict) else {}
    output_purpose = layout.get("output_purpose") if isinstance(layout, dict) else None
    if isinstance(output_purpose, str):
        if output_purpose in _PRINT_CANVASES:
            medium = "print"
        elif output_purpose.startswith("screen"):
            medium = "screen"
    if medium is None:
        frame = mapspec.get("frame") if isinstance(mapspec.get("frame"), dict) else None
        page_size = frame.get("pageSize") if isinstance(frame, dict) else None
        if isinstance(page_size, dict):
            candidate = str(page_size.get("size") or page_size.get("preset") or "")
            if candidate.lower() in _PRINT_PAGE_HINTS:
                medium = "print"
    return (purpose, medium)


__all__ = [
    "MAP_PROFILE_AXES",
    "ProfileSpec",
    "ProfileSpecError",
    "infer_profile",
    "resolve_profile",
]
