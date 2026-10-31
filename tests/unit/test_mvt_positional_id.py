"""M3：MVT 位置要素 id（field 1）—— 仅无链内稳定 id 的要素编码。

护栏：
- 有链内 id 的数据集瓦片**不含 field 1**（字节与 master 语义一致，ETag/缓存
  奇偶性不动）；
- 无 id 数据集 field 1 = FC 自然索引+1，且 index 路径与 coordinate 路径
  字节一致（含 no-shapely 回退）；
- 同一 AM 分裂要素多记录同 id；
- feature_id_index 链内 id→自然索引 O(1)（first-wins 重复语义）。
"""
from __future__ import annotations


import pytest

import app.services.mvt as mvt_mod
from app.services.mvt import (
    SpatialIndexEntry,
    build_spatial_index_entry,
    encode_tile,
    encode_tile_from_index,
)


# ─── minimal protobuf decoder（field-1 aware） ─────────────────────────────


def _varint(buf, pos):
    result = 0
    shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def _iter_fields(buf):
    pos = 0
    while pos < len(buf):
        key, pos = _varint(buf, pos)
        num, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = _varint(buf, pos)
        elif wire == 2:
            length, pos = _varint(buf, pos)
            value = buf[pos:pos + length]
            pos += length
        else:
            raise AssertionError(f"unexpected wire type {wire}")
        yield num, wire, value


def _decode_tile_feature_ids(data: bytes):
    """[(feature_id|None, tag_pairs)] for layer 'data'（保持出现序）。"""
    out = []
    for num, _wire, layer_buf in _iter_fields(data):
        if num != 3:
            continue
        for fnum, fwire, value in _iter_fields(layer_buf):
            if fnum == 2 and fwire == 2:
                fid = None
                for fn, fw, v in _iter_fields(value):
                    if fn == 1 and fw == 0:
                        fid = v
                out.append(fid)
    return out


def _pt(lon, lat, props, fid=None):
    f = {"type": "Feature", "properties": props,
         "geometry": {"type": "Point", "coordinates": [lon, lat]}}
    if fid is not None:
        f["id"] = fid
    return f


_ZXY = (10, 807, 420)  # 成都附近（104.0, 30.6）


def _fc(*features):
    return {"type": "FeatureCollection", "features": list(features)}


class TestPositionalFeatureId:
    def test_idless_features_get_positional_ids(self):
        fc = _fc(_pt(104.0, 30.6, {"n": "a"}), _pt(104.01, 30.61, {"n": "b"}))
        ids = _decode_tile_feature_ids(encode_tile(fc, *_ZXY))
        assert ids == [1, 2]

    def test_chain_id_features_have_no_field1(self):
        fc = _fc(
            _pt(104.0, 30.6, {"OBJECTID": 7}),
            _pt(104.01, 30.61, {}, fid="top-id"),
            _pt(104.02, 30.62, {"osm_id": 12345}),
        )
        ids = _decode_tile_feature_ids(encode_tile(fc, *_ZXY))
        assert ids == [None, None, None]

    def test_mixed_dataset_only_idless_get_ids(self):
        fc = _fc(
            _pt(104.0, 30.6, {"id": "x"}),     # 自然索引 0 → 链内 id，不编码
            _pt(104.01, 30.61, {"n": "b"}),    # 自然索引 1 → 位置 id 2
            _pt(104.02, 30.62, {"fid": 9}),    # 自然索引 2 → 链内 id
            _pt(104.03, 30.63, {"n": "d"}),    # 自然索引 3 → 位置 id 4
        )
        ids = _decode_tile_feature_ids(encode_tile(fc, *_ZXY))
        assert ids == [None, 2, None, 4]

    def test_index_path_byte_parity_with_coordinate_path(self):
        fc = _fc(*[_pt(104.0 + i * 0.001, 30.6, {"i": i}) for i in range(40)])
        entry = build_spatial_index_entry(("s1", "ref:a"), fc)
        tile_index = encode_tile_from_index(entry, *_ZXY)
        tile_coord = encode_tile(fc, *_ZXY)
        assert tile_index == tile_coord
        assert _decode_tile_feature_ids(tile_index) == [i + 1 for i in range(40)]

    def test_fallback_path_parity_without_shapely(self, monkeypatch):
        fc = _fc(*[_pt(104.0 + i * 0.001, 30.6, {"i": i}) for i in range(20)])
        entry = build_spatial_index_entry(("s1", "ref:b"), fc)
        monkeypatch.setattr(mvt_mod, "_SHAPELY", None)
        tile_fallback = encode_tile_from_index(entry, *_ZXY)
        tile_coord = encode_tile(fc, *_ZXY)
        assert tile_fallback == tile_coord
        assert _decode_tile_feature_ids(tile_fallback) == [i + 1 for i in range(20)]

    def test_am_split_feature_shares_id(self):
        # 跨反经线线要素：分裂成多记录，全部携带同一位置 id
        f = {
            "type": "Feature",
            "properties": {"n": "am"},
            "geometry": {"type": "LineString", "coordinates": [
                [179.95, 10.0], [180.05, 10.0], [180.1, 10.0],
            ]},
        }
        fc = _fc(_pt(104.0, 30.6, {"first": 1}), f)
        z, x, y = 3, 7, 3
        ids = _decode_tile_feature_ids(encode_tile(fc, z, x, y))
        # 线要素（自然索引 1 → 位置 id 2）在瓦片 x7 内；AM 分裂可能产生
        # 1..N 条记录（z3 下简化可能并段），全部必须同 id。
        line_ids = [i for i in ids if i == 2]
        assert line_ids, f"线要素未入瓦片: {ids}"
        assert len(set(line_ids)) == 1  # 同一要素（分裂件）同 id


class TestFeatureIdIndex:
    def test_chain_ids_map_to_natural_indices(self):
        fc = _fc(
            _pt(104.0, 30.6, {"id": "a"}),
            _pt(104.01, 30.61, {"n": "x"}),  # 无 id，不进索引
            _pt(104.02, 30.62, {"OBJECTID": 42}),
        )
        entry = build_spatial_index_entry(("s2", "ref:c"), fc)
        index = entry.feature_id_index()
        assert index == {"a": 0, "42": 2}
        # 懒构建缓存
        assert entry.feature_id_index() is index

    def test_duplicate_ids_first_wins(self):
        fc = _fc(_pt(104.0, 30.6, {"id": "dup"}), _pt(104.01, 30.61, {"id": "dup"}))
        entry = build_spatial_index_entry(("s2", "ref:d"), fc)
        assert entry.feature_id_index() == {"dup": 0}

    def test_entry_direct_construction_has_id_index_none(self):
        entry = SpatialIndexEntry(("k",), [], None, None, None, None, [])
        assert entry.id_index is None
        assert entry.feature_id_index() == {}


class TestPositionalIdHelper:
    @pytest.mark.parametrize("feature,expected", [
        ({"id": 5}, None),
        ({"id": "x"}, None),
        ({"properties": {"id": 1}}, None),
        ({"properties": {"OBJECTID": 2}}, None),
        ({"properties": {"@id": "osm/1"}}, None),
        ({"properties": {"name": "无 id"}}, 7),
        ({}, 7),
        ("not-a-dict", None),
    ])
    def test_positional_feature_id(self, feature, expected):
        assert mvt_mod._positional_feature_id(feature, 6) == expected

    def test_negative_index_rejected(self):
        assert mvt_mod._positional_feature_id({"properties": {}}, -1) is None


def test_module_import_surface_stable():
    """公开面不回退：encode_tile 增参 natural_indices 为可选（兼容旧调用）。"""
    import inspect

    sig = inspect.signature(encode_tile)
    assert "natural_indices" in sig.parameters
    assert sig.parameters["natural_indices"].default is None
