"""The replay record contracts constrain both producers and consumers."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from app.lib.harness.result_contracts import (
    baseline_entry,
    project_dispatch,
    project_mutation,
)
from app.lib.harness.replay.sanitize import digest_payload, sanitize_tool_result_ref


ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = [
    "app/contracts/gis_trace.py",
    "app/evaluation/runtime_metrics.py",
    "app/lib/harness/result_contracts.py",
    "app/lib/harness/replay/recording_contracts.py",
]


def test_projection_wire_omissions_and_nulls_are_unchanged() -> None:
    assert project_dispatch({"call_id": "c1", "tool": "buffer", "allowed": False}) == {
        "call_id": "c1", "allowed": False,
    }
    assert project_dispatch({
        "call_id": "c1", "tool": "buffer", "allowed": True, "capability": "",
    }) == {"call_id": "c1", "allowed": True, "capability": ""}
    assert project_mutation({"op": "unknown", "unknown_op": True}) == {
        "op": "unknown", "success": None,
        "spec_fingerprint": None, "error_code": None,
    }
    assert baseline_entry({"scenario_id": "s1", "ok": False}) == {
        "scenario_id": "s1", "ok": False, "replay_digest": None,
    }


def test_reference_projection_keeps_digest_shape_and_omits_absent_fields() -> None:
    raw = {"geojson_ref": "ref:geojson:fixture", "features": [{"private": "payload"}]}
    projected = sanitize_tool_result_ref(raw)
    assert projected == {
        "geojson_ref": "ref:geojson:fixture", "digest": digest_payload(raw),
    }
    assert json.loads(json.dumps(projected)) == projected
    assert sanitize_tool_result_ref(None) == {"digest": digest_payload(None)}


@pytest.mark.parametrize("valid", [True, False])
def test_contracts_accept_valid_records_and_reject_wrong_fields(tmp_path: Path, valid: bool) -> None:
    pytest.importorskip("mypy")
    snippet = tmp_path / "typed_record_consumer.py"
    imports = """
from app.contracts.gis_trace import TraceManifest
from app.evaluation.runtime_metrics import RoutingObservation
from app.lib.harness.result_contracts import MetricRow, ReceiptEntry, BenchReport
from app.lib.harness.replay.recording_contracts import RecordedToolCall
"""
    if valid:
        body = """
row: MetricRow = {"scene_id": "s", "check_id": "gate.pass", "value": 1.0}
receipt: ReceiptEntry = {"status": "ok", "ref_minted": True, "error_code": None}
manifest: TraceManifest = {"version": 1, "last_seq": 0, "segments": []}
routing: RoutingObservation = {"reason_codes": ["fallback"], "latency_s": 0.2}
call: RecordedToolCall = {"tool_name": "buffer", "arguments": {"distance": 5}}
report: BenchReport = {"green": 1, "entries": [{"scenario_id": "s", "ok": True}], "ratchet_rows": [row]}
score: float = report["ratchet_rows"][0]["value"]
minted: bool = receipt["ref_minted"]
"""
    else:
        body = """
row: MetricRow = {"scene_id": "s", "check_id": "gate.pass", "value": "one"}
receipt: ReceiptEntry = {"status": "ok", "ref_minted": True}
manifest: TraceManifest = {"version": 1, "last_seq": 0, "segments": [{"file": "a"}]}
routing: RoutingObservation = {"latency_s": "slow"}
call: RecordedToolCall = {"tool_name": "buffer", "args_truncated": "yes"}
report: BenchReport = {"green": 1, "ratchet_rows": [{"value": 1.0}]}
"""
    snippet.write_text(imports + body, encoding="utf-8")
    checked = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", "--follow-imports=skip",
         "--no-incremental", *CONTRACTS, str(snippet)],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    if valid:
        assert checked.returncode == 0, checked.stdout + checked.stderr
    else:
        assert checked.returncode == 1, checked.stdout + checked.stderr
        assert checked.stdout.count("error:") == 6, checked.stdout
        for field in ("value", "error_code", "TraceSegment", "latency_s", "args_truncated", "MetricRow"):
            assert field in checked.stdout
