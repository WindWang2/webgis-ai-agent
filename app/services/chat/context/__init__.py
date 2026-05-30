"""Chat context builder subpackage.

Aggregates geometry, schema inference, history budget control, and formatting modules.
"""
from __future__ import annotations

from .formatters import (
    _untrusted,
    _short,
    _xml_fence,
    format_selected_feature,
    format_style_summary,
    TAG_UNTRUSTED_LAYER_NAME,
    TAG_UNTRUSTED_FEATURE_PROPERTY,
    TAG_UNTRUSTED_LAYER_ALIAS,
    TAG_UNTRUSTED_LAYER_TYPE,
    TAG_UNTRUSTED_BASE_LAYER,
    TAG_UNTRUSTED_REGION_NAME,
    TAG_UNTRUSTED_USER_ACTION,
)
from .geometry import (
    _bbox_intersects,
    _bbox_contains,
    viewport_layer_relation,
)
from .layer_schema import (
    _layer_schema_cache,
    _LAYER_SCHEMA_CACHE_MAX,
    clear_layer_schema_cache,
    build_layer_schema,
    format_layer_schema,
    format_layer_lines,
)
from .session_overview import (
    _format_duration,
    build_session_overview,
)
from .history_compression import (
    HISTORY_TOKEN_BUDGET,
    HISTORY_MIN_TURNS,
    _estimate_tokens,
    _message_tokens,
    _group_into_turns,
    truncate_history_by_budget,
    _build_truncation_notice,
)

__all__ = [
    "_untrusted",
    "_short",
    "_xml_fence",
    "format_selected_feature",
    "format_style_summary",
    "TAG_UNTRUSTED_LAYER_NAME",
    "TAG_UNTRUSTED_FEATURE_PROPERTY",
    "TAG_UNTRUSTED_LAYER_ALIAS",
    "TAG_UNTRUSTED_LAYER_TYPE",
    "TAG_UNTRUSTED_BASE_LAYER",
    "TAG_UNTRUSTED_REGION_NAME",
    "TAG_UNTRUSTED_USER_ACTION",
    "_bbox_intersects",
    "_bbox_contains",
    "viewport_layer_relation",
    "_layer_schema_cache",
    "_LAYER_SCHEMA_CACHE_MAX",
    "clear_layer_schema_cache",
    "build_layer_schema",
    "format_layer_schema",
    "format_layer_lines",
    "_format_duration",
    "build_session_overview",
    "HISTORY_TOKEN_BUDGET",
    "HISTORY_MIN_TURNS",
    "_estimate_tokens",
    "_message_tokens",
    "_group_into_turns",
    "truncate_history_by_budget",
    "_build_truncation_notice",
]
