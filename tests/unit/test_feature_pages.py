"""feature_pages（W12）：会话 ref 窗口/分页读的域内契约。

关键不变量：稳定序 + 不漏不重 + 有界扫描 + 畸形输入 typed 拒绝。
"""
import pytest

from app.services.feature_pages import (
    FeaturePageError,
    decode_page_cursor,
    encode_page_cursor,
    page_features,
    parse_bbox_param,
    parse_fields_param,
    project_feature,
)


def _fc(n, lat=30.0):
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": i,
                "geometry": {"type": "Point", "coordinates": [104.0 + i * 0.001, lat + i * 0.001]},
                "properties": {"i": i, "name": f"f{i}"},
            }
            for i in range(n)
        ],
    }


def test_cursor_roundtrip_and_monotonic():
    assert decode_page_cursor(encode_page_cursor(0)) == 0
    assert decode_page_cursor(encode_page_cursor(12345)) == 12345
    assert encode_page_cursor(7) != encode_page_cursor(8)


def test_malformed_cursor_typed_reject():
    for bad in ("garbage", "e30=", "xxx..", ""):
        with pytest.raises(FeaturePageError):
            decode_page_cursor(bad)
    # 负数 index 在编码端就是非法语义：解码必须拒绝。
    with pytest.raises(FeaturePageError):
        decode_page_cursor(encode_page_cursor(-1))


def test_bbox_param_parsing():
    assert parse_bbox_param(None) is None
    assert parse_bbox_param("") is None
    assert parse_bbox_param("104,30,106,32") == (104.0, 30.0, 106.0, 32.0)
    for bad in ("104,30", "a,b,c,d", "106,30,104,32", "104,32,106,30", "nan,1,2,3"):
        with pytest.raises(FeaturePageError):
            parse_bbox_param(bad)


def test_fields_param_parsing_dedup_and_cap():
    assert parse_fields_param(None) is None
    assert parse_fields_param("a,b,a") == ["a", "b"]
    with pytest.raises(FeaturePageError):
        parse_fields_param(",".join(f"f{i}" for i in range(65)))


def test_paging_through_all_pages_no_miss_no_dup_stable_order():
    fc = _fc(250)
    ids = []
    cursor = None
    pages = 0
    while True:
        page = page_features(fc, limit=100, cursor=cursor)
        ids.extend(f["id"] for f in page["features"])
        pages += 1
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]
    assert ids == list(range(250)), "翻页拼接必须等于原序（稳定序、不漏不重）"
    assert pages == 3


def test_page_partial_tail():
    fc = _fc(7)
    p1 = page_features(fc, limit=5)
    assert p1["returned"] == 5 and p1["has_more"] is True
    p2 = page_features(fc, limit=5, cursor=p1["next_cursor"])
    assert [f["id"] for f in p2["features"]] == [5, 6]
    assert p2["has_more"] is False and p2["next_cursor"] is None


def test_cursor_beyond_end_is_empty_not_error():
    fc = _fc(3)
    page = page_features(fc, limit=5, cursor=encode_page_cursor(10))
    assert page["features"] == [] and page["has_more"] is False


def test_bbox_window_no_miss_and_bounded_scan():
    # 2500 要素，bbox 只命中 i>=2000 的尾部 —— 前向扫描跨页必须一个不漏。
    n = 2500
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": i,
                "geometry": {"type": "Point", "coordinates": [106.0 if i >= 2000 else 100.0, 30.0]},
                "properties": {"i": i},
            }
            for i in range(n)
        ],
    }
    found = []
    cursor = None
    while True:
        page = page_features(fc, limit=500, cursor=cursor, bbox=(105.5, 29.5, 106.5, 30.5))
        found.extend(f["id"] for f in page["features"])
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]
    assert found == list(range(2000, 2500)), "bbox 稀疏窗口跨页不得漏要素"
    assert page_features(fc, limit=10, bbox=(105.5, 29.5, 106.5, 30.5))["scanned"] <= 10_000


def test_fields_projection_keeps_identity():
    f = {"type": "Feature", "id": 1, "geometry": {"type": "Point", "coordinates": [1, 2]},
         "properties": {"a": 1, "b": 2, "c": 3}}
    p = project_feature(f, ["a", "c"])
    assert p["properties"] == {"a": 1, "c": 3}
    assert p["geometry"] == f["geometry"] and p["id"] == 1
    assert project_feature(f, None) is f


def test_invalid_geometry_tolerated_in_bbox_filter():
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "id": 0, "geometry": None, "properties": {}},
            {"type": "Feature", "id": 1, "geometry": {"type": "Point", "coordinates": [104, 30]}, "properties": {}},
        ],
    }
    page = page_features(fc, limit=10, bbox=(103, 29, 105, 31))
    assert [f["id"] for f in page["features"]] == [1], "无几何要素不进窗口（也不得抛异常）"


def test_non_fc_payload_typed_reject():
    with pytest.raises(FeaturePageError):
        page_features({"type": "Feature"}, limit=5)
