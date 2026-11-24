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


def test_contrast_ratio_fails_closed_on_non_string():
    """非 str 输入不得抛 AttributeError（文档承诺非法色按黑处理）。"""
    from app.lib.cartography.palettes import contrast_ratio

    assert contrast_ratio(None, "#ffffff") == 21.0
    assert contrast_ratio("#ffffff", None) == 21.0
    assert contrast_ratio(None, None) == 1.0


def test_grayscale_ramp_separation_uses_parsed_rgb_for_functional_colors():
    """rgb()/rgba() 不能按"不透黑"算亮度（白底 alpha 合成须生效）。"""
    from app.lib.cartography.palettes import grayscale_ramp_separation

    sep = grayscale_ramp_separation(["#000000", "rgba(0, 0, 0, 0.5)"])
    assert sep is not None and sep > 0.0
