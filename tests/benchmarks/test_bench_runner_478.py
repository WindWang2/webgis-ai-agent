"""#478: bench_runner.py must be reproducible from a clean clone.

The script previously hardcoded a foreign author-machine worktree in its
import path and wrote results back there, so the committed before/after
evidence was unregenerable (`ModuleNotFoundError` everywhere else). These
tests pin the two properties that make the benchmark trustworthy:

1. it imports from THIS repository (no absolute foreign paths anywhere in
   the script source);
2. it actually runs end-to-end (BENCH_FAST=1 shrinks the scenarios) and can
   write its results to an explicit output path inside the current tree.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "bench_runner.py"


def test_script_has_no_hardcoded_foreign_paths():
    """No author-machine absolute paths may live in the runner source (#478)."""
    source = _SCRIPT.read_text(encoding="utf-8")
    assert "/home/" not in source, "bench_runner.py must not hardcode home paths"
    assert "wt-mvt" not in source, "bench_runner.py must not reference foreign worktrees"
    assert 'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))' in source or (
        "_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))" in source
    ), "bench_runner.py must anchor sys.path to its own location"


def test_script_imports_from_this_repo(monkeypatch):
    """Importing the runner resolves app.* against THIS checkout (#478)."""
    monkeypatch.syspath_prepend(str(_REPO_ROOT))
    import bench_runner  # noqa: F401  (import itself is the assertion)

    import app.services.mvt as mvt

    assert Path(mvt.__file__).is_relative_to(_REPO_ROOT)


@pytest.mark.perf
def test_bench_runner_fast_mode_runs_and_writes_output(tmp_path, monkeypatch):
    """BENCH_FAST=1 smoke run completes and writes results where told (#478).

    Runs as a subprocess exactly like a fresh checkout would: ``python
    bench_runner.py <out>``. Asserts the process exits 0, the JSON parses,
    and the historical scenario keys plus the production-route-body scenario
    are all present.
    """
    out = tmp_path / "bench_results.json"
    env = {**os.environ, "BENCH_FAST": "1", "PYTHONPATH": str(_REPO_ROOT)}
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), str(out)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(_REPO_ROOT),
        env=env,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    results = json.loads(out.read_text(encoding="utf-8"))
    for key in (
        "scenario1_100k_Point",
        "scenario2_10x10k",
        "scenario5_50_concurrent",
        "scenario7_overwrite_lifecycle",
        "scenario9_index_lru_eviction",
        "scenario11_production_route_body",
    ):
        assert key in results, f"missing scenario {key}"
    route = results["scenario11_production_route_body"]
    assert route["tile_bytes"] > 0
    assert route["warm_cache_hit_identical"] is True
    assert route["concurrent_identical"] is True
