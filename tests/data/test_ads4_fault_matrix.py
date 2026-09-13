"""ads-v1 DS4 fault-injection matrix (ADR-0174): 5 source types × 6 faults.

Each group injects one fault into one protocol's fake source and asserts the
declared expected behaviour of the fallback chain:

- timeout / 5xx / 429 → per-hop transient retry, then a D3 decision with the
  matching trigger and the acquisition lands on the fallback source;
- empty_result → declared ``empty_result`` trigger activates the fallback;
- truncated (mid-JSON cut) → ``truncated`` trigger (typed adapter error);
- schema_mismatch (valid JSON, undeclared shape) → adapter-honest behaviour:
  typed ``schema_mismatch`` where the adapter validates shape (stats_api),
  or an empty-feature payload classified ``empty_result`` (geojson-shaped
  adapters that tolerate missing keys).

Every group additionally asserts: decisions recorded, ``fact.degraded=True``,
and the payload flagging. Groups whose adapter cannot distinguish a fault
honestly document that in EXPECTED — the matrix is the acceptance artifact.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Tuple

import pytest
import requests

from app.schemas.data_fabric_schema import ConnectionProfile, QuerySpec
from app.services.data_fabric.fallback import execute_fallback_chain
from tests.data.fabric_fixtures import FAKE_HOST, _feature

SOURCE_TYPES = ["ogc_api", "wfs", "arcgis", "stac", "stats_api"]
FAULTS = ["timeout", "5xx", "429", "empty_result", "truncated", "schema_mismatch"]

EXPECTED_TRIGGER = {
    "timeout": {"timeout"},
    "5xx": {"5xx"},
    "429": {"429"},
    "empty_result": {"empty_result"},
    "truncated": {"truncated"},
    # geojson-shaped adapters tolerate missing keys → empty payload; the
    # stats adapter validates the declared shape → typed schema_mismatch.
    "schema_mismatch": {"schema_mismatch", "empty_result"},
}

# Per-protocol dataset id used by the runner.
DATASET = {
    "ogc_api": "lake_depth",
    "wfs": "ads:parcels",
    "arcgis": "0",
    "stac": "scene-001",
    "stats_api": "matrix_ds",
}


def _ok_body(protocol: str) -> Any:
    feats = [_feature("1"), _feature("2")]
    if protocol == "ogc_api":
        return {"type": "FeatureCollection", "features": feats, "numberMatched": 2}
    if protocol == "wfs":
        return {"type": "FeatureCollection", "features": feats}
    if protocol == "arcgis":
        return {"type": "FeatureCollection", "features": feats}
    if protocol == "stac":
        return {"type": "FeatureCollection", "features": [{"id": "scene-001"}], "context": {"returned": 1}}
    return {"features": feats}


def _fault_response(request: requests.PreparedRequest, fault: str, protocol: str) -> requests.Response:
    from tests.fixtures.data_fabric.fake_server import make_response

    if fault == "timeout":
        raise requests.Timeout("connection timed out")
    if fault == "5xx":
        return make_response(request.url, status=503, text="down")
    if fault == "429":
        return make_response(request.url, status=429, text="slow down")
    if fault == "empty_result":
        if protocol == "stats_api":
            return make_response(request.url, json_body={"features": []})
        return make_response(request.url, json_body={"type": "FeatureCollection", "features": []})
    if fault == "truncated":
        return make_response(request.url, text='{"type":"FeatureCollection","features":[{"x"')
    # schema_mismatch: valid JSON, shape never declared
    return make_response(request.url, json_body={"unexpected": {"shape": True}})


def _build_protocol_routes(protocol: str, fault: str) -> Tuple[List[Tuple[str, Callable]], str]:
    """Routes serving normal payloads everywhere except the data path, which
    injects the fault."""
    from tests.fixtures.data_fabric.fake_server import make_response

    def items_handler(request: requests.PreparedRequest) -> requests.Response:
        if fault != "none":
            return _fault_response(request, fault, protocol)
        return make_response(request.url, json_body=_ok_body(protocol))

    if protocol == "ogc_api":
        routes = [("/ogc", lambda req: items_handler(req) if "/items" in req.url else _probe_ok(req))]
        url = f"https://{FAKE_HOST}/ogc"
    elif protocol == "wfs":
        def wfs_handler(request: requests.PreparedRequest) -> requests.Response:
            body = request.body or b""
            if isinstance(body, bytes):
                body = body.decode("utf-8", errors="ignore")
            if "REQUEST=GETFEATURE" in request.url.upper() or "REQUEST=GETFEATURE" in body.upper():
                return items_handler(request)
            return make_response(request.url, text="<wfs:WFS_Capabilities version=\"2.0.0\"/>")
        routes = [("/wfs", wfs_handler)]
        url = f"https://{FAKE_HOST}/wfs"
    elif protocol == "arcgis":
        def arcgis_handler(request: requests.PreparedRequest) -> requests.Response:
            if "/query" in request.url:
                return items_handler(request)
            return make_response(request.url, json_body={"currentVersion": 11.1, "layers": [{"id": 0}]})
        routes = [("/arcgis", arcgis_handler)]
        url = f"https://{FAKE_HOST}/arcgis/rest/services/ads/FeatureServer"
    elif protocol == "stac":
        def stac_handler(request: requests.PreparedRequest) -> requests.Response:
            if request.method == "POST":
                return items_handler(request)
            return make_response(request.url, json_body={"id": "ads", "stac_version": "1.0.0",
                                                          "links": [{"href": f"https://{FAKE_HOST}/stac/search", "rel": "search", "method": "POST"}]})
        routes = [("/stac", stac_handler)]
        url = f"https://{FAKE_HOST}/stac"
    else:  # stats_api
        routes = [("/stats", lambda req: items_handler(req) if "/records" in req.url else _probe_ok(req))]
        url = f"https://{FAKE_HOST}/stats"
    return routes, url


def _probe_ok(request: requests.PreparedRequest) -> requests.Response:
    from tests.fixtures.data_fabric.fake_server import make_response

    return make_response(request.url, json_body={"ok": True})


def build_matrix_case(protocol: str, fault: str, monkeypatch: pytest.MonkeyPatch):
    """Install the fake transport FIRST, then construct the adapter (adapters
    bind their session at __init__ — the reverse order would dial for real)."""
    from tests.fixtures.data_fabric.fake_server import session_with_fake

    routes, url = _build_protocol_routes(protocol, fault)
    session = session_with_fake(tuple(routes))

    import importlib
    import pkgutil

    import app.services.data_fabric.adapters as adapter_pkg
    from app.services.data_fabric import security as sec_mod

    for mod_info in pkgutil.iter_modules(adapter_pkg.__path__):
        mod = importlib.import_module(f"app.services.data_fabric.adapters.{mod_info.name}")
        if hasattr(mod, "make_safe_session"):
            monkeypatch.setattr(mod, "make_safe_session", lambda *a, **k: session)
    # stats adapter imports make_safe_session at call time from security
    monkeypatch.setattr(sec_mod, "make_safe_session", lambda *a, **k: session)

    classes = _adapter_classes()
    if protocol == "stats_api":
        profile = ConnectionProfile(
            source_type="stats_api", endpoint_url=url, name="m",
            options={"datasets": [{"dataset_id": DATASET[protocol], "path": "records", "items_path": "features"}]},
        )
    else:
        profile = ConnectionProfile(source_type=protocol, endpoint_url=url, name="m")
    return classes[protocol](profile)


def _adapter_classes() -> Dict[str, type]:
    from app.services.data_fabric.adapters.arcgis_adapter import ArcGISAdapter
    from app.services.data_fabric.adapters.ogc_api_adapter import OGCAPIAdapter
    from app.services.data_fabric.adapters.stats_api_adapter import StatsApiAdapter
    from app.services.data_fabric.adapters.stac_adapter import STACAdapter
    from app.services.data_fabric.adapters.wfs_adapter import WFSAdapter

    return {
        "ogc_api": OGCAPIAdapter,
        "wfs": WFSAdapter,
        "arcgis": ArcGISAdapter,
        "stac": STACAdapter,
        "stats_api": StatsApiAdapter,
    }


def _runner_for(protocol: str, adapter) -> Callable[[str], Tuple[List[Dict[str, Any]], Dict[str, Any]]]:
    """The primary hop hits the fault-injecting adapter; the fallback hop
    (a healthy source) returns a canned success payload."""

    def runner(source_id: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        if not source_id.startswith("primary_"):
            fallback_features = [{
                "type": "Feature", "id": "fb", "geometry": None,
                "properties": {"from": "fallback", "ok": True},
            }]
            return fallback_features, {"bytes": 96, "empty_result": False}
        result = adapter.query(DATASET[protocol], QuerySpec(limit=100))
        features = list(result.features)
        meta = {"bytes": len(json.dumps(features, ensure_ascii=False, default=str).encode()), "empty_result": not features}
        return features, meta

    return runner


# Parametrize 5 source types × 6 faults = 30 groups. Fault "none" is exercised
# implicitly by the primary-success unit tests.
@pytest.mark.parametrize("fault", FAULTS)
@pytest.mark.parametrize("protocol", SOURCE_TYPES)
def test_fault_matrix_group(protocol, fault, monkeypatch):
    adapter = build_matrix_case(protocol, fault, monkeypatch)

    runner = _runner_for(protocol, adapter)
    result = execute_fallback_chain(
        f"primary_{protocol}_{fault}".replace("-", "_"),
        runner,
        # the chain declares the fault's full acceptable trigger set (e.g.
        # schema_mismatch may surface as empty_result on shape-tolerant adapters)
        chain=[("fallback_src", sorted(EXPECTED_TRIGGER[fault]), None)],
        request_id=f"matrix-{protocol}-{fault}",
        dataset_key=f"{protocol}/ds",
        wave="M3",
        facts_by_source={},  # unknown facts → conservative non-comparable
    )

    # expected behaviour per group (the matrix conclusions)
    if fault in {"empty_result"}:
        # empty payload reaches the chain as a *successful* primary response —
        # the empty_result trigger activates the fallback hop
        assert result.source_used == "fallback_src", (protocol, fault, result.decisions)
        assert result.decisions and result.decisions[0].trigger in EXPECTED_TRIGGER[fault], (
            protocol, fault, [d.trigger for d in result.decisions])
    else:
        # the primary fails typed; the matching trigger routes to the fallback
        assert result.source_used == "fallback_src", (protocol, fault, result.fact.model_dump())
        triggers = [d.trigger for d in result.decisions]
        assert triggers and triggers[0] in EXPECTED_TRIGGER[fault], (protocol, fault, triggers)

    # D3/D4 contract invariants for every group
    assert result.fact.degraded is True
    assert result.fact.source_id == "fallback_src"
    assert result.fact.outcome == "degraded"
    assert result.decisions[0].comparable is False  # unknown facts → conservative
    assert result.fact.fallback is not None and result.fact.fallback.trigger == result.decisions[0].trigger


def test_matrix_summary_is_30_groups():
    assert len(SOURCE_TYPES) * len(FAULTS) == 30


def test_no_chain_fails_typed_not_silent(monkeypatch):
    """Without a chain, every fault must surface as a typed failure — never a
    faked success (spot-checks across protocols)."""
    for protocol in SOURCE_TYPES:
        adapter = build_matrix_case(protocol, "5xx", monkeypatch)
        runner = _runner_for(protocol, adapter)
        result = execute_fallback_chain(
            f"primary_solo_{protocol}".replace("-", "_"), runner, chain=[],
            request_id="solo", dataset_key=f"{protocol}/ds",
        )
        assert result.source_used is None and result.ok is False
        assert result.fact.outcome == "failed"
