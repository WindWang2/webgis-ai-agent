"""载荷纪律测试：确定性截断、上限、幂等投影。"""
from __future__ import annotations

import json

from app.lib.runtime.clock import epoch_to_utc
from app.services.turn_journal.contracts import (
    MAX_DETAIL_BYTES,
    TurnEventRecord,
    sanitize_detail,
)


def _canon(obj) -> int:
    return len(json.dumps(obj, ensure_ascii=False, sort_keys=True,
                          default=str).encode("utf-8"))


def test_small_detail_passes_through() -> None:
    detail = {"tool": "query", "n": 3}
    assert sanitize_detail(detail) == detail


def test_oversized_detail_is_deterministically_truncated() -> None:
    detail = {"big": "x" * (MAX_DETAIL_BYTES * 4), "tool": "query"}
    first = sanitize_detail(detail)
    second = sanitize_detail(detail)
    assert first == second  # 确定性：重复 sanitize 不影响 event_id 判重
    assert _canon(first) <= MAX_DETAIL_BYTES + 512  # 摘要自身有界
    assert first.get("truncated") is True
    assert first.get("keys") == ["big", "tool"]
    assert first.get("tool") == "query"  # 小字段保留


def test_unserializable_detail_does_not_explode() -> None:
    circular: dict = {}
    circular["self"] = circular  # json 循环引用 → ValueError
    out = sanitize_detail(circular)
    assert out.get("truncated") is True
    assert out.get("reason") == "unserializable"


def test_to_row_kwargs_truncates_and_freezes() -> None:
    record = TurnEventRecord.from_epoch(
        "s", "map_mutated",
        at_epoch=1000.0,
        turn_id="t" * 100,
        event_id="e" * 200,
        note="n" * 400,
        detail={"revision": 7, "blob": "y" * 9999},
        mutation_revision=7,
    )
    row = record.to_row_kwargs()
    assert len(row["turn_id"]) <= 80
    assert len(row["event_id"]) <= 160
    assert len(row["note"]) <= 300
    assert row["mutation_revision"] == 7  # 凭证引用原样落列
    assert _canon(row["detail"]) <= MAX_DETAIL_BYTES + 512
    assert row["occurred_at"] == epoch_to_utc(1000.0).replace(tzinfo=None)
