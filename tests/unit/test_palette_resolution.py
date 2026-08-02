"""Tests for palette resolution (ADR-0037 Win 4)."""
from app.lib.cartography.palettes import COLOR_PALETTES, resolve_palette_colors


def test_resolve_known_palette_returns_its_colors():
    """A known palette name resolves to its COLOR_PALETTES entry (as a fresh list)."""
    result = resolve_palette_colors("YlOrRd")
    assert result == list(COLOR_PALETTES["YlOrRd"])
    # Returns a copy, not the internal list — mutating must not leak.
    result.append("#000000")
    assert "#000000" not in COLOR_PALETTES["YlOrRd"]


def test_resolve_unknown_palette_falls_back_to_default():
    """An unknown palette name falls back to YlOrRd."""
    result = resolve_palette_colors("does-not-exist")
    assert result == list(COLOR_PALETTES["YlOrRd"])


def test_resolve_unknown_palette_with_custom_fallback():
    """The fallback argument is honored when the primary is unknown."""
    result = resolve_palette_colors("does-not-exist", fallback="Blues")
    assert result == list(COLOR_PALETTES["Blues"])


def test_resolve_returns_list_type():
    """Always returns a list (callers index/slice it)."""
    assert isinstance(resolve_palette_colors("Viridis"), list)
    assert isinstance(resolve_palette_colors("nope"), list)
