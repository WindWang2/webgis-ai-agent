"""Deterministic text signals shared by planner fact projection."""
from __future__ import annotations
import re

_INTERPOLATION_QUERY_RE: re.Pattern[str] | None = None
_UNCERTAINTY_QUERY_RE: re.Pattern[str] | None = None


def _interpolation_query_signals(query: str) -> tuple[bool, str]:
    """Return (plan_interpolation_capability, algorithm_hint) for a query."""
    import re as _re

    global _INTERPOLATION_QUERY_RE
    if _INTERPOLATION_QUERY_RE is None:
        _INTERPOLATION_QUERY_RE = _re.compile(
            r"(克里金|kriging|插值|interpolat)", _re.I
        )
    q = query or ""
    if not _INTERPOLATION_QUERY_RE.search(q):
        return (False, "")
    hint = "interpolation.kriging" if _re.search(
        r"(克里金|kriging)", q, _re.I
    ) else ""
    return (True, hint)



def _uncertainty_demand(query: str) -> bool:
    """查询文本是否携带不确定性需求（方差/误差/置信…；纯词面、确定性）。"""
    import re as _re

    global _UNCERTAINTY_QUERY_RE
    if _UNCERTAINTY_QUERY_RE is None:
        _UNCERTAINTY_QUERY_RE = _re.compile(
            r"(不确定|方差|误差|置信|uncertaint|variance|confidence|error)", _re.I
        )
    return bool(_UNCERTAINTY_QUERY_RE.search(query or ""))
