"""G48 WP10: External Analytic Affine Oracle & Solver Diagnostics Tests (R10-R16)."""
from __future__ import annotations

import math

import pytest

from app.schemas.georeferencing_schema import GcpPoint
from app.services.georeferencing.solver import solve_affine_gcps


def _oracle_forward(affine: tuple[float, ...], px: float, py: float) -> tuple[float, float]:
    """External direct 6-parameter analytic affine oracle: (a1, a2, a0, b1, b2, b0)."""
    a1, a2, a0, b1, b2, b0 = affine
    return a1 * px + a2 * py + a0, b1 * px + b2 * py + b0


def _make_gcp(gid: str, px: float, py: float, mx: float, my: float, *, role="fit", enabled=True) -> GcpPoint:
    return GcpPoint(
        gcp_id=gid,
        label=gid,
        px=px,
        py=py,
        map_x=mx,
        map_y=my,
        role=role,
        enabled=enabled,
    )


def test_r10_analytic_affine_oracle_translation_scale_rotate_shear():
    W, H = 800, 600
    theta = math.radians(23.5)
    sx, sy = 2.4, -3.1
    shear = 0.08
    a1 = math.cos(theta) * sx
    a2 = math.cos(theta) * (shear * sx) - math.sin(theta) * sy
    b1 = math.sin(theta) * sx
    b2 = math.sin(theta) * (shear * sx) + math.cos(theta) * sy
    a0, b0 = 445123.75, 4418902.5
    oracle = (a1, a2, a0, b1, b2, b0)

    pts = [(40.5, 50.5), (750.5, 45.5), (760.5, 560.5), (30.5, 550.5), (400.25, 300.75)]
    gcps = [_make_gcp(f"g{i}", px, py, *_oracle_forward(oracle, px, py)) for i, (px, py) in enumerate(pts)]

    diag = solve_affine_gcps(gcps, width=W, height=H)
    assert diag.status == "valid"
    assert diag.rank == 3
    assert diag.fit_rmse is not None and diag.fit_rmse < 1e-6
    c = diag.coefficients
    assert c is not None
    assert (c.a1, c.a2, c.a0, c.b1, c.b2, c.b0) == pytest.approx(oracle, rel=1e-9, abs=1e-6)

    px_c, py_c = 400.0, 300.0
    got_x = c.a0 + c.a1 * px_c + c.a2 * py_c
    got_y = c.b0 + c.b1 * px_c + c.b2 * py_c
    exp_x, exp_y = _oracle_forward(oracle, px_c, py_c)
    assert math.hypot(got_x - exp_x, got_y - exp_y) < 1e-6


def test_r11_insufficient_and_collinear_rank_deficient():
    W, H = 500, 400
    diag2 = solve_affine_gcps(
        [_make_gcp("p1", 10, 10, 1000, 2000), _make_gcp("p2", 400, 10, 2000, 2000)],
        width=W,
        height=H,
    )
    assert diag2.status == "insufficient"
    assert diag2.coefficients is None

    collinear = [
        _make_gcp(f"c{i}", 50.0 * i + 10.0, 50.0 * i + 10.0, 1000.0 + 100.0 * i, 2000.0 - 100.0 * i)
        for i in range(4)
    ]
    diag_col = solve_affine_gcps(collinear, width=W, height=H)
    assert diag_col.status == "degenerate"
    assert diag_col.coefficients is None


def test_r12_reflection_vs_singular_map_targets():
    W, H = 400, 400
    refl = [
        _make_gcp("r1", 20, 20, 2000, 3000),
        _make_gcp("r2", 380, 20, 1000, 3000),
        _make_gcp("r3", 380, 380, 1000, 2000),
        _make_gcp("r4", 20, 380, 2000, 2000),
    ]
    diag_r = solve_affine_gcps(refl, width=W, height=H)
    assert diag_r.status == "valid"
    assert diag_r.coefficients is not None
    assert diag_r.coefficients.is_reflection is True
    assert diag_r.coefficients.determinant > 0

    singular = [
        _make_gcp("s1", 20, 20, 1000, 2000),
        _make_gcp("s2", 380, 20, 1000, 2000),
        _make_gcp("s3", 380, 380, 1000, 2000),
        _make_gcp("s4", 20, 380, 1000, 2000),
    ]
    diag_s = solve_affine_gcps(singular, width=W, height=H)
    assert diag_s.status == "degenerate"
    assert diag_s.coefficients is None


def test_r13_three_point_exact_zero_residual_honest_warning():
    W, H = 500, 400
    oracle = (2.0, 0.0, 500000.0, 0.0, -2.0, 4000000.0)
    gcps = [
        _make_gcp(f"t{i}", px, py, *_oracle_forward(oracle, px, py))
        for i, (px, py) in enumerate([(20, 20), (480, 20), (20, 380)])
    ]
    diag = solve_affine_gcps(gcps, width=W, height=H)
    assert diag.status == "valid"
    assert diag.zero_residual_unverified is True
    assert diag.fit_rmse is not None and diag.fit_rmse < 1e-8
    assert any("zero" in w.lower() for w in diag.warnings)


def test_r14_holdout_check_points_isolated_and_disable_keeps_row():
    W, H = 500, 400
    oracle = (5.0, 0.0, 300000.0, 0.0, -5.0, 4000000.0)
    fit_pts = [
        _make_gcp(f"f{i}", px, py, *_oracle_forward(oracle, px, py), role="fit")
        for i, (px, py) in enumerate([(20, 20), (480, 20), (480, 380), (20, 380)])
    ]
    chk_clean = _make_gcp("chk1", 250, 200, *_oracle_forward(oracle, 250, 200), role="check")
    diag_clean = solve_affine_gcps(fit_pts + [chk_clean], width=W, height=H)

    mx, my = _oracle_forward(oracle, 250, 200)
    chk_bad = _make_gcp("chk1", 250, 200, mx + 500.0, my, role="check")
    diag_bad = solve_affine_gcps(fit_pts + [chk_bad], width=W, height=H)

    c1, c2 = diag_clean.coefficients, diag_bad.coefficients
    assert c1 is not None and c2 is not None
    assert (c1.a0, c1.a1, c1.a2, c1.b0, c1.b1, c1.b2) == pytest.approx(
        (c2.a0, c2.a1, c2.a2, c2.b0, c2.b1, c2.b2), abs=1e-12
    )
    assert diag_clean.check_rmse < 1e-6
    assert diag_bad.check_rmse == pytest.approx(500.0, rel=1e-6)

    bad_fit_disabled = _make_gcp("f_dis", 100, 100, 999999, 999999, role="fit", enabled=False)
    diag_dis = solve_affine_gcps(fit_pts + [bad_fit_disabled], width=W, height=H)
    assert diag_dis.disabled_count == 1
    assert diag_dis.coefficients.a0 == pytest.approx(c1.a0, abs=1e-12)


def test_r15_clustered_low_convex_hull_coverage_warning():
    W, H = 1000, 1000
    oracle = (1.0, 0.0, 500000.0, 0.0, -1.0, 4000000.0)
    pts = [(10, 10), (70, 10), (70, 70), (10, 70)]
    gcps = [_make_gcp(f"cl{i}", px, py, *_oracle_forward(oracle, px, py)) for i, (px, py) in enumerate(pts)]
    diag = solve_affine_gcps(gcps, width=W, height=H)
    assert diag.status == "valid"
    assert diag.low_coverage_warning is True
    assert diag.hull_coverage_ratio < 0.02
    assert any("convex-hull" in w.lower() for w in diag.warnings)


def test_r16_outlier_flagged_without_deletion_and_oob_rejected():
    W, H = 600, 500
    oracle = (2.0, 0.0, 400000.0, 0.0, -2.0, 4500000.0)
    good = [(20, 20), (580, 20), (580, 480), (20, 480), (300, 20), (300, 480)]
    gcps = [_make_gcp(f"ok{i}", px, py, *_oracle_forward(oracle, px, py)) for i, (px, py) in enumerate(good)]
    ox, oy = _oracle_forward(oracle, 300, 250)
    gcps.append(_make_gcp("outlier_pt", 300, 250, ox + 120.0, oy))
    diag = solve_affine_gcps(gcps, width=W, height=H)
    assert "outlier_pt" in diag.outlier_gcp_ids
    assert len(diag.residuals) == 7

    diag_oob = solve_affine_gcps(
        gcps + [_make_gcp("oob", -5.0, 10.0, 400000.0, 4500000.0)],
        width=W,
        height=H,
    )
    assert diag_oob.status == "invalid"
    assert len(diag_oob.blockers) >= 1
