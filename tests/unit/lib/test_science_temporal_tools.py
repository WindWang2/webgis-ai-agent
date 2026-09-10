"""science_temporal_tools 工具面测试（W8/W11）。

TOOL_UNTESTED 棘轮约束：注册的工具必须有工具级测试。本文件走
ToolRegistry.dispatch 的真实入口（非 lib 直调），并钉 descriptor
完整性（side_effect/tier/domains）。
"""
from __future__ import annotations

import numpy as np
import pytest


@pytest.fixture(scope="module")
def registry():
    from app.tools.registry import ToolRegistry
    from app.tools.science_temporal_tools import (
        register_science_temporal_tools,
    )

    reg = ToolRegistry()
    register_science_temporal_tools(reg)
    return reg


def _fixture_stack(t_len=16, h=4, w=4, seed=3, with_cloud=True):
    rng = np.random.default_rng(seed)
    t_norm = np.arange(t_len) / t_len
    base = 0.4 + 0.3 * np.sin(2 * np.pi * t_norm)
    stack = np.broadcast_to(base[:, None, None], (t_len, h, w)).copy()
    stack += rng.normal(0, 0.02, stack.shape)
    times = (np.arange(t_len) * 16.0).tolist()
    cloud = None
    if with_cloud:
        cloud = np.ones((t_len, h, w))
        cloud[5, 0, 0] = 0.0                      # 单切片云遮蔽
    return stack.tolist(), times, (None if cloud is None else cloud.tolist())


class TestScienceTemporalTools:
    def test_descriptors_registered_complete(self, registry):
        for name in ("temporal_cube_stats", "phenology_features",
                     "temporal_anomaly"):
            d = registry.descriptors()[name]
            assert d.side_effect.value == "deterministic_compute"
            assert d.tier == 2
            assert "temporal" in d.domains

    async def test_temporal_cube_stats_dispatch(self, registry):
        stack, times, cloud = _fixture_stack()
        out = await registry.dispatch(
            "temporal_cube_stats",
            {"stack": stack, "times": times, "cloud_mask": cloud,
             "source_type": "optical"},
        )
        assert "summary" in out
        assert out["n_slices"] == 16
        assert out["slice_spacing"]["missing_slices"] == 0
        assert out["gap_length_range"][1] >= 1     # 云遮蔽缺口被披露
        assert out["climatology_summary"]["mean"]["finite_pixels"] == 16

    async def test_phenology_features_dispatch(self, registry):
        stack, times, cloud = _fixture_stack()
        out = await registry.dispatch(
            "phenology_features",
            {"stack": stack, "times": times, "cloud_mask": cloud,
             "window": 5, "polyorder": 2, "max_gap": 2,
             "threshold_frac": 0.5},
        )
        assert "物候特征完成" in out["summary"]
        fs = out["features_summary"]
        for key in ("sos_idx", "eos_idx", "los", "peak_value", "amplitude"):
            assert key in fs
        assert fs["amplitude"]["finite_pixels"] == 16   # 缺口已填
        assert any("缺口" in s for s in out["meta"]["disclosures"])

    async def test_temporal_anomaly_dispatch(self, registry):
        stack, times, _ = _fixture_stack(with_cloud=False)
        out = await registry.dispatch(
            "temporal_anomaly",
            {"stack": stack, "times": times, "baseline_slices": 0},
        )
        assert "时间异常完成" in out["summary"]
        assert "anomaly_last" in out["features_summary"]
        assert "change_z" in out["features_summary"]

    async def test_invalid_input_error_response(self, registry):
        # dispatch 错误映射：形状不一致 → 错误响应（不静默成功）
        out = await registry.dispatch(
            "temporal_cube_stats",
            {"stack": [[[1.0]]], "times": [1.0, 2.0]},
        )
        assert (out.get("type") == "error") or ("error" in str(out).lower()) or             (out.get("success") is False), f"非错误响应: {str(out)[:200]}"
