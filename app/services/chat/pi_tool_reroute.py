"""Legacy rewrite of status-as-analysis (kept for unit tests).

Pi dispatch now fail-closes extra keys on ``webgis_cartography_status`` via
``pi_native_surface.resolve_pi_tool_call`` (native schemas + empty-args
status). This helper is not called from the live dispatch path.
"""
from __future__ import annotations

from typing import Any, Mapping

STATUS_TOOL = "webgis_cartography_status"
INTENT_TOOL = "webgis_map_intent"

# Keys that mean "this is a data/analysis query", not a verdict pull.
_ANALYSIS_KEYS = frozenset(
    {
        "city",
        "topic",
        "scope",
        "query",
        "subject",
        "district",
        "poi",
        "category",
        "subtype",
        "name",
        "name_like",
    }
)
_PASSTHROUGH_KEYS = frozenset({"session_id"})


def reroute_cartography_status_misuse(
    tool_name: str,
    arguments: Mapping[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    """If status was called with analysis-shaped args, rewrite to map intent.

    Returns ``(tool_name, arguments)`` unchanged when the call is a genuine
    verdict pull (empty args, or only ``session_id``).
    """
    args = dict(arguments or {})
    if tool_name != STATUS_TOOL:
        return tool_name, args

    extra = {
        key: value
        for key, value in args.items()
        if key not in _PASSTHROUGH_KEYS and value not in (None, "")
    }
    analysis_keys = _ANALYSIS_KEYS.intersection(extra)
    if not analysis_keys:
        return tool_name, args

    city = str(
        extra.get("city") or extra.get("district") or extra.get("scope") or ""
    ).strip()
    topic = str(
        extra.get("topic")
        or extra.get("query")
        or extra.get("subject")
        or extra.get("name")
        or extra.get("name_like")
        or extra.get("subtype")
        or extra.get("poi")
        or extra.get("category")
        or ""
    ).strip()
    query = " ".join(part for part in (city, topic) if part) or "地图分析"

    intent_args: dict[str, Any] = {"query": query}
    if city:
        intent_args["scope_hint"] = city
    subject = extra.get("topic") or extra.get("subject") or extra.get("subtype")
    if subject:
        intent_args["subject_hint"] = str(subject).strip()
    return INTENT_TOOL, intent_args
