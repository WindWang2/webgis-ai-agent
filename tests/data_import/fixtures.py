"""G17 导入工作台测试共享 fixture 构造器（真实文件，非 mock）。"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString, Point, Polygon


def make_geojson_bytes(features: list) -> bytes:
    fc = {"type": "FeatureCollection", "features": features}
    return json.dumps(fc, ensure_ascii=False).encode("utf-8")


def point_feature(props: dict, x: float, y: float) -> dict:
    return {
        "type": "Feature",
        "properties": props,
        "geometry": {"type": "Point", "coordinates": [x, y]},
    }


def make_three_layer_gpkg(path: Path) -> None:
    """点/线/面三层，不同 schema 与 CRS（面层故意无 CRS → unknown）。"""
    pts = gpd.GeoDataFrame(
        {"station": ["s1", "s2"], "code": ["00123", "00456"]},
        geometry=[Point(116.39, 39.90), Point(116.41, 39.92)],
        crs="EPSG:4326",
    )
    lines = gpd.GeoDataFrame(
        {"road_id": [10, 20], "length_km": [1.5, 2.5]},
        geometry=[
            LineString([(500000, 4400000), (500500, 4400500)]),
            LineString([(501000, 4401000), (501500, 4401500)]),
        ],
        crs="EPSG:32650",
    )
    polys = gpd.GeoDataFrame(
        {"zone": ["A"]},
        geometry=[Polygon([(0, 0), (1, 0), (1, 1), (0, 0)])],
        crs=None,
    )
    pts.to_file(path, layer="pt_layer", driver="GPKG", engine="pyogrio")
    lines.to_file(path, layer="line_layer", driver="GPKG", engine="pyogrio")
    polys.to_file(path, layer="poly_layer", driver="GPKG", engine="pyogrio")


def make_zip_two_sets(path: Path, tmp: Path) -> None:
    """同目录两套 shapefile（不同字段名防串用）+ 一套缺 dbf 的组件。"""
    a = gpd.GeoDataFrame(
        {"a_name": ["a1", "a2"]},
        geometry=[Point(116.3, 39.8), Point(116.4, 39.9)],
        crs="EPSG:4326",
    )
    b = gpd.GeoDataFrame(
        {"b_value": [1.25, 2.5, 3.75]},
        geometry=[Point(121.4, 31.2), Point(121.5, 31.3), Point(121.6, 31.4)],
        crs="EPSG:4326",
    )
    work = tmp / "zipsrc"
    work.mkdir(parents=True, exist_ok=True)
    a.to_file(work / "setA.shp", engine="pyogrio")
    b.to_file(work / "setB.shp", engine="pyogrio")
    with zipfile.ZipFile(path, "w") as zf:
        for comp in ("setA.shp", "setA.dbf", "setA.shx", "setA.prj", "setA.cpg",
                     "setB.shp", "setB.dbf", "setB.shx", "setB.prj", "setB.cpg"):
            zf.write(work / comp, f"data/{comp}")
        # 孤儿 .shp（无 .dbf）→ 发现期披露不可导入
        zf.writestr("broken/orphan.shp", b"\x00\x00\x27\x0a" + b"\x00" * 100)


def make_zip_no_prj(path: Path, tmp: Path) -> None:
    """投影坐标 shapefile 故意不含 .prj（场景 4）。"""
    g = gpd.GeoDataFrame(
        {"pid": [1]},
        geometry=[Point(500000, 4500000)],
        crs=None,
    )
    work = tmp / "zipsrc2"
    work.mkdir(parents=True, exist_ok=True)
    g.to_file(work / "proj.shp", engine="pyogrio")
    (work / "proj.prj").unlink(missing_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for comp in ("proj.shp", "proj.dbf", "proj.shx", "proj.cpg"):
            zf.write(work / comp, comp)


MINIMAL_KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark><name>p1</name><Point><coordinates>116.4,39.9</coordinates></Point></Placemark>
    <Placemark><name>p2</name><Point><coordinates>116.5,39.95</coordinates></Point></Placemark>
  </Document>
</kml>
""".encode("utf-8")


def make_csv_bytes(rows: list, header: list, *, encoding: str = "utf-8", delimiter: str = ",") -> bytes:
    lines = [delimiter.join(header)]
    for r in rows:
        lines.append(delimiter.join(str(v) for v in r))
    return "\n".join(lines).encode(encoding)
