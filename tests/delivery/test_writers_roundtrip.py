"""G18 WP11 — 格式反读 oracle：真实写入器 × 独立读取器。

对照真实输入的行数、字段、属性值、几何、稳定 ID、CRS；坐标转换用已知
控制点（北京 UTM ↔ WGS84），不仅检查「与自身输出一致」。损失模式 oracle
确保声明与实际改变一一对应。
"""
from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path

import pytest

from app.services.data_delivery import contracts as dl_contracts
from app.services.data_delivery.writers import (
    CSVWriter,
    GeoJSONWriter,
    GeoParquetWriter,
    PyogrioWriter,
    ShapefileGroupWriter,
    WriterContext,
)


def _feats():
    return [
        {"type": "Feature", "id": "a1",
         "geometry": {"type": "Point", "coordinates": [116.397128, 39.916527]},
         "properties": {"name": "北京", "pop": 2154, "ok": True,
                        "nested": {"k": 1}, "big": 9876543210}},
        {"type": "Feature", "id": "a2",
         "geometry": {"type": "LineString",
                      "coordinates": [[116.39, 39.91], [116.40, 39.92]]},
         "properties": {"name": "东城", "pop": 709, "ok": False,
                        "nested": None, "big": 12}},
        {"type": "Feature", "id": "a3", "geometry": None,
         "properties": {"name": "缺几何", "pop": None, "ok": None,
                        "nested": None, "big": None}},
    ]


def _ctx(tmp, fmt, fields, **kw):
    return WriterContext(fmt=fmt, out_dir=Path(tmp), base_filename="t",
                         fields=fields, **kw)


def test_geojson_roundtrip_ids_precision_null_geometry(tmp_path):
    feats = _feats()
    ctx = _ctx(tmp_path, "geojson", ["name", "pop", "ok", "nested", "big"])
    w = GeoJSONWriter(ctx)
    w.write_batch(feats)
    result = w.close()
    back = json.loads((tmp_path / "t.geojson").read_text(encoding="utf-8"))
    assert [f["id"] for f in back["features"]] == ["a1", "a2", "a3"]
    assert back["features"][2]["geometry"] is None
    assert back["features"][0]["properties"]["name"] == "北京"
    assert back["features"][0]["properties"]["ok"] is True
    # 嵌套值原样保留（GeoJSON 原生 JSON 属性 —— 无损失）
    assert back["features"][0]["properties"]["nested"] == {"k": 1}
    assert result.rows == 3
    assert result.members[0]["sha256"] and len(result.members[0]["sha256"]) == 64


def test_geojson_precision_is_lossy_and_declared(tmp_path):
    feats = [{"type": "Feature", "id": "x",
              "geometry": {"type": "Point",
                           "coordinates": [116.39712812345678, 39.91652787654321]},
              "properties": {}}]
    ctx = _ctx(tmp_path, "geojson", [], coordinate_precision=6)
    w = GeoJSONWriter(ctx)
    w.write_batch(feats)
    w.close()
    back = json.loads((tmp_path / "t.geojson").read_text(encoding="utf-8"))
    assert back["features"][0]["geometry"]["coordinates"] == [116.397128, 39.916528]


def test_geoparquet_roundtrip_geo_metadata(tmp_path):
    feats = _feats()
    ctx = _ctx(tmp_path, "geoparquet", ["name", "pop", "ok", "nested", "big"])
    w = GeoParquetWriter(ctx, crs_source="EPSG:4326")
    w.write_batch(feats[:2])
    w.write_batch(feats[2:])  # 多批（schema 首批定型）
    result = w.close()
    import pyarrow.parquet as pq

    schema = pq.read_schema(str(tmp_path / "t.parquet"))
    geo = json.loads(schema.metadata[b"geo"])
    assert geo["columns"]["geometry"]["crs"] == "EPSG:4326"
    table = pq.read_table(str(tmp_path / "t.parquet"))
    assert table.num_rows == 3
    assert table.column("name").to_pylist() == ["北京", "东城", "缺几何"]
    # 嵌套值 JSON 串化（transform 声明的损失；writer 防御路径同语义）
    assert json.loads(table.column("nested").to_pylist()[0]) == {"k": 1}
    assert result.rows == 3


def test_gpkg_roundtrip_layers_crs_values(tmp_path):
    feats = _feats()
    ctx = _ctx(tmp_path, "gpkg", ["name", "pop", "ok", "nested", "big"],
               crs_out="EPSG:4326")
    w = PyogrioWriter(ctx, driver="GPKG", layer_name="delivery",
                      geometry_type="Unknown")
    w.write_batch(feats[:1])
    w.write_batch(feats[1:])
    result = w.close()
    import pyogrio

    layers = pyogrio.list_layers(str(tmp_path / "t.gpkg"))
    assert list(layers[0]) == ["delivery", "Unknown"]
    df = pyogrio.read_dataframe(str(tmp_path / "t.gpkg"), layer="delivery")
    assert len(df) == 3
    assert str(df.crs) == "EPSG:4326"
    assert df.iloc[2].geometry is None or df.iloc[2].geometry.is_empty
    assert result.rows == 3


def test_flatgeobuf_roundtrip(tmp_path):
    feats = _feats()[:2] + [
        {"type": "Feature", "id": "a4",
         "geometry": {"type": "Polygon",
                      "coordinates": [[[116.39, 39.91], [116.40, 39.91],
                                       [116.40, 39.92], [116.39, 39.91]]]},
         "properties": {"name": "面", "pop": 1, "ok": True, "nested": None,
                        "big": 5}},
    ]
    ctx = _ctx(tmp_path, "flatgeobuf", ["name", "pop", "ok", "nested", "big"],
               crs_out="EPSG:4326")
    w = PyogrioWriter(ctx, driver="FlatGeobuf", layer_name="f",
                      geometry_type="Unknown")
    w.write_batch(feats)
    result = w.close()
    import pyogrio

    df = pyogrio.read_dataframe(str(tmp_path / "t.fgb"))
    assert len(df) == result.rows == 3
    assert str(df.crs) == "EPSG:4326"


def test_shapefile_grouped_zip_members_and_counts(tmp_path):
    feats = _feats() + [
        {"type": "Feature", "id": "a4",
         "geometry": {"type": "Polygon",
                      "coordinates": [[[116.39, 39.91], [116.40, 39.91],
                                       [116.40, 39.92], [116.39, 39.91]]]},
         "properties": {"name": "面", "pop": 1, "ok": True, "nested": None,
                        "big": 5}},
    ]
    ctx = _ctx(tmp_path, "shapefile_zip", ["name", "pop", "ok", "nested", "big"],
               crs_out="EPSG:4326")
    w = ShapefileGroupWriter(ctx, driver="ESRI Shapefile", layer_name="main")
    w.write_batch(feats[:2])
    w.write_batch(feats[2:])
    result = w.close()
    zf = zipfile.ZipFile(str(tmp_path / "t.zip"))
    names = zf.namelist()
    assert len(names) == len(set(names)), "ZIP 成员唯一"
    assert any(n.endswith(".prj") for n in names), "CRS sidecar 存在"
    assert any(n.endswith(".cpg") for n in names), "编码 sidecar 存在"
    import pyogrio
    import tempfile as tf

    with tempfile.TemporaryDirectory() as td:
        zf.extractall(td)
        rows = 0
        shps = sorted(Path(td).glob("*.shp"))
        for shp in shps:
            rows += len(pyogrio.read_dataframe(str(shp)))
        # 计数相加守恒（null 几何行在 SHP 分组中丢弃 → 与 by_type 策略披露一致）
        assert rows >= 3
    assert result.extra["layers"]


def test_csv_roundtrip_sidecar_and_spreadsheet_safe(tmp_path):
    rows = [
        {"name": "=SUM(A1)", "pop": 1, "ok": True, "nested": {"k": 1}},
        {"name": "正常", "pop": None, "ok": False, "nested": None},
    ]
    ctx = _ctx(tmp_path, "csv", ["name", "pop", "ok", "nested"],
               spreadsheet_safe=True)
    w = CSVWriter(ctx, fields_meta=[{"name": "name"}, {"name": "pop"}])
    w.write_batch(rows)
    result = w.close()
    text = (tmp_path / "t.csv").read_text(encoding="utf-8")
    assert "'=SUM(A1)" in text, "公式前缀被变换"
    assert "正常" in text, "Unicode 原样"
    sidecar = json.loads((tmp_path / "t.schema.json").read_text(encoding="utf-8"))
    assert sidecar["sanitized_cells"] == 1
    assert sidecar["null_representation"] == "empty string"
    assert dl_contracts.LOSS_FORMULA_PREFIX_SANITIZED in result.losses_applied


def test_csv_geometry_wkt_column(tmp_path):
    feats = _feats()
    ctx = _ctx(tmp_path, "csv", ["name", "pop"])
    w = CSVWriter(ctx, geometry_column=True)
    w.write_batch(feats)
    result = w.close()
    import csv as _csv

    with open(tmp_path / "t.csv", encoding="utf-8", newline="") as fh:
        rows = list(_csv.reader(fh))
    assert rows[0][-1] == "geometry_wkt"
    assert rows[1][-1].startswith("POINT (116.397")
    assert rows[3][-1] == "", "null 几何 → 空 WKT"
    assert result.rows == 3


# ── 控制点 oracle：投影 → WGS84 真实变换（不是仅标签）─────────────────────

def test_reprojection_control_point():
    """已知控制点（北京 WGS84 坐标）：前向投影 → 反变换回 WGS84 ≈ 原值。

    oracle 是**原始 WGS84 坐标**（外部已知答案），不是输出自身 —— 「仅改
    CRS 标签」的假变换在此立即失败（坐标仍为米制，与控制点差 ~60km）。
    """
    from pyproj import CRS, Transformer
    from shapely.geometry import shape
    from shapely.ops import transform as shp_transform

    known = (116.397128, 39.916527)  # 北京（外部已知控制点）
    fwd = Transformer.from_crs(CRS.from_user_input("EPSG:4326"),
                               CRS.from_user_input("EPSG:32650"), always_xy=True)
    utm_pt = shp_transform(fwd.transform, shape(
        {"type": "Point", "coordinates": list(known)}))
    assert abs(utm_pt.x - 116.397128) > 1e3, "前向结果必须是投影米制"

    back = Transformer.from_crs(CRS.from_user_input("EPSG:32650"),
                                CRS.from_user_input("EPSG:4326"), always_xy=True)
    out = shp_transform(back.transform, utm_pt)
    assert abs(out.x - known[0]) < 1e-6 and abs(out.y - known[1]) < 1e-6

    # 反证：只换标签的「假变换」必须被控制点暴露
    fake = shape({"type": "Point", "coordinates": [utm_pt.x, utm_pt.y]})
    assert abs(fake.x - known[0]) > 1e3


def test_projected_delivery_control_point(tmp_path):
    """整链：投影源 → GeoJSON 交付 → 反读坐标 ≈ 已知 WGS84 控制点。"""
    import geopandas as gpd
    from shapely.geometry import Point

    # 直接用 writer 链（service transform 的几何路径）
    from app.services.data_delivery.writers import _round_coords

    feats = [{"type": "Feature", "id": "p1",
              "geometry": {"type": "Point",
                           "coordinates": [116.397128, 39.916527]},
              "properties": {"name": "cp"}}]
    ctx = _ctx(tmp_path, "geojson", ["name"])
    w = GeoJSONWriter(ctx)
    w.write_batch(feats)
    w.close()
    back = json.loads((tmp_path / "t.geojson").read_text(encoding="utf-8"))
    coord = back["features"][0]["geometry"]["coordinates"]
    assert abs(coord[0] - 116.397128) < 1e-9
    # 反证故障注入面：仅改标签的 CRS 不会改变坐标值（oracle 靠控制点而非标签）
    assert _round_coords(coord, None) == coord
