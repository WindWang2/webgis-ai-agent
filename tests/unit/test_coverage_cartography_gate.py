"""Regression tests for scripts/coverage_cartography_gate.py."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from scripts import coverage_cartography_gate as gate


@pytest.mark.parametrize(
    ("lane_rc", "expected_rc"),
    [(0, 0), (7, 7)],
)
def test_default_json_temp_directory_is_removed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    lane_rc: int,
    expected_rc: int,
) -> None:
    """Default reports must not leave ac10-cov-* behind, even on lane failure."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    report_paths = []

    def fake_run_lane(json_path: Path, *, smoke: bool = False) -> int:
        report_paths.append(json_path)
        json_path.write_text(json.dumps({
            "files": {
                "app/lib/cartography/example.py": {
                    "summary": {"num_statements": 2, "covered_lines": 2},
                },
            },
        }), encoding="utf-8")
        return lane_rc

    monkeypatch.setattr(gate, "run_lane", fake_run_lane)

    assert gate.main([]) == expected_rc
    assert len(report_paths) == 1
    assert not report_paths[0].parent.exists()


def test_explicit_json_report_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "coverage.json"

    def fake_run_lane(json_path: Path, *, smoke: bool = False) -> int:
        assert json_path == report_path
        json_path.write_text(json.dumps({
            "files": {
                "app/lib/cartography/example.py": {
                    "summary": {"num_statements": 2, "covered_lines": 2},
                },
            },
        }), encoding="utf-8")
        return 0

    monkeypatch.setattr(gate, "run_lane", fake_run_lane)

    assert gate.main(["--json", str(report_path)]) == 0
    assert report_path.is_file()
