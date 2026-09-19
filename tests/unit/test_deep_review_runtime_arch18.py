"""ARCH-18: no-progress state has a single owner (GisProgressTracker).

Before: ``pi_no_progress`` kept a session streak dict, ``agent_pi_bridge``
kept a parallel ``_gis_progress_trackers`` dict, and the legacy engine kept
local per-turn counters with inline threshold logic. Now the tracker owns the
streak/threshold state machine, a shared bounded registry hands the SAME
tracker to both hosts, and the engine uses the shared counting/decision
helpers.
"""
from __future__ import annotations

import pytest

from app.services.chat.no_progress import (
    GisProgressTracker,
    _session_progress_trackers,
    clear_session_progress_tracker,
    get_session_progress_tracker,
    next_no_progress_streak,
    no_progress_should_stop,
)


@pytest.fixture(autouse=True)
def _clean_shared_registry():
    _session_progress_trackers.clear()
    yield
    _session_progress_trackers.clear()


def test_helpers_are_the_single_counting_rule():
    assert next_no_progress_streak(0, has_progress=False) == 1
    assert next_no_progress_streak(5, has_progress=True) == 0
    assert no_progress_should_stop(3, 3) is True
    assert no_progress_should_stop(2, 3) is False


def test_tracker_owns_streak_and_resets_on_progress():
    tracker = GisProgressTracker(no_progress_threshold=2)
    assert tracker.record_no_progress(has_progress=False) is False
    assert tracker.no_progress_streak == 1
    assert tracker.record_no_progress(has_progress=False) is True
    assert tracker.no_progress_streak == 2
    assert tracker.record_no_progress(has_progress=True) is False
    assert tracker.no_progress_streak == 0


def test_both_hosts_resolve_the_same_tracker():
    tracker = get_session_progress_tracker("s-shared")
    # Bridge-side observation (reason-code tracker) ...
    tracker.record_call("buffer", {"a": 1}, "ok", map_epoch="m1")

    from app.services.chat.pi_no_progress import (
        clear_pi_no_progress_streak,
        pi_no_progress_should_stop,
        pi_no_progress_streak,
    )

    # ... and the Pi hard-stop read/write the SAME state.
    assert get_session_progress_tracker("s-shared") is tracker
    pi_no_progress_should_stop("s-shared", ["unchanged_map:4"])
    assert tracker.no_progress_streak == 1
    assert pi_no_progress_streak("s-shared") == 1
    clear_pi_no_progress_streak("s-shared")
    assert pi_no_progress_streak("s-shared") == 0
    clear_session_progress_tracker("s-shared")


def test_pi_hard_stop_semantics_unchanged(monkeypatch):
    monkeypatch.setenv("LLM_NO_PROGRESS_THRESHOLD", "2")
    from app.services.chat.pi_no_progress import (
        pi_no_progress_should_stop,
        pi_no_progress_streak,
        pi_no_progress_threshold,
    )

    sid = "s-hard-stop"
    assert pi_no_progress_threshold() == 2
    assert pi_no_progress_should_stop(sid, []) is False
    assert pi_no_progress_should_stop(sid, ["unchanged_map:4"]) is False
    assert pi_no_progress_should_stop(sid, ["unchanged_map:5"]) is True
    assert pi_no_progress_streak(sid) == 2
    assert pi_no_progress_should_stop(sid, []) is False
    assert pi_no_progress_streak(sid) == 0
