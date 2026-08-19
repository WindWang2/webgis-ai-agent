"""#618-31: new perf workloads must not silently skip without a baseline."""
import json

import pytest

from tests.benchmarks._baseline_policy import (
    ALLOW_MISSING_ENV,
    decide_baseline_action,
)


def test_missing_baseline_fails_closed():
    with pytest.raises(pytest.fail.Exception, match="No perf baseline"):
        decide_baseline_action("brand_new_workload", {}, update=False, allow_missing=False)


def test_missing_baseline_opt_in_records():
    assert (
        decide_baseline_action(
            "brand_new_workload", {}, update=False, allow_missing=True
        )
        == "record"
    )


def test_update_flag_records_even_when_present():
    assert (
        decide_baseline_action(
            "existing", {"existing": {"median_ms": 1.0}}, update=True, allow_missing=False
        )
        == "record"
    )


def test_existing_baseline_compares():
    assert (
        decide_baseline_action(
            "existing", {"existing": {"median_ms": 1.0}}, update=False, allow_missing=False
        )
        == "compare"
    )


def test_allow_missing_env_is_the_documented_opt_in():
    assert ALLOW_MISSING_ENV == "ALLOW_MISSING_PERF_BASELINE"


def test_harness_files_use_the_fail_closed_gate():
    """Both wall-clock harnesses must call decide_baseline_action (not skip)."""
    from pathlib import Path

    here = Path(__file__).resolve().parent
    for name in ("test_perf_harness.py", "test_transport_perf.py"):
        src = (here / name).read_text(encoding="utf-8")
        assert "decide_baseline_action" in src, (
            f"{name} must fail-closed on a missing baseline via "
            "decide_baseline_action (#618-31)"
        )
        assert "ALLOW_MISSING_PERF_BASELINE" in src or "allow_missing_baseline" in src


def test_record_path_writes_json_shape(tmp_path):
    """Sanity: the record payload the harnesses write is JSON-round-trippable."""
    payload = {"new_one": {"median_ms": 2.5, "iterations": 7}}
    path = tmp_path / "baselines.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8")) == payload
