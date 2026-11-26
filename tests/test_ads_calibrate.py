"""Regression: scripts/ads_calibrate.py derives the eval sample count from
tests/data/ads2_retrieval_eval.json instead of a hardcoded number.

The report title used to say "278 样本" literally; once the eval set is
regenerated with a different size the written docs/dev/ads-v1-calibration.md
silently reports a stale count.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "ads_calibrate.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("ads_calibrate_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_report_title_uses_actual_eval_sample_count(tmp_path, monkeypatch):
    mod = _load_script()
    n = 7
    eval_path = tmp_path / "ads2_retrieval_eval.json"
    eval_path.write_text(
        json.dumps(
            {
                "version": "9.9",
                "n": n,
                "samples": [{"query": f"q{i}", "expected": f"card{i}"} for i in range(n)],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "docs" / "dev" / "ads-v1-calibration.md"
    out.parent.mkdir(parents=True)

    monkeypatch.setattr(mod, "EVAL", eval_path)
    monkeypatch.setattr(mod, "OUT", out)
    monkeypatch.setattr(mod, "REPO", tmp_path)  # OUT.relative_to(REPO) in the summary print
    # The sweep itself is out of scope here: stub it so the test only exercises
    # the report text, with no global provider / retrieval-service side effects.
    monkeypatch.setattr(mod, "mrr", lambda weights, service, samples: 0.5)
    monkeypatch.setattr(mod, "get_retrieval_service", lambda: object())
    monkeypatch.setattr(mod, "register_provider", lambda provider: None)

    assert mod.main() == 0

    title = next(line for line in out.read_text(encoding="utf-8").splitlines() if "排序权重" in line)
    assert f"（{n} 样本" in title, title
    assert "278" not in title, title
