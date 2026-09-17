"""Standards QA service wrapper (ADR-0200).

Thin, stateless projection of the pure library seam for service/tool callers:
resolve the profile (explicit axes or MapSpec inference), run the versioned
pack, and return JSON-safe dicts. No mutation, no verdict authority — the
desired-state quality loop (`app.lib.cartography.quality_loop`) remains the
only repair executor.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.lib.cartography.standards.packs import get_standards_registry
from app.lib.cartography.standards.profile import resolve_profile
from app.lib.cartography.standards.qa import (
    evaluate_standards_qa,
    standards_precompile_gate,
)


def run_precompile_gate(
    mapspec: Dict[str, Any],
    source_profiles: Optional[Dict[str, Dict[str, Any]]] = None,
    *,
    purpose: Optional[str] = None,
    audience: Optional[str] = None,
    medium: Optional[str] = None,
    pack_id: str = "core",
    pack_version: Optional[str] = None,
    enabled: bool = True,
) -> Dict[str, Any]:
    """Gate a candidate before compilation; blocking needs an explicit profile."""
    explicit = any(v is not None for v in (purpose, audience, medium))
    profile = (
        resolve_profile(purpose=purpose, audience=audience, medium=medium, mapspec=mapspec)
        if explicit
        else resolve_profile(mapspec=mapspec)
    )
    return standards_precompile_gate(
        mapspec,
        source_profiles,
        profile=profile,
        pack_id=pack_id,
        pack_version=pack_version,
        registry=get_standards_registry(),
        enabled=enabled,
    )


def run_postcompile_qa(
    mapspec: Dict[str, Any],
    source_profiles: Optional[Dict[str, Dict[str, Any]]] = None,
    *,
    purpose: Optional[str] = None,
    audience: Optional[str] = None,
    medium: Optional[str] = None,
    pack_id: str = "core",
    pack_version: Optional[str] = None,
    enabled: bool = True,
) -> Dict[str, Any]:
    """Post-compile standards report (projection alongside the quality loop)."""
    explicit = any(v is not None for v in (purpose, audience, medium))
    profile = (
        resolve_profile(purpose=purpose, audience=audience, medium=medium, mapspec=mapspec)
        if explicit
        else resolve_profile(mapspec=mapspec)
    )
    return evaluate_standards_qa(
        mapspec,
        source_profiles,
        profile=profile,
        pack_id=pack_id,
        pack_version=pack_version,
        registry=get_standards_registry(),
        enabled=enabled,
    ).to_dict()


__all__ = ["run_postcompile_qa", "run_precompile_gate"]
