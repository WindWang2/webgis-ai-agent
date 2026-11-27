"""P3 data-integrity: all-null-geometry vectors must not emit NaN bbox / JSON-null geometry_type.

``parse_vector`` 曾对全部 feature 几何为 null 的矢量文件把
``gdf.total_bounds`` 的 ``[nan, nan, nan, nan]`` 原样写进 meta.json（非严格
JSON，进 uploads.bbox 的 JSON 列即坏），且 ``geometry.type.unique()[0]`` 为
None 使 meta.geometry_type 成为 JSON null（进 uploads.geometry_type 的
String(50) NOT NULL 列会 IntegrityError）。
"""
import json

from app.services.data_parser import parse_vector, save_meta


def _write_all_null_geojson(path):
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"name": "a"}, "geometry": None},
            {"type": "Feature", "properties": {"name": "b"}, "geometry": None},
        ],
    }
    path.write_text(json.dumps(fc), encoding="utf-8")


def _reject_constant(value):
    raise AssertionError(f"meta.json 含非严格 JSON 常量 {value!r}")


def test_all_null_geometry_meta_has_no_nan_bbox(tmp_path):
    src = tmp_path / "null_geom.geojson"
    _write_all_null_geojson(src)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    meta = parse_vector(src, out_dir, "null-geom-upload")

    # NaN 不是合法 JSON：没有可用几何时诚实的 bbox 是 None，不是 [nan, nan, nan, nan]
    assert meta["bbox"] is None
    # JSON null 的 geometry_type 同样毒化 meta.json 与 uploads 表（String NOT NULL）
    assert meta["geometry_type"] == "None"
    # 非缺陷路径行为不变
    assert meta["file_type"] == "vector"
    assert meta["feature_count"] == 2


def test_all_null_geometry_meta_json_is_strict(tmp_path):
    src = tmp_path / "null_geom.geojson"
    _write_all_null_geojson(src)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    meta = parse_vector(src, out_dir, "null-geom-upload")
    save_meta(out_dir, meta)

    text = (out_dir / "meta.json").read_text(encoding="utf-8")
    # parse_constant 只在遇到 json.dump 为非有限 float 发出的 NaN/Infinity
    # 字面量时触发 —— 严格 JSON 里不允许出现这些常量。
    json.loads(text, parse_constant=_reject_constant)
    assert "NaN" not in text
