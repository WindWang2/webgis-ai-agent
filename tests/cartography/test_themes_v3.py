"""Cartographic Theme & Palette V3 — 主题/调色板描述层契约测试（ADR-0101 D4）。

锁定：
- PaletteDescriptor 不复制 hex（推导自 palettes.py）；print-safe 推导确定；
- colorblind_safe 与 PALETTE_KINDS 零漂移；
- theme 推荐清单只含已注册色带；print 主题必须色盲安全优先；
- 对比度工具数学正确（黑/白 = 21:1）；
- registry 校验干净 + 确定性。
"""
from __future__ import annotations

import pytest

from app.lib.cartography.palettes import COLOR_PALETTES, NATIVE_HEATMAP_COLORS
from app.lib.cartography.themes import (
    SEED_THEMES,
    build_palette_descriptors,
    get_cartographic_theme_registry,
    relative_luminance,
    reset_cartographic_theme_registry,
    wcag_contrast_ratio,
)

pytestmark = pytest.mark.cartography


@pytest.fixture(autouse=True)
def _fresh_registry():
    reset_cartographic_theme_registry()
    yield
    reset_cartographic_theme_registry()


def test_palette_descriptors_cover_all_registered_palettes() -> None:
    reg = get_cartographic_theme_registry()
    known = set(COLOR_PALETTES) | set(NATIVE_HEATMAP_COLORS)
    assert {p.id for p in reg.palettes()} == known


def test_palette_descriptor_has_no_hex() -> None:
    """描述层不携带颜色真值（hex 唯一真值在 palettes.py）。"""
    for p in build_palette_descriptors():
        dumped = p.model_dump_json()
        assert "#" not in dumped or "note_zh" in dumped, (
            f"{p.id}: descriptor 携带了 hex（违反单一真值约束）"
        )
        assert not hasattr(p, "colors") and not hasattr(p, "hexes")


def test_colorblind_flags_match_palette_kinds() -> None:
    from app.lib.cartography.model_library import PALETTE_KINDS

    reg = get_cartographic_theme_registry()
    for pid, meta in PALETTE_KINDS.items():
        desc = reg.get_palette(pid)
        assert desc is not None
        assert desc.colorblind_safe == meta.colorblind_safe, (
            f"{pid}: colorblind_safe 与 PALETTE_KINDS 漂移"
        )
        assert desc.kind == meta.kind


def test_print_safe_derivation_deterministic_and_sane() -> None:
    reg_a = get_cartographic_theme_registry()
    reset_cartographic_theme_registry()
    reg_b = get_cartographic_theme_registry()
    for pid in COLOR_PALETTES:
        assert reg_a.get_palette(pid).print_safe == reg_b.get_palette(pid).print_safe
    # Viridis 官方宣传即灰度打印保真 —— 推导不得推翻
    assert reg_a.get_palette("Viridis").print_safe


def test_theme_palette_recommendations_resolve() -> None:
    reg = get_cartographic_theme_registry()
    for theme in reg.themes():
        for pid in theme.palettes.ids():
            assert reg.get_palette(pid) is not None, (
                f"theme {theme.id}: 推荐色带 {pid} 未注册"
            )


def test_print_theme_requires_colorblind_safe_first() -> None:
    reg = get_cartographic_theme_registry()
    for theme in reg.themes_for_profile("print"):
        assert theme.colorblind_safe_first
        # RdYlGn（红绿色盲不友好）不得出现在 print 推荐
        assert "RdYlGn" not in theme.palettes.diverging


def test_recommend_converges_to_registered_ids() -> None:
    reg = get_cartographic_theme_registry()
    ids = reg.recommend("cartographic.print_paper", "sequential")
    assert ids and all(reg.get_palette(i) for i in ids)
    assert reg.recommend("nonexistent-theme", "sequential") == []


def test_contrast_math() -> None:
    assert abs(wcag_contrast_ratio("#000000", "#ffffff") - 21.0) < 0.01
    assert wcag_contrast_ratio("#ffffff", "#ffffff") == 1.0
    # 同色对比为 1；黑白上下文次序无关
    assert wcag_contrast_ratio("#ffffff", "#000000") == wcag_contrast_ratio("#000000", "#ffffff")
    assert 0.0 < relative_luminance("#777777") < 1.0


def test_theme_registry_validate_clean() -> None:
    assert get_cartographic_theme_registry().validate() == []


def test_theme_validate_catches_stale_palette_reference() -> None:
    from app.lib.cartography.themes import CartographicThemeDescriptor, PaletteRecommendation

    reg = get_cartographic_theme_registry()
    reg._themes["cartographic.bad"] = CartographicThemeDescriptor(
        id="cartographic.bad", name_zh="坏主题",
        palettes=PaletteRecommendation(sequential=["NoSuchPalette"]),
    )
    issues = reg.validate()
    assert any("cartographic.bad" in i for i in issues)


def test_seed_theme_ids_stable() -> None:
    reg = get_cartographic_theme_registry()
    assert {t.id for t in SEED_THEMES} <= set(t.id for t in reg.themes())
