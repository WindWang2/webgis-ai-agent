"""Backend coverage is the union of every successful shard, never an average."""
from __future__ import annotations

from collections import Counter
import configparser
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import coverage
import pytest
import yaml

from scripts.combine_backend_coverage import DATA_FILE, MANIFEST_FILE, record_shard, validate_shards

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "combine_backend_coverage.py"
COMMIT = "a" * 40
RUN_ID = "12345"
SHARDS = list(range(1, 7))


def _jobs() -> dict:
    return yaml.safe_load((ROOT / ".github/workflows/production.yml").read_text())["jobs"]


def test_backend_shards_cover_every_test_file_once() -> None:
    job = _jobs()["test-backend"]
    run = next(step["run"] for step in job["steps"] if step["name"] == "Run Tests with Coverage")
    targets = re.findall(r'(\d+)\) TARGETS="([^"]+)"', run)
    assert [int(shard) for shard, _ in targets] == job["strategy"]["matrix"]["shard"]
    counts = Counter()
    for _, patterns in targets:
        for pattern in patterns.split():
            matches = glob.glob(str(ROOT / pattern))
            assert matches, f"Shard target does not exist: {pattern}"
            for match in matches:
                path = Path(match)
                counts.update(
                    [path] if path.is_file()
                    else set(path.rglob("test_*.py")) | set(path.rglob("*_test.py"))
                )
    expected = set((ROOT / "tests").rglob("test_*.py")) | set((ROOT / "tests").rglob("*_test.py"))
    assert set(counts) == expected, f"Unassigned tests: {sorted(expected - set(counts))}"
    assert {path: count for path, count in counts.items() if count != 1} == {}


def test_workflow_preserves_raw_data_and_requires_complete_aggregation() -> None:
    jobs = _jobs()
    steps = jobs["test-backend"]["steps"]
    install = next(step for step in steps if step["name"] == "Install Dependencies")
    assert "coverage==7.17.1" in install["run"]
    record = next(step for step in steps if step["name"] == "Record Coverage Shard")
    assert record["if"] == "always()"
    assert record["env"]["TEST_OUTCOME"] == "${{ steps.backend-tests.outcome }}"
    assert '--commit "$GITHUB_SHA"' in record["run"]
    assert '--run-id "$GITHUB_RUN_ID"' in record["run"]
    assert '--run-attempt "$GITHUB_RUN_ATTEMPT"' in record["run"]
    upload = next(step for step in steps if step["name"] == "Upload Coverage Data")
    assert upload["if"] == "always()"
    assert upload["with"]["include-hidden-files"] is True
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["overwrite"] is True
    assert upload["with"]["name"] == "backend-coverage-${{ matrix.shard }}"
    assert {DATA_FILE, MANIFEST_FILE} <= set(upload["with"]["path"].split())
    gate_steps = jobs["test-backend-gate"]["steps"]
    install = next(step for step in gate_steps if step["name"] == "Install Coverage")
    assert install["run"] == "pip install -c requirements.lock coverage==7.17.1"
    download = next(step for step in gate_steps if step["name"] == "Download Coverage Data")
    assert download["with"] == {"pattern": "backend-coverage-*", "path": "coverage-shards"}
    enforce = next(step for step in gate_steps if step["name"] == "Enforce Combined Backend Coverage")
    assert "if" not in enforce and not enforce.get("continue-on-error")
    expected_shards = " ".join(map(str, jobs["test-backend"]["strategy"]["matrix"]["shard"]))
    assert f"--shards {expected_shards} " in enforce["run"]
    assert '--commit "$GITHUB_SHA"' in enforce["run"]
    assert '--run-id "$GITHUB_RUN_ID"' in enforce["run"]
    assert '--run-attempt "$GITHUB_RUN_ATTEMPT"' in enforce["run"]
    assert "--fail-under 75" in enforce["run"]
    combined_upload = next(step for step in gate_steps if step["name"] == "Upload Combined Coverage XML")
    assert combined_upload["with"]["overwrite"] is True


def test_coverage_configuration_counts_unimported_namespace_files() -> None:
    config = configparser.ConfigParser()
    config.read(ROOT / ".coveragerc")
    assert config["run"]["source"] == "app"
    assert config.getboolean("run", "relative_files")
    assert config.getboolean("report", "include_namespace_packages")


def _coverage_child_env() -> dict[str, str]:
    # These fixtures intentionally measure a tiny application in another cwd.
    # Inherit neither a parent pytest-cov startup hook nor its data destination.
    return {
        key: value for key, value in os.environ.items()
        if not key.startswith(("COV_CORE_", "COVERAGE_"))
    }


def test_coverage_fixture_children_do_not_inherit_parent_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("COVERAGE_FILE", "COVERAGE_PROCESS_START", "COV_CORE_SOURCE", "COV_CORE_DATAFILE"):
        monkeypatch.setenv(key, "parent-tracer")
    monkeypatch.setenv("BACKEND_COVERAGE_TEST_SENTINEL", "keep")
    env = _coverage_child_env()
    assert not any(key.startswith(("COV_CORE_", "COVERAGE_")) for key in env)
    assert env["BACKEND_COVERAGE_TEST_SENTINEL"] == "keep"


@pytest.fixture
def artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    shutil.copyfile(ROOT / ".coveragerc", tmp_path / ".coveragerc")
    (tmp_path / "app/namespace").mkdir(parents=True)
    (tmp_path / "app/__init__.py").write_text("")
    (tmp_path / "app/a.py").write_text("a = 1\nb = 2\nc = 3\n")
    (tmp_path / "app/b.py").write_text("d = 4\ne = 5\nf = 6\n")
    # No __init__.py and never executed: these two statements still count.
    (tmp_path / "app/namespace/unimported.py").write_text("g = 7\nh = 8\n")
    directory = tmp_path / "artifacts"
    directory.mkdir()
    for shard in SHARDS:
        module = "a" if shard <= 3 else "b"
        result = subprocess.run(
            [sys.executable, "-m", "coverage", "run", "--rcfile=.coveragerc", f"app/{module}.py"],
            env=_coverage_child_env(), capture_output=True, text=True, check=False, timeout=20,
        )
        assert result.returncode == 0, result.stderr
        record_shard(shard, COMMIT, "success", RUN_ID, 1)
        shard_dir = directory / f"backend-coverage-{shard}"
        shard_dir.mkdir()
        for name in (DATA_FILE, MANIFEST_FILE):
            shutil.copyfile(name, shard_dir / name)
    return directory


def _combine(artifacts: Path, attempt: int = 1) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "combine", "--artifacts-dir", str(artifacts),
         "--shards", *map(str, SHARDS), "--commit", COMMIT, "--run-id", RUN_ID,
         "--run-attempt", str(attempt), "--fail-under", "75"],
        env=_coverage_child_env(), capture_output=True, text=True, check=False, timeout=20,
    )


def _edit_manifest(artifacts: Path, **updates: str | int | bool) -> None:
    path = artifacts / "backend-coverage-6" / MANIFEST_FILE
    manifest = json.loads(path.read_text())
    manifest.update(updates)
    path.write_text(json.dumps(manifest))


def test_union_passes_75_with_unexecuted_namespace_in_denominator(artifacts: Path) -> None:
    result = _combine(artifacts)
    assert result.returncode == 0, result.stdout + result.stderr
    report = ET.parse("coverage.xml").getroot()
    assert report.attrib["lines-valid"] == "8"
    assert report.attrib["lines-covered"] == "6"
    assert float(report.attrib["line-rate"]) == 0.75
    assert "namespace/unimported.py" in Path("coverage.xml").read_text()
    # Shard data is preserved, not consumed by combine.
    assert len(list(artifacts.glob("*/.coverage"))) == 6


def test_unmeasured_source_files_cannot_disappear_from_combined_denominator(artifacts: Path) -> None:
    # A module not even present in raw coverage must still be counted. This also
    # ensures aggregation does not execute it to populate the coverage database.
    Path("app/namespace/not_recorded.py").write_text("raise RuntimeError('never import me')\n")
    result = _combine(artifacts)
    assert result.returncode == 1
    report = ET.parse("coverage.xml").getroot()
    assert report.attrib["lines-valid"] == "9"
    assert report.attrib["lines-covered"] == "6"
    assert "66.67%" in result.stdout


def test_existing_coverage_omissions_are_preserved(artifacts: Path) -> None:
    Path("app/skills").mkdir()
    Path("app/skills/existing_omit.py").write_text("raise RuntimeError('excluded skill')\n")
    assert _combine(artifacts).returncode == 0
    assert ET.parse("coverage.xml").getroot().attrib["lines-valid"] == "8"


def test_combined_coverage_below_75_is_a_failure(artifacts: Path) -> None:
    for shard in (4, 5, 6):
        directory = artifacts / f"backend-coverage-{shard}"
        shutil.copyfile(artifacts / "backend-coverage-1" / DATA_FILE, directory / DATA_FILE)
        manifest = json.loads((directory / MANIFEST_FILE).read_text())
        manifest["data_sha256"] = hashlib.sha256((directory / DATA_FILE).read_bytes()).hexdigest()
        (directory / MANIFEST_FILE).write_text(json.dumps(manifest))
    result = _combine(artifacts)
    assert result.returncode == 1
    assert "37.50%" in result.stdout
    assert Path("coverage.xml").is_file(), "Keep reports for diagnosing a real coverage deficit"


@pytest.mark.parametrize("outcome", ["failure", "cancelled", "skipped", "", "unknown"])
def test_failed_shard_cannot_be_hidden_by_other_coverage(artifacts: Path, outcome: str) -> None:
    _edit_manifest(artifacts, test_outcome=outcome)
    result = _combine(artifacts)
    assert result.returncode != 0
    assert "unsuccessful test outcome" in result.stderr


@pytest.mark.parametrize("update", [
    {"commit": "b" * 40}, {"shard": 5}, {"run_id": "stale-run"},
    {"run_attempt": 0}, {"run_attempt": 2}, {"run_attempt": True},
])
def test_wrong_shard_identity_is_rejected(artifacts: Path, update: dict[str, str | int | bool]) -> None:
    _edit_manifest(artifacts, **update)
    assert _combine(artifacts).returncode != 0


def test_successful_same_commit_shards_can_be_reused_on_failed_job_rerun(artifacts: Path) -> None:
    assert _combine(artifacts, attempt=2).returncode == 0


@pytest.mark.parametrize("missing", ["directory", DATA_FILE, MANIFEST_FILE])
def test_missing_shard_or_input_is_rejected(artifacts: Path, missing: str) -> None:
    directory = artifacts / "backend-coverage-6"
    if missing == "directory":
        shutil.rmtree(directory)
    else:
        (directory / missing).unlink()
    assert _combine(artifacts).returncode != 0


@pytest.mark.parametrize("kind", ["empty", "corrupt", "modified", "absolute-path"])
def test_invalid_raw_data_is_rejected_even_with_five_valid_shards(artifacts: Path, kind: str) -> None:
    path = artifacts / "backend-coverage-6" / DATA_FILE
    if kind in {"empty", "absolute-path"}:
        path.unlink()
        data = coverage.CoverageData(basename=str(path))
        data.add_lines({str(Path("app/a.py").resolve()): [1]} if kind == "absolute-path" else {})
        data.write()
    else:
        path.write_bytes(b"not a coverage database")
    if kind != "modified":
        _edit_manifest(artifacts, data_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    assert _combine(artifacts).returncode != 0


def test_unexpected_or_duplicate_shards_are_rejected(artifacts: Path) -> None:
    with pytest.raises(ValueError, match="unique"):
        validate_shards(artifacts, [1, 1], COMMIT, RUN_ID, 1)
    (artifacts / "backend-coverage-7").mkdir()
    assert _combine(artifacts).returncode != 0


def test_failed_shard_without_coverage_still_records_diagnostics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    record_shard(2, COMMIT, "failure", RUN_ID, 1)
    manifest = json.loads(Path(MANIFEST_FILE).read_text())
    assert manifest["data_sha256"] is None
    assert manifest["test_outcome"] == "failure"
