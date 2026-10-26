"""G3D03 三维量测库 conformance（ADR-0274 · app/lib/geo_analysis/measurements.py）。

解析曲面 oracle（独立手算期望，非实现回声）：

- 斜平面 z = s·(row + col)（cell 1 m）：bilinear 在线性表面精确 →
  surface_distance/ascent/slope/area 全部解析精确；
- 抛物线剖面：弦和 ≤ 真弧长，闭式 ∫√(1+4t²)dt 对照（容差反映采样离散）；
- LOS 曲率：独立密集解析网格 f(s)=h(1−s/D) − s(D−s)/(2R_eff) 的
  min/visible 判定对照（earth 与 earth_refracted 两模型）；
- 垂直基准治理：datum 词表外拒绝 / 覆盖值 datum 与 DEM 不一致拒绝 /
  unknown 单源放行 + 旗标；ft 精确 0.3048 折算。

负例与护栏：nodata error/skip 政策、超采样 ResourceScaleMismatch、
协作取消（段边界 checkpoint）、零长折线 DegenerateData、越界端点、
确定性（重复调用 dict 相等）。
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.lib.cancellation import CancellationToken, OperationCancelled, use_token
from app.lib.geo_analysis.measurements import (
    EARTH_RADIUS_M,
    REFRACTION_COEFFICIENT,
    line_of_sight,
    resolve_vertical_reference,
    surface_area,
    surface_distance,
    terrain_profile,
)
from app.lib.gis.scientific_errors import (
    DegenerateData,
    NoValidObservations,
    ResourceScaleMismatch,
    VerticalReferenceMismatch,
    VerticalUnitMismatch,
)

pytestmark = pytest.mark.unit

# GDAL 仿射（cell 1 m，UL 原点 (0, 12)）：world x ∈ [0, 12] 向右、
# y ∈ [0, 12] 向下 —— row = 12 − y − 0.5、col = x − 0.5。
_TF = (1.0, 0.0, 0.0, 0.0, -1.0, 12.0)


def _plane(n: int = 12, s: float = 0.5) -> np.ndarray:
    """z = s·(row + col)：沿 +x 或 +y 每 米 高差 s（平面，bilinear 精确）。"""
    return s * np.add.outer(np.arange(n), np.arange(n)).astype(float)


# ── 斜平面解析精确性 ────────────────────────────────────────────────


def test_surface_distance_inclined_plane_exact():
    dem = _plane()
    stats, meta = surface_distance(
        dem, 1.0, vertices=[[1.5, 0.5], [9.5, 0.5]], transform=_TF
    )
    exact_surface = 8.0 * math.sqrt(1 + 0.25)  # 8 m × √(1+s²), s=0.5
    assert stats["horizontal_m"] == pytest.approx(8.0, abs=1e-4)
    assert stats["surface_distance_m"] == pytest.approx(exact_surface, abs=1e-4)
    assert stats["slope_distance_m"] == pytest.approx(exact_surface, abs=1e-4)
    assert stats["ascent_m"] == pytest.approx(4.0, abs=1e-4)
    assert stats["descent_m"] == pytest.approx(0.0, abs=1e-4)
    assert stats["z_diff_m"] == pytest.approx(4.0, abs=1e-4)
    # 不变量：地表距离 ≥ 水平距离（同一米制网格空间）
    assert stats["surface_distance_m"] >= stats["horizontal_m"]
    # 平面上半步收敛指示为 0（线性表面采样精确）
    assert meta["convergence"]["delta_rel"] == 0.0


def test_surface_distance_polyline_vertex_inclusion():
    # 三顶点折线：每段水平 4 m；顶点必须是采样点（高差在顶点处折转）
    dem = _plane()
    stats, _ = surface_distance(
        dem, 1.0, vertices=[[1.5, 0.5], [5.5, 0.5], [5.5, 4.5]], transform=_TF
    )
    assert stats["horizontal_m"] == pytest.approx(8.0, abs=1e-4)
    # 第二段沿 +y：z = s·(row+col)，y 增 → row 减 → 高程下降 → 下坡 2 m
    assert stats["ascent_m"] == pytest.approx(2.0, abs=1e-4)
    assert stats["descent_m"] == pytest.approx(2.0, abs=1e-4)
    assert stats["surface_distance_m"] == pytest.approx(8.0 * math.sqrt(1.25), abs=1e-4)


def test_surface_area_inclined_plane_exact():
    n = 12
    dem = _plane(n=n)
    stats, meta = surface_area(dem, 1.0)
    # 对偶网格 quad 数 = (n−1)²；平面粗糙比 = √(1+2s²)
    exact = (n - 1) ** 2 * math.sqrt(1.5)
    assert stats["planar_area_m2"] == pytest.approx((n - 1) ** 2, abs=1e-4)
    assert stats["surface_area_m2"] == pytest.approx(exact, abs=1e-4)
    assert stats["roughness_ratio"] == pytest.approx(math.sqrt(1.5), abs=1e-6)
    assert stats["roughness_ratio"] >= 1.0
    assert stats["usable_quads"] == (n - 1) ** 2


def test_surface_area_mask_restricts_quads():
    dem = _plane()
    mask = np.zeros((12, 12), dtype=bool)
    mask[4:8, 4:8] = True
    stats, _ = surface_area(dem, 1.0, mask=mask)
    assert stats["planar_area_m2"] == pytest.approx(9.0, abs=1e-4)
    assert stats["surface_area_m2"] == pytest.approx(9.0 * math.sqrt(1.5), abs=1e-4)


def test_profile_inclined_plane_exact():
    dem = _plane()
    prof, meta = terrain_profile(
        dem, 1.0, vertices=[[1.5, 0.5], [9.5, 0.5]], transform=_TF
    )
    st = prof["statistics"]
    assert st["horizontal_m"] == pytest.approx(8.0, abs=1e-4)
    assert st["surface_distance_m"] == pytest.approx(8.0 * math.sqrt(1.25), abs=1e-4)
    assert st["ascent_m"] == pytest.approx(4.0, abs=1e-4)
    assert st["descent_m"] == pytest.approx(0.0, abs=1e-4)
    assert st["z_min_m"] == pytest.approx(6.0, abs=1e-4)  # row 11, col 1 → s·12
    assert st["z_max_m"] == pytest.approx(10.0, abs=1e-4)  # row 11, col 9
    assert meta["sample_count"] == 9  # 默认步长 1 m
    # 逐点单调距离与 z
    ds = [p["distance_m"] for p in prof["points"]]
    assert ds == sorted(ds)
    assert prof["points"][0]["z_m"] == pytest.approx(6.0, abs=1e-4)
    assert prof["points"][-1]["z_m"] == pytest.approx(10.0, abs=1e-4)
    # 平面上逐段坡度恒为 s
    slopes = [p["slope"] for p in prof["points"][1:] if p["slope"] is not None]
    assert all(abs(sl - 0.5) < 1e-6 for sl in slopes)


# ── 抛物线：采样近似 ≤ 真弧长，闭式对照 ─────────────────────────────


def test_surface_distance_parabola_closed_form():
    # z = t²（t = x 米，col t → world x = t + 0.5）：0..8 m 弧长闭式
    n = 10
    dem = np.zeros((4, n))
    # 路径沿 row 0（y = 3.5）；z = t²，t = x − 0.5（0..8 m）
    dem[0, :] = np.arange(n, dtype=float) ** 2
    tf = (1.0, 0.0, 0.0, 0.0, -1.0, 4.0)

    def arc(t0: float, t1: float) -> float:
        # ∫√(1+4t²)dt = (t/2)·√(1+4t²) + (1/4)·asinh(2t) —— review P1-1
        # 修正（首版括号层级使 asinh 系数差 2 倍因子，真值 64.9916）
        def prim(t: float) -> float:
            return (t / 2.0) * math.sqrt(1 + 4 * t * t) + 0.25 * math.asinh(2 * t)

        return prim(t1) - prim(t0)

    stats, meta = surface_distance(
        dem, 1.0, vertices=[[0.5, 3.5], [8.5, 3.5]], transform=tf, sampling_step_m=0.25
    )
    true_len = arc(0.0, 8.0)
    assert true_len == pytest.approx(64.991556, abs=1e-4)  # mpmath 复核锚点
    # bilinear 在凸函数上略微高估（线性插值 ≥ 凸函数），0.25 m 步长
    # 双侧离散误差 ~1.1e-3 相对
    assert stats["surface_distance_m"] == pytest.approx(true_len, rel=2e-3)
    # 半步收敛指示存在且小（加密应单调逼近真值）
    assert 0.0 <= meta["convergence"]["delta_rel"] < 2e-3
    assert (
        abs(meta["convergence"]["surface_distance_half_step_m"] - true_len)
        <= abs(stats["surface_distance_m"] - true_len) + 1e-6
    )


# ── LOS ────────────────────────────────────────────────────────────


def test_los_plane_visible_and_min_clearance():
    dem = _plane()
    los, meta = line_of_sight(
        dem,
        1.0,
        observer_xy=[1.5, 0.5],
        target_xy=[9.5, 0.5],
        transform=_TF,
        observer_height=5.0,
        target_height=2.0,
    )
    st = los["statistics"]
    assert st["visible"] is True
    assert st["obstruction"] is None
    # 平面上间隙线性 5 → 2：最小值 2 出现在目标端
    assert st["min_clearance_m"] == pytest.approx(2.0, abs=1e-4)
    assert st["min_clearance_distance_m"] == pytest.approx(8.0, abs=1e-4)
    assert meta["curvature"]["model"] == "none"


def test_los_ridge_occlusion_first_obstruction():
    dem = np.zeros((20, 20))
    dem[:, 10:14] = 30.0  # world x ∈ [10, 14)（col 10..13）的墙
    los, _ = line_of_sight(
        dem,
        1.0,
        observer_xy=[1.5, 0.5],
        target_xy=[18.5, 0.5],
        transform=_TF,
        observer_height=2.0,
        target_height=2.0,
    )
    st = los["statistics"]
    assert st["visible"] is False
    ob = st["obstruction"]
    # 首遮挡点 = 墙前沿的首个采样（步长 1 m → x=10.5 采样落在墙内）
    assert ob["x"] == pytest.approx(10.5, abs=1e-6)
    assert ob["terrain_z_m"] == pytest.approx(30.0, abs=1e-4)
    assert ob["clearance_m"] < 0
    assert st["min_clearance_m"] == pytest.approx(ob["clearance_m"], abs=1e-4)


def _analytic_min_clearance(d: float, obs_h: float, r_eff: float) -> float:
    ss = np.linspace(0.0, d, 400_001)
    return float(np.min(obs_h * (1.0 - ss / d) - ss * (d - ss) / (2.0 * r_eff)))


def test_los_curvature_matches_analytic_grid():
    big = np.zeros((2, 9004))
    tf = (1.0, 0.0, 0.0, 0.0, -1.0, 2.0)
    r_earth = EARTH_RADIUS_M
    r_refr = EARTH_RADIUS_M / (1.0 - REFRACTION_COEFFICIENT)
    for d, obs_h, curv, r_eff in [
        (7000.0, 1.7, "earth", r_earth),
        (7000.0, 1.7, "earth_refracted", r_refr),
        (9000.0, 1.7, "earth_refracted", r_refr),
        (5000.0, 1.7, "earth_refracted", r_refr),
    ]:
        los, meta = line_of_sight(
            big,
            1.0,
            observer_xy=[1.5, 0.5],
            target_xy=[1.5 + d, 0.5],
            transform=tf,
            observer_height=obs_h,
            target_height=0.0,
            curvature=curv,
        )
        want = _analytic_min_clearance(d, obs_h, r_eff)
        assert los["statistics"]["min_clearance_m"] == pytest.approx(want, abs=8e-3), (
            d,
            curv,
            los["statistics"]["min_clearance_m"],
            want,
        )
        assert los["statistics"]["visible"] == (want > 0.0)
        assert meta["curvature"]["effective_earth_radius_m"] == pytest.approx(
            r_eff, abs=1.0
        )
    # 无曲率模型平地永远可见（对照）
    los_none, _ = line_of_sight(
        big,
        1.0,
        observer_xy=[1.5, 0.5],
        target_xy=[6999.5, 0.5],
        transform=tf,
        observer_height=1.7,
        target_height=0.0,
        curvature="none",
    )
    assert los_none["statistics"]["visible"] is True


def test_los_symmetry_offsets_do_not_change_verdict():
    dem = np.zeros((20, 20))
    dem[:, 10:14] = 30.0
    fwd, _ = line_of_sight(
        dem,
        1.0,
        observer_xy=[1.5, 0.5],
        target_xy=[18.5, 0.5],
        transform=_TF,
        observer_height=2.0,
        target_height=6.0,
    )
    bwd, _ = line_of_sight(
        dem,
        1.0,
        observer_xy=[18.5, 0.5],
        target_xy=[1.5, 0.5],
        transform=_TF,
        observer_height=6.0,
        target_height=2.0,
    )
    assert fwd["statistics"]["visible"] == bwd["statistics"]["visible"] is False


# ── 垂直基准 / 单位治理（ADR-0274 核心）────────────────────────────


def test_vertical_reference_vocabulary_and_mismatch():
    r = resolve_vertical_reference("ellipsoid_height", "m")
    assert r["z_to_m"] == 1.0 and r["flags"] == []
    r_unk = resolve_vertical_reference("unknown", "m")
    assert r_unk["flags"] == ["vertical_datum_unknown"]
    with pytest.raises(VerticalReferenceMismatch):
        resolve_vertical_reference("navd88", "m")
    with pytest.raises(VerticalUnitMismatch):
        resolve_vertical_reference("unknown", "fathoms")
    with pytest.raises(VerticalReferenceMismatch) as ei:
        resolve_vertical_reference(
            "geoid_height_egm96", "m", override_vertical_datum="ellipsoid_height"
        )
    assert ei.value.scientific_code == "VERTICAL_REFERENCE_MISMATCH"
    # 一致声明（含 unknown==unknown）不拒绝 —— 拒绝的是「不同」
    ok = resolve_vertical_reference("unknown", "m", override_vertical_datum="unknown")
    assert ok["flags"] == ["vertical_datum_unknown"]


def test_measurement_ft_units_exact_conversion():
    n = 12
    dem_m = _plane(n=n)
    dem_ft = dem_m / 0.3048
    exact_m = (n - 1) ** 2 * math.sqrt(1.5)
    stats, meta = surface_area(dem_ft, 1.0, z_units="ft")
    assert stats["surface_area_m2"] == pytest.approx(exact_m, abs=1e-4)
    assert meta["z_units"] == "ft"
    assert any("0.3048" in t for t in meta["z_unit_transformations"])
    sd_ft, _ = surface_distance(
        dem_ft, 1.0, vertices=[[1.5, 0.5], [9.5, 0.5]], transform=_TF, z_units="ft"
    )
    assert sd_ft["surface_distance_m"] == pytest.approx(8.0 * math.sqrt(1.25), abs=1e-4)
    assert sd_ft["ascent_m"] == pytest.approx(4.0, abs=1e-4)


def test_los_absolute_z_override_same_datum_ok():
    dem = _plane()
    los, meta = line_of_sight(
        dem,
        1.0,
        observer_xy=[1.5, 0.5],
        target_xy=[9.5, 0.5],
        transform=_TF,
        observer_z=6.5,
        observer_height=2.0,
        target_height=2.0,
        vertical_datum="geoid_height_egm96",
        override_vertical_datum="geoid_height_egm96",
    )
    assert los["statistics"]["eye_z_observer_m"] == pytest.approx(8.5, abs=1e-4)
    assert "absolute_z_override_applied" in meta["quality_flags"]
    with pytest.raises(VerticalReferenceMismatch):
        line_of_sight(
            dem,
            1.0,
            observer_xy=[1.5, 0.5],
            target_xy=[9.5, 0.5],
            transform=_TF,
            observer_z=6.5,
            vertical_datum="geoid_height_egm96",
            override_vertical_datum="ellipsoid_height",
        )


# ── nodata / ErrorPolicy 语义 ──────────────────────────────────────


def _holed_plane() -> np.ndarray:
    dem = _plane()
    dem[:, 5:7] = np.nan
    return dem


def test_nodata_policy_error_and_skip():
    dem = _holed_plane()
    with pytest.raises(NoValidObservations) as ei:
        surface_distance(dem, 1.0, vertices=[[1.5, 0.5], [9.5, 0.5]], transform=_TF)
    assert ei.value.scientific_code == "NO_VALID_OBSERVATIONS"
    stats, meta = surface_distance(
        dem, 1.0, vertices=[[1.5, 0.5], [9.5, 0.5]], transform=_TF, nodata_policy="skip"
    )
    assert any(q.startswith("nodata_samples_skipped:") for q in meta["quality_flags"])
    assert any(q.startswith("nodata_fraction:") for q in meta["quality_flags"])
    # 洞区间剔除：地表距离小于无洞平面（诚实低估 + 披露）
    assert any(
        q.startswith("surface_stats_exclude_nodata_intervals:")
        for q in meta["quality_flags"]
    )
    assert stats["surface_distance_m"] < 8.0 * math.sqrt(1.25)


def test_covered_horizontal_invariant_with_hole_skip():
    """skip 政策跨洞剔除后：surface ≥ covered_horizontal 无条件成立
    （horizontal 含洞长可能大于 surface —— 契约限定披露，review P2-1）。"""
    dem = _holed_plane()
    stats, _ = surface_distance(
        dem, 1.0, vertices=[[1.5, 0.5], [9.5, 0.5]], transform=_TF, nodata_policy="skip"
    )
    assert stats["surface_distance_m"] >= stats["covered_horizontal_m"]
    assert stats["covered_horizontal_m"] < stats["horizontal_m"]  # 洞被剔除
    assert stats["surface_distance_m"] < stats["horizontal_m"]  # 限定披露场景
    # 无洞时 covered == horizontal
    clean, _ = surface_distance(
        _plane(), 1.0, vertices=[[1.5, 0.5], [9.5, 0.5]], transform=_TF
    )
    assert clean["covered_horizontal_m"] == pytest.approx(
        clean["horizontal_m"], abs=1e-4
    )


def test_fewer_than_two_valid_samples_typed_failure():
    dem = _plane()
    dem[:, 1:12] = np.nan  # 只剩 col 0 一个有效采样列
    # 先放宽比例阈值，让「有效点 < 2」守卫（比例守卫之后）可达
    with pytest.raises(NoValidObservations, match="fewer than 2 valid samples"):
        surface_distance(
            dem,
            1.0,
            vertices=[[0.5, 0.5], [9.5, 0.5]],
            transform=_TF,
            nodata_policy="skip",
            max_invalid_fraction=1.0,
        )


def test_nodata_skip_fraction_threshold_typed_failure():
    dem = _plane()
    dem[:, 2:12] = np.nan  # 大部分无效
    with pytest.raises(NoValidObservations):
        surface_distance(
            dem,
            1.0,
            vertices=[[1.5, 0.5], [9.5, 0.5]],
            transform=_TF,
            nodata_policy="skip",
        )


def test_los_endpoint_on_nodata_undefined():
    dem = _plane()
    dem[11, 1] = np.nan  # world (1.5, 0.5) → row 11, col 1
    with pytest.raises(NoValidObservations):
        line_of_sight(
            dem, 1.0, observer_xy=[1.5, 0.5], target_xy=[9.5, 0.5], transform=_TF
        )
    # 端点在栅格外同样无定义（skip 政策下端点锚点仍必须有效）
    with pytest.raises(NoValidObservations):
        line_of_sight(
            dem,
            1.0,
            observer_xy=[1.5, 0.5],
            target_xy=[9.5, 0.5],
            transform=_TF,
            nodata_policy="skip",
        )


def test_surface_area_nodata_threshold():
    dem = _plane()
    dem[:, 8:12] = np.nan  # 4/12 列无效 → quad 失效比例 ~0.5
    with pytest.raises(NoValidObservations, match="invalid quad fraction"):
        surface_area(dem, 1.0, max_invalid_fraction=0.25)
    stats, meta = surface_area(dem, 1.0, max_invalid_fraction=0.8)
    assert any(q.startswith("invalid_quad_fraction:") for q in meta["quality_flags"])
    assert stats["usable_quads"] < 11 * 11


# ── 护栏 / 取消 / 退化 / 确定性 ────────────────────────────────────


def test_oversampling_guard_resource_scale_mismatch():
    dem = np.zeros((4, 22000))
    tf = (1.0, 0.0, 0.0, 0.0, -1.0, 4.0)
    with pytest.raises(ResourceScaleMismatch):
        surface_distance(
            dem,
            1.0,
            vertices=[[1.5, 0.5], [21000.5, 0.5]],
            transform=tf,
            sampling_step_m=0.01,
        )


def test_cancellation_at_segment_boundary():
    dem = _plane()
    verts = [[1.5 + i, 0.5] for i in range(64)]
    token = CancellationToken(job_id="test-los-cancel")
    token.cancel()
    with use_token(token):
        with pytest.raises(OperationCancelled):
            surface_distance(dem, 1.0, vertices=verts, transform=_TF)


def test_degenerate_zero_length_polyline():
    dem = _plane()
    with pytest.raises(DegenerateData):
        surface_distance(dem, 1.0, vertices=[[3.5, 0.5], [3.5, 0.5]], transform=_TF)
    with pytest.raises(DegenerateData):
        line_of_sight(
            dem, 1.0, observer_xy=[3.5, 0.5], target_xy=[3.5, 0.5], transform=_TF
        )


def test_out_of_bounds_sampling_is_nodata_semantics():
    dem = _plane()
    # 目标点在栅格外（x > 12）：error 政策下类型化失败
    with pytest.raises(NoValidObservations):
        surface_distance(dem, 1.0, vertices=[[1.5, 0.5], [15.5, 0.5]], transform=_TF)


def test_invalid_argument_vocabularies():
    dem = _plane()
    with pytest.raises(ValueError, match="curvature"):
        line_of_sight(
            dem,
            1.0,
            observer_xy=[1.5, 0.5],
            target_xy=[9.5, 0.5],
            transform=_TF,
            curvature="galactic",
        )
    with pytest.raises(ValueError, match="nodata_policy"):
        surface_distance(
            dem,
            1.0,
            vertices=[[1.5, 0.5], [9.5, 0.5]],
            transform=_TF,
            nodata_policy="ignore",
        )
    with pytest.raises(ValueError, match="at least 2"):
        terrain_profile(dem, 1.0, vertices=[[1.5, 0.5]], transform=_TF)


def test_determinism_repeated_calls_dict_equal():
    dem = _plane()
    r1 = surface_distance(dem, 1.0, vertices=[[1.5, 0.5], [9.5, 0.5]], transform=_TF)
    r2 = surface_distance(dem, 1.0, vertices=[[1.5, 0.5], [9.5, 0.5]], transform=_TF)
    assert r1 == r2
    l1 = line_of_sight(
        dem,
        1.0,
        observer_xy=[1.5, 0.5],
        target_xy=[9.5, 0.5],
        transform=_TF,
        curvature="earth_refracted",
    )
    l2 = line_of_sight(
        dem,
        1.0,
        observer_xy=[1.5, 0.5],
        target_xy=[9.5, 0.5],
        transform=_TF,
        curvature="earth_refracted",
    )
    assert l1 == l2
