"""Fail-closed coverage aggregation for the six backend CI shards.

Each shard records its test outcome, commit and raw-data digest, including when
pytest fails. The required Backend Tests job validates every artifact before
combining line execution data and applying the full-application coverage floor.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import coverage
from coverage.exceptions import CoverageException


DATA_FILE = ".coverage"
MANIFEST_FILE = "coverage-manifest.json"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def record_shard(shard: int, commit: str, test_outcome: str, run_id: str, attempt: int) -> None:
    """Keep failure diagnostics too; only aggregation decides admissibility."""
    data = Path(DATA_FILE)
    manifest = {
        "shard": shard,
        "commit": commit,
        "test_outcome": test_outcome,
        "run_id": run_id,
        "run_attempt": attempt,
        "data_sha256": _digest(data) if data.is_file() else None,
    }
    Path(MANIFEST_FILE).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def validate_shards(
    artifacts_dir: Path, shards: list[int], commit: str, run_id: str, attempt: int,
) -> list[str]:
    if not shards or len(set(shards)) != len(shards):
        raise ValueError("Expected shard IDs must be nonempty and unique")
    expected = {f"backend-coverage-{shard}" for shard in shards}
    found = {path.name for path in artifacts_dir.iterdir()}
    if found != expected:
        raise ValueError(
            f"Coverage shard set mismatch: missing={sorted(expected - found)}, "
            f"unexpected={sorted(found - expected)}"
        )
    paths = []
    for shard in shards:
        directory = artifacts_dir / f"backend-coverage-{shard}"
        manifest = json.loads((directory / MANIFEST_FILE).read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or any(
            manifest.get(key) != value
            for key, value in {"shard": shard, "commit": commit, "run_id": run_id, "test_outcome": "success"}.items()
        ):
            raise ValueError(f"Shard {shard}: wrong identity, commit or unsuccessful test outcome")
        # A failed-job rerun may reuse successful shards from an earlier attempt
        # of this exact run/commit, but not a future or unidentifiable attempt.
        recorded_attempt = manifest.get("run_attempt")
        if type(recorded_attempt) is not int or not 1 <= recorded_attempt <= attempt:
            raise ValueError(f"Shard {shard}: invalid workflow run attempt")
        path = directory / DATA_FILE
        if not path.is_file() or manifest.get("data_sha256") != _digest(path):
            raise ValueError(f"Shard {shard}: missing coverage data or digest mismatch")
        # coverage combine normally warns and skips unreadable inputs. Validate
        # each database first so partial coverage can never satisfy this gate.
        data = coverage.CoverageData(basename=str(path))
        data.read()
        files = data.measured_files()
        if not files or not any(data.lines(filename) for filename in files):
            raise ValueError(f"Shard {shard}: empty coverage data")
        if any(
            Path(filename).is_absolute()
            or not filename.startswith("app/")
            or ".." in Path(filename).parts
            for filename in files
        ):
            raise ValueError(f"Shard {shard}: coverage paths must be relative to app/")
        paths.append(str(path))
    return paths


def combine_shards(
    artifacts_dir: Path, shards: list[int], commit: str, run_id: str, attempt: int, fail_under: float,
) -> int:
    paths = validate_shards(artifacts_dir, shards, commit, run_id, attempt)
    cov = coverage.Coverage(config_file=".coveragerc", data_file=DATA_FILE)
    cov.erase()
    cov.combine(data_paths=paths, strict=True, keep=True)
    cov.save()
    if cov.get_option("run:source") != ["app"]:
        raise ValueError("Backend coverage must account for the full app/ source tree")
    # Never derive the denominator solely from measured_files: a namespace
    # module absent from every shard still counts as uncovered. Explicit files
    # are parsed, never imported/executed, and retain the existing exclusions.
    source_files = sorted(str(path) for path in Path("app").rglob("*.py"))
    if not source_files:
        raise ValueError("No application source files found")
    omit = cov.get_option("run:omit")
    cov.xml_report(morfs=source_files, omit=omit, outfile="coverage.xml")
    total = cov.report(morfs=source_files, omit=omit, show_missing=True)
    if total < fail_under:
        print(f"FAIL Required combined coverage of {fail_under:g}% not reached: {total:.2f}%")
        return 1
    print(f"Combined backend coverage: {total:.2f}% (required {fail_under:g}%)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    record = commands.add_parser("record")
    record.add_argument("--shard", type=int, required=True)
    record.add_argument("--commit", required=True)
    record.add_argument("--test-outcome", required=True)
    combine = commands.add_parser("combine")
    combine.add_argument("--artifacts-dir", type=Path, required=True)
    combine.add_argument("--shards", type=int, nargs="+", required=True)
    combine.add_argument("--commit", required=True)
    combine.add_argument("--fail-under", type=float, default=75)
    for command in (record, combine):
        command.add_argument("--run-id", required=True)
        command.add_argument("--run-attempt", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "record":
            record_shard(args.shard, args.commit, args.test_outcome, args.run_id, args.run_attempt)
            return 0
        return combine_shards(
            args.artifacts_dir, args.shards, args.commit, args.run_id, args.run_attempt, args.fail_under,
        )
    except (OSError, ValueError, CoverageException) as exc:
        print(f"Backend coverage aggregation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
