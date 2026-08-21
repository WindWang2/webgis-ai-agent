"""Issue #693 item 3: buffer unit whitelist (m/km), illegal values fail explicitly."""

import pytest
from app.lib.geo_processor.geometry import buffer_smart


def _pt():
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.39, 39.9]}, "properties": {}}
    ]}


def test_buffer_unit_km_ok():
    res = buffer_smart(_pt(), distance=1, unit="km")
    assert res.success


def test_buffer_unit_m_ok():
    res = buffer_smart(_pt(), distance=100, unit="m")
    assert res.success


def test_buffer_unit_invalid_fails():
    res = buffer_smart(_pt(), distance=100, unit="ft")
    assert not res.success
    assert "Unsupported" in res.summary or "unit" in res.summary.lower()


def test_buffer_schema_rejects_invalid_unit():
    from app.tools.spatial import BufferAnalysisArgs
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        BufferAnalysisArgs(geojson={}, distance=100, unit="ft")
    # allowed values pass
    BufferAnalysisArgs(geojson={}, distance=100, unit="km")
    BufferAnalysisArgs(geojson={}, distance=100, unit="m")
