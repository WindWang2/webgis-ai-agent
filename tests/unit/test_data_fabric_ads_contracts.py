"""ads-v1 contract tests (DS0, ADR-0170): D1–D4 shape, JSON Schema dump, upgrade path.

The four contracts are frozen after DS0 (task book §2). These tests enforce:
- every contract serializes to JSON and back losslessly;
- the dumped JSON Schemas under docs/dev/ads-v1-contracts/ match the models
  (schema drift = red; re-dump via scripts/ads_dump_contracts.py);
- evolution is additive-only (extra optional fields are accepted and
  preserved — the v1 → v1.1 upgrade path).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.data_fabric import contracts as C
from app.schemas.data_fabric_schema import DatasetDescriptor

CONTRACT_DIR = Path(__file__).resolve().parents[2] / "docs" / "dev" / "ads-v1-contracts"


# ── D1 ───────────────────────────────────────────────────────────────────────


def _fabric_descriptor() -> DatasetDescriptor:
    return DatasetDescriptor(
        id="ds_lake_depth",
        source_type="ogc_api",
        title="Lake depth",
        crs="EPSG:4326",
        bbox=[100.0, 20.0, 130.0, 50.0],
        fields=[{"name": "depth", "type": "float"}],
    )


def test_d1_extends_fabric_descriptor_and_defaults_are_optional():
    d1 = C.D1DatasetDescriptor.from_fabric_descriptor(_fabric_descriptor())
    # base fields survive the upgrade seam
    assert d1.id == "ds_lake_depth"
    assert d1.crs == "EPSG:4326"
    # supply-side fields default honestly (no fabricated coverage/quality)
    assert d1.version == "latest"
    assert d1.version_pinned is False
    assert d1.temporal_coverage is None
    assert d1.quality_signals.verified is False
    assert d1.quality_signals.known_issues == []
    # D1 IS-A DatasetDescriptor: existing consumers keep working
    assert isinstance(d1, DatasetDescriptor)


def test_d1_roundtrip_with_all_fields():
    d1 = C.D1DatasetDescriptor.from_fabric_descriptor(
        _fabric_descriptor(),
        version="rev-42",
        version_pinned=True,
        temporal_coverage=C.TemporalCoverage(start="2015-01-01", end="2024-12-31", granularity="yearly"),
        granularity="county",
        license="CC-BY-4.0",
        freshness=C.FreshnessInfo(updated_at="2026-09-01", update_frequency="monthly"),
        quality_signals=C.QualitySignals(declared_crs="EPSG:4326", declared_completeness=0.97, known_issues=["gcj02_offset"], verified=True),
        cost_hint=C.CostHint(rows=120_000, bytes=8_400_000, latency_ms=900, local=False),
    )
    payload = json.loads(d1.model_dump_json())
    d1b = C.D1DatasetDescriptor(**payload)
    assert d1b == d1
    assert d1b.quality_signals.declared_completeness == 0.97
    assert d1b.cost_hint.rows == 120_000


def test_d1_additive_evolution_path():
    """v1 → v1.1 upgrade: unknown-but-added optional fields are preserved."""
    payload = json.loads(C.D1DatasetDescriptor.from_fabric_descriptor(_fabric_descriptor()).model_dump_json())
    payload["future_field_v1_1"] = {"anything": True}
    d1 = C.D1DatasetDescriptor(**payload)
    assert d1.model_dump()["future_field_v1_1"] == {"anything": True}


# ── D2 ───────────────────────────────────────────────────────────────────────


def _plan(plan_id: str = "plan-1") -> C.AcquisitionPlan:
    return C.AcquisitionPlan(
        plan_id=plan_id,
        dataset_key="ogc_api:ds_lake_depth",
        version="rev-42",
        steps=[
            C.AcquisitionStep(step_type="source_select", params={"source_id": "ogc_main"}),
            C.AcquisitionStep(step_type="bbox_clip", params={"bbox": [100.0, 20.0, 130.0, 50.0]}),
            C.AcquisitionStep(step_type="field_projection", params={"columns": ["depth"]}),
            C.AcquisitionStep(step_type="aggregate_pushdown", params={"agg": "count"}),
        ],
        cost_estimate=C.CostEstimate(rows=1200, bytes=90_000, latency_ms=250.0, quota=1.0),
        budget=C.AcquisitionBudget(max_rows=5000, max_ms=3000),
        explain="select ogc_main → pushdown bbox+projection+count",
    )


def test_d2_serializable_diffable_replayable():
    plan = _plan()
    payload = json.loads(plan.model_dump_json())
    restored = C.AcquisitionPlan(**payload)
    assert restored == plan
    assert plan.diff(restored) == {}
    # dataset pin differs → diff reports it (replay guard)
    other = _plan()
    other.version = "latest"
    assert plan.diff(other)["version"] == ("rev-42", "latest")
    other2 = _plan()
    other2.steps = other2.steps[:2]
    assert "steps" in plan.diff(other2)


def test_d2_unknown_step_type_rejected():
    with pytest.raises(Exception):
        C.AcquisitionPlan(plan_id="p", dataset_key="k", steps=[C.AcquisitionStep(step_type="drop_table")])


# ── D3 ───────────────────────────────────────────────────────────────────────


def test_d3_defaults_conservative_and_comparable_false():
    d = C.FallbackDecision(trigger="timeout", from_source="ogc_main", to_source="osm_local", reason="probe timeout x3")
    # 保守默认：换源默认不可比（下游必须标注），置信度 0.5
    assert d.comparable is False
    assert d.confidence == 0.5
    payload = json.loads(d.model_dump_json())
    assert C.FallbackDecision(**payload) == d


def test_d3_invalid_trigger_and_confidence_rejected():
    with pytest.raises(Exception):
        C.FallbackDecision(trigger="meteor", from_source="a", to_source="b")
    with pytest.raises(Exception):
        C.FallbackDecision(trigger="timeout", from_source="a", to_source="b", confidence=1.5)


# ── D4 ───────────────────────────────────────────────────────────────────────


def test_d4_fact_full_fields_roundtrip():
    fact = C.AcquisitionFact(
        request_id="req-7",
        dataset_key="ogc_api:ds_lake_depth",
        source_id="ogc_main",
        version="rev-42",
        rows=1200,
        bytes=90_000,
        latency_ms=250.0,
        retries=1,
        degraded=True,
        outcome="degraded",
        fallback=C.FallbackDecision(trigger="5xx", from_source="ogc_main", to_source="ogc_backup", reason="503", comparable=True),
        drift="CONTENT",
        wave="M1",
    )
    payload = json.loads(fact.model_dump_json())
    assert C.AcquisitionFact(**payload) == fact
    assert payload["fallback"]["comparable"] is True


def test_d4_outcome_enum_enforced():
    with pytest.raises(Exception):
        C.AcquisitionFact(request_id="r", dataset_key="d", outcome="fine")


# ── JSON Schema dumps (docs/dev/ads-v1-contracts/) ──────────────────────────


@pytest.mark.parametrize(
    "model,filename",
    [
        (C.D1DatasetDescriptor, "d1_dataset_descriptor.schema.json"),
        (C.AcquisitionPlan, "d2_acquisition_plan.schema.json"),
        (C.FallbackDecision, "d3_fallback_decision.schema.json"),
        (C.AcquisitionFact, "d4_acquisition_fact.schema.json"),
    ],
)
def test_contract_schemas_dumped_and_in_sync(model, filename):
    schema_path = CONTRACT_DIR / filename
    assert schema_path.exists(), f"{filename} missing — run scripts/ads_dump_contracts.py"
    dumped = json.loads(schema_path.read_text(encoding="utf-8"))
    # same decoration rule as scripts/ads_dump_contracts.py
    expected = model.model_json_schema()
    expected["$id"] = f"https://webgis-ai-agent.local/schemas/ads-v1/{filename}"
    expected["x-ads-contract-version"] = C.CONTRACTS_VERSION
    assert dumped == expected, f"{filename} drifted from the model — re-run scripts/ads_dump_contracts.py"
    assert dumped.get("$id", "").endswith(filename)


def test_contracts_version_marker_in_all_dumped_schemas():
    for path in sorted(CONTRACT_DIR.glob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        assert schema.get("title"), path.name
