"""Regression tests for audit-ff9a392 findings #813/#814/#815.

#813: declared gcj02/bd09 `crs` members must not crash the to_utm_gdf family
     (raw CRSError) nor silently keep the offset — they are offset WGS84
     frames and are normalized to true WGS84.
#814: network_accessibility must parse FeatureCollection demand/facility
     layers instead of fabricating 0% coverage, and must refuse empty parses.
#815: attribute_filter must carry the declared `crs` member onto the rebuilt
     output FeatureCollection.
"""

import asyncio
import math

import pytest


def _wgs84_fc() -> dict:
    return {
        "type": "FeatureCollection",
        "crs": "EPSG:4326",
        "features": [
            {
                "type": "Feature",
                "properties": {"w": 100},
                "geometry": {"type": "Point", "coordinates": [104.06, 30.66]},
            },
            {
                "type": "Feature",
                "properties": {"w": 200},
                "geometry": {"type": "Point", "coordinates": [104.07, 30.67]},
            },
        ],
    }


class TestAudit813ChineseCrsNormalization:
    @pytest.mark.parametrize("chinese", ["gcj02", "bd09"])
    def test_to_utm_gdf_normalizes_declared_chinese_crs(self, chinese):
        from app.lib.geo_processor.core import to_utm_gdf
        from app.utils.coord_transform import transform_geojson

        fc_wgs = _wgs84_fc()
        fc_offset = transform_geojson(fc_wgs, "EPSG:4326", chinese)
        assert fc_offset["crs"]["properties"]["name"] == chinese

        gdf, utm = to_utm_gdf(fc_offset)  # previously: pyproj CRSError
        assert gdf is not None and utm

        ref_gdf, _ = to_utm_gdf(fc_wgs)
        # Offsets removed: normalized positions match the WGS84 reference
        # within the documented inverse-approximation error (<10 m in China).
        assert abs(gdf.geometry.x.iloc[0] - ref_gdf.geometry.x.iloc[0]) < 10.0
        assert abs(gdf.geometry.y.iloc[0] - ref_gdf.geometry.y.iloc[0]) < 10.0

    @pytest.mark.parametrize("chinese", ["gcj02", "bd09"])
    def test_gdf_from_features_normalizes_declared_chinese_crs(self, chinese):
        from app.lib.geo_processor.core import gdf_from_features
        from app.utils.coord_transform import transform_geojson

        fc_offset = transform_geojson(_wgs84_fc(), "EPSG:4326", chinese)
        gdf = gdf_from_features(fc_offset)
        # Previously the except-fallback kept ~100-600 m offsets uncorrected.
        assert abs(gdf.geometry.x.iloc[0] - 104.06) < 1e-4
        assert abs(gdf.geometry.y.iloc[0] - 30.66) < 1e-4

    def test_buffer_smart_survives_gcj02_declared_input(self):
        from app.lib.geo_processor.geometry import buffer_smart
        from app.utils.coord_transform import transform_geojson

        fc_offset = transform_geojson(_wgs84_fc(), "EPSG:4326", "gcj02")
        res = buffer_smart(fc_offset, 100)
        assert res.success, f"buffer_smart failed on declared-gcj02 input: {res.summary}"
        assert res.data and res.data.get("features")


def _grid_network() -> dict:
    nodes = [(104.0 + dx * 0.002, 30.0 + dy * 0.002) for dy in range(3) for dx in range(3)]
    feats = []
    for y in range(3):
        for x in range(3):
            i = y * 3 + x
            if x < 2:
                a, b = nodes[i], nodes[i + 1]
                m = math.dist(a, b) * 96_000
                feats.append({"type": "Feature", "properties": {"length_m": m},
                              "geometry": {"type": "LineString", "coordinates": [a, b]}})
            if y < 2:
                a, b = nodes[i], nodes[i + 3]
                m = math.dist(a, b) * 110_540
                feats.append({"type": "Feature", "properties": {"length_m": m},
                              "geometry": {"type": "LineString", "coordinates": [a, b]}})
    return {"type": "FeatureCollection", "features": feats}, nodes


class TestAudit814AccessibilityLayerShapes:
    def test_featurecollection_demand_matches_list_demand(self):
        from app.services.network.engine import NetworkGraphEngine

        network, nodes = _grid_network()
        engine = NetworkGraphEngine()
        demand_fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"population": 100},
             "geometry": {"type": "Point", "coordinates": nodes[4]}},
            {"type": "Feature", "properties": {"population": 200},
             "geometry": {"type": "Point", "coordinates": nodes[8]}},
        ]}
        facilities_fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"name": "F1"},
             "geometry": {"type": "Point", "coordinates": nodes[0]}},
        ]}
        res_fc = asyncio.run(engine.solve_accessibility(
            network=network, demand_layer=demand_fc, facilities=facilities_fc,
            cutoff_minutes=30))
        res_list = asyncio.run(engine.solve_accessibility(
            network=network,
            demand_layer=[{"coordinates": nodes[4], "weight": 100},
                          {"coordinates": nodes[8], "weight": 200}],
            facilities=[nodes[0]], cutoff_minutes=30))
        # Previously the FC form fabricated total_demand=0 / coverage=0%.
        assert res_fc.accessibility.total_demand == pytest.approx(300.0)
        assert res_fc.accessibility.total_demand == res_list.accessibility.total_demand
        assert res_fc.accessibility.coverage_percentage == pytest.approx(
            res_list.accessibility.coverage_percentage)

    def test_unrecognized_demand_shape_raises_instead_of_zero_coverage(self):
        from app.services.network.engine import NetworkGraphEngine

        network, nodes = _grid_network()
        engine = NetworkGraphEngine()
        with pytest.raises(ValueError):
            asyncio.run(engine.solve_accessibility(
                network=network, demand_layer={"unrecognized": True},
                facilities=[nodes[0]], cutoff_minutes=30))

    def test_empty_featurecollection_demand_raises(self):
        from app.services.network.engine import NetworkGraphEngine

        network, nodes = _grid_network()
        engine = NetworkGraphEngine()
        with pytest.raises(ValueError):
            asyncio.run(engine.solve_accessibility(
                network=network,
                demand_layer={"type": "FeatureCollection", "features": []},
                facilities=[nodes[0]], cutoff_minutes=30))


class TestAudit815AttributeFilterCrsMember:
    def test_projected_crs_member_survives_filter(self):
        from app.services.spatial_analyzer import SpatialAnalyzer

        fc = {"type": "FeatureCollection", "crs": "EPSG:3857", "features": [
            {"type": "Feature", "properties": {"pop": 100},
             "geometry": {"type": "Point", "coordinates": [12945670.0, 4848000.0]}},
            {"type": "Feature", "properties": {"pop": 50},
             "geometry": {"type": "Point", "coordinates": [12945770.0, 4848100.0]}},
        ]}
        res = SpatialAnalyzer.attribute_filter(fc, "pop > 60")
        assert res.success
        assert res.data["crs"]["properties"]["name"] == "EPSG:3857"
        # coordinates unchanged (no silent reprojection); __geo_interface__ emits tuples
        assert list(res.data["features"][0]["geometry"]["coordinates"]) == [12945670.0, 4848000.0]

    def test_wgs84_output_gains_no_crs_member(self):
        from app.services.spatial_analyzer import SpatialAnalyzer

        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"pop": 100},
             "geometry": {"type": "Point", "coordinates": [104.0, 30.0]}},
        ]}
        res = SpatialAnalyzer.attribute_filter(fc, "pop > 60")
        assert res.success
        assert "crs" not in res.data
