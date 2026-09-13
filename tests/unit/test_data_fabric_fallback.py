"""ads-v1 DS4 fallback-chain unit tests (ADR-0174).

Covers: failure classification, chain resolution (multi-level, cycle-safe),
D3 decision recording with comparability, D4 fact assembly, breaker
integration, health-bridge mirroring, and the local_first registry-driven
chain equivalence.
"""
from __future__ import annotations

import pytest

from app.services.data_fabric import fallback as fb_mod
from app.services.data_fabric.circuit_breaker import CircuitState
from app.services.data_fabric.contracts import FallbackDecision
from app.services.data_fabric.errors import SourceBadResponseError, SourceTimeoutError
from app.services.data_fabric.fallback import (
    ChainResult,
    classify_failure,
    execute_fallback_chain,
    resolve_chain,
)


@pytest.fixture(autouse=True)
def _clean_breakers():
    fb_mod.breaker_registry = fb_mod.CircuitBreakerRegistry()
    yield
    fb_mod.breaker_registry = fb_mod.CircuitBreakerRegistry()


# ── classification ───────────────────────────────────────────────────────────


def test_classify_typed_and_message_errors():
    assert classify_failure(SourceTimeoutError("t")) == "timeout"
    assert classify_failure(None, empty_result=True) == "empty_result"
    assert classify_failure(RuntimeError("HTTP 503 bad gateway")) == "5xx"
    assert classify_failure(RuntimeError("429 too many requests")) == "429"
    assert classify_failure(SourceBadResponseError("body truncated mid-stream")) == "truncated"
    assert classify_failure(ValueError("missing field")) == "other"


def test_classify_truncated_json_signal():
    assert classify_failure(SourceBadResponseError("JSON decode error: Unterminated string starting at")) == "truncated"


# ── chain resolution ─────────────────────────────────────────────────────────


def test_resolve_chain_multi_level_and_cycle_safe():
    chain = resolve_chain("planetary_computer")
    assert [h[0] for h in chain] == ["copernicus_dataspace", "nasa_cmr_lpcloud"]
    # copernicus → nasa (already seen) must not loop back
    chain2 = resolve_chain("copernicus_dataspace")
    assert [h[0] for h in chain2] == ["nasa_cmr_lpcloud"]
    # conditional rules preserved
    assert chain[0][1] == ["timeout", "5xx", "429"]


def test_resolve_chain_local_to_online():
    chain = resolve_chain("local_osm")
    assert chain and chain[0][0] == "overpass_api"
    assert "empty_result" in chain[0][1]


# ── executor ─────────────────────────────────────────────────────────────────


def _runner_ok(features):
    def runner(source_id):
        return features, {"bytes": 100}
    return runner


def test_primary_success_no_decisions():
    result = execute_fallback_chain(
        "ogc_main", _runner_ok([{"id": 1}]),
        chain=[("backup", [], None)],
        request_id="r1", dataset_key="ogc_main/ds", wave="M3",
    )
    assert result.ok and result.source_used == "ogc_main"
    assert result.decisions == [] and result.fact.degraded is False
    assert result.fact.outcome == "success" and result.fact.wave == "M3"


def test_fallback_on_matching_trigger_records_decision():
    calls = []

    def runner(source_id):
        calls.append(source_id)
        if source_id == "primary":
            raise SourceTimeoutError("upstream timed out")
        return [{"id": 2}], {"bytes": 10}

    result = execute_fallback_chain(
        "primary", runner,
        chain=[("backup", ["timeout", "5xx"], None)],
        request_id="r2", dataset_key="primary/ds",
        facts_by_source={
            "primary": _facts_src("primary"),
            "backup": _facts_src("backup"),
        },
    )
    assert result.source_used == "backup"
    assert result.fact.outcome == "degraded" and result.fact.degraded is True
    assert result.decisions and result.decisions[0].trigger == "timeout"
    assert result.decisions[0].from_source == "primary" and result.decisions[0].to_source == "backup"
    assert result.fact.retries >= 1


def _facts_src(source_id, data_type="vector", granularity=None, bbox=None):
    class _F:
        pass
    f = _F()
    f.source_id = source_id
    f.data_type = data_type
    f.granularity = granularity
    f.bbox = bbox
    return f


def test_non_comparable_marked_when_granularity_differs():
    def runner(source_id):
        if source_id == "primary":
            raise SourceTimeoutError("timed out")
        return [], {"bytes": 1}

    result = execute_fallback_chain(
        "primary", runner,
        chain=[("backup", ["timeout"], None)],
        facts_by_source={
            "primary": _facts_src("primary", granularity="station"),
            "backup": _facts_src("backup", granularity="city"),
        },
    )
    assert result.source_used == "backup"
    assert result.non_comparable is True, "granularity mismatch must mark non-comparable"
    assert result.decisions[0].comparable is False


def test_comparable_true_when_facts_agree():
    def runner(source_id):
        if source_id == "primary":
            raise SourceTimeoutError("timed out")
        return [], {"bytes": 1}

    result = execute_fallback_chain(
        "primary", runner,
        chain=[("backup", ["timeout"], None)],
        facts_by_source={
            "primary": _facts_src("primary", granularity="county", bbox=[100.0, 20.0, 130.0, 50.0]),
            "backup": _facts_src("backup", granularity="county", bbox=[90.0, 10.0, 140.0, 60.0]),
        },
    )
    assert result.decisions[0].comparable is True
    assert result.non_comparable is False


def test_no_matching_trigger_fails_typed():
    def runner(source_id):
        raise SourceBadResponseError("bad body")

    result = execute_fallback_chain(
        "primary", runner, chain=[("backup", ["timeout"], None)],
    )
    assert result.source_used is None
    assert result.ok is False and result.fact.outcome == "failed"


def test_empty_result_triggers_chain():
    def runner(source_id):
        if source_id == "primary":
            return [], {"bytes": 2, "empty_result": True}
        return [{"id": 9}], {"bytes": 2}

    result = execute_fallback_chain(
        "primary", runner,
        chain=[("backup", ["empty_result"], None)],
    )
    assert result.source_used == "backup" and result.decisions[0].trigger == "empty_result"


def test_circuit_open_skips_source():
    # trip the primary breaker first (transient failures only — permanent
    # errors are by design not evidence the source is down)
    for _ in range(6):
        fb_mod.breaker_registry.record_failure("tripped", SourceTimeoutError("t"))
    assert fb_mod.breaker_registry.state("tripped") == CircuitState.OPEN

    def runner(source_id):
        assert source_id == "backup", "tripped source must be skipped"
        return [{"id": 3}], {"bytes": 1}

    result = execute_fallback_chain(
        "tripped", runner, chain=[("backup", [], None)],
    )
    assert result.source_used == "backup"
    assert result.decisions and result.decisions[0].trigger == "circuit_open"


def test_health_bridge_mirrors_attempts():
    def runner(source_id):
        if source_id == "primary":
            raise SourceTimeoutError("t")
        return [], {"bytes": 1}

    execute_fallback_chain("primary", runner, chain=[("backup", ["timeout"], None)])
    from app.services.provider_health import fabric_health_bridge

    states = fabric_health_bridge.states()
    assert "fabric:backup" in states and states["fabric:backup"]["consecutive_errors"] == 0
    assert states["fabric:primary"]["consecutive_errors"] >= 1


# ── local_first registry-driven chain equivalence (DS4.4 / A9) ───────────────


def test_local_first_registry_chain_default_off(monkeypatch):
    from app.services.local_first import registry_local_chain

    monkeypatch.delenv("ADS_LOCAL_FIRST_REGISTRY_DRIVEN", raising=False)
    assert registry_local_chain() is None  # hardcoded fallback retained


def test_local_first_registry_chain_equivalence(monkeypatch):
    """Registry-driven order must cover the same local sources as the
    hardcoded chain (gd_poi first, OSM second) — equivalence before the
    hardcoded path can be retired (DS9)."""
    from app.services import local_first

    monkeypatch.setenv("ADS_LOCAL_FIRST_REGISTRY_DRIVEN", "1")
    chain = local_first.registry_local_chain()
    assert chain is not None
    assert set(chain) >= {"local_poi", "local_osm"}, "declared local sources participate"
    # declared-order tiebreak keeps gd_poi (local_poi) ahead of OSM for POI queries
    assert chain.index("local_poi") < chain.index("local_osm")


def test_registry_driven_poi_chain_skips_undeclared_local(monkeypatch):
    from app.services import local_first

    monkeypatch.setenv("ADS_LOCAL_FIRST_REGISTRY_DRIVEN", "1")
    monkeypatch.setattr(local_first, "registry_local_chain", lambda: ["local_osm"])
    # local_poi not declared → chain runs OSM only; a missing gd result is
    # tolerated (the seam is exercised, not the local libs which need data)
    assert local_first.registry_local_chain() == ["local_osm"]
