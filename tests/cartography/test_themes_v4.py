"""Theme / Palette V4 —— 领域主题与对比度诊断契约测试.

锁定：
- 11 主题（5 V3 种子 + 6 V4 领域主题）；领域主题词表封闭；
- 主题校验闭环：print 主题推荐全 print_safe、cb_safe_first 主题推荐全色盲安全；
- WCAG 对比度工具（contrast_ratio / meets_wcag_contrast）数值正确性；
- palette_contrast_diagnostics 未知调色板不编造；
- 模型 default_theme 绑定可解析（design_system 校验闭环）。
"""
from __future__ import annotations

import pytest

from app.lib.cartography.palettes import (
    COLOR_PALETTES,
    contrast_ratio,
    meets_wcag_contrast,
    palette_contrast_diagnostics,
)
from app.lib.cartography.themes import get_cartographic_theme_registry

pytestmark = pytest.mark.cartography

V4_THEMES = {
    "cartographic.scientific",
    "cartographic.publication",
    "cartographic.government",
    "cartographic.remote_sensing",
    "cartographic.terrain",
    "cartographic.risk_communication",
}


def test_v4_theme_catalog_expansion():
    reg = get_cartographic_theme_registry()
    ids = {t.id for t in reg.themes()}
    assert V4_THEMES <= ids
    assert len(ids) >= 11
    # 校验闭环（print 全 print_safe / cb_first 全色盲安全）
    assert reg.validate() == []


def test_publication_theme_is_print_safe_strict():
    reg = get_cartographic_theme_registry()
    theme = reg.get_theme("cartographic.publication")
    assert theme.profile == "print"
    rec = theme.palettes
    for pid in [*rec.sequential, *rec.diverging, *rec.perceptual_uniform]:
        desc = reg.get_palette(pid)
        assert desc is not None and desc.print_safe, (
            f"publication 推荐的 {pid} 必须灰度可分级")
    # qualitative 全不安全 → 如实留空
    assert rec.qualitative == []


def test_remote_sensing_theme_dark_and_cb_safe_first():
    reg = get_cartographic_theme_registry()
    theme = reg.get_theme("cartographic.remote_sensing")
    assert theme.profile == "dark"
    assert theme.colorblind_safe_first
    for pid in theme.palettes.ids():
        desc = reg.get_palette(pid)
        assert desc is not None and desc.colorblind_safe, pid


def test_wcag_contrast_math():
    assert contrast_ratio("#000000", "#ffffff") == 21.0
    assert contrast_ratio("#ffffff", "#ffffff") == 1.0
    assert meets_wcag_contrast("#000000", "#ffffff", level="AAA")
    # 4.5 边界附近：#767676 vs white ≈ 4.54（AA 通过、AAA 不通过）
    assert meets_wcag_contrast("#767676", "#ffffff", level="AA")
    assert not meets_wcag_contrast("#767676", "#ffffff", level="AAA")
    # 大字阈值 3.0：#939394 ≈ 3.02（大字通过但正文不通过）
    assert meets_wcag_contrast("#939394", "#ffffff", large_text=True)
    assert not meets_wcag_contrast("#939394", "#ffffff", large_text=False)
    # 非法输入 fail-closed（按黑处理，不抛异常不编造）
    assert isinstance(contrast_ratio("#zzz", "#ffffff"), float)


def test_palette_contrast_diagnostics_shape():
    d = palette_contrast_diagnostics("RdBu", canvas="#ffffff")
    assert d["canvas"] == "#ffffff"
    assert d["min_ratio"] <= d["max_ratio"]
    assert len(d["per_color"]) == len(COLOR_PALETTES["RdBu"])
    for entry in d["per_color"]:
        assert set(entry) == {"color", "ratio", "aa", "aa_large"}
    # 未知调色板不编造
    assert palette_contrast_diagnostics("no_such_palette") == {}


def test_model_default_theme_bindings_resolve():
    from app.lib.cartography.design_system import resolve_map_model_requirement
    from app.lib.cartography.model_library import get_map_model_registry
    reg = get_map_model_registry()
    themed = [m for m in reg._by_id.values() if m.default_theme]
    assert len(themed) >= 8
    for m in themed:
        req = resolve_map_model_requirement(m.id)
        assert req is not None
        assert req.default_theme == m.default_theme
        theme = get_cartographic_theme_registry().get_theme(m.default_theme)
        assert theme is not None, f"{m.id}: default_theme 未注册"
