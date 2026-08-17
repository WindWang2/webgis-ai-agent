import json
import geopandas as gpd
from app.lib.geo_processor.core import (
    safe_parse, to_utm_gdf,
    wgs84_to_gcj02, gcj02_to_wgs84,
    gcj02_to_bd09, bd09_to_gcj02
)

def test_safe_parse():
    # Test string input
    assert safe_parse('{"type": "Point", "coordinates": [0, 0]}') == {"type": "Point", "coordinates": [0, 0]}
    # Test dict input
    assert safe_parse({"type": "Point", "coordinates": [0, 0]}) == {"type": "Point", "coordinates": [0, 0]}
    # Test invalid input
    assert safe_parse("invalid") is None
    assert safe_parse(None) is None

def test_coord_transform_smoke():
    # Beijing coordinates
    lng, lat = 116.404, 39.915
    
    # WGS84 -> GCJ-02
    gcj_lng, gcj_lat = wgs84_to_gcj02(lng, lat)
    assert gcj_lng != lng
    assert gcj_lat != lat
    
    # GCJ-02 -> WGS84
    wgs_lng, wgs_lat = gcj02_to_wgs84(gcj_lng, gcj_lat)
    assert abs(wgs_lng - lng) < 1e-5
    assert abs(wgs_lat - lat) < 1e-5

    # GCJ-02 -> BD-09
    bd_lng, bd_lat = gcj02_to_bd09(gcj_lng, gcj_lat)
    assert bd_lng != gcj_lng
    assert bd_lat != gcj_lat

    # BD-09 -> GCJ-02
    gcj_lng2, gcj_lat2 = bd09_to_gcj02(bd_lng, bd_lat)
    assert abs(gcj_lng2 - gcj_lng) < 1e-5
    assert abs(gcj_lat2 - gcj_lat) < 1e-5

def test_to_utm_gdf():
    geojson = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature", 
            "geometry": {"type": "Point", "coordinates": [116.4, 39.9]}, 
            "properties": {"name": "Beijing"}
        }]
    }
    gdf, utm_crs = to_utm_gdf(geojson)
    assert isinstance(gdf, gpd.GeoDataFrame)
    assert utm_crs.startswith("EPSG:326") # Northern hemisphere, zone 50 (approx)
    assert gdf.crs == utm_crs
    assert len(gdf) == 1
    assert gdf.iloc[0]["name"] == "Beijing"

def test_buffer_smart():
    from app.lib.geo_processor.geometry import buffer_smart
    geojson = {"type": "Point", "coordinates": [116.4, 39.9]}
    # 100 meters buffer
    res = buffer_smart(geojson, distance=100)
    assert res.success is True
    assert res.data["type"] == "FeatureCollection"
    
    # Check if area is roughly pi * 100^2
    # Convert back to UTM to check area
    from app.lib.geo_processor.core import to_utm_gdf
    gdf, _ = to_utm_gdf(res.data)
    assert abs(gdf.area.iloc[0] - 3.14159 * 100**2) < 500 # Allowing some tolerance

def test_clip_smart():
    from app.lib.geo_processor.geometry import clip_smart
    target = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0,0], [10,0], [10,10], [0,10], [0,0]]]}, "properties": {}}]
    }
    mask = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[5,5], [15,5], [15,15], [5,15], [5,5]]]}, "properties": {}}]
    }
    res = clip_smart(target, mask)
    assert res.success is True
    assert res.data["type"] == "FeatureCollection"
    
    # Result should be a 5x5 square
    from app.lib.geo_processor.core import to_utm_gdf
    gdf, _ = to_utm_gdf(res.data)
    assert len(gdf) > 0

def test_dissolve_smart():
    from app.lib.geo_processor.geometry import dissolve_smart
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0,0], [1,0], [1,1], [0,1], [0,0]]]}, "properties": {"group": 1}},
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[1,0], [2,0], [2,1], [1,1], [1,0]]]}, "properties": {"group": 1}}
        ]
    }
    res = dissolve_smart(geojson, field="group")
    assert res.success is True
    assert len(res.data["features"]) == 1

def test_overlay_smart():
    from app.lib.geo_processor.overlay import overlay_smart
    poly1 = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0,0], [2,0], [2,2], [0,2], [0,0]]]}, "properties": {"id": 1}}]
    }
    poly2 = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[1,1], [3,1], [3,3], [1,3], [1,1]]]}, "properties": {"id": 2}}]
    }
    
    # Intersection
    res_int = overlay_smart(poly1, poly2, how="intersection")
    assert res_int.success is True
    assert len(res_int.data["features"]) > 0
    
    # Union
    res_uni = overlay_smart(poly1, poly2, how="union")
    assert res_uni.success is True
    assert len(res_uni.data["features"]) > 0

def test_safe_parse_feature_list_and_ref_cursor():
    # Feature list input
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}}]
    assert safe_parse(feats) == feats
    assert safe_parse(json.dumps(feats)) == feats
    
    # Cursor ref strings rejected
    assert safe_parse("ref:session_123") is None
    assert safe_parse("ref:") is None

def test_to_utm_gdf_boundary_fallback():
    # Anti-meridian point (180 lon)
    geojson_180 = {"type": "Point", "coordinates": [180.0, 10.0]}
    gdf, utm_crs = to_utm_gdf(geojson_180)
    assert gdf is not None
    assert utm_crs.startswith("EPSG:32")
    assert not utm_crs.endswith("61")  # No EPSG:32661 crash

    # Out of bounds longitude
    geojson_oob = {"type": "Point", "coordinates": [180.0001, 39.9]}
    gdf_oob, utm_crs_oob = to_utm_gdf(geojson_oob)
    assert gdf_oob is not None
    assert utm_crs_oob.startswith("EPSG:32")
    assert not utm_crs_oob.endswith("61")

def test_make_valid_handling():
    from app.lib.geo_processor.geometry import buffer_smart, clip_smart, dissolve_smart
    from app.lib.geo_processor.overlay import overlay_smart

    # Invalid self-intersecting polygon (bowtie)
    invalid_poly = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [0, 2], [1, 1], [2, 2], [2, 0], [1, 1], [0, 0]]]
    }

    # buffer_smart with invalid poly
    res_buf = buffer_smart(invalid_poly, distance=10)
    assert res_buf.success is True

    # clip_smart with invalid poly
    mask_poly = {"type": "Polygon", "coordinates": [[[-1, -1], [3, -1], [3, 3], [-1, 3], [-1, -1]]]}
    res_clip = clip_smart(invalid_poly, mask_poly)
    assert res_clip.success is True

    # dissolve_smart with invalid poly
    res_dis = dissolve_smart(invalid_poly)
    assert res_dis.success is True

    # overlay_smart with invalid poly
    res_over = overlay_smart(invalid_poly, mask_poly, how="intersection")
    assert res_over.success is True

def test_bare_geometry_dict_parsing():
    from app.lib.geo_processor.geometry import clip_smart, dissolve_smart
    from app.lib.geo_processor.overlay import overlay_smart

    bare_poly1 = {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]}
    bare_poly2 = {"type": "Polygon", "coordinates": [[[1, 1], [3, 1], [3, 3], [1, 3], [1, 1]]]}

    # clip_smart with bare geometry dicts
    res_clip = clip_smart(bare_poly1, bare_poly2)
    assert res_clip.success is True
    assert res_clip.data["type"] == "FeatureCollection"

    # dissolve_smart with bare geometry dict
    res_dis = dissolve_smart(bare_poly1)
    assert res_dis.success is True
    assert res_dis.data["type"] == "FeatureCollection"

    # overlay_smart with bare geometry dicts
    res_over = overlay_smart(bare_poly1, bare_poly2, how="intersection")
    assert res_over.success is True
    assert res_over.data["type"] == "FeatureCollection"

def test_crs_alignment():
    from app.lib.geo_processor.geometry import clip_smart
    from app.lib.geo_processor.overlay import overlay_smart
    import geopandas as gpd
    from shapely.geometry import Polygon

    # Layer A in EPSG:4326
    poly_a = {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]}
    
    # Layer B converted to EPSG:3857 GeoJSON
    gdf_b = gpd.GeoDataFrame(
        geometry=[Polygon([(1, 1), (3, 1), (3, 3), (1, 3), (1, 1)])],
        crs="EPSG:4326"
    ).to_crs("EPSG:3857")
    layer_b_3857 = json.loads(gdf_b.to_json())

    # clip_smart should handle differing CRS smoothly
    res_clip = clip_smart(poly_a, layer_b_3857)
    assert res_clip.success is True

    # overlay_smart should handle differing CRS smoothly
    res_over = overlay_smart(poly_a, layer_b_3857, how="intersection")
    assert res_over.success is True


# ── Issue #599: operators must honor a declared GeoJSON `crs` member ────────
#
# reproject_coordinates (GIS-22) writes a legacy `crs` member on transformed
# output. clip/overlay/dissolve previously hardcoded EPSG:4326, so a declared
# projected input (EPSG:3857 metres) was misread as WGS84 and silently dropped
# (0 features). These tests assert the result matches explicitly reprojecting
# the input to WGS84 first.


def _beijing_3857_fc(radius: float = 30000.0) -> dict:
    """A FeatureCollection declaring EPSG:3857 around Beijing (116.4, 39.9)."""
    x, y = 12958175.0, 4852050.0  # ~ Beijing (116.4, 39.9) in EPSG:3857
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:3857"}},
        "features": [{
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [x - radius, y - radius], [x + radius, y - radius],
                    [x + radius, y + radius], [x - radius, y + radius],
                    [x - radius, y - radius],
                ]],
            },
            "properties": {"zone": "A"},
        }],
    }


_BEIJING_MASK = {
    "type": "FeatureCollection",
    "features": [{
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [116.2, 39.7], [116.6, 39.7], [116.6, 40.1], [116.2, 40.1],
                [116.2, 39.7],
            ]],
        },
        "properties": {},
    }],
}


def test_clip_smart_honors_declared_crs_member():
    """#599: clip of a declared-EPSG:3857 layer against a WGS84 mask must keep
    features — pre-fix the projected coordinates were read as WGS84 and the
    clip silently returned 0 features."""
    from app.lib.geo_processor.geometry import clip_smart
    from app.utils.coord_transform import transform_geojson

    res = clip_smart(_beijing_3857_fc(), _BEIJING_MASK)
    assert res.success is True, res.summary
    assert len(res.data["features"]) >= 1

    # Equivalent to explicitly reprojecting the input to WGS84 first.
    ref = clip_smart(
        transform_geojson(_beijing_3857_fc(), "EPSG:3857", "EPSG:4326"),
        _BEIJING_MASK,
    )
    assert ref.success is True
    assert len(ref.data["features"]) == len(res.data["features"])


def test_overlay_smart_honors_declared_crs_member():
    """#599: overlay of a declared-EPSG:3857 layer and a WGS84 mask must not
    silently drop the intersection (pre-fix hardcoded EPSG:4326 → 0 rows)."""
    from app.lib.geo_processor.overlay import overlay_smart

    res = overlay_smart(_beijing_3857_fc(), _BEIJING_MASK, how="intersection")
    assert res.success is True, res.summary
    assert len(res.data["features"]) >= 1


def test_dissolve_smart_honors_declared_crs_member():
    """#599: dissolve of a declared-EPSG:3857 layer must reproject to the WGS84
    working frame instead of dissolving raw projected metres as degrees."""
    from app.lib.geo_processor.geometry import dissolve_smart

    res = dissolve_smart(_beijing_3857_fc())
    assert res.success is True, res.summary
    assert len(res.data["features"]) == 1
    # Output coordinates are WGS84 degrees, not raw projected metres.
    from shapely.geometry import shape
    bounds = shape(res.data["features"][0]["geometry"]).bounds
    assert -180.0 <= bounds[0] and bounds[2] <= 180.0
    assert -90.0 <= bounds[1] and bounds[3] <= 90.0

def test_validation_errors():
    from app.lib.geo_processor.geometry import dissolve_smart
    from app.lib.geo_processor.overlay import overlay_smart

    geojson = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {"a": 1}}]
    }

    # dissolve_smart with missing field
    res_dis = dissolve_smart(geojson, field="non_existent_field")
    assert res_dis.success is False
    assert res_dis.error_type == "KeyError"

    # overlay_smart with invalid how parameter
    res_over = overlay_smart(geojson, geojson, how="invalid_how")
    assert res_over.success is False
    assert res_over.error_type == "ValueError"

