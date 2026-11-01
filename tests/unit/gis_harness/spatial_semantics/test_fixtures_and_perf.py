"""M7 —— fixture 语料判定谱 + 50k descriptor 级性能预算。

锁定（验收 Oracle #6 与 M7）：
- fixture 谱系（4326/3857/UTM/未声明度样/未声明投影样/GCJ/BD/跨 AM/极区/
  多带/无效几何/空 FC）在 inspect/resolve/validate 上判定确定性；
- 50k feature 输入只做 descriptor 评估（采样 ≤200 / 扫描 ≤2000 要素），
  preflight 耗时与 5k 同量级（有界，非 O(n)）；
- 坐标采样绝不出现在契约/披露里（不进 prompt）。
"""
from __future__ import annotations

import time

import pytest

from app.services.gis_harness.spatial_semantics import (
    SpatialOperationSpec,
    evidence_from_feature_collection,
    preflight_for_feature_collection,
)
from tests.data import spatial_semantics_fixtures as fx

BUFFER_SPEC = dict(operation="buffer", distance=500, distance_unit="m")


# ── fixture 判定谱（确定性）────────────────────────────────────────────────

@pytest.mark.parametrize("builder,expect_status,expect_codes_contains", [
    (fx.chengdu_points_wgs84, "pass", []),
    (fx.chengdu_points_undeclared, "pass_with_disclosure",
     ["SSC_CRS_RFC7946_DEFAULT"]),
    (fx.chengdu_points_projected_undeclared, "blocked",
     ["SSC_CRS_UNDECLARED_NON_DEGREE"]),
    (fx.chengdu_points_webmercator_declared, "degraded_with_disclosure",
     ["SSC_WEB_MERCATOR_SCALE_DISTORTION"]),
    (fx.chengdu_points_gcj02_declared, "pass_with_disclosure",
     ["SSC_DATUM_NORMALIZED"]),
    (fx.chengdu_points_bd09_declared, "pass_with_disclosure",
     ["SSC_DATUM_NORMALIZED"]),
    (fx.antimeridian_fiji_cluster, "pass_with_disclosure",
     ["SSC_ANTIMERIDIAN_CROSSED"]),
    (fx.wide_span_china, "degraded_with_disclosure", ["SSC_MULTIZONE_SPAN"]),
])
def test_fixture_spectrum_deterministic(builder, expect_status,
                                        expect_codes_contains) -> None:
    r = preflight_for_feature_collection(
        builder(), spec=SpatialOperationSpec(**BUFFER_SPEC))
    assert r.status == expect_status
    codes = [rc.code for rc in r.verdict.reason_codes]
    for code in expect_codes_contains:
        assert code in codes


def test_fixture_polar_and_invalid() -> None:
    r = preflight_for_feature_collection(
        fx.polar_svalbard(), spec=SpatialOperationSpec(operation="measure"))
    assert r.calculation.mode == "projected_polar"
    # 无效几何（null geometry）：扫描跳过 null，度样点仍在 —— 判定不崩
    r2 = preflight_for_feature_collection(
        fx.invalid_geometry_mixed(), spec=SpatialOperationSpec(**BUFFER_SPEC))
    assert r2.contract.feature_count == 2
    # 空 FC：无坐标证据 → crs_source=unknown（不猜）；度量敏感操作按
    # R1-P1 收敛规则 requires_clarification（CRS 未知不放行执行）
    r3 = preflight_for_feature_collection(
        fx.empty_fc(), spec=SpatialOperationSpec(**BUFFER_SPEC))
    assert r3.contract.crs_source == "unknown"
    assert r3.status == "requires_clarification"
    assert "SSC_UNKNOWN_CRS" in [rc.code for rc in r3.verdict.reason_codes]


def test_bd09_chain_evidence_complete() -> None:
    r = preflight_for_feature_collection(
        fx.chengdu_points_bd09_declared(),
        spec=SpatialOperationSpec(operation="spatial_join"))
    assert r.contract.datum_normalization == ["bd09→gcj02→wgs84"]


# ── 50k descriptor 级预算（Oracle #6 / M7）────────────────────────────────

def test_50k_preflight_is_descriptor_bounded() -> None:
    """50k feature：采样 ≤200、扫描 ≤2000，契约零坐标转储。"""
    fc = fx.bulk_points(50_000)
    ev = evidence_from_feature_collection(fc)
    assert ev.feature_count == 50_000
    assert len(ev.coord_samples) <= 200
    r = preflight_for_feature_collection(
        fc, spec=SpatialOperationSpec(**BUFFER_SPEC))
    d = r.disclosure()
    assert d["feature_count"] == 50_000
    assert "coord_samples" not in repr(d)


def test_50k_preflight_timing_budget() -> None:
    """50k 与 5k 的 preflight 耗时同量级（有界扫描，非 O(n)）。

    预算：50k ≤ 1.0s（CI 余量；本机典型 ~0.1s），且 50k/5k 比 < 20
    （5k 也在扫描上限内，比值应接近 1 —— 上限取 20 容 CI 抖动）。
    """
    fc_small = fx.bulk_points(5_000)
    fc_big = fx.bulk_points(50_000)
    spec = SpatialOperationSpec(**BUFFER_SPEC)

    t0 = time.perf_counter()
    r_small = preflight_for_feature_collection(fc_small, spec=spec)
    t_small = time.perf_counter() - t0

    t0 = time.perf_counter()
    r_big = preflight_for_feature_collection(fc_big, spec=spec)
    t_big = time.perf_counter() - t0

    assert r_big.status == r_small.status
    assert t_big < 1.0, f"50k preflight {t_big:.3f}s 超预算"
    if t_small > 0.005:  # 太小时比值无意义
        assert t_big / t_small < 20, (
            f"preflight 随规模增长：5k={t_small:.4f}s 50k={t_big:.4f}s")


def test_evidence_scan_caps_enforced() -> None:
    fc = fx.bulk_points(50_000)
    ev = evidence_from_feature_collection(fc)
    assert len(ev.coord_samples) == 200  # 打满即停
    assert sum(ev.geometry_kinds.values()) <= 2000  # 扫描上限


def test_repeat_runs_identical() -> None:
    """无网无密钥重复运行 2 次 —— 判定/披露完全一致。"""
    fc = fx.chengdu_points_wgs84()
    r1 = preflight_for_feature_collection(
        fc, spec=SpatialOperationSpec(**BUFFER_SPEC))
    r2 = preflight_for_feature_collection(
        fc, spec=SpatialOperationSpec(**BUFFER_SPEC))
    assert r1.contract.to_dict() == r2.contract.to_dict()
    assert r1.disclosure() == r2.disclosure()
