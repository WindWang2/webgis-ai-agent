"""Scene pipeline performance benchmarks (ADR-0199 M8, marker=perf).

合成 workload（无网络、无 LLM、无真实 DEM 数据）：
  1. scene_plan_10k_features  — 规划决策不随要素数放大（O(1) 决策表）
  2. scene_lod_table          — LOD 查询 O(bands)
  3. scene_camera_planner     — 相机规划常数时间
  4. scene_quality_gate_1k    — 1000 层 spec 的门禁线性扫描
  5. terrarium_encode_1k_sq   — 1000×1000 DEM 网格 terrarium 编码
  6. legend_invariance_1k     — 1000 层 legend digest 对比

环境差异声明：本地数字只用于回归棘轮（self-baseline），不是普适 SLO；
门语义与 perf harness 一致（中位数 + 上限因子）。

用法：``pytest tests/benchmarks/test_scene_perf.py -m perf -q``。
"""
from __future__ import annotations

import statistics
import time

import numpy as np
import pytest

from app.lib.cartography.scene_camera import plan_scene_camera
from app.lib.cartography.scene_lod import lod_for_zoom
from app.lib.cartography.scene_planning import SceneIntent, plan_scene
from app.lib.cartography.scene_quality import evaluate_scene_quality
from app.lib.cartography.terrain_encoding import encode_terrarium

pytestmark = pytest.mark.perf

#: 每个工作负载的迭代数（中位数抵御噪声）。
_ITERS = 7


def _median_ms(fn, *args, **kwargs) -> float:
    samples = []
    for _ in range(_ITERS):
        t0 = time.perf_counter()
        fn(*args, **kwargs)
        samples.append((time.perf_counter() - t0) * 1000.0)
    return statistics.median(samples)


def _make_layers(n: int) -> list:
    return [
        {
            "id": f"l{i}",
            "source": "s1",
            "type": "fill-extrusion" if i % 3 == 0 else "fill",
            "extrusion": {"height_field": "gdp"} if i % 3 == 0 else None,
            "legend_spec": {"kind": "graduated", "field": f"f{i}", "breaks": [1, 2, 3, 4]},
        }
        for i in range(n)
    ]


class TestScenePerf:
    def test_scene_plan_constant_time(self):
        """规划决策是 O(1) 决策表 —— 不随要素数放大。"""
        def run():
            plan_scene(SceneIntent(
                purpose="analysis",
                geometry_kinds=["polygon"],
                feature_count=10_000,
                has_height_attribute_evidence=True,
                vertical_extrusion_intent=True,
            ))
        ms = _median_ms(run)
        assert ms < 5.0, f"scene plan took {ms:.2f}ms (expected < 5ms)"

    def test_scene_lod_lookup_bounded(self):
        def run():
            for z in range(3, 19):
                lod_for_zoom(float(z))
        ms = _median_ms(run)
        assert ms < 2.0, f"LOD 16-zoom sweep took {ms:.2f}ms (expected < 2ms)"

    def test_scene_camera_planner_bounded(self):
        def run():
            plan_scene_camera([116.0, 39.0, 117.0, 40.0], role="detail")
        ms = _median_ms(run)
        assert ms < 2.0, f"camera plan took {ms:.2f}ms (expected < 2ms)"

    def test_scene_quality_gate_linear_1k_layers(self):
        spec = {
            "version": "1.4",
            "scene": {"mode": "3d", "terrain": {"source": "dem", "exaggeration": 1.0}},
            "sources": {"dem": {"type": "raster-dem", "url": "https://x.test/{z}/{x}/{y}.png"}},
            "layers": _make_layers(1000),
        }
        ms = _median_ms(evaluate_scene_quality, spec)
        # 1000 层线性扫描（dict 查找为主）应有界；本地基线 < 50ms。
        assert ms < 50.0, f"1k-layer scene gate took {ms:.2f}ms (expected < 50ms)"

    def test_terrarium_encode_1k_grid(self):
        rng = np.random.default_rng(7)
        elev = rng.uniform(0.0, 3000.0, size=(1000, 1000))
        valid = np.ones((1000, 1000), dtype=bool)
        ms = _median_ms(encode_terrarium, elev, valid)
        # 1e6 像素三通道编码；本地基线 < 40ms（矢量化 numpy）。
        assert ms < 40.0, f"1k² terrarium encode took {ms:.2f}ms (expected < 40ms)"

    def test_legend_digest_1k_layers_bounded(self):
        from app.lib.cartography.scene_quality import check_legend_invariance

        before = _make_layers(1000)
        after = [dict(l) for l in before]
        ms = _median_ms(check_legend_invariance, before, after)
        assert ms < 80.0, f"1k-layer legend invariance took {ms:.2f}ms (expected < 80ms)"
