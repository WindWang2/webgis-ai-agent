"""clock 纪律测试：aware/naive 边界 + 历史 naive 兼容 + 非法输入。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.lib.runtime.clock import (
    epoch_to_utc,
    from_db_utc,
    iso_utc,
    to_db_utc,
    utc_now,
)


def test_utc_now_is_aware_utc() -> None:
    now = utc_now()
    assert now.tzinfo is timezone.utc


def test_epoch_to_utc_roundtrip() -> None:
    aware = epoch_to_utc(1_700_000_000.5)
    assert aware.tzinfo is timezone.utc
    assert aware.timestamp() == 1_700_000_000.5


def test_to_db_utc_drops_offset_correctly() -> None:
    aware = datetime(2026, 9, 29, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    naive = to_db_utc(aware)
    assert naive.tzinfo is None
    assert naive == datetime(2026, 9, 29, 4, 0)  # UTC 换算，非截断


def test_from_db_utc_lifts_naive_as_utc_compat() -> None:
    aware = from_db_utc(datetime(2026, 1, 1, 0, 0))
    assert aware is not None and aware.tzinfo is timezone.utc


def test_from_db_utc_normalizes_aware_to_utc() -> None:
    shifted = datetime(2026, 1, 1, 8, 0, tzinfo=timezone(timedelta(hours=8)))
    aware = from_db_utc(shifted)
    assert aware is not None
    assert aware.hour == 0 and aware.tzinfo is timezone.utc


def test_from_db_utc_handles_iso_strings_and_garbage() -> None:
    assert from_db_utc("2026-09-29T00:00:00+00:00") is not None
    assert from_db_utc("2026-09-29T00:00:00") is not None  # 旧 naive 串
    assert from_db_utc("not-a-date") is None
    assert from_db_utc("") is None
    assert from_db_utc(None) is None
    assert from_db_utc(12345) is None  # 非时间类型


def test_iso_utc_output_shape() -> None:
    text = iso_utc(datetime(2026, 1, 1, 0, 0))
    assert text is not None and text.endswith("+00:00")
    assert iso_utc(None) is None
