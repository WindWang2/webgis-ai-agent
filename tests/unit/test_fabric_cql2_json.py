"""CQL2-JSON 编译器与探测接线测试（ADR-0119 W5）。

- compile_predicate_cql2_json 全 op 词表 + 值类型保持（无字符串拼接面）；
- 非法属性标识符 typed 拒绝；NULL/嵌套逻辑结构正确；
- OGC adapter：conformance 声明 cql2-json → filter-lang=cql2-json 且
  payload 为合法 JSON；仅 text → 历史 cql2-text 逐位；
- STAC adapter：filter extension conformance → capabilities_v2 升级；
  未声明 → 保持静态默认（filter 本地求值）。
"""

import json

import pytest

from app.services.data_fabric.query.compilers import (
    compile_bbox_cql2_json,
    compile_predicate_cql2,
    compile_predicate_cql2_json,
)
from app.services.data_fabric.query.predicates import PredicateError, predicate_from_dict


def _pred(d):
    return predicate_from_dict(d)


def test_cql2_json_all_scalar_ops():
    p = _pred({"op": "and", "args": [
        {"op": "eq", "field": "owner", "value": "alpha"},
        {"op": "ne", "field": "status", "value": None},
        {"op": "gt", "field": "area", "value": 10},
        {"op": "le", "field": "depth", "value": 3.5},
        {"op": "in", "field": "zone", "values": ["a", "b"]},
        {"op": "not_in", "field": "risk", "values": [1, 2]},
        {"op": "between", "field": "elev", "low": 0, "high": 100},
        {"op": "like", "field": "name", "pattern": "%park%"},
        {"op": "is_null", "field": "note"},
        {"op": "is_null", "field": "flag", "negated": True},
    ]})
    out = compile_predicate_cql2_json(p)
    assert out["op"] == "and"
    args = out["args"]
    assert args[0] == {"op": "=", "args": [{"property": "owner"}, "alpha"]}
    assert args[1] == {"op": "<>", "args": [{"property": "status"}, None]}
    assert args[2] == {"op": ">", "args": [{"property": "area"}, 10]}
    assert args[4] == {"op": "in", "args": [{"property": "zone"}, ["a", "b"]]}
    assert args[5]["op"] == "not"
    assert args[6] == {"op": "between", "args": [{"property": "elev"}, [0, 100]]}
    assert args[7] == {"op": "like", "args": [{"property": "name"}, "%park%"]}
    assert args[8] == {"op": "isNull", "args": [{"property": "note"}]}
    assert args[9]["args"][0]["op"] == "isNull"
    # 可 JSON 序列化（POST body 直用）
    json.dumps(out)


def test_cql2_json_rejects_bad_property_identifier():
    for bad in ("", "a\nb"):
        with pytest.raises(PredicateError):
            compile_predicate_cql2_json(_pred({"op": "eq", "field": bad, "value": 1}))


def test_cql2_json_matches_text_semantics_on_shared_ast():
    ast = {"op": "eq", "field": "owner", "value": "O'Brien"}
    node = _pred(ast)
    text = compile_predicate_cql2(node)
    js = compile_predicate_cql2_json(node)
    assert text == "owner = 'O''Brien'"
    assert js == {"op": "=", "args": [{"property": "owner"}, "O'Brien"]}


def test_cql2_json_bbox():
    out = compile_bbox_cql2_json([0.0, 1.0, 2.0, 3.0])
    assert out["op"] == "s_intersects"
    ring = out["args"][1]["coordinates"][0]
    assert ring[0] == ring[-1] and len(ring) == 5


# ── OGC adapter 接线 ────────────────────────────────────────────────────


from tests.unit.test_ogc_adapters_753 import FakeResponse, FakeSession, OGC_ITEMS


@pytest.fixture
def ogc():
    from app.schemas.data_fabric_schema import ConnectionProfile
    from app.services.data_fabric.adapters.ogc_api_adapter import OGCAPIAdapter

    profile = ConnectionProfile(provider_type="ogc_api", endpoint="")
    profile.url = "https://example.com/ogc"
    adapter = OGCAPIAdapter(profile)
    s = FakeSession()

    def route(url, params=None, timeout=None, **kwargs):
        s.calls.append({"url": url, "params": dict(params or {})})
        if url.endswith("/conformance"):
            return FakeResponse(json_data={"conformsTo": s.conformance})
        if "/items" in url:
            return FakeResponse(json_data=s.items_response)
        return FakeResponse(json_data={})

    s.conformance = []
    s.items_response = dict(OGC_ITEMS)
    s.get = route
    adapter.session = s
    return adapter


def test_ogc_cql2_json_encoding_preferred_when_declared(ogc):
    ogc.session.conformance = [
        "http://www.opengis.net/spec/ogcapi-features-2/1.0/conf/cql2-text",
        "http://www.opengis.net/spec/cql2/1.0/conf/cql2-json",
    ]
    spec = _spec()
    ogc.query("parcels", spec)
    call = [c for c in ogc.session.calls if c["url"].endswith("/items")][-1]
    assert call["params"]["filter-lang"] == "cql2-json"
    decoded = json.loads(call["params"]["filter"])
    assert decoded == {"op": "=", "args": [{"property": "owner"}, "alpha"]}


def test_ogc_cql2_text_only_keeps_legacy_encoding(ogc):
    ogc.session.conformance = [
        "http://www.opengis.net/spec/ogcapi-features-2/1.0/conf/cql2-text"]
    spec = _spec()
    ogc.query("parcels", spec)
    call = [c for c in ogc.session.calls if c["url"].endswith("/items")][-1]
    assert call["params"]["filter"] == "owner = 'alpha'"
    assert call["params"]["filter-lang"] == "cql2-text"


def _spec():
    from app.schemas.data_fabric_schema import QuerySpec

    return QuerySpec(limit=5, filter_expr={"op": "eq", "field": "owner", "value": "alpha"})


# ── STAC adapter 探测 ───────────────────────────────────────────────────


def test_stac_capabilities_v2_probes_filter_extension(monkeypatch):
    from app.schemas.data_fabric_schema import ConnectionProfile
    from app.services.data_fabric.adapters import stac_adapter as stac_mod

    profile = ConnectionProfile(provider_type="stac", endpoint="")
    profile.url = "https://example.com/stac"
    profile.endpoint = "https://example.com/stac"
    adapter = stac_mod.STACAdapter(profile)

    calls = []

    def fake_safe_json_get(session, url, **kw):
        calls.append(url)
        if url.endswith("/conformance"):
            return {"conformsTo": [
                "https://api.stacspec.org/v1.0.0-rc.3/item-search#filter"]}
        return {}

    monkeypatch.setattr(stac_mod, "safe_json_get", fake_safe_json_get)
    caps = adapter.capabilities_v2()
    assert caps.filter_pushdown is True
    assert caps.filter_encoding == "cql2-json"
    # 缓存生效：二次调用不再请求
    adapter.capabilities_v2()
    assert len(calls) == 1


def test_stac_capabilities_v2_falls_back_when_conformance_missing(monkeypatch):
    from app.schemas.data_fabric_schema import ConnectionProfile
    from app.services.data_fabric.adapters import stac_adapter as stac_mod

    profile = ConnectionProfile(provider_type="stac", endpoint="")
    profile.url = "https://example.com/stac"
    profile.endpoint = "https://example.com/stac"
    adapter = stac_mod.STACAdapter(profile)

    monkeypatch.setattr(
        stac_mod, "safe_json_get",
        lambda session, url, **kw: {"conformsTo": []})
    caps = adapter.capabilities_v2()
    assert caps.filter_pushdown is False
    assert caps.filter_encoding is None
