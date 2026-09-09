"""Fuzz 回归语料闸（Quality V2 W7）。

规则：
1. ``tests/fixtures/fuzz_corpus/*.json`` 中的每条样本（历史上发现过
   未按契约处理的输入，或手工构造的边界输入）必须被对应解析器**按契约
   拒绝或安全解析**——绝不允许 raise 非契约异常；
2. 语料是单一事实源的一部分：每条样本带 ``target``（解析器名）与
   ``expect``（none-safe / value-error）标注；
3. 新 crasher 治理：缩减为最小样本 → 落语料 → 修解析器或钉契约。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests"))

CORPUS_DIR = REPO / "tests/fixtures/fuzz_corpus"


def _corpus() -> list:
    if not CORPUS_DIR.exists():
        return []
    out = []
    for path in sorted(CORPUS_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for entry in payload if isinstance(payload, list) else [payload]:
            entry = dict(entry)
            entry["_source"] = path.name
            out.append(entry)
    return out


def test_corpus_entries_are_wellformed():
    entries = _corpus()
    assert entries, "fuzz 语料不得为空（语料是 crasher 治理的载体）"
    for e in entries:
        assert e.get("target") in ("parse_bbox", "safe_parse",
                                   "make_cache_key"), e
        assert e.get("expect") in ("none-safe", "value-error"), e
        assert "input" in e


def _run_target(target: str, raw_input: str):
    if target == "parse_bbox":
        from app.tools._utils import parse_bbox

        return ("ok", parse_bbox(raw_input))
    if target == "safe_parse":
        from app.lib.geo_processor.core import safe_parse

        try:
            decoded = json.loads(raw_input)
        except (json.JSONDecodeError, TypeError):
            decoded = raw_input
        return ("ok", safe_parse(decoded))
    if target == "make_cache_key":
        from app.lib.tool_cache import make_cache_key

        try:
            decoded = json.loads(raw_input)
        except (json.JSONDecodeError, TypeError):
            decoded = raw_input
        args = decoded if isinstance(decoded, dict) else {"v": decoded}
        return ("ok", make_cache_key("fuzz_target", args))
    raise AssertionError(f"未知 target: {target}")


def _entry_id(e: dict) -> str:
    label = e.get("id") or str(e.get("input", ""))[:16]
    return f"{e.get('_source')}:{label}"


@pytest.mark.parametrize("entry", _corpus(), ids=_entry_id)
def test_corpus_entry_handled_by_contract(entry):
    target = entry["target"]
    raw = entry["input"]
    if isinstance(raw, (dict, list)):
        raw = json.dumps(raw, ensure_ascii=False)
    if entry["expect"] == "value-error":
        with pytest.raises(ValueError):
            _run_target(target, raw)
    else:
        try:
            _run_target(target, raw)
        except ValueError:
            return  # value-error 也是可接受的安全拒绝
        except Exception as e:  # noqa: BLE001
            pytest.fail(
                f"语料样本 {entry.get('_source')}:{entry.get('id')} 打破 "
                f"{target} 契约：{type(e).__name__}: {e}")
