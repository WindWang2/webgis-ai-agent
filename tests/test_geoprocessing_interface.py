import pytest
from app.lib.geoprocessing.interface import GeoAnalysisResult

def test_geo_analysis_result_to_llm_response():
    result = GeoAnalysisResult(
        success=True,
        data={"coords": [10, 20]},
        summary="Found 1 point."
    )
    response = result.to_llm_response()
    assert response["success"] is True
    assert response["data"] == {"coords": [10, 20]}
    assert response["summary"] == "Found 1 point."
    assert "error_type" in response
    assert "correction_hint" in response

def test_geo_analysis_result_with_errors():
    result = GeoAnalysisResult(
        success=False,
        data=None,
        summary="Failed to process.",
        error_type="ValueError",
        correction_hint="Check your parameters."
    )
    response = result.to_llm_response()
    assert response["success"] is False
    assert response["error_type"] == "ValueError"
    assert response["correction_hint"] == "Check your parameters."
