"""Bounded, owner-scoped journey diagnostic report contracts."""
from __future__ import annotations

import copy
import json

from app.lib.harness.replay.journey import render_journey_diagnostic
from tests.harness_replay.test_journey_evidence import _trace_without_render


def test_diagnostic_is_bounded_redacted_and_actionable():
    trace = copy.deepcopy(_trace_without_render())
    trace["user_input"] = "敏感用户文本：导出我的私有学校数据"
    trace["chain"]["stages"][2]["arguments"] = {
        "api_key": "sk-diagnostic-secret",
        "url": "https://user:password@example.invalid/private",
    }
    trace["chain"]["stages"][4]["ref"] = "ref:geojson-diagnostic-secret-ref"

    payload = render_journey_diagnostic(
        trace,
        session_id="sess-journey",
        turn_id="turn-journey",
        max_bytes=2048,
    )
    encoded = payload.encode("utf-8")
    report = json.loads(payload)

    assert len(encoded) <= 2048
    assert report["truncated"] is True
    assert report["last_valid_stage"] == "mutation"
    assert report["runtime_fingerprint"]["registry_digest"] == "registry-v1"
    assert any(
        item["stage"] == "render_observation"
        for item in report["missing_evidence"]
    )
    assert report["recovery"] == {
        "eligible": True,
        "action": "reobserve",
        "automatic": False,
        "reason_code": "render_observation_absent",
    }
    for forbidden in (
        "sk-diagnostic-secret", "password@example", "敏感用户文本",
        "diagnostic-secret-ref",
    ):
        assert forbidden not in payload


def test_diagnostic_denies_cross_owner_read_without_echoing_ids():
    payload = render_journey_diagnostic(
        _trace_without_render(),
        session_id="sess-other",
        turn_id="turn-other",
        max_bytes=2048,
    )
    report = json.loads(payload)

    assert report["overall"] == "blocked"
    assert report["reason_code"] == "permission_denied"
    assert "identity" not in report
    assert "sess-journey" not in payload
    assert "turn-journey" not in payload


def test_diagnostic_cli_runs_local_json_contract(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    trace_path = tmp_path / "trace.json"
    trace_path.write_text(json.dumps(_trace_without_render()), encoding="utf-8")
    script = Path(__file__).resolve().parents[2] / "scripts" / "harness_journey_diagnose.py"
    command = [
        sys.executable, str(script), "--input", str(trace_path),
        "--session-id", "sess-journey", "--turn-id", "turn-journey",
        "--max-bytes", "2048",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=30)

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["schema_version"] == "gis_journey_diagnostic.v1"
    assert report["identity"]["session_id"] == "sess-journey"
    assert len(completed.stdout.encode("utf-8")) <= 2048

    denied_command = list(command)
    denied_command[5] = "sess-other"
    denied_command[7] = "turn-other"
    denied = subprocess.run(
        denied_command, capture_output=True, text=True, timeout=30,
    )
    assert denied.returncode == 2
    assert json.loads(denied.stdout)["reason_code"] == "permission_denied"
