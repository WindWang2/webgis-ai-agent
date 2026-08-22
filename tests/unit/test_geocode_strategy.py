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


# ─── #771: (0, 0) is a geocode failure, never a Null-Island success ─────────


@pytest.mark.asyncio
async def test_geocode_strategy_zero_zero_is_failure_771():
    """#771: tianditu/baidu default a MISSING location to 0 — a (0,0) row must
    be counted as failed (and rotate/fall back), not as an ok geocode at Null
    Island."""
    strategy = GeocodeProviderStrategy()

    async def mock_geocode(addresses, provider, max_concurrency):
        # tianditu-shaped miss: status passes old checker, location [0.0, 0.0]
        return {
            "results": [
                {"index": 0, "results": [{"location": [0.0, 0.0]}], "provider": provider}
            ]
        }

    results, _multi = await strategy.geocode_addresses(
        ["不存在的地址"], batch_geocode=mock_geocode,
        providers=["tianditu"],  # single provider: no rotation possible
    )
    assert results[0].status == "failed"
    assert results[0].lat is None
    assert results[0].lon is None


# ─── #772: row-level provider attribution + precision + fallback marker ─────


@pytest.mark.asyncio
async def test_geocode_strategy_row_provider_attribution_772():
    """#772: when with_fallback silently answered an amap request via baidu,
    the row must be attributed to baidu (with a switch marker), not to the
    requested provider; precision level is carried through."""
    strategy = GeocodeProviderStrategy()

    async def mock_geocode(addresses, provider, max_concurrency):
        assert provider == "amap"
        return {
            "results": [
                {
                    "index": 0,
                    "results": [{
                        "location": [116.4, 39.9],
                        "precision_level": "district",
                    }],
                    "provider": "baidu",
                    "provenance": {"source": "baidu"},
                }
            ]
        }

    results, multi = await strategy.geocode_addresses(["地址"], batch_geocode=mock_geocode)
    assert results[0].status == "ok"
    assert results[0].provider == "baidu"          # the ACTUAL provider
    assert results[0].provider_switched is True     # per-row fallback marker
    assert results[0].precision == "district"       # precision level kept
    assert multi is True                            # summary is truthful


@pytest.mark.asyncio
async def test_geocode_strategy_no_switch_no_marker_772():
    """#772: an honestly-answered amap row carries no switch marker and a
    truthful multi_provider=False (previously a same-provider batch could not
    distinguish intra-batch switches at all)."""
    strategy = GeocodeProviderStrategy()

    async def mock_geocode(addresses, provider, max_concurrency):
        return {
            "results": [
                {"index": 0, "results": [{"location": [116.4, 39.9], "level": "门址"}],
                 "provider": "amap"}
            ]
        }

    results, multi = await strategy.geocode_addresses(["地址"], batch_geocode=mock_geocode)
    assert results[0].provider == "amap"
    assert results[0].provider_switched is False
    assert results[0].precision == "门址"
    assert multi is False
