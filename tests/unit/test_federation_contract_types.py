"""Closed planner/evidence records retain their wire shapes and static errors."""
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from pydantic import TypeAdapter

from app.services.data_fabric.query.federation import (
    FederatedQueryRequest,
    plan_federated,
    _v6_plan_dicts,
)
from app.services.data_fabric.query.federated.bloom import build_bloom_from_rows
from app.services.data_fabric.query.federation_contracts import (
    BloomBuildRecord,
    FederatedPlanRecord,
    SourceQueryOptions,
    V6PlanRecord,
)


def test_plan_record_roundtrips_existing_wire_shape() -> None:
    plan = plan_federated(FederatedQueryRequest(
        left_source_id="left", left_dataset_id="cities",
        right_source_id="right", right_dataset_id="regions",
        join_field_left="region_id", join_field_right="id",
        group_by_right=["name"], aggregates=[{"func": "count"}],
    )).to_dict()
    assert TypeAdapter(FederatedPlanRecord).validate_python(plan) == plan
    assert plan["left"] == {"source_id": "left", "dataset_id": "cities"}
    assert plan["aggregates"] == [{"func": "count"}]
    assert "rejected_orders" not in plan


def test_bloom_evidence_and_query_options_have_concrete_fields() -> None:
    bloom, record = build_bloom_from_rows([{"id": 1}, {"id": 2}], "id")
    assert bloom is not None and 1 in bloom and 2 in bloom
    assert TypeAdapter(BloomBuildRecord).validate_python(record) == {
        "keys_added": 2, "saturated": False,
    }
    options: SourceQueryOptions = {
        "limit": 2, "offset": 0, "fields": ["id"],
        "where": {"op": "eq", "field": "id", "value": 1},
        "bbox": [-1.0, -2.0, 1.0, 2.0],
    }
    assert TypeAdapter(SourceQueryOptions).validate_python(options) == options


def test_contracts_reject_wrong_field_types_statically(tmp_path: Path) -> None:
    fixture = tmp_path / "federation_contract_errors.py"
    fixture.write_text('''from app.services.data_fabric.query.federation_contracts import (
    AggregateSpecRecord, BloomBuildRecord, SourceSideRecord,
)
good: AggregateSpecRecord = {"func": "sum", "field": "population"}
bad_func: AggregateSpecRecord = {"func": "median"}
bad_bloom: BloomBuildRecord = {"keys_added": "two", "saturated": False}
bad_source: SourceSideRecord = {"source_id": 42}
''')
    completed = subprocess.run(
        [sys.executable, "-m", "mypy", "--follow-imports=silent",
         "--ignore-missing-imports", "--no-incremental", "--show-error-codes",
         "--cache-dir", str(tmp_path / "cache"), str(fixture)],
        cwd=Path(__file__).resolve().parents[2],
        text=True, capture_output=True, check=False,
    )
    errors = [line for line in completed.stdout.splitlines() if ": error:" in line]
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert len(errors) == 3, completed.stdout + completed.stderr
    assert all("[typeddict-item]" in line for line in errors), errors


def test_v6_plan_keeps_unbounded_source_limit_as_null() -> None:
    from app.services.data_fabric.query.federated.logical import LogicalJoin, LogicalScan

    tree = LogicalJoin(
        join_kind="attribute_join",
        left=LogicalScan(source_id="a", dataset_id="left"),
        right=LogicalScan(source_id="b", dataset_id="right"),
        join_field_left="id", join_field_right="id",
    )
    records = _v6_plan_dicts(SimpleNamespace(tree=tree))
    assert len(records) == 1
    assert records[0]["left"]["fetch_limit"] is None
    assert TypeAdapter(list[V6PlanRecord]).validate_python(records) == records
