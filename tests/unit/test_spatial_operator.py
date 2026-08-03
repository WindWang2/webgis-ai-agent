import pytest
from app.services.spatial_operator import spatial_operator
from app.lib.geo_processor.core import GeoAnalysisResult

# Mock to_feature_collection to just wrap strings to demonstrate it fired
@pytest.fixture(autouse=True)
def mock_to_fc(monkeypatch):
    def fake_to_fc(data):
        if isinstance(data, str) and data == "raw_data":
            return {"type": "FeatureCollection", "features": []}
        return data
    monkeypatch.setattr("app.services.spatial_operator.to_feature_collection", fake_to_fc)

class DummyAnalyzer:
    @classmethod
    @spatial_operator(name="test_op", progress_pct=25)
    def simple_op(cls, features, extra=None, callback=None):
        return {"result": extra}

    @classmethod
    @spatial_operator(name="multi_op", feature_keys=["feat_a", "feat_b"])
    def multi_op(cls, feat_a, feat_b, callback=None):
        return {"a": feat_a, "b": feat_b}

    @classmethod
    @spatial_operator(name="fail_op")
    def fail_op(cls, features):
        raise ValueError("Something broke")

    @classmethod
    @spatial_operator(name="geo_op")
    def geo_op(cls, features):
        return GeoAnalysisResult(True, {"geo": "result"}, "Already wrapped")

def test_single_feature_normalization():
    res = DummyAnalyzer.simple_op("raw_data", extra="test")
    assert isinstance(res, GeoAnalysisResult)
    assert res.success is True
    assert res.summary == "test_op analysis completed successfully"

def test_multi_feature_normalization():
    res = DummyAnalyzer.multi_op("raw_data", "raw_data")
    assert res.success is True
    assert res.data["a"]["type"] == "FeatureCollection"
    assert res.data["b"]["type"] == "FeatureCollection"

def test_progress_callback():
    calls = []
    def my_callback(pct, msg):
        calls.append((pct, msg))
    
    DummyAnalyzer.simple_op("raw_data", callback=my_callback)
    assert len(calls) == 1
    assert calls[0] == (25, "Executing test_op analysis...")

def test_exception_wrapping():
    res = DummyAnalyzer.fail_op("raw_data")
    assert res.success is False
    assert "fail_op failed: Something broke" in res.summary

def test_returns_geo_analysis_result():
    res = DummyAnalyzer.geo_op("raw_data")
    assert res.success is True
    assert res.summary == "Already wrapped"
