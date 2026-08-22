import pytest
from app.lib.geo_analysis.statistics import calculate_sde, moran_i_narrated, hotspot_narrated, h3_lisa
from app.lib.geo_analysis.aggregation import spatial_aggregate, generate_fishnet, h3_binning
from app.lib.geo_analysis.network import calculate_isochrones
from app.lib.geo_processor.core import GeoAnalysisResult

@pytest.fixture
def sample_points():
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.39, 39.9]}, "properties": {"val": 10}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.4, 39.91]}, "properties": {"val": 20}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.41, 39.92]}, "properties": {"val": 30}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.42, 39.93]}, "properties": {"val": 40}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.43, 39.94]}, "properties": {"val": 50}}
        ]
    }

@pytest.fixture
def sample_polygons():
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature", 
                "geometry": {
                    "type": "Polygon", 
                    "coordinates": [[[116.38, 39.89], [116.44, 39.89], [116.44, 39.95], [116.38, 39.95], [116.38, 39.89]]]
                },
                "properties": {"id": 1}
            }
        ]
    }

def test_calculate_sde(sample_points):
    result = calculate_sde(sample_points)
    assert isinstance(result, GeoAnalysisResult)
    assert result.success is True
    assert "geometry" in result.data
    assert "Directional Insight" in result.summary

def test_moran_i(sample_points):
    result = moran_i_narrated(sample_points, "val")
    assert isinstance(result, GeoAnalysisResult)
    assert result.success is True
    assert "moran_i" in result.data
    assert "pattern" in result.data

def test_hotspot(sample_points):
    result = hotspot_narrated(sample_points, "val")
    assert isinstance(result, GeoAnalysisResult)
    assert result.success is True
    assert "hot_spots_count" in result.data

def test_spatial_aggregate(sample_points, sample_polygons):
    result = spatial_aggregate(sample_points, sample_polygons)
    assert isinstance(result, GeoAnalysisResult)
    assert result.success is True
    assert "features" in result.data

def test_generate_fishnet():
    bounds = [116.38, 39.89, 116.44, 39.95]
    result = generate_fishnet(bounds, 0.01)
    assert isinstance(result, GeoAnalysisResult)
    assert result.success is True
    assert len(result.data["features"]) > 0

def test_h3_binning_auto_resolution(sample_points):
    # Test with resolution=None, should auto-select
    result = h3_binning(sample_points, resolution=None)
    assert result.success is True
    assert "resolution" in result.summary.lower()
    assert len(result.data["features"]) > 0

def test_calculate_isochrones(sample_points):
    # Mock network
    network = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[116.39, 39.9], [116.4, 39.91]]},
                "properties": {"length": 1000}
            }
        ]
    }
    result = calculate_isochrones(network, sample_points, 5)
    assert isinstance(result, GeoAnalysisResult)
    assert result.success is True

def test_h3_binning(sample_points):
    # Test count stat
    result = h3_binning(sample_points, resolution=8)
    assert isinstance(result, GeoAnalysisResult)
    assert result.success is True
    assert "features" in result.data
    assert len(result.data["features"]) > 0
    assert "count" in result.data["features"][0]["properties"]
    
    # Test sum stat with stat_field
    result_sum = h3_binning(sample_points, resolution=8, stat_field="val", stat_method="sum")
    assert result_sum.success is True
    assert "sum" in result_sum.data["features"][0]["properties"]


def test_h3_binning_honors_declared_crs_member(sample_points):
    """#599: a declared-EPSG:3857 FeatureCollection must bin to the SAME H3
    cells as its WGS84 original — pre-fix the projected metres were fed
    straight into latitude/longitude H3 lookups (silent misplacement)."""
    from app.utils.coord_transform import transform_geojson

    transformed = transform_geojson(sample_points, "EPSG:4326", "EPSG:3857")
    transformed["crs"] = {"type": "name", "properties": {"name": "EPSG:3857"}}

    ref = h3_binning(sample_points, resolution=8)
    res = h3_binning(transformed, resolution=8)

    assert ref.success is True
    assert res.success is True, res.summary
    assert len(res.data["features"]) > 0
    ref_bins = sorted(f["properties"]["h3_index"] for f in ref.data["features"])
    res_bins = sorted(f["properties"]["h3_index"] for f in res.data["features"])
    assert res_bins == ref_bins


def test_h3_binning_antimeridian_cell_true_span_763():
    """#763: an H3 cell straddling the antimeridian must emit a polygon of its
    true ~0.003° longitude span (negative lngs unwrapped past +180), not a
    ring whose edges run through lng 0 — that produced a ~360°-wide,
    ~160,000x area-bloated polygon. A control cell away from ±180 is unchanged
    (lngs stay within [-180, 180])."""
    import h3
    from shapely.geometry import shape

    am_fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"v": 1},
             "geometry": {"type": "Point", "coordinates": [179.9995, 1.0]}}
        ],
    }
    res = h3_binning(am_fc, resolution=9)
    assert res.success, res.summary
    geom = shape(res.data["features"][0]["geometry"])
    minx, _miny, maxx, _maxy = geom.bounds
    # true res-9 span is ~0.003°; the bug produced a ~359.999° bounds width
    assert maxx - minx < 0.01, (
        f"#763 regression: AM cell bounds width {maxx - minx:.5f} deg — ring joined through lng 0"
    )
    assert geom.area < 2.0e-5, (
        f"#763 regression: AM cell area {geom.area:.5e} deg^2 (true res-9 hex ~6.3e-6)"
    )

    # control cell one degree away: geometry unchanged, lngs inside [-180, 180]
    ctrl_fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"v": 1},
             "geometry": {"type": "Point", "coordinates": [178.9995, 1.0]}}
        ],
    }
    ctrl = h3_binning(ctrl_fc, resolution=9)
    assert ctrl.success
    cgeom = shape(ctrl.data["features"][0]["geometry"])
    cminx, _cminy, cmaxx, _cmaxy = cgeom.bounds
    assert -180.0 <= cminx and cmaxx <= 180.0, "control cell must not be unwrapped"
    assert cmaxx - cminx < 0.01
    # the AM cell keeps the control's true area (same resolution, same latitude)
    assert geom.area == pytest.approx(cgeom.area, rel=0.05)


def test_h3_lisa():
    pytest.importorskip("esda", exc_type=ImportError)
    pytest.importorskip("libpysal", exc_type=ImportError)
    # Create a grid of hexes via points
    import h3
    center = h3.latlng_to_cell(39.9, 116.39, 8)
    hexes = h3.grid_disk(center, 4)
    
    # Give the central hexes high values, outer hexes low values
    features = []
    for h in hexes:
        dist = h3.grid_distance(center, h)
        boundary = h3.cell_to_boundary(h)
        if dist <= 1:
            val = 100 + (int(h, 16) % 5)
        elif dist == 2:
            val = 50 + (int(h, 16) % 5)
        else:
            val = 10 + (int(h, 16) % 5)
            
        # Flip coordinates from lat/lng to lng/lat for GeoJSON
        coords = [[ [lng, lat] for lat, lng in boundary ]]
        # Close the polygon
        coords[0].append(coords[0][0])
        
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": coords
            },
            "properties": {
                "h3_index": h,
                "value": val
            }
        })
        
    fc = {
        "type": "FeatureCollection",
        "features": features
    }
    
    result = h3_lisa(fc, "value")
    
    assert isinstance(result, GeoAnalysisResult)
    assert result.success is True
    
    # We should have High-High and Low-Low clusters
    clusters = [f["properties"]["lisa_cluster"] for f in result.data["features"]]
    
    assert "HH" in clusters
    assert "LL" in clusters
    assert "summary" in result.__dict__ or "summary" in dir(result)
