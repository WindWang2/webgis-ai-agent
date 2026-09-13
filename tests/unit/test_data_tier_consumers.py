"""A7 threshold single-point consumer gate (§8.1.1 adaptation, ADR-0170/0163).

V11 landed first: the single point is ``app.lib.cartography.data_tiers``
(ADR-0163). This line deleted its self-built ``acquisition_limits`` module and
re-points every acquisition-side consumer to V11's tiers — **禁止两线各建一套**.

This file keeps only the grep assertions (per §8.1.1): every former literal
site imports the single point; the old module no longer exists; tier values
are the frozen calibration anchors.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

from app.core.config import settings
from app.lib.cartography.data_tiers import (
    TIER_EXPORT_FEATURES,
    TIER_INLINE_FEATURES,
    TIER_SCAN_CAP_FEATURES,
)

REPO = Path(__file__).resolve().parents[2]

# The seven former A7 sites (file → required import token).
A7_SITES = {
    "app/services/mapspec_source.py": "data_tiers",
    "app/services/data_fabric/adapters/postgis_adapter.py": "data_tiers",
    "app/api/routes/data_quality.py": "data_tiers",
    "app/services/data_profile/unified.py": "data_tiers",
    "app/services/mapspec/composite_builder.py": "data_tiers",
    "app/services/mapspec/lifecycle_engine.py": "data_tiers",
    "app/services/publication_export.py": "data_tiers",
}


def test_tier_values_are_frozen_anchors():
    """V11 single point values must equal the former acquisition magnitudes."""
    assert TIER_INLINE_FEATURES == 5_000
    assert TIER_SCAN_CAP_FEATURES == 20_000
    assert TIER_EXPORT_FEATURES == 50_000


def test_self_built_single_point_is_deleted():
    """§8.1.1: no competing second single point may exist."""
    with __import__("pytest").raises(ModuleNotFoundError):
        importlib.import_module("app.services.data_fabric.acquisition_limits")


def test_settings_map_quality_gate_default_matches_tier():
    """lifecycle_engine's settings fallback and config default stay at tier one."""
    assert settings.MAP_QUALITY_GATE_MAX_FEATURES == TIER_INLINE_FEATURES


def test_a7_sites_import_single_point_and_no_literal_left():
    for rel in A7_SITES:
        src = (REPO / rel).read_text(encoding="utf-8")
        assert "data_tiers" in src, f"{rel} no longer imports the single point"
    forbidden_patterns = [
        r"INLINE_FEATURE_LIMIT\s*=\s*5000\b",
        r"MVT_MAX_FEATURES_PER_TILE\s*=\s*20_?000\b",
        r"_MAX_INLINE_FEATURES\s*=\s*20000\b",
        r"maxFeatures\":\s*50000\b",
        r"return\s+50000\b",
        r",\s*50000\)",
        r"MAP_QUALITY_GATE_MAX_FEATURES[^\n]*,\s*5000\b",
    ]
    for rel in A7_SITES:
        src = (REPO / rel).read_text(encoding="utf-8")
        for pattern in forbidden_patterns:
            hit = re.search(pattern, src)
            assert hit is None, f"scattered threshold literal survived in {rel}: {pattern}"


def test_no_maxfeatures_numeric_literal_anywhere_in_app():
    offenders = []
    for py in (REPO / "app").rglob("*.py"):
        src = py.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r'"maxFeatures"\s*:\s*(\d+)', src):
            offenders.append(f"{py.relative_to(REPO)}:{src[:m.start()].count(chr(10)) + 1} → {m.group(0)}")
    assert offenders == [], "numeric maxFeatures literals remain:\n" + "\n".join(offenders)
