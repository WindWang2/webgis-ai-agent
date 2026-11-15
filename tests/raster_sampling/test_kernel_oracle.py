"""WP05/WP06 采样内核 oracle — 解析期望值，不用被测函数生成期望。"""
from __future__ import annotations

import math

import numpy as np
import pytest

from app.services.raster_sampling import kernel
from app.services.raster_sampling.contracts import RasterSamplingError


def _grid(h=4, w=4):
    return np.arange(h * w, dtype="float64").reshape(h, w)


def test_source_pixel_floor_tie_semantics():
    """S05：floor 反算与 G20 probe 同口径（tie → 左/上像元）。"""
    data = _grid()
    valid = np.ones_like(data, dtype=bool)
    facts = kernel.BandFacts(index=1, label="band 1")
    # 站位恰在像元边界（col_f=1.0）→ 命中 col 1（floor）
    out = kernel.sample_source_pixel(data, valid, 1, 1, facts, shape=(4, 4))
    assert out["valid"] is True
    assert out["raw"] == data[1, 1] == 5.0


def test_source_pixel_outside_not_clamped():
    """S08：范围外保持 outside，不钳到边缘像元。"""
    data = _grid()
    valid = np.ones_like(data, dtype=bool)
    facts = kernel.BandFacts(index=1)
    out = kernel.sample_source_pixel(data, valid, 0, 4, facts, shape=(4, 4))
    assert out["valid"] is False
    assert out["reason"] == "outside_extent"
    out2 = kernel.sample_source_pixel(data, valid, -1, 0, facts, shape=(4, 4))
    assert out2["reason"] == "outside_extent"


def test_bilinear_linear_gradient_analytic():
    """S04：线性梯度内插 = 解析双线性（四像元中心之间）。"""
    data = np.array([[0.0, 2.0], [4.0, 6.0]])  # z = 2x + 4y（像元索引域）
    valid = np.ones((2, 2), dtype=bool)
    facts = kernel.BandFacts(index=1, semantic="continuous")
    # 像元 (0,0) 中心 (0.5,0.5)，(0,1) 中心 (1.5,0.5)… 站位 (1.0,1.0)
    # = 四中心正中 → 期望 (0+2+4+6)/4 = 3.0；z(1.0,1.0)=2*1+4*1=6? 否：
    # 索引域函数 z(r,c)=2c+4r → z(1,1)=6 ≠ 3 —— 双线性在中心插值不等于
    # 索引点值。解析期望按位置计算：u=v=0.5 → 0*(1-u)(1-v)+2*u(1-v)
    # +4*(1-u)v+6*uv = 1+2+1.5 = 3.0
    out = kernel.sample_bilinear(data, valid, 1.0, 1.0, facts, shape=(2, 2))
    assert out["valid"] is True
    assert math.isclose(out["interpolated"], 3.0, abs_tol=1e-12)
    assert out["support_ratio"] == 1.0
    # 权重和 = 1
    wsum = sum(s["weight"] for s in out["supports"])
    assert math.isclose(wsum, 1.0, abs_tol=1e-12)
    # 插值不是原像元观测
    f = kernel.value_facts_to_row_fields(facts, "bilinear", out)
    assert f["value_domain"] == "interpolated"
    assert f["interpolated_value"] == 3.0


def test_bilinear_at_pixel_center_equals_pixel():
    data = _grid()
    valid = np.ones_like(data, dtype=bool)
    facts = kernel.BandFacts(index=1, semantic="continuous")
    # 站位恰在像元 (2,2) 中心 (col_f=2.5, row_f=2.5)
    out = kernel.sample_bilinear(data, valid, 2.5, 2.5, facts, shape=(4, 4))
    assert math.isclose(out["interpolated"], float(data[2, 2]), abs_tol=1e-12)


def test_bilinear_strict_masked_support():
    """S09：四邻域一角无效 → 严格策略无效；renorm 显式方法 + 支撑占比。"""
    data = np.array([[0.0, 2.0], [4.0, 6.0]])
    valid = np.array([[True, True], [True, False]])
    facts = kernel.BandFacts(index=1, semantic="continuous")
    out = kernel.sample_bilinear(data, valid, 1.0, 1.0, facts, shape=(2, 2))
    assert out["valid"] is False
    assert out["reason"] == "masked_support"
    assert out["support_ratio"] == 0.75
    # renorm：有效三像元权重归一 —— 解析期望 (0*1 + 2*1 + 4*1)/3 → 中心点
    # u=v=0.5：权重 (0.25,0.25,0.25,0.25)，缺 (1,1) 角 → 归一后
    # (0*0.25 + 2*0.25 + 4*0.25)/(0.75) = 2.0
    out2 = kernel.sample_bilinear(data, valid, 1.0, 1.0, facts, shape=(2, 2),
                                  mode="bilinear_renormalized")
    assert out2["valid"] is True
    assert math.isclose(out2["interpolated"], 2.0, abs_tol=1e-12)
    assert math.isclose(out2["support_ratio"], 0.75, abs_tol=1e-12)
    f2 = kernel.value_facts_to_row_fields(facts, "bilinear_renormalized", out2)
    assert f2["value_domain"] == "interpolated"


def test_renorm_insufficient_support_typed_invalid():
    """支撑占比低于阈值 → insufficient_support（不悄悄填洞）。"""
    data = np.zeros((2, 2))
    valid = np.array([[False, True], [False, False]])  # 仅 (0,1) 有效
    facts = kernel.BandFacts(index=1, semantic="continuous")
    # row_f=1.0, col_f=0.6 → (0,1) 权重 0.5*0.1=0.05 < 0.25 阈值
    out = kernel.sample_bilinear(data, valid, 1.0, 0.6, facts, shape=(2, 2),
                                 mode="bilinear_renormalized")
    assert out["valid"] is False
    assert out["reason"] == "insufficient_support"


def test_categorical_band_rejects_interpolation():
    """S06/S07：分类声明拒绝插值；dtype 不参与判定。"""
    facts = kernel.BandFacts(index=1, semantic="categorical",
                             semantic_source="declared", dtype="float32")
    with pytest.raises(RasterSamplingError) as ei:
        facts.validate_method("bilinear")
    assert ei.value.code == "METHOD_INCOMPATIBLE"
    # 整数 dtype + continuous 声明 → 可插值（S07）
    ok = kernel.BandFacts(index=1, semantic="continuous",
                          semantic_source="declared", dtype="int16")
    ok.validate_method("bilinear")
    # 未声明 → 插值拒绝（需要显式语义来源）
    undeclared = kernel.BandFacts(index=1, dtype="float32")
    with pytest.raises(RasterSamplingError):
        undeclared.validate_method("bilinear")
    # 源像元方法永远可用
    facts.validate_method("source_pixel")
    undeclared.validate_method("source_pixel")


def test_scale_offset_exactly_once():
    """S10：scale=0 / 负 scale / offset=0 都按声明恰一次换算。"""
    # scale=0（合法声明）→ physical = raw*0 + offset = offset
    facts0 = kernel.BandFacts(index=1, scale=0.0, offset=5.0,
                              scale_declared=True, offset_declared=True)
    assert kernel.apply_physical_once(100.0, facts0) == 5.0
    # 负 scale
    facts_neg = kernel.BandFacts(index=1, scale=-0.5, offset=10.0,
                                 scale_declared=True, offset_declared=True)
    assert kernel.apply_physical_once(100.0, facts_neg) == -40.0
    # offset=0 显式声明（不被 falsy 吞掉）→ 只有 scale 生效
    facts_o0 = kernel.BandFacts(index=1, scale=0.1, offset=0.0,
                                scale_declared=True, offset_declared=True)
    assert kernel.apply_physical_once(100.0, facts_o0) == 10.0
    # 未声明 → 中性（1/0），不是「无换算值」
    facts_none = kernel.BandFacts(index=1)
    assert kernel.apply_physical_once(100.0, facts_none) == 100.0


def test_invalid_reason_taxonomy():
    """S08：nodata / masked / 非有限元分型。"""
    facts = kernel.BandFacts(index=1, nodata=-9999.0)
    ok, reason = kernel.classify_raw(-9999.0, False, facts)
    assert (ok, reason) == (False, "nodata")
    ok, reason = kernel.classify_raw(5.0, True, facts)
    assert (ok, reason) == (False, "masked")
    ok, reason = kernel.classify_raw(float("nan"), False, facts)
    assert (ok, reason) == (False, "non_finite")
    ok, reason = kernel.classify_raw(5.0, False, facts)
    assert ok and reason is None


def test_raw_exact_int64_beyond_js_safe():
    """S33：|int| > 2^53 → 精确十进制串，不默认 float 丢位。"""
    big = 2 ** 53 + 7  # 9007199254740999 —— float64 会舍入为 ...000
    exact = kernel.raw_exact_if_needed(big)
    assert exact == "9007199254740999"
    assert float(big) != big  # float 确实丢位（换算通道存在的理由）
    assert kernel.raw_exact_if_needed(42) is None
    assert kernel.raw_exact_if_needed(1.5) is None


def test_world_to_grid_rotated_rectilinear_affine():
    """S16：旋转/非方形仿射的反算（解析 oracle）。"""
    # 旋转网格：x = -10·row + 100（row 轴向西），y = 10·col + 200（col 轴向北）
    transform = (0.0, -10.0, 100.0, 10.0, 0.0, 200.0)
    # (col=2, row=3) → x = -30 + 100 = 70; y = 20 + 200 = 220
    col_f, row_f = kernel.world_to_grid(transform, 70.0, 220.0)
    assert math.isclose(col_f, 2.0, abs_tol=1e-9)
    assert math.isclose(row_f, 3.0, abs_tol=1e-9)
    # 非方形像元：a=2.5（x 方向 2.5/col），e=-4.0
    t2 = (2.5, 0.0, 0.0, 0.0, -4.0, 100.0)
    c2, r2 = kernel.world_to_grid(t2, 10.0, 60.0)
    assert math.isclose(c2, 4.0, abs_tol=1e-9)
    assert math.isclose(r2, 10.0, abs_tol=1e-9)


def test_bilinear_edge_half_pixel_missing_support():
    """站位在栅格边缘半像元内：支撑越界 → masked_support（不钳位）。"""
    data = _grid()
    valid = np.ones_like(data, dtype=bool)
    facts = kernel.BandFacts(index=1, semantic="continuous")
    # col_f=0.2（< 0.5）：支撑 col -1 越界
    out = kernel.sample_bilinear(data, valid, 2.5, 0.2, facts, shape=(4, 4))
    assert out["valid"] is False
    assert out["reason"] == "masked_support"
    # 但 col_f=0.6（≥0.5）支撑完整
    out2 = kernel.sample_bilinear(data, valid, 2.5, 0.6, facts, shape=(4, 4))
    assert out2["valid"] is True


def test_band_facts_semantics_resolution_order():
    """语义来源优先级：用户声明 > 元数据（dataset classification）> 未声明。"""

    class _FakeSource:
        ref_id = "ref:x"
        band_meta = [{"index": 1, "label": "band 1",
                      "scale_declared": False, "offset_declared": False}]

        def band(self, index):
            return next((m for m in self.band_meta
                         if int(m.get("index")) == int(index)),
                        {"index": index, "label": f"band {index}"})

        dataset_tags = {"classification": "continuous"}

    base = kernel.band_facts_from_source(_FakeSource(), 1)
    assert base.semantic == "continuous" and base.semantic_source == "metadata"
    declared = kernel.band_facts_from_source(
        _FakeSource(), 1, declared_semantics="categorical")
    assert declared.semantic == "categorical"
    assert declared.semantic_source == "declared"


def test_bilinear_center_zero_weight_neighbor_not_fatal():
    """站位恰在像元中心：权重 0 的无效邻像元不影响估计（评审 P3）。
    估计值 = 中心像元原值（权重 1）。"""
    data = np.array([[1.0, 99.0], [99.0, 99.0]])  # (0,0) 有效，其余无效
    valid = np.array([[True, False], [False, False]])
    facts = kernel.BandFacts(index=1, semantic="continuous")
    # 站位在像元 (0,0) 中心：col_f=row_f=0.5 → 三个邻支撑权重恰 0
    out = kernel.sample_bilinear(data, valid, 0.5, 0.5, facts, shape=(2, 2))
    assert out["valid"] is True
    assert math.isclose(out["interpolated"], 1.0, abs_tol=1e-12)
    # 仍有权重的支撑无效才致命（对照：稍偏离中心 → masked_support）
    out2 = kernel.sample_bilinear(data, valid, 0.5, 0.6, facts, shape=(2, 2))
    assert out2["valid"] is False
    assert out2["reason"] == "masked_support"
