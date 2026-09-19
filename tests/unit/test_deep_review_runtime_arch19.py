"""ARCH-19: promoted public symbols for cross-layer private imports.

``app/services/chat/pi_input_gate.py`` imported ``_field_type_adapter`` /
``_annotation_is_any`` / ``_is_args_oversized`` from ``app/tools/registry``,
and ``app/services/tool_dispatch_service.py`` imported ``_fingerprint_metadata``
/ ``_runtime_patch`` from ``app/tools/cartography_tools``. Public names now
exist and the private names are back-compat aliases.
"""
from __future__ import annotations


def test_registry_public_helpers_are_aliased():
    from app.tools import registry

    assert registry.field_type_adapter is registry._field_type_adapter
    assert registry.annotation_is_any is registry._annotation_is_any
    assert registry.is_args_oversized is registry._is_args_oversized


def test_registry_public_annotation_is_any_semantics():
    from typing import Any, Optional

    from app.tools.registry import annotation_is_any

    assert annotation_is_any(Any) is True
    assert annotation_is_any(Optional[Any]) is True
    assert annotation_is_any(str) is False
    assert annotation_is_any(Optional[str]) is False


def test_cartography_public_helpers_are_aliased():
    from app.tools import cartography_tools

    assert cartography_tools.fingerprint_metadata is cartography_tools._fingerprint_metadata
    assert cartography_tools.runtime_patch is cartography_tools._runtime_patch


def test_fingerprint_public_helper_matches_legacy_alias():
    from app.tools.cartography_tools import _fingerprint_metadata, fingerprint_metadata

    value = {"b": 2, "a": [1, 2, 3]}
    assert fingerprint_metadata(value, "profile") == _fingerprint_metadata(value, "profile")
    assert fingerprint_metadata(value, "profile").startswith("profile-sha256:")
