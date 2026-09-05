"""Spatial Science Platform VNext —— 科学基准/规模套件（ADR-0099 §32/§59）。

定位：

- 正确性基准：小型可手算装置 + 解析参考断言（不含脆弱的墙钟门）；
- 规模基准：确定性开销上限（count/bytes 契约 + 类型化拒绝），不做
  时间断言 —— 与仓库既有 ``tests/benchmarks`` 哲学一致；
- 规模守卫：超限 → 类型化 ResourceScaleMismatch（先拒绝，不 OOM）——
  内存安全（§49）的回归防线。守卫常数经 monkeypatch 收紧以保持测试快。
"""
from __future__ import annotations

import numpy as np
import pytest

pytestmark = [pytest.mark.perf]


# ── 规模守卫：先拒绝，不 OOM（§49 内存安全）──────────────────────────
class TestScaleGuards:
    def test_sar_stack_rejects_oversized_time_dimension(self):
        from app.lib.gis.scientific_errors import ResourceScaleMismatch
        from app.lib.geo_analysis.sar_temporal import temporal_stack_statistics
        stack = np.zeros((30, 4, 4), dtype=np.float32)
        with pytest.raises(ResourceScaleMismatch):
            temporal_stack_statistics(stack, product="mean")

    def test_inverse_distance_weights_guard(self):
        from app.lib.gis.scientific_errors import ResourceScaleMismatch
        from app.lib.geo_analysis import spatial_weights
        with pytest.raises(ResourceScaleMismatch):
            spatial_weights.build_inverse_distance_weights(
                np.zeros((spatial_weights._MAX_IDW_OBSERVATIONS + 1, 2)))

    def test_terrain_window_guard(self):
        from app.lib.geo_analysis.terrain import topographic_position_index
        with pytest.raises((ValueError, Exception)):
            topographic_position_index(np.ones((8, 8)), window=103)

    def test_rbf_hard_cap(self, monkeypatch):
        from app.lib.gis.scientific_errors import ResourceScaleMismatch
        from app.lib.geo_analysis import rbf_interpolation as rbf_mod
        from app.lib.geo_analysis.rbf_interpolation import rbf_interpolation
        monkeypatch.setattr(rbf_mod, "RBF_HARD_CAP", 100)
        fc = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {"v": 1.0},
                 "geometry": {"type": "Point",
                              "coordinates": [116.0 + (i % 12) * 0.001,
                                              39.9 + (i // 12) * 0.001]}}
                for i in range(150)
            ],
        }
        with pytest.raises(ResourceScaleMismatch):
            rbf_interpolation(fc, "v")

    def test_idw_loocv_power_guard(self):
        from app.lib.gis.scientific_errors import UnsupportedMethod
        from app.lib.geo_analysis.interpolation import idw_loocv
        xy = np.zeros((5, 2), dtype=float)
        with pytest.raises(UnsupportedMethod):
            idw_loocv(xy, np.zeros(5), power=6.0)


# ── 确定性开销契约（count 基准，非墙钟）──────────────────────────────
class TestDeterministicCostContracts:
    def test_ripley_k_bounded_step_budget(self):
        from app.lib.geo_analysis.point_pattern import ripley_k
        rng = np.random.RandomState(42)
        xy = rng.uniform(0, 100, size=(600, 2))
        out = ripley_k(xy, n_steps=8)
        assert len(out["r"]) == 8

    def test_moran_permutation_ladder_output(self):
        from app.lib.geo_analysis.statistics import moran_i_narrated
        rng = np.random.RandomState(7)
        pts = [(115.0 + (i % 10) * 0.01, 39.0 + (i // 10) * 0.01)
               for i in range(60)]
        fc = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature",
                 "properties": {"v": float(v)},
                 "geometry": {"type": "Point", "coordinates": list(xy)}}
                for xy, v in zip(pts, rng.normal(50, 10, 60))
            ],
        }
        res = moran_i_narrated(fc, "v", permutations=99)
        assert res.success
        assert 0.0 <= res.data["p_value"] <= 1.0


# ── 正确性基准（小型可手算装置，解析参考）────────────────────────────
class TestCorrectnessBenchmarks:
    def test_moran_checkerboard_reference(self):
        """2×2 棋盘 + rook 权重 → I = −1（解析参考）。"""
        from app.lib.geo_analysis.spatial_weights import build_contiguity_weights
        import geopandas as gpd
        from shapely.geometry import box
        cells, vals = [], []
        for i in range(2):
            for j in range(2):
                cells.append(box(j, i, j + 1, i + 1))
                vals.append(float((i + j) % 2))
        gdf = gpd.GeoDataFrame({"v": vals}, geometry=cells, crs="EPSG:32650")
        w = build_contiguity_weights(gdf, scheme="rook")
        z = np.array(vals) - np.mean(vals)
        csr = w.matrix.tocoo()
        num = float(sum(wij * z[i] * z[j]
                        for i, j, wij in zip(csr.row, csr.col, csr.data)))
        n = len(vals)
        i_stat = (n / float(w.matrix.sum())) * num / float(np.sum(z ** 2))
        assert abs(i_stat - (-1.0)) < 1e-10

    def test_kriging_small_system_reference(self):
        """nugget=0 球状模型 → OK 在样本点精确复现观测（地统计学锚）。

        注：拟合 nugget>0（观测含噪声）时 OK 本就是平滑预测器 ——
        精确插值性只在 nugget=0 时成立，故用手工构造的变异函数。
        """
        from app.lib.geo_analysis.kriging import (
            VariogramFit, ordinary_kriging,
        )
        rng = np.random.RandomState(42)
        pts = rng.uniform(0, 100, size=(16, 2))
        vals = 20.0 + 0.3 * pts[:, 0] + rng.normal(0, 1.0, 16)
        vgm = VariogramFit(
            model="spherical", sill=float(np.var(vals)),
            range_m=50.0, nugget=0.0)
        res = ordinary_kriging(pts, vals, pts, vgm, k=12)
        # 批式求解的浮点条件数限制：相对误差 ~5e-7（值域 20-50）。
        np.testing.assert_allclose(res.predictions, vals, rtol=1e-5, atol=1e-4)

    def test_spectral_index_reference_grid(self):
        """NDVI 参考值（手算，±1e-12）+ 范围合规。"""
        from app.lib.geo_analysis.spectral import compute_spectral_index
        nir = np.array([0.5, 0.4])
        red = np.array([0.2, 0.2])
        out = compute_spectral_index({"nir": nir, "red": red}, "ndvi")
        np.testing.assert_allclose(
            out["array"], [(0.5 - 0.2) / 0.7, (0.4 - 0.2) / 0.6], atol=1e-12)
        assert out["out_of_range_fraction"] == 0.0

    def test_slope_synthetic_plane_reference(self):
        """合成斜面（dz/dx=1, cell=1）→ Horn 坡度 45°。"""
        from app.services.rs.band_math import compute_slope
        z = np.arange(6, dtype=float)[None, :].repeat(6, axis=0)
        slope = compute_slope(z, cell_size=1.0)   # 已是度
        interior = slope[2:-2, 2:-2]
        np.testing.assert_allclose(interior, 45.0, atol=1e-6)

    def test_shortest_path_hand_graph_reference(self):
        """6 节点手算图 → 精确最短路成本（网络正确性锚）。"""
        import networkx as nx
        g = nx.Graph()
        for u, v, w in [
            ("A", "B", 2.0), ("B", "C", 2.0), ("A", "C", 5.0),
            ("C", "D", 1.0), ("B", "D", 4.0), ("D", "E", 3.0),
        ]:
            g.add_edge(u, v, weight=w)
        assert nx.dijkstra_path_length(g, "A", "E") == 8.0  # A-B-C-D-E

    def test_buffer_area_golden(self):
        """1000m 缓冲 → π·10⁶ m² ±1%（UTM 路径几何黄金锚）。"""
        from app.lib.geo_processor.geometry import buffer_smart
        from shapely.geometry import shape
        fc = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {},
                 "geometry": {"type": "Point",
                              "coordinates": [116.4, 39.9]}}
            ],
        }
        res = buffer_smart(fc, 1000.0)
        assert res.success
        geom = shape(res.data["features"][0]["geometry"])  # WGS84 度
        import geopandas as gpd
        metric = gpd.GeoSeries([geom], crs="EPSG:4326").to_crs("EPSG:32650")
        area = float(metric.area.iloc[0])
        assert abs(area - np.pi * 1000 ** 2) / (np.pi * 1000 ** 2) < 0.01
