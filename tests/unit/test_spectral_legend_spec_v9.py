"""V9 P7 —— spectral_engine legend_spec 真 TODO 清偿的行为钉子。"""
from __future__ import annotations

from app.services.rs.band_math import RasterAnalysisResult


def test_legend_spec_flows_into_llm_response():
    result = RasterAnalysisResult(
        index_type="ndvi",
        bounds=[116.0, 39.5, 116.5, 40.0],
        stats={"min": -0.2, "max": 0.8, "mean": 0.3},
        legend_spec={"type": "continuous", "palette": "Viridis",
                     "min": -0.2, "max": 0.8, "unit": "NDVI"},
    )
    payload = result.to_llm_response()
    assert payload["success"] is True
    assert payload["legend_spec"]["palette"] == "Viridis"
    assert payload["legend_spec"]["unit"] == "NDVI"


def test_legend_spec_absent_is_honest_omission():
    result = RasterAnalysisResult(index_type="ndvi", stats={"min": 0, "max": 1})
    payload = result.to_llm_response()
    assert "legend_spec" not in payload, "未计算图例时绝不虚构键"


def test_error_path_ignores_legend_spec():
    result = RasterAnalysisResult(index_type="ndvi", is_error=True,
                                  error_msg="x",
                                  legend_spec={"type": "continuous"})
    payload = result.to_llm_response()
    assert payload["success"] is False
    assert "legend_spec" not in payload
