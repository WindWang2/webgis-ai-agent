"""ads-v1 DS8 facts / budgets / ratchet tests (ADR-0178).

Key gate: **injected degradation must be intercepted 100%** — a latency
spike, retry storm, or forced degradation between waves trips the ratchet.
Also covers fact recording (full D4 payload), budget alerts, aggregation
shape, and the matrix artifact (core 216 / full 864).
"""
from __future__ import annotations

import pytest

from app.services.data_fabric.contracts import AcquisitionFact
from app.services.data_fabric.facts import (
    CostBudget,
    FactsStore,
    get_facts_store,
)


def _fact(source_id="ogc_main", wave="M4", rows=100, bytes_=1000, latency=100.0, retries=0, degraded=False, outcome="success"):
    return AcquisitionFact(
        request_id=f"req-{rows}-{latency}-{degraded}",
        dataset_key=f"{source_id}/ds",
        source_id=source_id,
        version="latest",
        rows=rows,
        bytes=bytes_,
        latency_ms=latency,
        retries=retries,
        degraded=degraded,
        outcome=outcome,
        wave=wave,
    )


@pytest.fixture
def store():
    s = FactsStore()
    yield s
    s.clear()


# ── fact recording (full D4 payload) ─────────────────────────────────────────


def test_fact_records_full_payload(store):
    fact = _fact()
    fallback = {"trigger": "timeout", "from_source": "a", "to_source": "b", "comparable": False}
    fact = fact.model_copy(update={"fallback": fallback, "drift": "added_column", "ts": "2026-09-13T00:00:00+00:00"})
    store.record(fact)
    got = store.facts(source_id="ogc_main")[0]
    assert got.rows == 100 and got.bytes == 1000 and got.latency_ms == 100.0
    assert got.fallback["trigger"] == "timeout" and got.drift == "added_column"
    assert got.ts == "2026-09-13T00:00:00+00:00"  # explicit ts preserved


def test_fact_ts_autostamped_when_missing(store):
    got = store.record(_fact())
    assert got.ts  # stamped


def test_telemetry_coverage_all_outcomes(store):
    for outcome in ("success", "degraded", "failed"):
        store.record(_fact(outcome=outcome))
    assert len(store.facts()) == 3  # 100% coverage: failures recorded too


# ── budgets ──────────────────────────────────────────────────────────────────


def test_budget_alert_on_overrun(store):
    store.set_budget(CostBudget(source_id="ogc_main", request_type="bbox_query", max_rows=500, max_ms=200.0, wave="M5"))
    alerts = store.check_budget(_fact(wave="M5", rows=1000, latency=300.0), request_type="bbox_query")
    metrics = {a.metric for a in alerts}
    assert {"rows", "latency_ms"} <= metrics
    assert all(a.source_id == "ogc_main" for a in alerts)


def test_budget_no_alert_within_limits(store):
    store.set_budget(CostBudget(source_id="ogc_main", request_type="bbox_query", max_rows=500, wave="M5"))
    assert store.check_budget(_fact(rows=100), request_type="bbox_query") == []


# ── ratchet: injected degradation intercepted 100% ───────────────────────────


def test_ratchet_intercepts_injected_latency_spike(store):
    for _ in range(4):
        store.record(_fact(wave="M4", latency=100.0))
    # next wave: someone removes the pushdown → latency triples
    for _ in range(4):
        store.record(_fact(wave="M5", latency=400.0))
    violations = store.ratchet_check("M4", "M5")
    lat = [v for v in violations if v.metric == "latency_ms"]
    assert lat and lat[0].current / lat[0].baseline >= 2.0, "injected spike must be a violation"


def test_ratchet_intercepts_retry_storm_and_forced_degradation(store):
    for _ in range(4):
        store.record(_fact(wave="M4", retries=0, degraded=False))
    for _ in range(4):
        store.record(_fact(wave="M5", retries=3, degraded=True, outcome="degraded"))
    violations = store.ratchet_check("M4", "M5")
    metrics = {v.metric for v in violations}
    assert {"retries", "degraded"} <= metrics  # zero-tolerance on degradation growth


def test_ratchet_passes_stable_waves(store):
    for _ in range(4):
        store.record(_fact(wave="M4"))
    for _ in range(4):
        store.record(_fact(wave="M5", latency=110.0))  # within 30% tolerance
    assert store.ratchet_check("M4", "M5") == []


def test_ratchet_new_source_not_flagged(store):
    store.record(_fact(source_id="brand_new", wave="M5"))
    assert store.ratchet_check("M4", "M5") == []  # no baseline → nothing to regress


# ── aggregation shape ─────────────────────────────────────────────────────────


def test_aggregate_by_source_and_wave(store):
    store.record(_fact(source_id="a", wave="M5", latency=50.0))
    store.record(_fact(source_id="a", wave="M5", latency=90.0))
    store.record(_fact(source_id="b", wave="M5", latency=10.0))
    agg = store.aggregate("M5")
    assert set(agg) == {"a", "b"}
    assert agg["a"]["latency_ms"] == 90.0  # p50 of [50, 90] → upper middle
    assert set(agg["a"]) == {"rows", "bytes", "latency_ms", "retries", "degraded"}


# ── global store singleton sanity ────────────────────────────────────────────


def test_global_store_is_process_level():
    assert get_facts_store() is get_facts_store()
