"""G07 golden-task local CLI contract."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_golden_cli_writes_mode_aware_evidence_report(tmp_path):
    script = Path(__file__).resolve().parents[3] / "scripts" / "harness_reliability_golden.py"
    output = tmp_path / "golden-report.json"
    completed = subprocess.run([
        sys.executable, str(script),
        "--task", "rel-02-heatmap-eligibility",
        "--out", str(output),
    ], capture_output=True, text=True, timeout=120)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["kind"] == "gis_harness_reliability_report.v1"
    assert report["seed"] == 20261107
    assert report["summary"]["acceptance_scenarios"] == 2
    assert report["summary"]["failed"] == 0
    assert report["tasks"][0]["execution_mode"] == "live_dispatch_stub_llm"
    assert report["tasks"][0]["coverage"]["browser_renderer"] == "not_run"
    assert report["production_chain"]["browser_renderer"] == "not_run"
    assert report["production_chain"]["live_services_opt_in"] == "not_run"
    assert {run["scenario_id"] for run in report["runs"]} == {
        "f02a-heatmap-method", "f02b-sparse-heatmap-degraded",
    }
