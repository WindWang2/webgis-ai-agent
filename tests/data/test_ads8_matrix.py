"""ads-v1 DS8 validation matrix gate (ADR-0178).

Core batch (216 groups) must pass 100%; the full batch (864 groups) must
pass 100% too (both offline and deterministic). The committed CSV artifact
(docs/dev/ads-v1-validation-matrix.csv) is the acceptance ledger — this test
also verifies its shape and row count.
"""
from __future__ import annotations

import csv
from pathlib import Path

from app.services.data_fabric.matrix import CORE_REQUEST_TYPES, run_matrix

CSV_ARTIFACT = Path(__file__).resolve().parents[2] / "docs" / "dev" / "ads-v1-validation-matrix.csv"


def test_full_matrix_864_groups_all_pass():
    result = run_matrix()
    assert result["total"] == 12 * 12 * 3 * 2, "matrix dimensions drifted"
    assert result["core_total"] == 216 and result["core_passed"] == 216
    assert result["failed"] == 0, f"failed groups: {result['failed_groups']}"


def test_core_batch_is_216():
    assert len(CORE_REQUEST_TYPES) == 6


def test_committed_csv_artifact_in_sync():
    assert CSV_ARTIFACT.exists(), "run scripts/ads_matrix.py to regenerate the CSV"
    with open(CSV_ARTIFACT, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 864
    assert all(r["passed"] in {"True", "False"} for r in rows)
    assert sum(1 for r in rows if r["passed"] == "True") == 864, "committed CSV has failing groups"
