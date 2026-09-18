"""Pi GIS no-progress streak (#1384 H03).

Kept out of ``agent_pi_bridge`` so that module stays inside the god-module
ratchet slack. Callers still cancel the live turn from the bridge.
"""
from __future__ import annotations

import os
from typing import Dict, List

_GIS_TRACKER_MAX_SESSIONS = 64
_gis_no_progress_streaks: Dict[str, int] = {}


def pi_no_progress_threshold() -> int:
    """Same consecutive-round threshold ChatEngine uses."""
    try:
        from app.core.config import settings as _s
        default = int(getattr(_s, "LLM_NO_PROGRESS_THRESHOLD", 3))
    except Exception:  # noqa: BLE001
        default = 3
    try:
        thr = int(os.getenv("LLM_NO_PROGRESS_THRESHOLD", str(default)))
    except (TypeError, ValueError):
        thr = default
    return max(1, thr)


def pi_no_progress_should_stop(session_id: str, hints: List[str]) -> bool:
    """Increment/reset the per-session hint streak; True at ChatEngine threshold."""
    if not session_id:
        return False
    if not hints:
        _gis_no_progress_streaks.pop(session_id, None)
        return False
    if session_id in _gis_no_progress_streaks:
        streak = _gis_no_progress_streaks.pop(session_id) + 1
    else:
        if len(_gis_no_progress_streaks) >= _GIS_TRACKER_MAX_SESSIONS:
            _gis_no_progress_streaks.pop(next(iter(_gis_no_progress_streaks)))
        streak = 1
    _gis_no_progress_streaks[session_id] = streak
    return streak >= pi_no_progress_threshold()


def clear_pi_no_progress_streak(session_id: str) -> None:
    _gis_no_progress_streaks.pop(session_id, None)


def pi_no_progress_streak(session_id: str) -> int:
    return _gis_no_progress_streaks.get(session_id, 0)
