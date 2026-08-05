import pytest
from app.services.geocode_strategy import (
    extract_lat_lon,
    GeocodeProviderStrategy,
)

def test_extract_lat_lon():
    assert extract_lat_lon({"results": [{"location": [116.4, 39.9]}]}) == (116.4, 39.9)
    assert extract_lat_lon({"lat": 39.9, "lon": 116.4}) == (116.4, 39.9)
    assert extract_lat_lon({"lat": 39.9, "lng": 116.4}) == (116.4, 39.9)
    assert extract_lat_lon({"location": {"lat": 39.9, "lng": 116.4}}) == (116.4, 39.9)
    assert extract_lat_lon({"loc": [116.4, 39.9]}) == (116.4, 39.9)
    assert extract_lat_lon({"invalid": 1}) == (None, None)

@pytest.mark.asyncio
async def test_geocode_strategy_single_provider():
    strategy = GeocodeProviderStrategy()
    
    async def mock_geocode(addresses, provider, max_concurrency):
        return {
            "results": [
                {"index": 0, "lat": 39.9, "lon": 116.4}
            ]
        }
        
    results, multi = await strategy.geocode_addresses(["Beijing"], batch_geocode=mock_geocode)
    assert multi is False
    assert len(results) == 1
    assert results[0].lat == 39.9
    assert results[0].lon == 116.4
    assert results[0].status == "ok"
    assert results[0].provider == "amap"

@pytest.mark.asyncio
async def test_geocode_strategy_multi_provider_rotation():
    strategy = GeocodeProviderStrategy()
    
    async def mock_geocode(addresses, provider, max_concurrency):
        if provider == "amap":
            return {"error": "all failed"}
        elif provider == "baidu":
            return {
                "results": [
                    {"index": 0, "lat": 39.9, "lon": 116.4}
                ]
            }
        
    results, multi = await strategy.geocode_addresses(["Beijing"], batch_geocode=mock_geocode)
    assert multi is True
    assert len(results) == 1
    assert results[0].lat == 39.9
    assert results[0].provider == "baidu"

@pytest.mark.asyncio
async def test_geocode_strategy_threshold_rotation():
    strategy = GeocodeProviderStrategy()
    
    async def mock_geocode(addresses, provider, max_concurrency):
        if provider == "amap":
            return {
                "results": [
                    {"index": 0, "lat": 1.0, "lon": 1.0},
                    {"index": 1, "lat": 1.0, "lon": 1.0},
                ],
                "errors": [
                    {"index": 2, "error": "not found"},
                    {"index": 3, "error": "not found"}
                ]
            }
        elif provider == "baidu":
            return {
                "results": [
                    {"index": 0, "lat": 2.0, "lon": 2.0},
                    {"index": 1, "lat": 2.0, "lon": 2.0},
                ]
            }
        return {"error": "bad"}

    results, multi = await strategy.geocode_addresses(["A", "B", "C", "D"], batch_geocode=mock_geocode, failure_threshold=0.30)
    assert multi is True
    assert len(results) == 4
    assert results[0].provider == "amap"
    assert results[0].lat == 1.0
    assert results[2].provider == "baidu"
    assert results[2].lat == 2.0
