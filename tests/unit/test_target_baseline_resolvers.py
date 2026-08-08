"""
Unit tests for TargetAreaResolver and BaselineResolver.
Verifies spatial decision target area resolution and baseline metric resolution.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.spatial_decision.models import TargetAreaSpec, MetricDeltaV2
from app.services.spatial_decision.target_resolver import TargetAreaResolver, resolve_target_area
from app.services.spatial_decision.baseline_resolver import BaselineResolver, resolve_baseline_metrics


@pytest.mark.asyncio
async def test_target_resolver_passthrough_and_dict_spec():
    resolver = TargetAreaResolver()
    
    spec = TargetAreaSpec(
        query="Existing Spec",
        geometry_type="Point",
        center=(100.0, 20.0),
        geometry={"type": "Point", "coordinates": [100.0, 20.0]},
        bbox=[100.0, 20.0, 100.0, 20.0],
        resolved_name="Existing Spec",
        source="test",
        confidence=1.0
    )
    res = await resolver.resolve(spec)
    assert res == spec

    spec_dict = spec.model_dump()
    res_dict = await resolver.resolve(spec_dict)
    assert res_dict.resolved_name == "Existing Spec"
    assert res_dict.center == (100.0, 20.0)


@pytest.mark.asyncio
async def test_target_resolver_geojson_dict():
    resolver = TargetAreaResolver()
    geojson_dict = {
        "type": "Polygon",
        "coordinates": [
            [[120.0, 30.0], [120.1, 30.0], [120.1, 30.1], [120.0, 30.1], [120.0, 30.0]]
        ]
    }
    result = await resolver.resolve(geojson_dict)
    assert isinstance(result, TargetAreaSpec)
    assert result.geometry_type == "Polygon"
    assert result.source == "geojson"
    assert result.confidence == 1.0
    assert result.center == (120.05, 30.05)
    assert result.bbox == [120.0, 30.0, 120.1, 30.1]
    assert result.correction_hint is None


@pytest.mark.asyncio
async def test_target_resolver_geojson_string():
    resolver = TargetAreaResolver()
    geojson_str = json.dumps({
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [[121.0, 31.0], [121.2, 31.0], [121.2, 31.2], [121.0, 31.2], [121.0, 31.0]]
            ]
        },
        "properties": {"name": "Test Feature Area"}
    })
    result = await resolver.resolve(geojson_str)
    assert result.geometry_type == "Polygon"
    assert result.source == "geojson"
    assert result.confidence == 1.0
    assert result.resolved_name == "Test Feature Area"
    assert result.center == (121.1, 31.1)
    assert result.bbox == [121.0, 31.0, 121.2, 31.2]


@pytest.mark.asyncio
async def test_target_resolver_bbox_string():
    resolver = TargetAreaResolver()
    
    # Bracketed format
    result1 = await resolver.resolve("[116.3, 39.9, 116.5, 40.1]")
    assert result1.geometry_type in ("BBOX", "Polygon")
    assert result1.source == "bbox"
    assert result1.confidence == 1.0
    assert result1.bbox == [116.3, 39.9, 116.5, 40.1]
    assert result1.center == (116.4, 40.0)
    assert result1.geometry["type"] == "Polygon"

    # Comma-separated format
    result2 = await resolver.resolve("120.0, 30.0, 120.2, 30.2")
    assert result2.source == "bbox"
    assert result2.bbox == [120.0, 30.0, 120.2, 30.2]


@pytest.mark.asyncio
async def test_target_resolver_invalid_bbox():
    resolver = TargetAreaResolver()
    # Invalid BBOX (west > east)
    result = await resolver.resolve("[120.0, 30.0, 110.0, 20.0]")
    assert result.confidence == 0.0
    assert result.geometry_type == "Unknown"
    assert result.correction_hint is not None


@pytest.mark.asyncio
async def test_target_resolver_session_ref():
    mock_store = AsyncMock()
    mock_store.get.return_value = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[114.0, 22.5], [114.2, 22.5], [114.2, 22.7], [114.0, 22.7], [114.0, 22.5]]
                    ]
                },
                "properties": {"name": "Session Layer"}
            }
        ]
    }
    
    resolver = TargetAreaResolver(session_store=mock_store)
    result = await resolver.resolve("ref:layer_shenzhen", session_id="session_001")
    
    assert result.source == "session_ref"
    assert result.confidence == 1.0
    assert result.bbox == [114.0, 22.5, 114.2, 22.7]
    assert result.center == (114.1, 22.6)
    mock_store.get.assert_called_with("session_001", "ref:layer_shenzhen")


@pytest.mark.asyncio
async def test_target_resolver_admin_district_amap():
    mock_amap = AsyncMock()
    mock_amap.district.return_value = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[116.2, 39.9], [116.4, 39.9], [116.4, 40.1], [116.2, 40.1], [116.2, 39.9]]
                    ]
                },
                "properties": {"name": "海淀区", "adcode": "110108"}
            }
        ]
    }
    
    resolver = TargetAreaResolver(geocode_provider=mock_amap)
    result = await resolver.resolve("海淀区")
    
    assert result.source == "geocode"
    assert result.resolved_name == "海淀区"
    assert result.confidence >= 0.9
    assert result.bbox == [116.2, 39.9, 116.4, 40.1]
    assert result.center == (116.3, 40.0)


@pytest.mark.asyncio
async def test_target_resolver_geocode_point_fallback():
    mock_amap = AsyncMock()
    mock_amap.district.return_value = {"features": []}
    mock_amap.geocode.return_value = {
        "results": [
            {
                "location": [121.47, 31.23],
                "formatted_address": "上海市人民广场"
            }
        ]
    }

    resolver = TargetAreaResolver(geocode_provider=mock_amap)
    result = await resolver.resolve("上海市人民广场")

    assert result.source == "geocode"
    assert result.geometry_type == "Point"
    assert result.center == (121.47, 31.23)
    assert result.confidence == 0.90
    assert result.resolved_name == "上海市人民广场"


@pytest.mark.asyncio
async def test_target_resolver_unresolvable_no_beijing_fallback():
    mock_amap = AsyncMock()
    mock_amap.district.return_value = {"type": "FeatureCollection", "features": []}
    mock_amap.geocode.return_value = {"results": [], "count": 0}

    mock_store = AsyncMock()
    mock_store.get.return_value = None

    resolver = TargetAreaResolver(session_store=mock_store, geocode_provider=mock_amap)
    result = await resolver.resolve("invalid_nonexistent_location_99999", session_id="s1")

    assert result.confidence == 0.0
    assert result.geometry_type == "Unknown"
    assert result.geometry is None
    assert result.center is None
    assert result.bbox is None
    assert result.correction_hint is not None
    assert "Unable to resolve target area" in result.correction_hint
    # Verify NO fallback to [116.4, 39.9]
    assert result.center != (116.4, 39.9)


@pytest.mark.asyncio
async def test_functional_wrapper_resolve_target_area():
    res = await resolve_target_area("[120.0, 30.0, 120.1, 30.1]")
    assert res.source == "bbox"
    assert res.center == (120.05, 30.05)


@pytest.mark.asyncio
async def test_baseline_resolver_with_session_store_data():
    mock_store = AsyncMock()
    mock_store.get.return_value = {
        "metrics": {
            "housing_price": 65000.0,
            "green_coverage": 0.42
        }
    }

    target_area = TargetAreaSpec(
        query="海淀区",
        geometry_type="Polygon",
        center=(116.3, 40.0),
        geometry={
            "type": "Polygon",
            "coordinates": [[[116.2, 39.9], [116.4, 39.9], [116.4, 40.1], [116.2, 40.1], [116.2, 39.9]]]
        },
        bbox=[116.2, 39.9, 116.4, 40.1],
        resolved_name="海淀区",
        source="geocode",
        confidence=0.95
    )

    resolver = BaselineResolver(session_store=mock_store)
    metrics = await resolver.resolve_baseline(
        baseline_data_ref="ref:baseline_haidian",
        target_area=target_area,
        session_id="s1",
        metrics_needed=["housing_price", "green_coverage"]
    )

    assert "housing_price" in metrics
    assert metrics["housing_price"].baseline == 65000.0
    assert not metrics["housing_price"].missing_baseline

    assert "green_coverage" in metrics
    assert metrics["green_coverage"].baseline == 0.42
    assert not metrics["green_coverage"].missing_baseline


@pytest.mark.asyncio
async def test_baseline_resolver_geojson_spatial_aggregation():
    mock_store = AsyncMock()
    mock_store.get.return_value = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [116.3, 40.0]},
                "properties": {"price": 60000.0, "pop_density": 5000}
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [116.35, 40.05]},
                "properties": {"price": 70000.0, "pop_density": 7000}
            },
            # Feature outside target area
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [120.0, 30.0]},
                "properties": {"price": 20000.0, "pop_density": 1000}
            }
        ]
    }

    target_area = TargetAreaSpec(
        query="Target Area",
        geometry_type="Polygon",
        center=(116.3, 40.0),
        geometry={
            "type": "Polygon",
            "coordinates": [[[116.2, 39.9], [116.4, 39.9], [116.4, 40.1], [116.2, 40.1], [116.2, 39.9]]]
        },
        bbox=[116.2, 39.9, 116.4, 40.1],
        resolved_name="Target Area",
        source="bbox",
        confidence=1.0
    )

    resolver = BaselineResolver(session_store=mock_store)
    metrics = await resolver.resolve_baseline(
        baseline_data_ref="ref:spatial_points",
        target_area=target_area,
        session_id="s1",
        metrics_needed=["price", "pop_density"]
    )

    # Price average of points inside (60000 + 70000)/2 = 65000
    assert metrics["price"].baseline == 65000.0
    assert metrics["pop_density"].baseline == 6000.0


@pytest.mark.asyncio
async def test_baseline_resolver_missing_data_auto_heal_and_no_dummy_100():
    mock_store = AsyncMock()
    mock_store.get.return_value = None  # No baseline data stored

    target_area = TargetAreaSpec(
        query="Area",
        geometry_type="Polygon",
        center=(116.3, 40.0),
        geometry={
            "type": "Polygon",
            "coordinates": [[[116.2, 39.9], [116.4, 39.9], [116.4, 40.1], [116.2, 40.1], [116.2, 39.9]]]
        },
        bbox=[116.2, 39.9, 116.4, 40.1],
        resolved_name="Area",
        source="bbox",
        confidence=1.0
    )

    resolver = BaselineResolver(session_store=mock_store)
    metrics = await resolver.resolve_baseline(
        baseline_data_ref="ref:nonexistent",
        target_area=target_area,
        session_id="s1",
        metrics_needed=["housing_price", "area_km2"]
    )

    # Domain specific missing metric: housing_price
    assert metrics["housing_price"].missing_baseline is True
    assert metrics["housing_price"].evidence_gap_note is not None
    assert "housing_price" in metrics["housing_price"].evidence_gap_note
    # Must NOT default to 100.0!
    assert metrics["housing_price"].baseline != 100.0

    # Auto-heal spatial metric: area_km2 computed from polygon geometry
    assert metrics["area_km2"].missing_baseline is False
    assert metrics["area_km2"].baseline > 0.0


@pytest.mark.asyncio
async def test_functional_wrapper_resolve_baseline_metrics():
    mock_store = AsyncMock()
    mock_store.get.return_value = {"housing_price": 50000.0}

    target_area = TargetAreaSpec(
        query="Area",
        geometry_type="Unknown",
        resolved_name="Area",
        source="unresolved",
        confidence=0.0
    )

    metrics = await resolve_baseline_metrics(
        baseline_data_ref="ref:data",
        target_area=target_area,
        session_id="s1",
        metrics_needed=["housing_price"],
        session_store=mock_store
    )
    assert metrics["housing_price"].baseline == 50000.0
