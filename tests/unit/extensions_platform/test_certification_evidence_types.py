"""Certification evidence has closed field types, without changing trust checks."""
from pathlib import Path
import subprocess
import sys

from pydantic import TypeAdapter

from app.extensions_platform.capability_certification import _check
from app.extensions_platform.certification_contracts import CapabilityCheckRecord


def test_check_wire_shape_and_status_are_unchanged() -> None:
    validator = TypeAdapter(CapabilityCheckRecord)
    cases = [(True, False, "pass"), (False, False, "fail"), (False, True, "warn")]
    for ok, warn, status in cases:
        result = _check("tests", "tool:sample", ok, "checked", warn_only=warn)
        assert validator.validate_python(result) == {
            "stage": "tests", "capability": "tool:sample",
            "status": status, "detail": "checked",
        }


def test_invalid_evidence_types_are_static_errors(tmp_path: Path) -> None:
    fixture = tmp_path / "bad_certification_evidence.py"
    fixture.write_text('''from app.extensions_platform.certification_contracts import CapabilityCheckRecord
valid: CapabilityCheckRecord = {"stage": "tests", "capability": "pack", "status": "pass", "detail": "ok"}
bad_status: CapabilityCheckRecord = {"stage": "tests", "capability": "pack", "status": True, "detail": "ok"}
bad_detail: CapabilityCheckRecord = {"stage": "tests", "capability": "pack", "status": "pass", "detail": 1}
''')
    completed = subprocess.run(
        [sys.executable, "-m", "mypy", "--follow-imports=silent",
         "--ignore-missing-imports", "--no-incremental", "--show-error-codes",
         "--cache-dir", str(tmp_path / "cache"), str(fixture)],
        cwd=Path(__file__).resolve().parents[3],
        text=True, capture_output=True, check=False,
    )
    errors = [line for line in completed.stdout.splitlines() if ": error:" in line]
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert len(errors) == 2, completed.stdout + completed.stderr
    assert all("[typeddict-item]" in line for line in errors), errors
