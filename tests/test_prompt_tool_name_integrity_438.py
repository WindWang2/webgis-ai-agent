"""#438: every tool name referenced by the system prompt or tool descriptions
must resolve to a registered tool (or a legacy alias).

The tool migrations (#204-#208) renamed tools but the prompt text and
cross-references inside other tools' descriptions were not fully swept:
prompt.py told the model to use `service_area` (registry: network_service_area
/ service_area_simple), map_view.py referenced `geocode_address` (registry:
geocode / geocode_cn) and osm.py referenced `search_route_cn` (registry:
plan_route). An LLM following them literally produced UNKNOWN_TOOL errors and
wasted rounds on self-healing.

The checker below scans the real registry (init_tools) and asserts:

  1. every backticked identifier-shaped token in SYSTEM_PROMPT + all tool /
     param descriptions resolves via the registry or LEGACY_TOOL_NAME_MAP
     (modulo a curated NON_TOOL allowlist of documented non-tool terms);
  2. the swept legacy names never reappear anywhere in those texts.
"""
from __future__ import annotations

import re

import pytest

from app.services.tool_dispatch_service import LEGACY_TOOL_NAME_MAP

# Backticked terms that are identifier-shaped but documented non-tools:
# ref ids, param names, sentinel tags, SSE event names, GeoJSON keywords.
NON_TOOL_BACKTICKED_TERMS = {
    "ref_id",
    "render_type",
    "return_geometry",
    "untrusted_layer_name",
    "untrusted_feature_property",
    "untrusted_layer_alias",
    "untrusted_layer_type",
    "untrusted_base_layer",
    "untrusted_region_name",
    "untrusted_user_action",
    "tool_executed",
    "tool_failed",
    "layer_toggled",
    "layer_removed",
    "base_layer_changed",
    "upload_completed",
}

# Names removed by the #204-#208 migrations that must never be referenced
# again in prompt/description text (the residue this issue swept).
FORBIDDEN_LEGACY_NAMES = {"service_area", "geocode_address", "search_route_cn"}


def _build_corpus(registry):
    """SYSTEM_PROMPT + every tool description + every param description."""
    from app.services.chat.prompt import SYSTEM_PROMPT

    texts = [("SYSTEM_PROMPT", SYSTEM_PROMPT)]
    schemas = registry.get_schemas_subset(set(registry.all_metadata().keys()))
    for s in schemas:
        fn = s["function"]
        texts.append((f"tool:{fn['name']}", fn.get("description") or ""))
        props = (fn.get("parameters") or {}).get("properties", {})
        for pname, pdef in props.items():
            if isinstance(pdef, dict) and isinstance(pdef.get("description"), str):
                texts.append((f"param:{fn['name']}.{pname}", pdef["description"]))
    return texts


def _known_names(registry):
    return set(registry.all_metadata().keys()) | set(LEGACY_TOOL_NAME_MAP.keys())


@pytest.fixture(scope="module")
def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    r = ToolRegistry()
    init_tools(r)
    return r


def test_registry_loaded(registry):
    assert len(registry.all_metadata()) > 100  # live registry, not a stub


def test_backticked_tool_references_resolve(registry):
    corpus = _build_corpus(registry)
    known = _known_names(registry)
    violations = []
    for where, text in corpus:
        for seg in re.findall(r"`([^`]+)`", text):
            for ident in re.findall(r"\b[a-z][a-z0-9_]{2,}\b", seg):
                if "_" not in ident:
                    continue
                if ident in known or ident in NON_TOOL_BACKTICKED_TERMS:
                    continue
                violations.append(f"{where}: `{ident}`")
    assert not violations, (
        "Backticked tool-name references that resolve to no registered tool "
        "/ alias (LLM would hit UNKNOWN_TOOL):\n" + "\n".join(violations)
    )


def test_swept_legacy_names_absent_everywhere(registry):
    corpus = _build_corpus(registry)
    for name in FORBIDDEN_LEGACY_NAMES:
        pattern = re.compile(rf"\b{name}\b")
        for where, text in corpus:
            assert not pattern.search(text), (
                f"{where} still references swept legacy tool name {name!r}"
            )


def test_replacement_names_are_registered(registry):
    known = _known_names(registry)
    for name in ("network_service_area", "geocode_cn", "plan_route"):
        assert name in known, name
