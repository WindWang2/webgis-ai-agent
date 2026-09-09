"""V6（ADR-0120 W5）legend 条目单源 —— 双语言 parity（后端侧）。

fixtures（tests/cartography/golden_corpus/legend_model/）与前端 vitest
共用；expected 为手写 oracle。oracle 计数与条目模型内部一致性也在此锁定。
"""
import json
from pathlib import Path

import pytest

from app.lib.cartography.render_scene import (
    derive_legend_items,
    format_legend_value,
    legend_oracle,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "golden_corpus" / "legend_model"


def _cases():
    cases = []
    for path in sorted(FIXTURE_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if "cases" in data:
            for i, case in enumerate(data["cases"]):
                cases.append((f"{path.stem}#{i}", case))
        else:
            cases.append((path.stem, data))
    return cases


@pytest.mark.parametrize("name,case", _cases(), ids=[c[0] for c in _cases()])
def test_legend_model_matches_golden(name, case):
    model = derive_legend_items(case["legendSpec"])
    expected = case["expected"]
    if expected is None:
        assert model is None, name
        return
    assert model is not None, name
    assert model["kind"] == expected["kind"], name
    assert model["title"] == expected["title"], name
    assert model["hasNodata"] == expected["hasNodata"], name
    assert model["entries"] == expected["entries"], name


def test_oracle_count_consistent_with_model():
    """oracle 数量口径 = 条目模型长度（对全部语料，含 nodata/bivariate）。"""
    for path in sorted(FIXTURE_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        cases = data.get("cases") or [data]
        for case in cases:
            spec = case["legendSpec"]
            model = derive_legend_items(spec)
            oracle = legend_oracle(spec)
            expected_count = len(model["entries"]) if model else 0
            assert oracle["entryCount"] == expected_count


def test_format_legend_value_mirrors_ts():
    assert format_legend_value(0) == "0"
    assert format_legend_value(10000) == "10k"
    assert format_legend_value(1500000) == "1.5M"
    assert format_legend_value(12345) == "1.2万" or format_legend_value(12345) == "12.3k"
    assert format_legend_value(0.5) == "0.5"
    assert format_legend_value(1234) == "1,234"
    assert format_legend_value(float("nan")) == "—"
    assert format_legend_value(0.0) == "0"


def test_nodata_only_categorical_counts_one():
    """oracle 历史口径回归：空 categories + nodata.color → 计数 1。"""
    oracle = legend_oracle(
        {"type": "categorical", "categories": [], "nodata": {"color": "#ccc"}}
    )
    assert oracle["entryCount"] == 1
    assert oracle["hasNodata"] is True
