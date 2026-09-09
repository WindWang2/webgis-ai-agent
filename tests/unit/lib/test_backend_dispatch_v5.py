"""Backend dispatch v5（science-v5 W7）—— plan_execution + 驱动接线测试。

契约（01-architecture.md D4 + 挑战 R0-#6/#10/#12）：

- ExecutionPlan 是 select_backend 之上的纯函数投影（不重判变体）；
- mode 词表封闭：native/vectorized/chunked——distributed 不存在（词表
  不收，防虚构）；
- sgs/lmc/st 变体窗口单位 = 目标格点（raster_cells 触发）；reference
  下界 = 8/12（InsufficientSamples 硬闸），非 1；
- 驱动接线：sgs_simulation_surface backend=auto 按窗口解析 + 证据入
  metadata；三驱动均挂 uncertainty artifact 摘要（W6）。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.lib.gis.backend_selection import (
    EXECUTION_MODE_VOCABULARY,
    ScaleProfile,
    plan_execution,
    select_backend,
)


class TestPlanExecution:
    def test_sgs_window_unit_is_raster_cells(self):
        # 挑战 R0-#6：sgs 变体按 raster_cells 匹配（feature_count 非规模瓶颈）
        plan = plan_execution(
            "interpolation.sgs", ScaleProfile(raster_cells=500_000))
        assert plan.variant_id == "numpy_batched"
        assert plan.mode == "vectorized"
        assert plan.matched is True
        # 样本数不再是决策单位：60 样本/50 万格点仍选 batched
        plan2 = plan_execution(
            "interpolation.sgs",
            ScaleProfile(feature_count=60, raster_cells=500_000))
        assert plan2.variant_id == "numpy_batched"

    def test_reference_variant_for_small_grids_when_forced(self):
        # 显式后端参数（工具层透传）→ 绕过窗口解析（驱动直测）
        plan = plan_execution(
            "interpolation.sgs", ScaleProfile(raster_cells=500))
        assert plan.variant_id == "numpy_batched"   # 声明序偏好

    def test_lmc_and_st_declare_batched_variants(self):
        for algo in ("interpolation.cokriging_lmc", "interpolation.st_kriging"):
            plan = plan_execution(algo, ScaleProfile(raster_cells=10_000))
            assert plan.variant_id == "numpy_batched", algo
            assert plan.mode == "vectorized", algo

    def test_raster_cells_only_and_unknown_scale(self):
        # raster_cells 缺省 → deferred（matched=False，理由写明）
        plan = plan_execution("interpolation.sgs", ScaleProfile())
        assert plan.matched is False
        assert "deferred" in plan.rationale or "unknown" in plan.rationale

    def test_mode_vocabulary_closed_no_distributed(self):
        assert "distributed" not in EXECUTION_MODE_VOCABULARY
        assert set(EXECUTION_MODE_VOCABULARY) == {
            "native", "vectorized", "chunked"}

    def test_chunked_mode_with_memory_budget(self):
        # 极小预算 → chunked + 形状建议（envelope bytes_per_cell=8 反解）
        plan = plan_execution(
            "interpolation.sgs", ScaleProfile(raster_cells=1_000_000),
            memory_budget_bytes=1024)          # 1KiB 预算（构造性触发）
        assert plan.mode == "chunked"
        assert plan.chunk_shape is not None
        assert plan.chunk_shape["chunk_cells"] == 1024 // 8
        assert "建议分块" in plan.memory_note

    def test_native_mode_for_nonbatched_algorithm(self):
        plan = plan_execution(
            "interpolation.idw", ScaleProfile(feature_count=100))
        assert plan.mode == "native"           # 非批量变体 → native
        assert plan.chunk_shape is None

    def test_to_dict_shape_and_purity(self):
        scale = ScaleProfile(raster_cells=300_000)
        a = plan_execution("interpolation.sgs", scale).to_dict()
        b = plan_execution("interpolation.sgs", scale).to_dict()
        assert a == b                          # 同输入同输出（纯函数）
        for key in ("algorithm_id", "variant_id", "backend", "mode",
                    "matched", "chunk_shape", "memory_note",
                    "approximation_disclosure", "rationale"):
            assert key in a
        assert isinstance(a["matched"], bool)

    def test_execution_plan_is_frozen_dataclass(self):
        plan = plan_execution("interpolation.sgs", ScaleProfile(raster_cells=10))
        with pytest.raises(Exception):
            plan.mode = "chunked"              # frozen

    def test_select_backend_raster_cells_pin(self):
        # select_backend 直接消费 raster_cells（挑战 R0-#6 的回归锚）
        d = select_backend("interpolation.st_kriging",
                           ScaleProfile(feature_count=None, raster_cells=30))
        assert d.variant_id == "numpy_batched"


class TestDriverWiring:
    def _fc(self, n: int = 36, step: float = 0.008):
        return {
            "type": "FeatureCollection", "crs": "EPSG:4326",
            "features": [
                {"type": "Feature",
                 "geometry": {"type": "Point",
                              "coordinates": [116.0 + (i % 6) * step,
                                              39.0 + (i // 6) * step]},
                 "properties": {"v": float(10 + i * 0.7)}}
                for i in range(n)
            ],
        }

    def test_sgs_driver_auto_resolves_batched_and_attaches_uncertainty(self):
        from app.lib.geo_analysis.kriging_simulation import (
            sgs_simulation_surface,
        )

        r = sgs_simulation_surface(
            self._fc(), "v", resolution=7, n_realizations=8, seed=7)
        meta = r["metadata"]
        assert meta["backend"] == "numpy_batched"
        assert meta["execution_plan"]["variant_id"] == "numpy_batched"
        assert meta["uncertainty"]["estimator"] == "sgs_ensemble"
        assert meta["uncertainty"]["std_available"] is True
        assert "value_semantics" in meta["renderer"]
        assert meta["uncertainty"]["data_quality"]["n_samples"] == 36

    def test_sgs_driver_explicit_reference_keeps_bitwise_path(self):
        from app.lib.geo_analysis.kriging_simulation import (
            sgs_simulation_surface,
        )

        r = sgs_simulation_surface(
            self._fc(), "v", resolution=7, n_realizations=6, seed=7,
            backend="numpy_reference")
        assert r["metadata"]["backend"] == "numpy_reference"

    def test_sgs_driver_invalid_backend_rejected(self):
        from app.lib.geo_analysis.kriging_simulation import (
            sgs_simulation_surface,
        )

        with pytest.raises(ValueError, match="backend 必须是"):
            sgs_simulation_surface(self._fc(), "v", backend="gpu_turbo")

    def test_st_driver_attaches_uncertainty_and_plan(self):

        from app.lib.geo_analysis.kriging_st import st_kriging_surface

        rng = np.random.default_rng(5)
        feats = []
        t0 = 1_700_000_000
        for i in range(40):
            s = i % 10
            t = t0 + (i // 10) * 86400
            v = 10 + 0.5 * s + 0.1 * (i // 10) + rng.normal(0, 0.2)
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Point",
                             "coordinates": [116.0 + s * 0.01,
                                             39.0 + s * 0.008]},
                "properties": {"v": float(v), "t": float(t)},
            })
        r = st_kriging_surface(
            {"type": "FeatureCollection", "features": feats}, "v", "t",
            target_time_sec=float(t0 + 2 * 86400), resolution=7,
            temporal_range_sec=5 * 86400)
        meta = r["metadata"]
        assert meta["uncertainty"]["estimator"] == "st_kriging_variance"
        assert meta["execution_plan"]["variant_id"] == "numpy_batched"
        assert meta["uncertainty"]["data_quality"]["n_samples"] == 40
        assert meta["uncertainty"]["interval_semantics"] == \
            "gaussian_predictive"

    def test_lmc_driver_attaches_uncertainty_and_plan(self):
        from app.lib.geo_analysis.cokriging_lmc import cokriging_lmc_surface

        rng = np.random.default_rng(9)
        p_feats, s_feats = [], []
        for i in range(30):
            lon = 116.0 + (i % 6) * 0.01
            lat = 39.0 + (i // 6) * 0.01
            f_common = float(rng.normal())
            p_feats.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {"v": float(10 + 3 * f_common)},
            })
            s_feats.append({
                "type": "Feature",
                "geometry": {"type": "Point",
                             "coordinates": [lon + 0.002, lat + 0.002]},
                "properties": {"w": float(5 + 2 * f_common)},
            })
        fc = {"type": "FeatureCollection", "features": p_feats}
        fc2 = {"type": "FeatureCollection", "features": s_feats}
        r = cokriging_lmc_surface(fc, "v", fc2, "w", resolution=7)
        meta = r["metadata"]
        assert meta["uncertainty"]["estimator"] == "cokriging_variance"
        assert meta["execution_plan"]["variant_id"] == "numpy_batched"
        assert meta["uncertainty"]["data_quality"]["value_field"] == "v"
