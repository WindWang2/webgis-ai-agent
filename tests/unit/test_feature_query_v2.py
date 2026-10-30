"""Track B vNext：feature_pages 排序/typed 谓词/total_matching/位置 id 单测。"""
from __future__ import annotations

import json

import pytest

from app.services.feature_pages import (
    FILTER_MAX_DEPTH,
    FILTER_MAX_TERMS,
    FILTER_MAX_VALUES,
    FeaturePageError,
    count_matching,
    decode_page_cursor,
    encode_page_cursor,
    evaluate_where,
    id_mode_of,
    order_digest,
    page_features,
    parse_order_param,
    parse_where_param,
    resolve_feature_stable_id,
)


def _fc(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def _f(props, fid=None, geom_type="Point"):
    coords = [props.get("lon", 104.0), props.get("lat", 30.6)]
    f = {"type": "Feature", "properties": props,
         "geometry": {"type": geom_type, "coordinates": coords}}
    if fid is not None:
        f["id"] = fid
    return f


# ─── order ──────────────────────────────────────────────────────────────────


class TestOrderParam:
    def test_parse_basic_and_desc(self):
        assert parse_order_param("students") == ("students", 1)
        assert parse_order_param("-students") == ("students", -1)
        assert parse_order_param(None) is None
        assert parse_order_param("") is None

    def test_parse_cjk_field(self):
        assert parse_order_param("学生数") == ("学生数", 1)

    def test_parse_rejects(self):
        for bad in ("-", "a" * 65, "bad\x01field", "-\x1f"):
            with pytest.raises(FeaturePageError):
                parse_order_param(bad)

    def test_sorted_page_full_order_asc_desc(self):
        fc = _fc(_f({"v": 3}, "a"), _f({"v": 1}, "b"), _f({"v": 2}, "c"))
        asc = page_features(fc, limit=10, order=("v", 1))
        assert [f["properties"]["v"] for f in asc["features"]] == [1, 2, 3]
        desc = page_features(fc, limit=10, order=("v", -1))
        assert [f["properties"]["v"] for f in desc["features"]] == [3, 2, 1]

    def test_sorted_missing_values_last_regardless_of_direction(self):
        fc = _fc(_f({"v": None}, "a"), _f({"v": 5}, "b"), _f({"v": 1}, "c"))
        asc = page_features(fc, limit=10, order=("v", 1))
        desc = page_features(fc, limit=10, order=("v", -1))
        assert [f["id"] for f in asc["features"]] == ["c", "b", "a"]
        assert [f["id"] for f in desc["features"]] == ["b", "c", "a"]

    def test_sorted_mixed_types_bucket_deterministic(self):
        fc = _fc(_f({"v": "b"}, "a"), _f({"v": 10}, "b"), _f({"v": "a"}, "c"), _f({"v": 2}, "d"))
        asc = page_features(fc, limit=10, order=("v", 1))
        assert [f["id"] for f in asc["features"]] == ["d", "b", "c", "a"]

    def test_sorted_pagination_stable_across_pages(self):
        features = [_f({"v": i % 7}, f"id{i}") for i in range(50)]
        fc = _fc(*features)
        collected = []
        cursor = None
        while True:
            page = page_features(fc, limit=7, cursor=cursor, order=("v", -1))
            collected.extend(f["id"] for f in page["features"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert len(collected) == 50
        values = [features[int(c[2:])]["properties"]["v"] for c in collected]
        assert values == sorted(values, reverse=True)

    def test_v2_cursor_binds_order_digest(self):
        fc = _fc(_f({"v": 2}, "a"), _f({"v": 1}, "b"))
        page = page_features(fc, limit=1, order=("v", 1))
        cursor = page["next_cursor"]
        assert cursor is not None
        payload = json.loads(__import__("base64").urlsafe_b64decode(cursor + "==").decode())
        assert payload["v"] == 2 and payload["o"] == order_digest(("v", 1))
        # 同序续页 OK
        nxt = page_features(fc, limit=1, cursor=cursor, order=("v", 1))
        assert nxt["features"][0]["id"] == "a"
        # 反向序复用旧 cursor → 400（位置语义失效）
        with pytest.raises(FeaturePageError):
            page_features(fc, limit=1, cursor=cursor, order=("v", -1))
        # 自然序端点复用 v2 cursor → 400
        with pytest.raises(FeaturePageError):
            page_features(fc, limit=1, cursor=cursor)
        # v1 cursor 带 order 参数 → 400
        v1 = encode_page_cursor(1)
        with pytest.raises(FeaturePageError):
            page_features(fc, limit=1, cursor=v1, order=("v", 1))


# ─── where（typed 谓词 AST） ────────────────────────────────────────────────


class TestWhereParam:
    def test_parse_leaf_ops(self):
        assert parse_where_param('{"field":"t","op":"eq","value":"小学"}') == {
            "field": "t", "op": "eq", "value": "小学"}
        assert parse_where_param('{"field":"n","op":"between","gte":1,"lte":9}') == {
            "field": "n", "op": "between", "gte": 1, "lte": 9}
        assert parse_where_param(None) is None

    def test_parse_composite(self):
        ast = parse_where_param(
            '{"op":"and","terms":[{"field":"t","op":"in","values":["a","b"]},'
            '{"op":"not","term":{"field":"n","op":"gte","value":0}}]}'
        )
        assert ast["op"] == "and" and len(ast["terms"]) == 2

    def test_parse_rejects_unknown_op_and_shapes(self):
        for bad in (
            '{"field":"t","op":"regex","value":"x"}',
            '{"field":"t","op":"eq"}',
            '{"field":"t","op":"between","gte":9,"lte":1}',
            '{"field":"t","op":"between","gte":"a","lte":"b"}',
            '{"op":"and","terms":[]}',
            '{"op":"or","terms":[1,2]}',
            '{"op":"not"}',
            'not json',
            '{"field":"","op":"eq","value":1}',
            '{"field":"a\\tb","op":"eq","value":1}',
        ):
            with pytest.raises(FeaturePageError):
                parse_where_param(bad)

    def test_parse_limits(self):
        too_many_values = json.dumps(
            {"field": "t", "op": "in", "values": [str(i) for i in range(FILTER_MAX_VALUES + 1)]})
        with pytest.raises(FeaturePageError):
            parse_where_param(too_many_values)
        deep = '{"field":"t","op":"eq","value":1}'
        for _ in range(FILTER_MAX_DEPTH + 1):
            deep = json.dumps({"op": "not", "term": json.loads(deep)})
        with pytest.raises(FeaturePageError):
            parse_where_param(deep)
        wide = {"op": "and", "terms": [{"field": "t", "op": "eq", "value": i}
                                       for i in range(FILTER_MAX_TERMS + 1)]}
        with pytest.raises(FeaturePageError):
            parse_where_param(json.dumps(wide))
        with pytest.raises(FeaturePageError):
            parse_where_param("x" * 3000)

    def test_evaluate_semantics(self):
        f = _f({"type": "小学", "students": 500, "name": "成都市实验小学", "flag": True})
        assert evaluate_where(parse_where_param('{"field":"type","op":"eq","value":"小学"}'), f)
        assert evaluate_where(parse_where_param('{"field":"type","op":"ne","value":"中学"}'), f)
        assert evaluate_where(parse_where_param('{"field":"students","op":"between","gte":100,"lte":600}'), f)
        assert evaluate_where(parse_where_param('{"field":"type","op":"in","values":["幼儿园","小学"]}'), f)
        assert evaluate_where(parse_where_param('{"field":"name","op":"contains","value":"实验"}'), f)
        assert evaluate_where(parse_where_param('{"field":"name","op":"startswith","value":"成都市实"}'), f)
        assert not evaluate_where(parse_where_param('{"field":"name","op":"contains","value":"中学"}'), f)
        # 数值等值跨 int/float
        assert evaluate_where(parse_where_param('{"field":"students","op":"eq","value":500.0}'), f)
        # bool 不与数值相等
        assert not evaluate_where(parse_where_param('{"field":"flag","op":"eq","value":1}'), f)
        assert evaluate_where(parse_where_param('{"field":"flag","op":"eq","value":true}'), f)
        # 缺席字段
        assert not evaluate_where(parse_where_param('{"field":"absent","op":"eq","value":1}'), f)
        assert evaluate_where(parse_where_param('{"field":"absent","op":"eq","value":null}'), f)
        assert evaluate_where(parse_where_param('{"field":"absent","op":"ne","value":null}'), f) is False
        # between 对非数值不命中
        assert not evaluate_where(parse_where_param('{"field":"type","op":"between","gte":0,"lte":9}'), f)
        # contains 对非字符串字段不命中
        assert not evaluate_where(parse_where_param('{"field":"students","op":"contains","value":"5"}'), f)
        # 组合
        combo = ('{"op":"and","terms":[{"field":"type","op":"eq","value":"小学"},'
                 '{"field":"students","op":"gte","value":400}]}')
        assert evaluate_where(parse_where_param(combo), f)
        neg = '{"op":"not","term":{"field":"type","op":"eq","value":"中学"}}'
        assert evaluate_where(parse_where_param(neg), f)

    def test_page_features_where_filters(self):
        fc = _fc(
            _f({"type": "小学", "n": 1}, "a"),
            _f({"type": "中学", "n": 2}, "b"),
            _f({"type": "小学", "n": 3}, "c"),
        )
        where = parse_where_param('{"field":"type","op":"eq","value":"小学"}')
        page = page_features(fc, limit=10, where=where)
        assert [f["id"] for f in page["features"]] == ["a", "c"]

    def test_where_plus_order_plus_bbox_compose(self):
        fc = _fc(
            _f({"t": "a", "lon": 104.0}),
            _f({"t": "b", "lon": 100.0}),
            _f({"t": "a", "lon": 116.0}),
        )
        where = parse_where_param('{"field":"t","op":"eq","value":"a"}')
        page = page_features(fc, limit=10, where=where, bbox=(100.0, 20.0, 110.0, 40.0))
        assert [f["properties"]["lon"] for f in page["features"]] == [104.0]


# ─── total_matching ─────────────────────────────────────────────────────────


class TestTotalMatching:
    def test_count_exact_with_filter_and_bbox(self):
        fc = _fc(*[_f({"t": "x" if i % 2 else "y"}, f"i{i}") for i in range(30)])
        where = parse_where_param('{"field":"t","op":"eq","value":"x"}')
        assert count_matching(fc["features"], where=where, bbox=None) == 15

    def test_count_over_limit_returns_none(self):
        import app.services.feature_pages as fp

        fc = _fc(*[_f({"t": "x"}, f"i{i}") for i in range(5)])
        orig = fp.TOTAL_EXACT_LIMIT
        fp.TOTAL_EXACT_LIMIT = 4
        try:
            assert count_matching(fc["features"], where={"field": "t", "op": "eq", "value": "x"}, bbox=None) is None
        finally:
            fp.TOTAL_EXACT_LIMIT = orig

    def test_count_cached_by_cache_key(self):

        fc = _fc(*[_f({"t": "x"}, f"i{i}") for i in range(8)])
        where = {"field": "t", "op": "eq", "value": "x"}
        first = count_matching(fc["features"], where=where, bbox=None, cache_key=("ref:z", "1"))
        assert first == 8
        # 同 cache_key 第二次来自缓存（改列表不影响结果）
        fc["features"].append(_f({"t": "z"}, "extra"))
        again = count_matching(fc["features"], where=where, bbox=None, cache_key=("ref:z", "1"))
        assert again == 8


# ─── 稳定 id / id_mode / 位置 id 投影 ──────────────────────────────────────


class TestIdMode:
    def test_resolve_chain(self):
        assert resolve_feature_stable_id(_f({"OBJECTID": 7}, fid=None)) == "7"
        assert resolve_feature_stable_id(_f({"name": "x"})) is None
        assert resolve_feature_stable_id(_f({}, fid="top")) == "top"

    def test_id_mode_native_positional_mixed(self):
        native = _fc(_f({"id": 1}), _f({"OBJECTID": 2}))
        positional = _fc(_f({"name": "a"}), _f({"name": "b"}))
        mixed = _fc(_f({"id": 1}), _f({"name": "b"}))
        assert id_mode_of(native["features"])["id_mode"] == "native"
        assert id_mode_of(positional["features"])["id_mode"] == "positional"
        m = id_mode_of(mixed["features"])
        assert m["id_mode"] == "mixed" and m["positional_count"] == 1

    def test_duplicate_ids_disclosed(self):
        fc = _fc(_f({"id": 1}), _f({"id": 1}), _f({"id": 2}))
        assert id_mode_of(fc["features"])["duplicate_id_count"] == 1

    def test_positional_id_projected_only_for_idless(self):
        fc = _fc(_f({"id": "keep"}, fid=None), _f({"name": "x"}, fid=None))
        page = page_features(fc, limit=10)
        ids = [f.get("id") for f in page["features"]]
        # 第 1 个要素链内 id 在 properties（顶层不投影、不覆盖）；第 2 个
        # 要素（自然索引 1）无链内 id → 顶层投影位置 id 2。
        assert ids == [None, 2]
        assert page["features"][0]["properties"]["id"] == "keep"
        assert page["positional_ids"] == 1

    def test_positional_id_stable_under_sort(self):
        fc = _fc(_f({"n": 2, "name": "a"}), _f({"n": 1, "id": "b"}), _f({"n": 3, "name": "c"}))
        page = page_features(fc, limit=10, order=("n", 1))
        by_name = {f["properties"].get("name") or f["properties"]["id"]: f.get("id") for f in page["features"]}
        # 位置 id = FC 自然索引+1，不随排序变；链内 id（properties.id="b"）
        # 的要素顶层不投影位置 id。
        assert by_name == {"a": 1, "b": None, "c": 3}

    def test_truncated_flag_on_budget_stop(self):
        import app.services.feature_pages as fp

        fc = _fc(*[_f({"t": "x", "lon": float(100 + i % 50)}, f"i{i}") for i in range(200)])
        where = {"field": "t", "op": "eq", "value": "x"}
        orig = fp.WINDOW_SCAN_BUDGET
        fp.WINDOW_SCAN_BUDGET = 10
        try:
            page = page_features(fc, limit=200, where=where, bbox=(100.0, 0.0, 120.0, 60.0))
            assert page["returned"] < 200
            assert page["truncated"] is True and page["has_more"] is True
            # 无过滤的整页（limit 内集满）不标 truncated
            full = page_features(fc, limit=5)
            assert full["truncated"] is False
        finally:
            fp.WINDOW_SCAN_BUDGET = orig


# ─── R1/R2 评审清偿回归 ────────────────────────────────────────────────────


class TestReviewFixes:
    def test_in_with_null_matches_missing(self):
        f = _f({"t": None}, "a")
        g = _f({"t": "x"}, "b")
        ast = parse_where_param('{"field":"t","op":"in","values":[null,"x"]}')
        assert evaluate_where(ast, f) is True   # null 值命中缺席
        assert evaluate_where(ast, g) is True   # 字符串值照常
        assert evaluate_where(parse_where_param('{"field":"t","op":"in","values":["y"]}'), f) is False

    def test_cursor_rejects_bool_index(self):
        import base64 as _b64

        def _cursor(payload: dict) -> str:
            return _b64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")

        with pytest.raises(FeaturePageError):
            decode_page_cursor(_cursor({"v": 1, "i": True}))

    def test_order_over_limit_rejected(self):
        import app.services.feature_pages as fp

        fc = _fc(*[_f({"v": i}, f"i{i}") for i in range(5)])
        orig = fp.ORDER_SCAN_LIMIT
        fp.ORDER_SCAN_LIMIT = 4
        try:
            with pytest.raises(FeaturePageError, match="order unsupported"):
                page_features(fc, limit=10, order=("v", 1))
        finally:
            fp.ORDER_SCAN_LIMIT = orig

    def test_bbox_only_count_cached(self):

        fc = _fc(*[_f({"t": "x", "lon": 104.0 + (i % 3) * 0.1}, f"i{i}") for i in range(10)])
        key = ("ref:c", "1")
        first = count_matching(fc["features"], where=None, bbox=(104.0, 30.0, 104.15, 31.0), cache_key=key)
        assert first == 7  # lon ∈ {104.0, 104.1}（i%3 ∈ {0,1}）
        # 改数据后同 cache_key 命中缓存（bbox-only 也缓存）
        fc["features"].append(_f({"t": "x", "lon": 104.05}, "extra"))
        again = count_matching(fc["features"], where=None, bbox=(104.0, 30.0, 104.15, 31.0), cache_key=key)
        assert again == 7

    def test_derived_cache_weight_eviction(self):
        import app.services.feature_pages as fp

        fc = _fc(*[_f({"v": i}, f"i{i}") for i in range(100)])
        with fp._derived_cache_lock:
            fp._derived_cache.clear()
            fp._derived_cache_weight = 0
        with fp._derived_cache_lock:
            weight0 = fp._derived_cache_weight
        # 填 3 个 100 权重排列后手动压低预算帽验证 LRU 逐出逻辑可达
        for k in range(3):
            page_features(fc, limit=5, order=("v", 1), cache_key=(f"ref:w{k}", "1"))
        with fp._derived_cache_lock:
            assert fp._derived_cache_weight <= fp._DERIVED_CACHE_MAX_WEIGHT
            assert len(fp._derived_cache) <= fp._DERIVED_CACHE_MAX_ENTRIES
        assert weight0 == 0
        fp._derived_cache.clear()
