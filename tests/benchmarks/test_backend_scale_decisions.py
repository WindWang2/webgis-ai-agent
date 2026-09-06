"""Foundation V2 — backend/scale decision contracts (A7)。

确定性 count/复杂度契约 + 类型化规模拒绝（tests/benchmarks/ 方法学
惯例：不用脆弱 wall-clock）。快速契约归 unit 层随 PR 门跑。覆盖：

1. scale-guard「先拒绝、后分配」：V2 新增高复杂度路径按预算先拒绝
   （monkeypatch 守卫常数压小，验证拒绝边界精确生效）；
2. backend selection 决策契约：确定性、可解释、规模窗口边界精确；
3. 变体切换语义：centrality exact/sampled 的声明边界与实现边界一致。
"""
from __future__ import annotations

import networkx as nx
import numpy as np
import pytest

pytestmark = pytest.mark.unit

from app.lib.gis.backend_selection import ScaleProfile, select_backend
from app.lib.gis.scientific_errors import ResourceScaleMismatch


def _fc(coords, values, field="v"):
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [float(x), float(y)]},
             "properties": {"v": float(v), field: float(v)}}
            for (x, y), v in zip(coords, values)
        ],
    }


# ── 1. 先拒绝/先降级（新算法热点路径）────────────────────────────────


class TestScaleGuards:
    def test_sar_ml_eigen_guard(self, monkeypatch):
        from app.lib.geo_analysis import spatial_regression as sr

        monkeypatch.setattr(sr, "SAR_EIGEN_MAX_N", 50)
        rng = np.random.RandomState(42)
        # narrated 函数按 EPSG:4326 输入先做 UTM 投影 —— 用真实度数范围
        coords = np.column_stack([116.0 + rng.rand(400) * 0.05,
                                  39.0 + rng.rand(400) * 0.05])
        vals = rng.rand(400)
        with pytest.raises(ResourceScaleMismatch) as ei:
            sr.sar_ml_regression_narrated(
                _fc(coords, vals), target_field="v", explanatory_fields=["v"])
        assert ei.value.scientific_code == "RESOURCE_SCALE_MISMATCH"

    def test_gwr_full_surface_degrades_above_cap(self, monkeypatch):
        """GWR 上限不是拒绝而是降级：>cap 只回摘要，逐观测系数面缺席必须披露。"""
        from app.lib.geo_analysis import spatial_regression as sr

        monkeypatch.setattr(sr, "GWR_FULL_SURFACE_MAX_N", 40)
        rng = np.random.RandomState(7)
        coords = np.column_stack([116.0 + rng.rand(200) * 0.05,
                                  39.0 + rng.rand(200) * 0.05])
        vals = rng.rand(200)
        res = sr.gwr_regression_narrated(
            _fc(coords, vals, field="x1"), target_field="v",
            explanatory_fields=["x1"])
        data = res.data if hasattr(res, "data") else res
        assert data.get("full_coefficient_surfaces") is False

    def test_ripley_observation_cap_shared_by_gfj(self, monkeypatch):
        from app.lib.geo_analysis import point_pattern as pp

        monkeypatch.setattr(pp, "_MAX_RIPLEY_OBSERVATIONS", 60)
        rng = np.random.RandomState(42)
        pts = rng.rand(100, 2) * 100.0
        with pytest.raises(ResourceScaleMismatch):
            pp.g_f_j_functions(pts, window=(0.0, 0.0, 100.0, 100.0))

    def test_knox_sparse_pair_budget(self, monkeypatch):
        from app.lib.geo_analysis import point_pattern as pp

        monkeypatch.setattr(pp, "_MAX_RIPLEY_PAIRS", 50)
        rng = np.random.RandomState(3)
        pts = rng.rand(200, 2) * 100.0
        times = np.arange(200) * 3600.0
        with pytest.raises(ResourceScaleMismatch):
            pp.knox_test(pts, times, critical_distance=50.0,
                         critical_time=3600.0)

    def test_edge_betweenness_exact_cap_no_fake_sampling(self, monkeypatch):
        from app.services.network import centrality as cen

        monkeypatch.setattr(cen, "_EXACT_EDGE_BETWEENNESS_EDGES", 5)
        g = nx.path_graph(20).to_directed()  # 38 有向边 > 5
        svc = cen.NetworkCentralityService()
        with pytest.raises(ResourceScaleMismatch):
            svc.network_centrality(g, metrics="edge_betweenness")

    def test_speckle_filter_grid_guard(self, monkeypatch):
        from app.lib.geo_analysis import sar_filter as sf

        monkeypatch.setattr(sf, "SPECKLE_SCALE_LIMIT_PIXELS", 100)
        img = 1.0 + np.random.RandomState(0).rand(32, 32)
        with pytest.raises(ResourceScaleMismatch):
            sf.speckle_filter(img, method="lee", window=3)

    def test_glcm_ops_guard(self, monkeypatch):
        from app.lib.geo_analysis import glcm

        monkeypatch.setattr(glcm, "GLCM_OPS_LIMIT", 50)
        img = np.random.RandomState(0).rand(32, 32) * 16
        with pytest.raises(ResourceScaleMismatch):
            glcm.glcm_texture(img, window=3, levels=8, directions="all4")

    def test_raster_pca_cell_guard(self, monkeypatch):
        from app.lib.geo_analysis import raster_pca

        monkeypatch.setattr(raster_pca, "PCA_SCALE_LIMIT_CELLS", 10)
        a = np.random.RandomState(0).rand(32, 32)
        with pytest.raises(ResourceScaleMismatch):
            raster_pca.pca_bands([a, a * 2.0, a + 1.0])

    def test_geomorphons_radius_guard(self, monkeypatch):
        from app.lib.geo_analysis import terrain as tr

        dem = np.random.RandomState(0).rand(16, 16) * 10.0
        with pytest.raises(ResourceScaleMismatch):
            tr.geomorphons(dem, 1.0, lookup_radius_cells=10**6)

    def test_priority_flood_cell_guard(self, monkeypatch):
        from app.lib.geo_analysis import terrain as tr

        monkeypatch.setattr(tr, "MAX_HYDRO_CELLS", 50)
        dem = np.random.RandomState(0).rand(16, 16) * 10.0
        with pytest.raises(ResourceScaleMismatch):
            tr.fill_depressions(dem, 1.0)


# ── 2. backend selection 决策契约 ────────────────────────────────────


class TestBackendScaleDecisionContract:
    def test_decision_is_pure_function_of_inputs(self):
        a = select_backend("interpolation.kriging", ScaleProfile(feature_count=120))
        b = select_backend("interpolation.kriging", ScaleProfile(feature_count=120))
        assert a == b

    def test_variant_window_boundary_is_exact(self):
        exact = select_backend("network.centrality",
                               ScaleProfile(feature_count=2000))
        sampled = select_backend("network.centrality",
                                 ScaleProfile(feature_count=2001))
        assert exact.variant_id == "exact_brandes"
        assert sampled.variant_id == "sampled_brandes"
        assert exact.matched and sampled.matched

    def test_rationale_discloses_choice_and_is_bounded(self):
        d = select_backend("network.centrality", ScaleProfile(feature_count=5000))
        assert "n=5000" in d.rationale
        assert len(d.rationale) <= 160
        assert d.to_diagnostic()["value"] == "sampled_brandes"

    def test_implementation_matches_declared_boundary(self):
        """centrality 声明的 exact/sampled 窗口边界 = 实现切换边界（2000）。"""
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.services.network import centrality as cen

        desc = get_algorithm_registry().get("network.centrality")
        declared = {v.id: (v.min_features, v.max_features)
                    for v in desc.backend_variants}
        assert declared["exact_brandes"] == (None, cen._EXACT_BETWEENNESS_NODES)
        assert declared["sampled_brandes"] == (cen._EXACT_BETWEENNESS_NODES + 1, None)

    def test_sampled_betweenness_disclosed_at_runtime(self, monkeypatch):
        from app.services.network import centrality as cen

        monkeypatch.setattr(cen, "_EXACT_BETWEENNESS_NODES", 8)
        monkeypatch.setattr(cen, "_SAMPLE_K", 12)
        g = nx.path_graph(20).to_directed()
        svc = cen.NetworkCentralityService()
        res = svc.network_centrality(g, metrics="betweenness")
        assert res.summary["betweenness_mode"] == "sampled"

    def test_all_declared_variants_use_closed_backend_vocabulary(self):
        from app.lib.gis.algorithm_registry import (
            BACKEND_VOCABULARY, get_algorithm_registry,
        )
        reg = get_algorithm_registry()
        for aid in reg.all_ids:
            for v in reg.get(aid).backend_variants:
                assert v.backend in BACKEND_VOCABULARY, (aid, v.id)
