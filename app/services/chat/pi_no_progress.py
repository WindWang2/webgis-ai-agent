"""Pi GIS no-progress streak (#1384 H03) — ARCH-18 thin adapter.

The streak/state machine now lives on ``GisProgressTracker`` (single owner,
``app/services/chat/no_progress.py``) plus its bounded per-session registry.
Both Agent hosts resolve the SAME tracker for a session, so the Pi hard-stop
circuit and the bridge's reason-code tracker can no longer drift apart. This
module keeps the historical names for existing callers/tests.
"""
from __future__ import annotations

from typing import List

from app.services.chat.no_progress import (
    _session_progress_trackers,
    clear_session_progress_tracker,
    get_session_progress_tracker,
)
from app.services.chat.no_progress import no_progress_threshold as pi_no_progress_threshold

__all__ = [
    "pi_no_progress_threshold",
    "pi_no_progress_should_stop",
    "clear_pi_no_progress_streak",
    "pi_no_progress_streak",
]

#: Back-compat view of the shared registry (tests clear it between cases).
_gis_no_progress_streaks = _session_progress_trackers


def pi_no_progress_should_stop(session_id: str, hints: List[str]) -> bool:
    """Increment/reset the per-session streak; True at the shared threshold."""
    if not session_id:
        return False
    tracker = get_session_progress_tracker(session_id)
    return tracker.record_no_progress(has_progress=not hints)


def clear_pi_no_progress_streak(session_id: str) -> None:
    clear_session_progress_tracker(session_id)


def pi_no_progress_streak(session_id: str) -> int:
    tracker = _session_progress_trackers.get(session_id)
    return int(getattr(tracker, "no_progress_streak", 0) or 0)
