"""G11 — rs 模块 legend_spec「TODO 清偿」注记闭环回归。

两处注记声明的行为此前无测试守护：
- app/services/rs/spectral_engine.py（V9 P7 注记）：compute_index 算出的
  连续色带图例 legend_spec **挂在返回值对象上**，不再算完就丢 —— 回归即
  spectral_engine 路径返回的 RasterAnalysisResult.legend_spec 非空，且
  min/max 与 stats 配准、unit 为指数大写名；
- app/services/rs/band_math.py（V9 P7 注记）：RasterAnalysisResult.
  legend_spec 字段经 to_llm_response 进入 LLM/工具 payload —— 回归即字段
  存在时透传、缺省（含错误结果）时键不出现（不给下游半真半假的数据）。

下游消费面（tool_dispatch_service 白名单/图层挂接、chat、project_memory、
pi_agent_harness replay 校验）依赖以上两个不变量。
"""
from __future__ import annotations

import numpy as np


def _engine_with_bands(red, nir):
    from app.services.rs.spectral_engine import SpectralRasterEngine

    class _FakeStac:
        def __init__(self, bands):
            self._bands = bands

        async def fetch_stac_items_and_bands(self, **kwargs):
            return {"bands": self._bands, "bounds": [10.0, 40.0, 12.0, 42.0],
                    "items": [{"id": "item-1"}]}

    engine = SpectralRasterEngine()
    engine.stac = _FakeStac({"red": red, "nir": nir})
    return engine


def test_compute_index_attaches_legend_spec_from_stats():
    """光谱引擎：legend_spec 挂到返回值（此前算出即丢的 V9 P7 回归点），
    min/max 与 stats 配准，unit 为指数大写名。"""
    red = np.array([[0.2, 0.4], [0.6, 0.8]])
    nir = red + 0.2

    import asyncio

    result = asyncio.run(
        _engine_with_bands(red, nir).compute_index(
            bbox=[10.0, 40.0, 12.0, 42.0],
            date_from="2026-06-01",
            date_to="2026-06-30",
            index_type="ndvi",
        )
    )
    assert result.is_error is False
    legend = result.legend_spec
    assert legend is not None, "legend_spec 被丢弃 —— V9 P7 清偿回归"
    assert legend["type"] == "continuous"
    assert legend["palette"] == "Viridis"
    assert legend["unit"] == "NDVI"
    assert legend["min"] == result.stats.get("min")
    assert legend["max"] == result.stats.get("max")


def test_compute_index_error_result_carries_no_legend():
    """STAC 无数据（错误结果）→ is_error 且无 legend_spec 半成品。"""
    from app.services.rs.spectral_engine import SpectralRasterEngine

    class _EmptyStac:
        async def fetch_stac_items_and_bands(self, **kwargs):
            return {"error": "指定区域和时间段无数据", "items": []}

    engine = SpectralRasterEngine()
    engine.stac = _EmptyStac()

    import asyncio

    result = asyncio.run(
        engine.compute_index(
            bbox=[10.0, 40.0, 12.0, 42.0],
            date_from="2026-06-01",
            date_to="2026-06-30",
            index_type="ndvi",
        )
    )
    assert result.is_error is True
    assert result.legend_spec is None


def test_to_llm_response_passes_legend_spec_through():
    """波段运算值对象：legend_spec 字段存在时经 to_llm_response 透传给
    LLM/工具 payload（band_math 注记声明的挂接面）。"""
    from app.services.rs.band_math import RasterAnalysisResult

    spec = {"type": "continuous", "palette": "Viridis", "min": 0.0, "max": 1.0,
            "unit": "NDVI"}
    result = RasterAnalysisResult(
        index_type="ndvi",
        bounds=[10.0, 40.0, 12.0, 42.0],
        stats={"min": 0.0, "max": 1.0, "mean": 0.5},
        legend_spec=spec,
    )
    payload = result.to_llm_response()
    assert payload["success"] is True
    assert payload["legend_spec"] == spec


def test_to_llm_response_omits_legend_spec_when_absent():
    """缺省路径：无 legend_spec 时键不出现 —— 下游（白名单/挂接面）据此
    区分「无图例」与「图例为空」，不得收到 None 值键。"""
    from app.services.rs.band_math import RasterAnalysisResult

    result = RasterAnalysisResult(
        index_type="ndvi",
        bounds=[10.0, 40.0, 12.0, 42.0],
        stats={"min": 0.0, "max": 1.0},
    )
    payload = result.to_llm_response()
    assert payload["success"] is True
    assert "legend_spec" not in payload

    error_payload = RasterAnalysisResult(
        index_type="ndvi", is_error=True, error_msg="boom"
    ).to_llm_response()
    assert "legend_spec" not in error_payload
