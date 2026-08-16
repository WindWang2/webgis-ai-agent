"""#507: parse_relative_window must accept arbitrary ``last_N_<unit>`` windows.

The main regex used a ``\\b`` between ``(\\d+)`` and ``_*(unit)`` — digit and
underscore are both word characters, so the boundary never matched, the whole
main branch was dead, and any N without a hardcoded preset fallback (e.g.
``last_2_days``, ``past_6_weeks``) raised ValueError.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services.temporal.filter import TemporalFilterEngine

REF = datetime(2026, 8, 16, 12, 0, 0, tzinfo=timezone.utc)


def _window_start(window: str):
    interval = TemporalFilterEngine.parse_relative_window(window, ref_time=REF)
    assert interval.start is not None and interval.end is not None
    start = interval.start.to_datetime() if hasattr(interval.start, "to_datetime") else interval.start
    return start


@pytest.mark.parametrize(
    "window,expected_delta",
    [
        ("last_1_days", timedelta(days=1)),
        ("last_2_days", timedelta(days=2)),  # the #507 reproducer (raised before)
        ("last_7_days", timedelta(days=7)),
        ("last_30_days", timedelta(days=30)),
        ("last_90_days", timedelta(days=90)),
        ("past_45_days", timedelta(days=45)),
        ("last_24_hours", timedelta(hours=24)),
        ("last_48_hours", timedelta(hours=48)),  # no preset fallback existed
        ("past_6_hours", timedelta(hours=6)),
        ("last_2_weeks", timedelta(weeks=2)),  # no preset fallback existed
        ("past_13_weeks", timedelta(weeks=13)),
        ("last_1_month", timedelta(days=30)),
        ("last_6_months", timedelta(days=180)),  # no preset fallback existed
        ("past_18_months", timedelta(days=540)),
        ("last_1_year", timedelta(days=365)),
        ("past_2_years", timedelta(days=730)),  # no preset fallback existed
        ("last-5-days", timedelta(days=5)),  # dash normalization still works
        ("LAST_3_DAYS", timedelta(days=3)),  # case normalization still works
    ],
)
def test_arbitrary_n_windows_parse(window, expected_delta):
    start = _window_start(window)
    assert REF - start == expected_delta


def test_invalid_windows_still_rejected():
    for bad in ("last_days", "last_x_days", "last_7_fortnights", "yesterday", "last__days"):
        with pytest.raises(ValueError):
            TemporalFilterEngine.parse_relative_window(bad, ref_time=REF)


def test_main_regex_branch_now_live():
    """Before #507 the main regex branch was dead code: everything matched via
    preset substrings or raised. Distinguish the branches with a value only
    the general regex can compute (15 days has no preset)."""
    assert REF - _window_start("last_15_days") == timedelta(days=15)
