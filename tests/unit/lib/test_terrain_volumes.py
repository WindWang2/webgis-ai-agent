"""G3D-V2-C 土方体积 conformance（app/lib/geo_analysis/volumes.py）。

解析 oracle（独立手算期望，非实现回声——中点求积对 ≤1 次表面精确）：

- 平面常基准：z ≡ 10，base 8，10×10 像元 × 100 m² → cut = 2 m ×
  10000 m² = 20000 m³ 精确，fill = 0；
- 斜面列分割：z = 0.1·(col − 4.5)，base = mean → cut = fill = 1250 m³
  （闭式：10 行 × 100 m² × 0.1 × (0.5+1.5+2.5+3.5+4.5) = 1250），
  net = 0（mean 基准的解析恒等式）；
- 立方台阶：半域抬升 5 m → cut = 50 × 5 × 100 精确；
- 掩膜半域：仅右半计入 → 体积恰为一半（线性表面积可加）；
- nodata 洞：洞剔除不进任何方量，invalid_fraction 披露，超阈类型化失败；
- ft DEM：×0.3048 精确折算到 m³；
- 垂直基准：基准面声明 datum ≠ DEM datum → VerticalReferenceMismatch；
  词表外 datum/单位 → 类型化拒绝；unknown 单源放行 + 旗标。

负例：空掩膜 / 全 nodata / reference 词表外 / constant 缺 base_z /
mean 带 base_z / 非有限 base_z / 掩膜形状不符。
"""

from __future__ import annotations

import numpy as np
import pytest

from app.lib.geo_analysis.volumes import cut_fill_volume
from app.lib.gis.scientific_errors import (
    NoValidObservations,
    VerticalReferenceMismatch,
    VerticalUnitMismatch,
)

pytestmark = pytest.mark.unit


def _flat(n: int = 10, z: float = 10.0, cell: float = 10.0) -> np.ndarray:
    return np.full((n, n), z, dtype=float)


# ── 解析 oracle：平面常基准精确 ────────────────────────────────────


def test_flat_plane_constant_base_exact():
    stats, meta = cut_fill_volume(_flat(), 10.0, reference="constant", base_z=8.0)
    assert stats["cut_volume_m3"] == pytest.approx(20000.0, abs=1e-6)
    assert stats["fill_volume_m3"] == pytest.approx(0.0, abs=1e-9)
    assert stats["net_volume_m3"] == pytest.approx(20000.0, abs=1e-6)
    assert stats["usable_cells"] == 100
    assert stats["cut_depth_max_m"] == pytest.approx(2.0)
    assert meta["volume_unit"].startswith("m³")


def test_flat_plane_below_base_is_fill():
    stats, _ = cut_fill_volume(_flat(z=3.0), 10.0, reference="constant", base_z=8.0)
    assert stats["cut_volume_m3"] == pytest.approx(0.0, abs=1e-9)
    assert stats["fill_volume_m3"] == pytest.approx(50000.0, abs=1e-6)
    assert stats["net_volume_m3"] == pytest.approx(-50000.0, abs=1e-6)
    assert stats["fill_depth_max_m"] == pytest.approx(5.0)


# ── 解析 oracle：斜面 mean 基准（闭式）────────────────────────────


def test_tilted_plane_mean_base_closed_form():
    """z = 0.1·(col − 4.5)（10×10，cell 10 m，base = mean = 0）。

    cut（col ≥ 5 的正部）= 10 行 × 100 m² × 0.1 × Σ(col−4.5, col=5..9)
                        = 1000 × 0.1 × 12.5 = 1250 m³；fill 对称。
    """
    cols = np.arange(10)
    dem = 0.1 * (cols[None, :] - 4.5) * np.ones((10, 1))
    stats, meta = cut_fill_volume(dem, 10.0, reference="mean")
    assert stats["cut_volume_m3"] == pytest.approx(1250.0, abs=1e-6)
    assert stats["fill_volume_m3"] == pytest.approx(1250.0, abs=1e-6)
    assert stats["net_volume_m3"] == pytest.approx(0.0, abs=1e-9)
    assert stats["base_z"] == pytest.approx(0.0, abs=1e-9)
    assert stats["grade_cells"] == 0


def test_mean_base_net_zero_is_analytic_identity():
    """任意表面上 mean 基准 → Σdz = 0 → cut − fill = 0（恒等式）。"""
    rng = np.random.default_rng(11)
    dem = rng.normal(50.0, 8.0, size=(12, 9))
    stats, _ = cut_fill_volume(dem, 5.0, cell_size_x=7.0, reference="mean")
    assert stats["net_volume_m3"] == pytest.approx(0.0, abs=1e-6)


# ── 解析 oracle：台阶（分段常数精确）──────────────────────────────


def test_step_plateau_exact():
    dem = np.zeros((10, 10))
    dem[5:, :] = 5.0  # 半域抬升 5 m
    stats, _ = cut_fill_volume(dem, 10.0, reference="constant", base_z=0.0)
    assert stats["cut_volume_m3"] == pytest.approx(50 * 5 * 100, abs=1e-6)
    assert stats["cut_cells"] == 50
    assert stats["grade_cells"] == 50


def test_mask_restricts_region_additively():
    """线性表面上体积按区域可加：左半 + 右半 = 全域（分区不重不漏）。"""
    cols = np.arange(10)
    dem = 0.2 * np.tile(cols, (10, 1)).astype(float)  # z = 0.2·col ≥ 0
    mask_r = np.zeros((10, 10), dtype=bool)
    mask_r[:, 5:] = True
    mask_l = ~mask_r
    full, _ = cut_fill_volume(dem, 10.0, reference="constant", base_z=0.0)
    right, _ = cut_fill_volume(
        dem, 10.0, mask=mask_r, reference="constant", base_z=0.0
    )
    left, _ = cut_fill_volume(
        dem, 10.0, mask=mask_l, reference="constant", base_z=0.0
    )
    assert full["cut_volume_m3"] == pytest.approx(
        right["cut_volume_m3"] + left["cut_volume_m3"], abs=1e-6
    )
    assert right["usable_cells"] == 50


# ── nodata 治理 ────────────────────────────────────────────────────


def test_nodata_hole_excluded_and_disclosed():
    dem = _flat(z=10.0)
    dem[4:6, 4:6] = np.nan  # 中央 2×2 洞
    stats, meta = cut_fill_volume(dem, 10.0, reference="constant", base_z=8.0)
    assert stats["usable_cells"] == 96
    assert stats["cut_volume_m3"] == pytest.approx(96 * 2 * 100, abs=1e-6)
    assert meta["invalid_fraction"] == pytest.approx(0.04)
    assert any("invalid_cell_fraction" in f for f in meta["quality_flags"])


def test_nodata_over_threshold_fails_typed():
    dem = _flat(z=10.0)
    dem[:, :6] = np.nan  # 60% 无效
    with pytest.raises(NoValidObservations):
        cut_fill_volume(
            dem, 10.0, reference="constant", base_z=8.0, max_invalid_fraction=0.5
        )


def test_all_nodata_region_fails():
    dem = _flat(z=10.0)
    dem[:] = np.nan
    with pytest.raises(NoValidObservations):
        cut_fill_volume(dem, 10.0, reference="constant", base_z=8.0)


# ── 单位与垂直基准（ADR-0274 复用）─────────────────────────────────


def test_feet_units_convert_exactly():
    stats, meta = cut_fill_volume(
        _flat(z=10.0), 10.0, reference="constant", base_z=8.0, z_units="ft"
    )
    # (10−8) ft = 2×0.3048 m；体积 = 0.6096 × 10000 m² × …
    assert stats["cut_volume_m3"] == pytest.approx(
        2.0 * 0.3048 * 10000, abs=1e-6
    )
    assert meta["z_unit_transformations"]


def test_override_datum_mismatch_refused():
    with pytest.raises(VerticalReferenceMismatch):
        cut_fill_volume(
            _flat(),
            10.0,
            reference="constant",
            base_z=8.0,
            vertical_datum="ellipsoid_height",
            override_vertical_datum="geoid_height_egm96",
        )


def test_datum_vocabulary_enforced():
    with pytest.raises(VerticalReferenceMismatch):
        cut_fill_volume(
            _flat(), 10.0, reference="constant", base_z=8.0,
            vertical_datum="navd88-not-in-vocabulary",
        )
    with pytest.raises(VerticalUnitMismatch):
        cut_fill_volume(
            _flat(), 10.0, reference="constant", base_z=8.0, z_units="furlongs"
        )


def test_unknown_datum_single_source_allowed_with_flag():
    _stats, meta = cut_fill_volume(
        _flat(), 10.0, reference="constant", base_z=8.0
    )
    assert "vertical_datum_unknown" in meta["quality_flags"]


# ── 边界披露 ───────────────────────────────────────────────────────


def test_boundary_cells_disclosed_for_partial_mask():
    dem = _flat()
    mask = np.zeros((10, 10), dtype=bool)
    mask[2:8, 2:8] = True
    stats, meta = cut_fill_volume(
        dem, 10.0, mask=mask, reference="constant", base_z=8.0
    )
    assert meta["boundary_cells"] == 20  # 6×6 内部块的边缘像元
    assert any(f.startswith("boundary_cells:") for f in meta["quality_flags"])
    assert stats["region_cells"] == 36


# ── 负例与确定性 ───────────────────────────────────────────────────


def test_reference_vocabulary_and_base_rules():
    with pytest.raises(ValueError):
        cut_fill_volume(_flat(), 10.0, reference="fitted_plane", base_z=1.0)
    with pytest.raises(ValueError):
        cut_fill_volume(_flat(), 10.0, reference="constant")
    with pytest.raises(ValueError):
        cut_fill_volume(_flat(), 10.0, reference="constant", base_z=float("nan"))
    with pytest.raises(ValueError):
        cut_fill_volume(_flat(), 10.0, reference="mean", base_z=3.0)


def test_empty_mask_and_shape_mismatch():
    with pytest.raises((NoValidObservations, ValueError)):
        cut_fill_volume(
            _flat(), 10.0, mask=np.zeros((10, 10), dtype=bool),
            reference="constant", base_z=1.0,
        )
    with pytest.raises(ValueError):
        cut_fill_volume(
            _flat(), 10.0, mask=np.zeros((3, 3), dtype=bool),
            reference="constant", base_z=1.0,
        )


def test_deterministic_repeat():
    rng = np.random.default_rng(3)
    dem = rng.normal(100.0, 10.0, size=(8, 8))
    a = cut_fill_volume(dem, 10.0, reference="constant", base_z=95.0)
    b = cut_fill_volume(dem, 10.0, reference="constant", base_z=95.0)
    assert a[0] == b[0] and a[1] == b[1]
