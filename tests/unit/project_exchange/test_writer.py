"""物化 writer 测试（T5）——多图层 gpkg / 空图层 / 栅格拷贝 / 表格 / 台账 / 保真。"""
import json

import pytest

from app.services.project_exchange import contracts as pc
from app.services.project_exchange.writer import (
    PackageMaterializer, fc_features_from_payload,
)


def _point_fc(n=5):
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": i,
         "geometry": {"type": "Point",
                      "coordinates": [104.0 + i * 0.01, 30.5 + i * 0.01]},
         "properties": {"name": f"p{i}", "value": i * 10}}
        for i in range(n)]}


class TestVectorMaterialization:
    def test_multi_layer_gpkg_roundtrip(self, tmp_path):
        mat = PackageMaterializer(tmp_path)
        total1, fields1 = mat.write_vector_dataset(
            "ds-a", fc_features_from_payload(_point_fc(5)),
            crs="EPSG:4326", geometry_type="Point")
        total2, fields2 = mat.write_vector_dataset(
            "ds-b", fc_features_from_payload(_point_fc(3)),
            crs="EPSG:4326", geometry_type="Point")
        assert (total1, total2) == (5, 3)
        assert fields1 == fields2 == ["name", "value"]
        entries, versions = mat.verify_materialized()
        by_id = {e.logical_id: e for e in entries}
        assert by_id["ds-a"].verdict == pc.FIDELITY_EXACT
        assert by_id["ds-a"].feature_count_match is True
        assert by_id["ds-a"].crs_match is True
        assert by_id["ds-b"].feature_count_match is True
        assert versions.get("pyogrio")

    def test_empty_layer_preserved(self, tmp_path):
        import pyogrio

        mat = PackageMaterializer(tmp_path)
        mat.write_empty_vector_layer("ds-empty", crs="EPSG:4326")
        frame = pyogrio.read_dataframe(str(tmp_path / "standard/layers.gpkg"),
                                       layer="ds-empty")
        assert len(frame) == 0

    def test_mixed_layers_are_independent(self, tmp_path):
        mat = PackageMaterializer(tmp_path)
        mat.write_vector_dataset(
            "ds-pt", fc_features_from_payload(_point_fc(2)),
            crs="EPSG:4326", geometry_type="Point")
        poly_fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "id": 0,
             "geometry": {"type": "Polygon", "coordinates": [[
                 [104.0, 30.0], [104.1, 30.0], [104.1, 30.1],
                 [104.0, 30.1], [104.0, 30.0]]]},
             "properties": {"zone": "z1"}}]}
        mat.write_vector_dataset(
            "ds-poly", fc_features_from_payload(poly_fc),
            crs="EPSG:4326", geometry_type="Polygon")
        entries, _ = mat.verify_materialized()
        by_id = {e.logical_id: e for e in entries}
        assert by_id["ds-pt"].verdict == pc.FIDELITY_EXACT
        assert by_id["ds-poly"].verdict == pc.FIDELITY_EXACT


class TestRasterAndTable:
    def test_raster_copy_byte_identical(self, tmp_path):
        import numpy as np
        import rasterio
        from rasterio.transform import from_bounds

        src = tmp_path / "src.tif"
        profile = {
            "driver": "GTiff", "height": 8, "width": 8, "count": 1,
            "dtype": "uint8", "crs": "EPSG:4326",
            "transform": from_bounds(104.0, 30.0, 105.0, 31.0, 8, 8),
            "nodata": 255,
        }
        with rasterio.open(str(src), "w", **profile) as dst:
            dst.write(np.arange(64, dtype=np.uint8).reshape(1, 8, 8))
        mat = PackageMaterializer(tmp_path / "pkg")
        mat.write_raster_copy("ds-raster", src, ".tif", crs="EPSG:4326")
        entries, _ = mat.verify_materialized()
        entry = entries[0]
        assert entry.verdict == pc.FIDELITY_EXACT
        assert entry.details["byte_identical"] is True
        assert entry.details.get("nodata") == 255

    def test_table_csv_roundtrip(self, tmp_path):
        rows = [{"city": "成都", "pop": 2100}, {"city": "北京", "pop": 2180}]
        mat = PackageMaterializer(tmp_path / "pkg2")
        mat.write_table("ds-table", rows)
        entries, _ = mat.verify_materialized()
        entry = entries[0]
        assert entry.verdict == pc.FIDELITY_EXACT
        assert entry.feature_count_match is True
        assert entry.fields_verified is True

    def test_zero_row_table_is_not_writable(self, tmp_path):
        # 零行表格无 schema 可写：writer 层拒绝，导出编排层负责诚实降级
        # （unsupported + 披露，见 exporter._execute_export_sync）。
        import pandas as pd

        mat = PackageMaterializer(tmp_path / "pkg3")
        mat.write_table("ds-empty-table", [])
        with pytest.raises(pd.errors.EmptyDataError):
            mat.verify_materialized()


class TestInventoryAndZip:
    def test_inventory_hashes_every_file(self, tmp_path):
        mat = PackageMaterializer(tmp_path)
        mat.write_vector_dataset(
            "ds-a", fc_features_from_payload(_point_fc(2)),
            crs="EPSG:4326", geometry_type="Point")
        mat.write_map("map-x", {"version": 1, "sources": {}})
        mat.write_disclosure_md(["-披露-"])
        entries = mat.inventory()
        rels = {e.path for e in entries}
        assert pc.LAYERS_GPKG_PATH in rels
        assert "maps/map-x.mapspec.json" in rels
        assert pc.DISCLOSURE_PATH in rels
        for e in entries:
            assert len(e.sha256) == 64
            assert e.bytes > 0

    def test_artifact_geojson_written(self, tmp_path):
        mat = PackageMaterializer(tmp_path)
        rel = mat.write_artifact_payload("art-x", ".geojson",
                                         data=_point_fc(2))
        path = tmp_path / rel
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["type"] == "FeatureCollection"
        assert len(data["features"]) == 2
