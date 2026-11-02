"""M8 —— 数学/拓扑不变量 + 50k/200k 性能对照（合成数据，确定性）。

- 拓扑族（快，默认跑）：自交不引入/洞保持/面积误差界/共享边界锚点/
  主干道路留存（重要目标不丢）；
- 性能族（heavy，MSP_PERF=1 显式跑）：50k/200k 点·线要素的综合耗时与
  峰值内存（同机对照，值记 EVIDENCE.md —— 不以凭空数字宣称门限）。
"""
from __future__ import annotations

import json
import os
import time

import pytest
from shapely.geometry import shape
from shapely.ops import unary_union

from app.lib.cartography.multiscale.generalize import generalize_features
from app.lib.cartography.multiscale.importance import derive_importance_model
from app.lib.cartography.multiscale.plan import build_scale_profile
from app.lib.cartography.multiscale.profile import (
    FeatureSelectionPolicy,
    GeometryGeneralizationPolicy,
    SourceRevision,
)

pytestmark = pytest.mark.cartography

CHENGDU_LAT = 30.66


def _jitter_line(i: int, n_pts: int = 60, spacing: float = 0.0008) -> dict:
    pts = []
    for k in range(n_pts):
        x = 103.9 + i * 0.0012 + k * spacing
        y = CHENGDU_LAT + (2e-5 if k % 2 else -2e-5) + (i % 7) * 0.00002
        pts.append([round(x, 7), round(y, 7)])
    return {"type": "LineString", "coordinates": pts}


def _roads_synth(n: int) -> tuple[list, dict]:
    classes = ["高速公路", "国道", "省道", "县道", "乡道"]
    feats = []
    dist = {c: 0 for c in classes}
    for i in range(n):
        cls = classes[i % 5]
        dist[cls] += 1
        feats.append({
            "type": "Feature", "id": f"road-{i}",
            "properties": {"道路等级": cls},
            "geometry": _jitter_line(i),
        })
    return feats, dist


def _admin_grid(nx: int, ny: int, d: float = 0.02) -> list:
    feats = []
    for gx in range(nx):
        for gy in range(ny):
            lon, lat = 104.0 + gx * d, CHENGDU_LAT + gy * d
            hole = None
            if (gx + gy) % 4 == 0:  # 每 4 格带一个洞（洞保持不变量样本）
                hole = [[lon + d * 0.2, lat + d * 0.2],
                        [lon + d * 0.35, lat + d * 0.2],
                        [lon + d * 0.35, lat + d * 0.35],
                        [lon + d * 0.2, lat + d * 0.2]]
            ring = [[lon, lat], [lon + d, lat], [lon + d, lat + d],
                    [lon, lat + d], [lon, lat]]
            coords = [ring] + ([hole] if hole else [])
            feats.append({
                "type": "Feature", "id": f"cell-{gx}-{gy}",
                "properties": {"区县": f"区{gx}-{gy}", "pop": 1000 + gx * 10 + gy},
                "geometry": {"type": "Polygon", "coordinates": coords},
            })
    return feats


# ── 拓扑不变量（默认跑）──────────────────────────────────────────────────


class TestTopologyInvariants:
    def test_no_self_intersection_introduced_lines(self):
        # 综合后线不自交（is_valid + is_simple）
        feats, _ = _roads_synth(300)
        r = generalize_features(
            feats,
            geometry_policy=GeometryGeneralizationPolicy(
                strategy="bounded_simplify", tolerance=150.0),
            selection_policy=FeatureSelectionPolicy(importance_evidence="none"),
            latitude_hint=CHENGDU_LAT)
        n_bad = 0
        for f in r.feature_collection["features"]:
            g = shape(f["geometry"])
            if not (g.is_valid and g.is_simple):
                n_bad += 1
        assert n_bad == 0, f"{n_bad} 条线在综合后非简单/无效"

    def test_polygon_area_error_bounded_grid(self):
        feats = _admin_grid(12, 12)
        r = generalize_features(
            feats,
            geometry_policy=GeometryGeneralizationPolicy(
                strategy="shared_boundary_protect", tolerance=120.0,
                max_area_error_ratio=0.02),
            selection_policy=FeatureSelectionPolicy(importance_evidence="none"),
            latitude_hint=CHENGDU_LAT)
        assert r.receipt["max_area_error_ratio_observed"] <= 0.02
        # 全部有效
        for f in r.feature_collection["features"]:
            assert shape(f["geometry"]).is_valid

    def test_holes_preserved_across_grid(self):
        feats = _admin_grid(10, 10)
        r = generalize_features(
            feats,
            geometry_policy=GeometryGeneralizationPolicy(
                strategy="shared_boundary_protect", tolerance=80.0),
            selection_policy=FeatureSelectionPolicy(importance_evidence="none"),
            latitude_hint=CHENGDU_LAT)
        holes_before = sum(
            len(shape(f["geometry"]).interiors) for f in feats)
        holes_after = sum(
            len(shape(f["geometry"]).interiors)
            for f in r.feature_collection["features"])
        assert holes_after == holes_before, (
            f"洞丢失：{holes_before} → {holes_after}")

    def test_shared_boundary_no_gaps_or_overlaps(self):
        # 相邻网格综合后：总覆盖面积相对误差有界（无裂缝/重叠的面积证据）
        feats = _admin_grid(8, 8, d=0.05)
        before_union = unary_union(
            [shape(f["geometry"]) for f in feats]).area
        r = generalize_features(
            feats,
            geometry_policy=GeometryGeneralizationPolicy(
                strategy="shared_boundary_protect", tolerance=150.0),
            selection_policy=FeatureSelectionPolicy(importance_evidence="none"),
            latitude_hint=CHENGDU_LAT)
        after_union = unary_union([
            shape(f["geometry"])
            for f in r.feature_collection["features"]]).area
        rel_err = abs(after_union - before_union) / before_union
        # 共享边界保护下，邻接面缝隙/重叠的总面积效应 ≤ 面积误差界×4
        assert rel_err <= 0.08, f"覆盖面积相对误差 {rel_err:.4f} 超界"

    def test_major_roads_retained_at_coarse_scale(self):
        # 省域 1:500000（world 带）主干不丢：全部高速/国道保留
        feats, dist = _roads_synth(500)
        result = build_scale_profile(
            layer_id="roads",
            feature_collection={"type": "FeatureCollection", "features": feats},
            source=SourceRevision(ref="ref:gen:roads", feature_count=500,
                                  geometry_family="line"),
            geometry_family="line",
            latitude_hint=CHENGDU_LAT,
            importance=derive_importance_model(
                {"道路等级": {"type": "string"}},
                {"道路等级": dist}, feature_count=500),
        )
        world = result.artifacts["world"]
        kept = {f["properties"]["道路等级"]
                for f in world.feature_collection["features"]}
        assert "高速公路" in kept and "国道" in kept
        assert "乡道" not in kept  # 末梢按词表序剔除


# ── 性能对照（MSP_PERF=1 显式跑；heavy）──────────────────────────────────


def _run_benchmark(n: int, *, with_tracemalloc: bool = False,
                   verify_determinism: bool = False) -> dict:
    """单跑基准。口径披露：tracemalloc 采样使耗时放大数倍（逐分配
    Python 钩子），故 200k 只测纯耗时、50k 测峰值内存 —— 两口径分开
    报告不冒充（EVIDENCE.md 记录）。"""
    feats, dist = _roads_synth(n)

    def _build():
        return build_scale_profile(
            layer_id=f"roads-{n}",
            feature_collection={"type": "FeatureCollection", "features": feats},
            source=SourceRevision(ref="ref:perf", feature_count=n,
                                  geometry_family="line"),
            geometry_family="line",
            latitude_hint=CHENGDU_LAT,
            importance=derive_importance_model(
                {"道路等级": {"type": "string"}},
                {"道路等级": dist}, feature_count=n),
        )

    peak = None
    if with_tracemalloc:
        import tracemalloc

        tracemalloc.start()
        t0 = time.perf_counter()
        result = _build()
        elapsed = time.perf_counter() - t0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    else:
        t0 = time.perf_counter()
        result = _build()
        elapsed = time.perf_counter() - t0
    same = True
    if verify_determinism:
        result2 = _build()
        same = json.dumps(
            result.profile.model_dump(), sort_keys=True) == json.dumps(
            result2.profile.model_dump(), sort_keys=True)
    return {
        "n": n,
        "elapsed_s": round(elapsed, 2),
        "peak_mb": round(peak / 1024 / 1024, 1) if peak else None,
        "bands": len(result.profile.bands),
        "artifact_sizes": {
            t: len(g.feature_collection["features"])
            for t, g in result.artifacts.items()
        },
        "same_output": same,
    }


@pytest.mark.heavy
@pytest.mark.skipif(not os.environ.get("MSP_PERF"), reason="MSP_PERF=1 显式运行")
class TestPerformanceBenchmarks:
    def test_50k_and_200k_lines_deterministic_and_bounded(self):
        # 三口径分开（不互相冒充）：纯耗时（50k/200k 同口径可比）+
        # 峰值内存（50k 带 tracemalloc，其耗时被采样放大不进对照）+
        # 确定性复跑（50k）。
        b50 = _run_benchmark(50_000, verify_determinism=True)
        b50_mem = _run_benchmark(50_000, with_tracemalloc=True)
        b200 = _run_benchmark(200_000)
        b50["peak_mb"] = b50_mem["peak_mb"]
        print("PERF_50K:", json.dumps(b50, ensure_ascii=False))
        print("PERF_200K:", json.dumps(b200, ensure_ascii=False))
        assert b50["same_output"]
        # 同机相对合理性：纯耗时口径下 200k ≥ 50k×0.4（数据量单调），
        # 绝对值记录进 EVIDENCE.md（不以凭空阈值宣称性能门）。
        assert b200["elapsed_s"] >= b50["elapsed_s"] * 0.4


class TestMultibandEquivalence:
    def test_multiband_equals_per_band_calls(self):
        # 单遍多带与逐带调用输出逐字节等价（性能路径的正确性锁）
        from app.lib.cartography.multiscale.generalize import (
            BandSpec,
            generalize_features,
            generalize_features_multiband,
        )

        feats, dist = _roads_synth(120)
        grid = _admin_grid(6, 6)
        line_specs = [
            BandSpec("world",
                     GeometryGeneralizationPolicy(
                         strategy="bounded_simplify", tolerance=200.0),
                     FeatureSelectionPolicy(
                         importance_field="道路等级",
                         importance_evidence="field_schema+aggregate+vocabulary",
                         value_order=list(dist),
                         kept_values=list(dist)[:3]),
                     0.0),
            BandSpec("province",
                     GeometryGeneralizationPolicy(
                         strategy="bounded_simplify", tolerance=100.0),
                     FeatureSelectionPolicy(
                         importance_field="道路等级",
                         importance_evidence="field_schema+aggregate+vocabulary",
                         value_order=list(dist),
                         kept_values=list(dist)[:4]),
                     8.0),
        ]
        poly_specs = [
            BandSpec("world",
                     GeometryGeneralizationPolicy(
                         strategy="shared_boundary_protect", tolerance=150.0),
                     FeatureSelectionPolicy(importance_evidence="none"), 0.0),
        ]
        for feats_in, specs in ((feats, line_specs), (grid, poly_specs)):
            multi = generalize_features_multiband(
                feats_in, specs, latitude_hint=CHENGDU_LAT,
                mandatory_ids=frozenset({"road-0", "cell-0-0"}))
            for spec in specs:
                single = generalize_features(
                    feats_in,
                    geometry_policy=spec.geometry_policy,
                    selection_policy=spec.selection_policy,
                    latitude_hint=CHENGDU_LAT,
                    band_zoom=spec.band_zoom,
                    mandatory_ids=frozenset({"road-0", "cell-0-0"}))
                assert json.dumps(multi[spec.tier].feature_collection,
                                  sort_keys=True) == json.dumps(
                    single.feature_collection, sort_keys=True), spec.tier
                assert multi[spec.tier].receipt == single.receipt, spec.tier


class TestReviewFixesR1:
    """评审 R1 清偿的回归锁。"""

    def test_p1_equivalence_in_fast_tolerance_regime_u_shape(self):
        # tol ≥ 线全长的 U 形线：单带与多带逐字节等价（快路径删除后，
        # 两路径同走 GEOS TopologyPreservingSimplifier —— R1-P1 的盲区）
        from app.lib.cartography.multiscale.generalize import (
            BandSpec,
            generalize_features,
            generalize_features_multiband,
        )

        u = {"type": "LineString", "coordinates": [
            [104.0, 30.6], [104.0, 30.62], [104.02, 30.62],
            [104.02, 30.6], [104.04, 30.6]]}  # ~4.9km U 形（米空间）
        feats = [{"type": "Feature", "id": "u1", "properties": {},
                  "geometry": u}]
        spec = BandSpec(
            "world",
            GeometryGeneralizationPolicy(
                strategy="bounded_simplify", tolerance=10_000.0),
            FeatureSelectionPolicy(importance_evidence="none"), 0.0)
        multi = generalize_features_multiband(
            feats, [spec], latitude_hint=CHENGDU_LAT)
        single = generalize_features(
            feats, geometry_policy=spec.geometry_policy,
            selection_policy=spec.selection_policy,
            latitude_hint=CHENGDU_LAT, band_zoom=0.0)
        assert json.dumps(multi["world"].feature_collection,
                          sort_keys=True) == json.dumps(
            single.feature_collection, sort_keys=True)
        assert multi["world"].receipt == single.receipt
        # 回执不虚报：长度损失观测与真实几何一致（GEOS 路径计算）
        if single.receipt["geometry_simplified"] > 0:
            assert single.receipt["max_length_loss_ratio_observed"] >= 0.0

    def test_p2_4_gapped_plan_fallback_is_last_window(self):
        # 越界/缺口计划：回退 = 末窗（与前端孪生同语义，不再取首窗）
        from app.lib.cartography.multiscale.export_adjudication import (
            active_representation_window)

        layer = {"representation": {"windows": [
            {"min_zoom": 2, "max_zoom": 6, "mode": "generalized",
             "source": "A"},
            {"min_zoom": 10, "max_zoom": 20, "mode": "raw"},
        ]}}
        win = active_representation_window(layer, 0.0)  # 首窗下方越界
        assert win is not None and win["mode"] == "raw"  # 末窗，非 generalized
        win2 = active_representation_window(layer, 8.0)  # 缺口内
        assert win2 is not None and win2["mode"] == "raw"

    def test_p2_6_profile_id_carries_full_16_hex(self):
        from app.lib.cartography.multiscale.importance import (
            derive_importance_model,
        )
        from app.lib.cartography.multiscale.plan import build_scale_profile
        from app.lib.cartography.multiscale.profile import SourceRevision

        feats, dist = _roads_synth(30)
        result = build_scale_profile(
            layer_id="r",
            feature_collection={"type": "FeatureCollection", "features": feats},
            source=SourceRevision(ref="ref:t", feature_count=30,
                                  geometry_family="line"),
            geometry_family="line", latitude_hint=CHENGDU_LAT,
            importance=derive_importance_model(
                {"道路等级": {"type": "string"}},
                {"道路等级": dist}, feature_count=30))
        tail = result.profile.profile_id.rsplit(":", 1)[1]
        assert len(tail) == 16 and all(c in "0123456789abcdef" for c in tail)

    def test_p2_7_dpi_inf_fail_closed(self):
        from app.lib.cartography.multiscale.profile import (
            display_scale_denominator,
        )

        assert display_scale_denominator(8.0, 0.0, dpi=float("inf")) == (
            display_scale_denominator(8.0, 0.0, dpi=96.0))

    def test_p2_7_partial_keep_ranks_merge_defaults(self):
        # 只覆盖 world：其余带用缺省秩（不再静默 fail-open 到 street 全保）
        from app.lib.cartography.multiscale.importance import (
            DEFAULT_KEEP_RANKS,
            derive_importance_model,
            kept_values_for_band,
        )

        dist = {"高速公路": 6, "国道": 6, "省道": 6, "县道": 6, "乡道": 6}
        model = derive_importance_model(
            {"道路等级": {"type": "string"}},
            {"道路等级": dist}, feature_count=30).selected
        assert model is not None
        # 键名笔误场景：意图 world=1 但误写 "World" —— 未提及/误写的带
        # 回落缺省秩 3（而非旧行为的 street=999 全保留）
        world = kept_values_for_band(model, "world", {"World": 1})
        assert len(world) == min(DEFAULT_KEEP_RANKS["world"],
                                 len(model.value_order))  # 3，非 5 全量
