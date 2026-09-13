"""ads-v1 DS6 semantic parsing eval gate (ADR-0176): ≥150 bilingual samples.

Accuracy over the labelled set must be ≥ 0.90 (provisional). Samples pin the
reference "now" (deterministic); the generator (scripts/ads_gen_time_eval.py)
self-checks every label at generation time — this test re-verifies the
parser against the committed labels.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.services.data_fabric.semantic.granularity import parse_granularity
from app.services.data_fabric.semantic.time_parser import parse_time_expr

EVAL = Path(__file__).resolve().parents[1] / "data" / "ads6_time_eval.json"


@pytest.fixture(scope="module")
def accuracy() -> dict:
    data = json.loads(EVAL.read_text(encoding="utf-8"))
    now = date.fromisoformat(data["reference_now"])
    correct = 0
    for s in data["samples"]:
        if s["kind"] == "time":
            r = parse_time_expr(s["expr"], now=now)
            ok = r is not None and r.start == s["start"] and r.end == s["end"] and r.granularity == s["granularity"]
        else:
            r = parse_granularity(s["expr"])
            ok = r is not None and r[0] == s["granularity"]
        correct += ok
    return {"n": len(data["samples"]), "accuracy": correct / len(data["samples"])}


def test_eval_set_is_big_enough():
    data = json.loads(EVAL.read_text(encoding="utf-8"))
    assert data["n"] >= 150, f"eval set shrunk to {data['n']} (need ≥150)"


def test_parsing_accuracy_threshold(accuracy):
    print(f"\n[ads6-eval] n={accuracy['n']} accuracy={accuracy['accuracy']:.4f}")
    assert accuracy["accuracy"] >= 0.90, f"accuracy {accuracy['accuracy']:.4f} < 0.90"
