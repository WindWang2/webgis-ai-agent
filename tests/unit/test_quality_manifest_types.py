"""Registry quality projections expose stable, typed evidence records."""
from pathlib import Path
import subprocess
import sys

from pydantic import TypeAdapter

from app.lib.quality.manifest import compile_quality_manifest, gate
from app.lib.quality.manifest_contracts import (
    AlgorithmQualityRecord, ArtifactQualityRecord, CapabilityQualityRecord,
    GateReportRecord, RatchetReportRecord, ToolQualityRecord,
)


def test_registry_projection_records_match_their_wire_contracts() -> None:
    manifest = compile_quality_manifest()
    sections = (
        (list[ToolQualityRecord], manifest.tools),
        (list[AlgorithmQualityRecord], manifest.algorithms),
        (list[CapabilityQualityRecord], manifest.capabilities),
        (list[ArtifactQualityRecord], manifest.artifact_types),
        (GateReportRecord, manifest.gate_report),
        (RatchetReportRecord, manifest.ratchet_report),
    )
    for schema, value in sections:
        assert TypeAdapter(schema).validate_python(value) == value
    assert gate({}) == 1
    assert gate(manifest.gate_report) == (0 if manifest.gate_report["pass"] else 1)


def test_quality_record_field_types_are_checked_statically(tmp_path: Path) -> None:
    fixture = tmp_path / "bad_quality_records.py"
    fixture.write_text('''from app.lib.quality.manifest_contracts import GateReportRecord, FindingViolationRecord
valid: GateReportRecord = {"total": 2, "fields": {}, "pass": True}
bad_pass: GateReportRecord = {"total": 2, "fields": {}, "pass": "yes"}
bad_count: FindingViolationRecord = {"code": "TOOL_UNTESTED", "baseline": "one", "current": 2}
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
    assert len(errors) == 2, completed.stdout + completed.stderr
    assert all("[typeddict-item]" in line for line in errors), errors
