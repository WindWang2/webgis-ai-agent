"""像素级 golden diff（ADR-0159 P3）回归锁。

覆盖任务书 §5 验收：golden diff 容差策略生效（逐通道 ±16，
多点距离 >48），有正负例测试。
"""
from __future__ import annotations

import io

import pytest

from app.lib.cartography.golden_diff import (
    PASS_PIXEL_RATIO,
    PER_CHANNEL_TOLERANCE,
    SAMPLE_POINT_MIN_DISTANCE,
    color_distance,
    compare_golden,
    image_diff,
    ink_ratio,
    sample_points_distinguishable,
)


def _png_bytes(color: tuple, size=(8, 8), noise: int = 0) -> bytes:
    """合成纯色（可加每通道确定性噪声）PNG。"""
    from PIL import Image

    img = Image.new("RGB", size, color)
    if noise:
        pixels = img.load()
        for y in range(size[1]):
            for x in range(size[0]):
                base = pixels[x, y]
                delta = ((x + y) % (noise + 1)) - noise // 2
                pixels[x, y] = tuple(
                    max(0, min(255, c + delta)) for c in base
                )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── 正例 ─────────────────────────────────────────────────────────────────


def test_identical_images_pass():
    png = _png_bytes((240, 240, 240))
    verdict = image_diff(png, png)
    assert verdict["pass"] is True
    assert verdict["within_ratio"] == 1.0
    assert verdict["max_channel_diff"] == 0


def test_small_noise_within_tolerance_passes():
    """逐通道 ±16 内的渲染抖动不算劣化（抗反锯齿/字体抖动）。"""
    base = _png_bytes((240, 240, 240))
    shifted = _png_bytes((240 + PER_CHANNEL_TOLERANCE, 240, 240))
    verdict = image_diff(base, shifted)
    assert verdict["pass"] is True
    assert verdict["within_ratio"] == 1.0


def test_compare_golden_positive_with_distinguishable_samples():
    base = _png_bytes((240, 240, 240))
    cur = _png_bytes((230, 235, 230))
    verdict = compare_golden(
        base, cur,
        baseline_samples=[(240, 240, 240), (200, 30, 30)],
        current_samples=[(230, 235, 230), (205, 35, 35)],
    )
    assert verdict["pass"] is True


# ── 负例 ─────────────────────────────────────────────────────────────────


def test_color_shift_beyond_tolerance_fails():
    """单通道 +17（> ±16 容差）全图超差 → 拦截。"""
    base = _png_bytes((200, 200, 200))
    shifted = _png_bytes((200 + PER_CHANNEL_TOLERANCE + 1, 200, 200))
    verdict = image_diff(base, shifted)
    assert verdict["pass"] is False
    assert verdict["within_ratio"] == 0.0


def test_partial_shift_below_pass_ratio_fails():
    """5% 像素超容差（> 2% 失败线）→ 拦截。"""
    base = _png_bytes((240, 240, 240))
    # 8x8=64 像素中让 4 个（6.25%）红 200+。
    drifted = _png_bytes((240, 240, 240))
    from PIL import Image

    img = Image.open(io.BytesIO(drifted))
    pixels = img.load()
    for x in range(4):
        pixels[x, 0] = (30, 30, 30)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    verdict = image_diff(base, buf.getvalue())
    assert verdict["within_ratio"] == pytest.approx(0.9375)
    assert verdict["pass"] is False


def test_size_mismatch_fails_without_resize():
    base = _png_bytes((240, 240, 240), size=(8, 8))
    other = _png_bytes((240, 240, 240), size=(16, 16))
    verdict = image_diff(base, other)
    assert verdict["pass"] is False
    assert "size mismatch" in verdict["reason"]


def test_undecodable_png_raises_valueerror():
    with pytest.raises(ValueError):
        image_diff(b"not a png", b"not a png")


# ── 取色点可分性（probe 经验：多点通道距离 >48） ─────────────────────────


def test_sample_points_distinguishable_positive():
    verdict = sample_points_distinguishable([(240, 240, 240), (200, 30, 30)])
    assert verdict["pass"] is True
    pair = verdict["pairs"][0]
    assert pair["distance"] > SAMPLE_POINT_MIN_DISTANCE


def test_sample_points_collapse_fails():
    """两个取色点收敛到同色（假 golden / 图层未渲染）→ 拦截。"""
    verdict = sample_points_distinguishable([(240, 240, 240), (235, 235, 235)])
    assert verdict["pass"] is False
    assert verdict["pairs"][0]["ok"] is False


def test_color_distance_respects_tolerance():
    assert color_distance((240, 240, 240), (240, 240, 240)) == 0
    # 默认 tolerance=0：每通道差 10 → 三通道和 30。
    assert color_distance((240, 240, 240), (250, 250, 250)) == 30
    assert color_distance(
        (240, 240, 240), (250, 250, 250), tolerance=PER_CHANNEL_TOLERANCE
    ) == 0
    assert color_distance(
        (240, 240, 240), (240, 240, 240 + 48 + 1),
        tolerance=PER_CHANNEL_TOLERANCE,
    ) == 48 + 1 - PER_CHANNEL_TOLERANCE


def test_pass_ratio_constant_is_strict():
    """通过线必须严于 1.0 - 1/32：真实渲染抖动可过，成片劣化必拦。"""
    assert 0.95 <= PASS_PIXEL_RATIO < 1.0


# ── 墨量带（review 修复：像素通过线对小要素消失是盲的） ─────────────────


def test_ink_ratio_measures_feature_share():
    """红块在浅底上的墨量 ≈ 其像素占比（角点为背景参照）。"""
    from PIL import Image

    img = Image.new("RGB", (100, 100), (240, 240, 240))
    pixels = img.load()
    for x in range(20, 30):        # 10x10 = 1% 墨量
        for y in range(20, 30):
            pixels[x, y] = (200, 30, 30)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    ink = ink_ratio(buf.getvalue())
    assert ink == pytest.approx(0.01, abs=0.002)


def test_ink_ratio_flat_canvas_is_zero():
    png = _png_bytes((240, 240, 240))
    assert ink_ratio(png) == 0.0


def test_ink_band_catches_feature_vanish_that_pixel_budget_misses():
    """1% 墨量的要素整块消失：within_ratio≈0.99 仍过 98% 线，墨量带必拦。"""
    base = _png_bytes((240, 240, 240))
    from PIL import Image

    drifted = Image.new("RGB", (8, 8), (240, 240, 240))  # 要素没了
    buf = io.BytesIO()
    drifted.save(buf, format="PNG")
    diff = image_diff(base, buf.getvalue())
    assert diff["pass"] is True  # 像素通过线对 1.6% 墨量损失是盲的
    ink_golden = ink_ratio(_png_bytes_with_dot())
    ink_now = ink_ratio(buf.getvalue())
    assert abs(ink_now - ink_golden) > max(0.001, 0.4 * ink_golden)  # 带拦截


def _png_bytes_with_dot() -> bytes:
    from PIL import Image

    img = Image.new("RGB", (8, 8), (240, 240, 240))
    pixels = img.load()
    for x in range(0, 1):        # 单像素着色 ≈ 1.6% 墨量
        pixels[x, 0] = (200, 30, 30)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── 晋升分档谓词（review 修复：quarantine 机制必须有测试） ───────────────


def test_blocking_predicate_matches_adr_0065_tiers():
    import importlib.util
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "scripts" / "golden_baseline.py"
    spec = importlib.util.spec_from_file_location("golden_baseline_mod", script)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("golden_baseline_mod", mod)
    spec.loader.exec_module(mod)

    assert mod._is_blocking("pr-blocking") is True
    assert mod._is_blocking("nightly-only") is False
    assert mod._is_blocking("quarantine") is False
