"""Profile resolution — purpose × audience × medium (v1, ADR-0200)."""
from __future__ import annotations

import pytest

from app.lib.cartography.standards.profile import (
    ProfileSpec,
    ProfileSpecError,
    infer_profile,
    resolve_profile,
)


class TestExplicitResolution:
    def test_explicit_profile_is_strict(self):
        profile = resolve_profile(purpose="publication", audience="public", medium="print")
        assert profile == ProfileSpec(
            purpose="publication", audience="public", medium="print",
            strict=True, inferred=False, source="explicit",
        )

    def test_partial_explicit_fills_from_inference_then_defaults(self, tmp_path):
        mapspec = {"cartographic_profile": "thematic_map"}
        profile = resolve_profile(purpose="publication", mapspec=mapspec)
        assert profile.purpose == "publication"
        assert profile.audience == "public"
        assert profile.medium == "screen"
        assert profile.strict is True

    def test_invalid_axis_rejected(self):
        with pytest.raises(ProfileSpecError):
            resolve_profile(purpose="propaganda")
        with pytest.raises(ProfileSpecError):
            resolve_profile(medium="hologram")


class TestInference:
    def test_rule_profile_to_purpose(self):
        assert infer_profile({"cartographic_profile": "thematic_map"}) == ("analysis", "screen")
        assert infer_profile({"cartographic_profile": "general_analysis"}) == ("exploration", "screen")
        assert infer_profile({"cartographic_profile": "statistical_map"}) == ("analysis", "screen")

    def test_output_purpose_to_medium(self):
        spec = {"layout": {"output_purpose": "a4_landscape"}}
        assert infer_profile(spec) == ("exploration", "print")
        spec = {"layout": {"output_purpose": "screen_16_9"}}
        assert infer_profile(spec) == ("exploration", "screen")

    def test_frame_page_size_hint_to_print(self):
        spec = {"frame": {"pageSize": {"size": "A4"}}}
        assert infer_profile(spec)[1] == "print"

    def test_empty_mapspec_defaults_lightest(self):
        assert infer_profile({}) == ("exploration", "screen")
        profile = resolve_profile(mapspec={})
        assert profile.strict is False and profile.inferred is True
        assert profile.source == "inferred"

    def test_strict_cannot_be_forced_onto_inferred(self):
        profile = resolve_profile(mapspec={})
        assert profile.strict is False
        with pytest.raises(ProfileSpecError):
            # construction guard: inferred + strict is contradictory
            ProfileSpec(purpose="analysis", audience="public", medium="screen",
                        strict=True, inferred=True, source="inferred")


class TestLegacyMapsUnaffected:
    def test_legacy_map_without_any_profile_keys(self):
        legacy = {"version": "1.0", "sources": {}, "layers": []}
        profile = resolve_profile(mapspec=legacy)
        assert (profile.purpose, profile.audience, profile.medium) == (
            "exploration", "public", "screen")
        assert profile.strict is False
