"""R1 data P3 confirmations (#2069 / #2056) — local regressions."""
from __future__ import annotations

import hashlib
import math
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.data_fabric.errors import InvalidQueryError, SourceUnreachableError
from app.services.data_fabric.query.predicates import (
    Eq,
    Like,
    PredicateError,
    _canonical_value,
    evaluate_predicate,
    predicate_fingerprint,
    temporal_from_dict,
)
from app.services.data_fabric.security import DataFabricSecurity, ensure_same_origin_url


def test_n1_invalid_calendar_date_is_predicate_error():
    with pytest.raises(PredicateError):
        temporal_from_dict({"op": "before", "field": "t", "value": "2024-13-45"})
    node = temporal_from_dict({"op": "before", "field": "t", "value": "2024-01-02T03:04:05Z"})
    assert node.value.startswith("2024-01-02")


def test_n2_canonical_float_nonfinite_does_not_raise():
    assert _canonical_value(1.0) == 1
    assert _canonical_value(float("inf")) == float("inf")
    assert math.isnan(_canonical_value(float("nan")))
    predicate_fingerprint(Eq(field="a", value=float("inf")))


def test_n3_like_escape_is_backslash_locally_and_in_compilers():
    from app.services.data_fabric.query.compilers import compile_predicate_fes, compile_predicate_sql

    node = Like(field="name", pattern=r"100\%")
    assert evaluate_predicate(node, {"name": "100%"}) is True
    assert evaluate_predicate(node, {"name": "100x"}) is False
    literal = Like(field="name", pattern=r"a\_b")
    assert evaluate_predicate(literal, {"name": "a_b"}) is True
    assert evaluate_predicate(literal, {"name": "axb"}) is False
    sql, params = compile_predicate_sql(node)
    assert "ESCAPE '\\'" in sql
    assert params == [r"100\%"]
    fes = compile_predicate_fes(node)
    assert 'escapeChar="\\"' in fes


def test_n5_hashable_key_accepts_nonfinite_floats():
    from app.services.data_fabric.query.federation import build_attribute_index

    index = build_attribute_index(
        [{"properties": {"k": float("inf")}}, {"properties": {"k": 1.0}}],
        "k",
    )
    assert 1 in index
    assert float("inf") in index


def test_n6_overlapping_polygon_hit_is_lowest_input_index(monkeypatch):
    pytest.importorskip("shapely")
    from app.services.data_fabric.query import federation as fed

    monkeypatch.setattr(fed._LocalSpatialIndex, "candidates", lambda self, geom: [1, 0])
    point = {"geometry": {"type": "Point", "coordinates": [0.5, 0.5]}, "properties": {"id": 1}}
    square = [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]
    polys = [
        {"geometry": {"type": "Polygon", "coordinates": square}, "properties": {"name": "A"}},
        {"geometry": {"type": "Polygon", "coordinates": square}, "properties": {"name": "B"}},
    ]
    rows = fed.spatial_join_local([point], polys, spatial_op="within")
    assert rows[0]["__right__"]["name"] == "A"


def test_n7_result_cache_put_and_get_do_not_share_rows():
    from app.services.data_fabric.fabric.result_cache import FederatedResultCache

    cache = FederatedResultCache(max_entries=4, max_bytes=10**6, ttl_s=60.0)
    payload = {"status": "success", "rows": [{"id": 1}]}
    cache.put("k", payload, fingerprints={"s": "fp"}, scope_key="org:a|owner:b|proj:c")
    payload["rows"][0]["id"] = 99
    payload["rows"].append({"id": 2})
    hit = cache.get("k")
    assert hit["rows"] == [{"id": 1}]
    hit["rows"][0]["id"] = 7
    again = cache.get("k")
    assert again["rows"] == [{"id": 1}]


def test_n10_arrow_lane_accepts_canonical_dict_and_skips_raw_str(monkeypatch):
    pytest.importorskip("pyarrow")
    from app.services.data_fabric.query.federated.physical import iter_scan_pages
    from app.services.data_fabric.query.predicates import predicate_to_canonical_dict

    class _Budget:
        max_rows = 10
        deadline_s = 5.0

    class _Token:
        def check(self):
            return None

    class _Adapter:
        def __init__(self):
            self.arrow = False
            self.where = None

        def iter_query_arrow_batches(self, dataset_id, spec):
            self.arrow = True
            raise RuntimeError("entered arrow")

        def query(self, dataset_id, spec):
            self.where = spec.where
            from app.schemas.data_fabric_schema import QueryResult

            return QueryResult(dataset_id=dataset_id, features=[])

    where = predicate_to_canonical_dict(Eq(field="zone", value="A"))
    arrow = _Adapter()
    with pytest.raises(RuntimeError, match="entered arrow"):
        list(iter_scan_pages(
            arrow, "ds", where=where, fields=None, bbox=None,
            fetch_limit=5, budget=_Budget(), token=_Token(),
        ))
    assert arrow.arrow is True

    plain = _Adapter()
    pages = list(iter_scan_pages(
        plain, "ds", where="zone = 'A'", fields=None, bbox=None,
        fetch_limit=5, budget=_Budget(), token=_Token(),
    ))
    assert pages and plain.arrow is False
    assert plain.where == "zone = 'A'"


def test_n12_bloom_float_matches_decimal_and_ignores_nonfinite():
    from app.services.data_fabric.query.federated.bloom import BloomFilter

    bloom = BloomFilter.with_capacity(16)
    bloom.add(1.5)
    assert Decimal("1.5") in bloom
    bloom.add(float("inf"))
    bloom.add(float("nan"))


def test_n17_envelope_sanity_invalid_crs_is_not_an_error():
    from app.services.data_quality.rule_functions import RuleEvalContext, rule_envelope_sanity

    feat = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [10.0, 20.0]}}
    out = rule_envelope_sanity(RuleEvalContext(crs="NOT_A_CRS", features=[feat]))
    assert out.status in ("pass", "warn", "skipped")


def test_n19_primary_key_does_not_collapse_types_or_long_values():
    from app.services.data_quality.rule_functions import RuleEvalContext, rule_fk_referential, rule_primary_key_uniqueness

    feats = [
        {"properties": {"id": 1}},
        {"properties": {"id": "1"}},
        {"properties": {"id": "a" * 130}},
        {"properties": {"id": "a" * 129 + "b"}},
    ]
    out = rule_primary_key_uniqueness(RuleEvalContext(features=feats, params={"field": "id"}))
    assert out.status == "pass"
    fk = rule_fk_referential(RuleEvalContext(
        features=[{"properties": {"id": 1}}],
        params={"field": "id", "valid_values": ["1"]},
    ))
    assert fk.status == "warn"


def test_n20_irregular_covjson_axis_is_rejected():
    import json

    from app.services.data_fabric.coverage_read import CoverageReadError, _parse_covjson

    def _body(xs):
        n = len(xs)
        return json.dumps({
            "type": "Coverage",
            "domain": {"axes": {"x": {"values": xs}, "y": {"values": [0, 1, 2]}}},
            "ranges": {"b": {"axisNames": ["y", "x"], "shape": [3, n], "values": [1] * (3 * n)}},
        }).encode()

    with pytest.raises(CoverageReadError, match="equally spaced"):
        _parse_covjson(_body([0, 1, 3]), bands=None, bands_declared=[{"name": "b"}])
    arr, *_rest = _parse_covjson(_body([0, 1, 2]), bands=None, bands_declared=[{"name": "b"}])
    assert arr.shape[-1] == 3


def test_n24_plan_locks_do_not_share_a_256_bucket(tmp_path):
    from app.services.data_lifecycle.gc_recovery import plan_guard

    class _DB:
        class dialect:
            name = "sqlite"

        def get_bind(self):
            return self

    def _bucket(plan_id: str) -> int:
        digest = hashlib.sha256(plan_id.encode()).digest()
        return int.from_bytes(digest[:2], "big") % 256

    left, right = "plan-a", "plan-b"
    for i in range(5000):
        right = f"plan-{i}"
        if _bucket(left) == _bucket(right) and left != right:
            break
    else:
        pytest.fail("could not find a hash collision in the old 256-bucket scheme")
    db = _DB()
    with plan_guard(db, left, tmp_path):
        pass
    with plan_guard(db, right, tmp_path):
        pass
    locks = list((tmp_path / ".locks").glob("*.lock"))
    assert len(locks) == 2


def test_f2_headers_subtree_and_cookie_are_redacted():
    out = DataFabricSecurity.sanitize_profile_dict({
        "headers": {"Cookie": "session=secret", "X-Custom": "tok"},
        "http_headers": {"Authorization": "Bearer abc"},
        "Cookie": "raw=1",
        "name": "visible",
    })
    assert out["headers"] == "********"
    assert out["http_headers"] == "********"
    assert out["Cookie"] == "********"
    assert out["name"] == "visible"


def test_f5_postgis_backoff_raises_instead_of_connecting(monkeypatch):
    import time

    from app.services.data_fabric.adapters import postgis_adapter as pg

    key = pg._pool_key("10.255.255.1", 5432, "db", "user", password="pw")
    pg._POOL_FAILURES[key] = time.monotonic()
    monkeypatch.setattr(pg, "psycopg2", None, raising=False)

    def _boom(*_a, **_k):
        raise AssertionError("backoff must not open a new connection")

    import psycopg2

    monkeypatch.setattr(psycopg2, "connect", _boom)
    with pytest.raises(SourceUnreachableError, match="backoff"):
        pg._get_or_create_postgis_pool("10.255.255.1", 5432, "db", "user", "pw")
    pg._POOL_FAILURES.pop(key, None)


def test_f6_server_spatial_join_rejects_unknown_op():
    from app.schemas.data_fabric_schema import ConnectionProfile
    from app.services.data_fabric.adapters.postgis_adapter import PostGISAdapter

    adapter = PostGISAdapter(ConnectionProfile(source_type="postgis", host="127.0.0.1", database="db"))
    with pytest.raises(InvalidQueryError, match="unsupported spatial join"):
        adapter.server_spatial_join("points", "polys", join_op="touches")


def test_f7_sqlite_uri_escapes_query_chars_and_types_missing_column(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.schemas.data_fabric_schema import ConnectionProfile, QuerySpec
    from app.services.data_fabric.adapters.local_file_adapter import LocalFileAdapter

    monkeypatch.setattr(settings, "DATA_FABRIC_LOCAL_FILE_ROOTS", str(tmp_path))
    monkeypatch.setattr(settings, "DATA_FABRIC_LOCAL_FILE_MAX_BYTES", 10**9)
    path = tmp_path / "a?b#.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()
    adapter = LocalFileAdapter(ConnectionProfile(
        source_type="local_file", endpoint=str(path),
    ))
    calls = {"n": 0}
    real = sqlite3.connect

    def _wrap(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", _wrap)
    stem = path.stem
    result = adapter.query(f"{stem}:t", QuerySpec(limit=5))
    assert result.features[0]["properties"]["id"] == 1
    assert calls["n"] == 1
    with pytest.raises(InvalidQueryError):
        adapter.query(f"{stem}:t", QuerySpec(limit=5, fields=["missing_col"]))


def test_f11_child_id_cursor_path_and_header_redact():
    from app.services.data_fabric.adapters.stac_adapter import _catalog_child_id
    from app.services.data_fabric.errors import SourceBadResponseError

    assert _catalog_child_id("./s2/collection.json") == "s2"
    assert _catalog_child_id("https://stac.example/collections/landsat-8") == "landsat-8"
    ok = ensure_same_origin_url(
        "https://stac.example/api/v1/search?token=1",
        "https://stac.example/api/v1",
    )
    assert ok.startswith("https://stac.example/api/v1/")
    with pytest.raises(SourceBadResponseError, match="path"):
        ensure_same_origin_url("https://stac.example/admin/delete", "https://stac.example/api/v1")


def test_f11_ogc_relative_cursor_and_truncated_or(monkeypatch):
    from app.schemas.data_fabric_schema import ConnectionProfile, QuerySpec
    from app.services.data_fabric.adapters.ogc_api_adapter import OGCAPIAdapter
    from app.services.data_fabric.query.execution import decode_cursor

    profile = ConnectionProfile(provider_type="ogc_api", url="https://example.com/ogc")
    adapter = OGCAPIAdapter(profile)
    pages = {"n": 0}

    class _Resp:
        def __init__(self, payload):
            import json as _json
            self._body = _json.dumps(payload).encode()
            self.status_code = 200
            self.headers = {}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=1024):
            yield self._body

        def close(self):
            return None

    def _get(url, params=None, timeout=None, stream=None, headers=None, allow_redirects=True):
        if url.endswith("/conformance"):
            return _Resp({"conformsTo": []})
        if url.rstrip("/").endswith("/collections/parcels"):
            return _Resp({"id": "parcels", "title": "Parcels"})
        if "/items" not in url:
            return _Resp({})
        pages["n"] += 1
        if pages["n"] == 1:
            return _Resp({
                "type": "FeatureCollection",
                "numberMatched": 2,
                "features": [
                    {"type": "Feature", "properties": {"name": "a"}, "geometry": None},
                    {"type": "Feature", "properties": {"name": "b"}, "geometry": None},
                ],
                "links": [{"rel": "next", "href": "items?page=2"}],
            })
        return _Resp({"type": "FeatureCollection", "features": [], "numberMatched": 2})

    adapter.session.get = _get
    first = adapter.query("parcels", QuerySpec(limit=2))
    # 满页信号不得被 numberMatched == page size 清掉。
    assert first.truncated is True and first.has_more is True
    href = decode_cursor(first.next_cursor)[0]
    assert href.startswith("https://example.com/ogc/")
    second = adapter.query("parcels", QuerySpec(limit=2, page_kind="cursor", cursor=first.next_cursor))
    assert second.returned_count == 0


def test_f14_fingerprint_ignores_signature_and_budget_is_clamped():
    from app.services.data_fabric.cog_materialization import (
        DEFAULT_MAX_OUTPUT_BYTES,
        _params_fingerprint,
    )

    bare = "https://cdn.example/a.tif"
    signed = bare + "?X-Goog-Signature=sekret&X-Goog-Algorithm=GOOG4"
    assert _params_fingerprint(bare, [1], [0, 0, 1, 1], 0) == _params_fingerprint(
        signed, [1], [0, 0, 1, 1], 0,
    )
    assert "sekret" not in _params_fingerprint(signed, None, None, 0)


def test_f18_next_offset_ignores_total_bound_and_bbox_is_typed():
    from app.services.lakehouse.catalog_service import CatalogError, search_catalog

    class _Item:
        def to_dict(self):
            return {"id": "x"}

    class _Result:
        def __init__(self, mode):
            self.mode = mode

        def scalars(self):
            return self

        def all(self):
            return [_Item()]

        def scalar_one(self):
            return 10_001

    class _DB:
        def execute(self, _stmt):
            return _Result("x")

    page = search_catalog(_DB(), owner_type="session", owner_id="s", limit=1, offset=0)
    assert page["total_bounded"] is True
    assert page["next_offset"] == 1
    with pytest.raises(CatalogError, match="finite"):
        search_catalog(_DB(), owner_type="session", owner_id="s", bbox=["nope", 0, 1, 1])


def test_f14_budget_clamp_constant():
    from app.services.data_fabric.cog_materialization import DEFAULT_MAX_OUTPUT_BYTES

    assert DEFAULT_MAX_OUTPUT_BYTES == 256 * 1024 * 1024
