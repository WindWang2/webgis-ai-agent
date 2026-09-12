"""AC-03 golden 数值测试：固定数据与种子下 resolve_symbology / 分类 / CVD
模拟 / print 变换的**确定性数值**锁定。

本文件锁的是数值本身（不只是行为）——颜色算法禁止引入浮点不确定性，
任何数值漂移都必须在这里显式失败并经评审确认。
"""
import pytest

from app.lib.cartography.classify import classify_values
from app.lib.cartography.palettes import (
    COLOR_PALETTES,
    ciede2000,
    contrast_ratio,
    grayscale_ramp_separation,
    min_adjacent_delta_e,
    parse_css_color,
    print_desaturate,
    sample_ramp_colors,
    simulate_cvd,
)
from app.lib.cartography.symbology import (
    SymbologyConstraints,
    SymbologyIntent,
    SymbologyProfile,
    apply_clip,
    resolve_symbology,
)

HEAVY = [1, 1, 2, 2, 3, 3, 4, 5, 6, 8, 10, 13, 20, 40, 80, 160, 320, 640, 1280, 5000]
MID_SKEW = [10, 12, 14, 11, 13, 15, 12, 14, 16, 13, 11, 15, 14, 12, 17, 16, 13, 18, 15, 14]
SPIKE = [float(i % 10 + 1) for i in range(1195)] + [100.0] * 5


# ── 分类断点数值（classify 引擎，ADR-0073 语义）──────────────────────────────


def test_golden_classify_breaks():
    vals = [float(v) for v in HEAVY]
    # np.quantile 线性插值（deterministic）——20 值重尾样本的 5 分位断点
    assert classify_values(vals, "quantiles", 5) == pytest.approx(
        [1.0, 2.8, 5.6, 15.8, 192.0, 5000.0])
    assert classify_values(vals, "equal_interval", 3) == pytest.approx(
        [1.0, 1667.3333333333333, 3333.6666666666665, 5000.0])
    ht = classify_values(vals, "head_tail", 5)
    assert ht[0] == pytest.approx(1.0) and ht[-1] == pytest.approx(5000.0)
    assert ht == sorted(ht)


# ── CVD 模拟数值（Machado 2009 severity=1.0，线性 RGB）───────────────────────


def test_golden_cvd_simulation_values():
    # 纯红在绿色盲下的模拟色（截断到 2026-09 的实现数值）
    assert simulate_cvd("#ff0000", "cvd_deuteranopia") == "#a39000"
    assert simulate_cvd("#00ff00", "cvd_deuteranopia") == "#efd63a"
    # 模拟是幂等纯函数：同输入同输出
    assert simulate_cvd("#3182bd", "cvd_protanopia") == simulate_cvd(
        "#3182bd", "cvd_protanopia")
    assert simulate_cvd("#3182bd", "no_such_kind") is None
    assert simulate_cvd("not-a-color", "cvd_deuteranopia") is None


def test_golden_cvd_separability_collapse():
    """RdYlGn 在红绿色盲下相邻 ΔE00 崩溃——CVD 优先裁决的数值依据。"""
    ramp = COLOR_PALETTES["RdYlGn"]
    screen = min_adjacent_delta_e(ramp)
    deut = min_adjacent_delta_e([simulate_cvd(c, "cvd_deuteranopia") for c in ramp])
    assert screen == pytest.approx(11.6334199430711, abs=1e-6)
    assert deut == pytest.approx(0.5192572385606737, abs=1e-6)
    # 局部红-绿对（非相邻最劣位）的塌缩佐证
    pair = [ciede2000(parse_css_color("#d73027"), parse_css_color("#91cf60")),
            ciede2000(parse_css_color(simulate_cvd("#d73027", "cvd_deuteranopia")),
                      parse_css_color(simulate_cvd("#91cf60", "cvd_deuteranopia")))]
    assert pair[0] == pytest.approx(67.07509284910667, abs=1e-6)
    assert pair[1] < pair[0] / 3


def test_golden_ciede2000_reference_pairs():
    # Sharma et al. 2005 测试对的两个抽样锚点（实现既有锁定，防回退）
    assert ciede2000(parse_css_color("#ffffff"), parse_css_color("#000000")) > 100.0
    assert ciede2000(parse_css_color("#aaaaaa"), parse_css_color("#bbbbbb")) < 5.0


# ── print 变换与灰度可分级 ───────────────────────────────────────────────────


def test_golden_print_desaturate_and_gray_separation():
    yl = COLOR_PALETTES["YlOrRd"]
    printed = print_desaturate(yl)
    # 首色 #ffffb2 的亮度已接近灰阶目标 → 混合后为极浅奶白（数值锁定）
    assert printed[0] == "#fdfdcb"
    sep_before = grayscale_ramp_separation(yl)
    sep_after = grayscale_ramp_separation(printed)
    assert sep_before == pytest.approx(0.1079872, abs=1e-4)
    assert sep_after == pytest.approx(0.1236981, abs=1e-4)
    assert sep_after >= 0.06


# ── 采样器与 WCAG ────────────────────────────────────────────────────────────


def test_golden_ramp_sampling_midpoints():
    assert sample_ramp_colors("YlOrRd", 3) == ["#fed976", "#fd8d3c", "#bd0026"]
    assert sample_ramp_colors("YlOrRd", 6) == COLOR_PALETTES["YlOrRd"]
    assert sample_ramp_colors("no-such", 3) == []


def test_golden_contrast_ratio_anchors():
    assert contrast_ratio("#000000", "#ffffff") == 21.0
    assert contrast_ratio("#777777", "#ffffff") == pytest.approx(4.48, abs=0.05)


# ── 决策 golden（完整决策 JSON 快照）────────────────────────────────────────


def test_golden_decision_heavy_tail():
    d = resolve_symbology(SymbologyProfile(values=[float(v) for v in HEAVY]))
    payload = d.to_dict()
    assert payload["method"] == "head_tail"
    assert payload["k"] == 5
    assert payload["palette"] == "YlOrRd"
    assert payload["clip_policy"] == "head_tail"
    assert payload["source"] == "distribution"
    assert payload["confidence"] == 0.85
    rejected_methods = {r["value"] for r in payload["rejected"] if r["kind"] == "method"}
    assert {"equal_interval", "quantiles"} <= rejected_methods


def test_golden_decision_clip_p99_numbers():
    d = resolve_symbology(SymbologyProfile(values=SPIKE))
    assert d.clip_policy == "clip_p99"
    assert d.n_clipped == 5
    assert d.clip_high == pytest.approx(10.0)
    clipped, _lo, high, n = apply_clip(SPIKE, "clip_p99")
    assert n == 5 and high == pytest.approx(10.0)
    assert max(clipped) == pytest.approx(10.0)


def test_golden_k_cap_blues_screen_strict():
    c = SymbologyConstraints(min_class_delta_e=12.0)
    d = resolve_symbology(
        SymbologyProfile(values=[float(v) for v in MID_SKEW]),
        SymbologyIntent(requested_palette="Blues"), c)
    assert d.palette == "Blues" and d.k == 3
    # 门限放宽回默认后同数据同色带可到 5 级
    d2 = resolve_symbology(
        SymbologyProfile(values=[float(v) for v in MID_SKEW]),
        SymbologyIntent(requested_palette="Blues"))
    assert d2.k == 5


@pytest.mark.parametrize("seed", [42])
def test_golden_jenks_downsample_determinism(seed):
    """Jenks >1000 样本降采样（default_rng(seed)）决策稳定。"""
    import numpy as np

    rng = np.random.default_rng(seed)
    values = rng.lognormal(mean=1.0, sigma=1.5, size=2000).tolist()
    b1 = classify_values(values, "natural_breaks", 5)
    b2 = classify_values(values, "natural_breaks", 5)
    assert b1 == b2
    d1 = resolve_symbology(SymbologyProfile(values=values))
    d2 = resolve_symbology(SymbologyProfile(values=values))
    assert d1.to_dict() == d2.to_dict()
