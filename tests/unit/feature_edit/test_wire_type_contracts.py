"""Static consumer checks for edit records, independent of the global mypy gate."""
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]


def _check(tmp_path: Path, source: str) -> subprocess.CompletedProcess[str]:
    fixture = tmp_path / "feature_edit_consumer.py"
    fixture.write_text(source)
    return subprocess.run(
        [
            sys.executable, "-m", "mypy", "--follow-imports=skip",
            "--ignore-missing-imports", "--no-incremental",
            "app/contracts/geojson.py", "app/services/feature_edit/wire_types.py",
            str(fixture),
        ],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )


def test_valid_edit_wire_contracts(tmp_path: Path) -> None:
    result = _check(tmp_path, '''
from app.services.feature_edit.wire_types import (
    EditJournal, EditFinding, FeatureDelta, FieldProfileRecord, EditReceipt,
)
profile: FieldProfileRecord = {"name": "zone", "types": {"string": 3}, "enum": ["A"]}
finding: EditFinding = {"code": "enum_violation", "message": "outside vocabulary", "detail": {"allowed": ["A"]}}
delta: FeatureDelta = {"wfid": "wf1-a", "fields": {"zone": {"before": None, "after": "A", "before_absent": True}}}
journal: EditJournal = {"entries": [{"kind": "apply", "deltas": [delta], "content_revision_after": 2}], "head_seq": 1, "dropped": 0}
receipt: EditReceipt = {"entry_seq": 1, "kind": "apply", "changed_wfids": ["wf1-a"], "mapspec_revision": None}
''')
    assert result.returncode == 0, result.stdout + result.stderr


def test_invalid_edit_wire_contracts_are_rejected(tmp_path: Path) -> None:
    result = _check(tmp_path, '''
from app.services.feature_edit.wire_types import EditJournal, FeatureDelta, FieldProfileRecord, EditReceipt
profile: FieldProfileRecord = {"name": "zone", "types": {"string": "three"}}
delta: FeatureDelta = {"wfid": "wf1-a", "fields": {"zone": {"before": None, "after": "A", "before_absent": "yes"}}}
journal: EditJournal = {"entries": [{"kind": "delete"}], "head_seq": 1, "dropped": 0}
receipt: EditReceipt = {"entry_seq": "one", "changed_wfids": [12]}
missing_identity: FeatureDelta = {"fields": {}}
''')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'Missing key "wfid"' in result.stdout
    assert result.stdout.count("error:") == 6, result.stdout
    assert 'TypedDict item "kind"' in result.stdout
    assert 'TypedDict item "before_absent"' in result.stdout
