"""ffb283c6（#1387 数据面）回归：shapefile .cpg / 编码回退契约。

修复前无 .cpg 的 shapefile 一律按 GDAL 默认解码，中文 DBF 静默 mojibake。
修复后：有 .cpg → 交给 GDAL（不额外披露）；无 .cpg → 按有界链
gb18030 → utf-8 重试，成功时在 metadata 披露 encoding / encoding_fallback，
全部失败抛 ParseError（绝不静默交付乱码）。本文件用 gpd.read_file stub
锁定该决策序列（无需真实 shapefile 资产）。
"""
from __future__ import annotations

import zipfile
from unittest import mock

import pytest

from app.services import data_parser
from app.services.data_parser import (
    ParseError,
    _read_shapefile,
    _shapefile_has_cpg,
)


def _touch_shp(tmp_path, name: str = "layer.shp"):
    path = tmp_path / name
    path.write_bytes(b"")
    return path


def test_cpg_sidecar_present_uses_gdal_default_no_fallback_meta(tmp_path):
    shp = _touch_shp(tmp_path)
    (tmp_path / "layer.cpg").write_text("UTF-8", encoding="utf-8")
    calls: list[dict] = []

    def fake_read_file(path, **kwargs):
        calls.append(kwargs)
        return object()

    with mock.patch.object(data_parser.gpd, "read_file", side_effect=fake_read_file):
        _, meta = _read_shapefile(shp)

    assert _shapefile_has_cpg(shp) is True
    assert calls == [{"engine": "pyogrio"}], "有 .cpg 时不得再传 encoding"
    assert meta == {}, "有 .cpg 时不需要额外披露"


def test_no_cpg_uses_first_working_encoding_and_discloses(tmp_path):
    shp = _touch_shp(tmp_path)
    calls: list[dict] = []

    def fake_read_file(path, **kwargs):
        calls.append(kwargs)
        return object()

    with mock.patch.object(data_parser.gpd, "read_file", side_effect=fake_read_file):
        _, meta = _read_shapefile(shp)

    assert calls == [{"engine": "pyogrio", "encoding": "gb18030"}]
    assert meta == {"encoding": "gb18030", "encoding_fallback": True}


def test_no_cpg_falls_through_to_next_encoding(tmp_path):
    shp = _touch_shp(tmp_path)
    seen: list[str | None] = []

    def fake_read_file(path, **kwargs):
        seen.append(kwargs.get("encoding"))
        if kwargs.get("encoding") == "gb18030":
            raise UnicodeDecodeError("gb18030", b"\xff", 0, 1, "bad dbf")
        return object()

    with mock.patch.object(data_parser.gpd, "read_file", side_effect=fake_read_file):
        _, meta = _read_shapefile(shp)

    assert seen == ["gb18030", "utf-8"]
    assert meta == {"encoding": "utf-8", "encoding_fallback": True}


def test_no_cpg_all_encodings_fail_raises_parse_error(tmp_path):
    shp = _touch_shp(tmp_path)

    def fake_read_file(path, **kwargs):
        raise UnicodeDecodeError("gb18030", b"\xff", 0, 1, "bad dbf")

    with mock.patch.object(data_parser.gpd, "read_file", side_effect=fake_read_file):
        with pytest.raises(ParseError, match="无 .cpg"):
            _read_shapefile(shp)


def test_zip_archive_cpg_detection(tmp_path):
    with_cpg = tmp_path / "with_cpg.zip"
    with zipfile.ZipFile(with_cpg, "w") as zf:
        zf.writestr("layer.shp", b"")
        zf.writestr("layer.dbf", b"")
        zf.writestr("layer.cpg", b"UTF-8")

    without_cpg = tmp_path / "without_cpg.zip"
    with zipfile.ZipFile(without_cpg, "w") as zf:
        zf.writestr("layer.shp", b"")
        zf.writestr("layer.dbf", b"")

    assert _shapefile_has_cpg(with_cpg) is True
    assert _shapefile_has_cpg(without_cpg) is False


def test_bad_zip_is_not_reported_as_having_cpg(tmp_path):
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"not a zip")
    assert _shapefile_has_cpg(broken) is False
