"""Builtin standards packs + the process-wide registry (ADR-0200).

Registry is truth; the generated catalog (standards/catalog.py →
docs/cartography/standards-catalog.md) is a deterministic projection with a
drift gate — same doctrine as model/component catalogs.
"""
from __future__ import annotations

from threading import Lock

from app.lib.cartography.standards.pack import (
    StandardsPack,
    StandardsRegistry,
)
from app.lib.cartography.standards.packs.core import build_core_pack

_REGISTRY: StandardsRegistry | None = None
_REGISTRY_LOCK = Lock()


def get_standards_registry() -> StandardsRegistry:
    global _REGISTRY
    with _REGISTRY_LOCK:
        if _REGISTRY is None:
            registry = StandardsRegistry()
            registry.register(build_core_pack())
            _REGISTRY = registry
        return _REGISTRY


def get_core_pack() -> StandardsPack:
    pack = get_standards_registry().resolve("core")
    assert pack is not None  # builtin registration is unconditional
    return pack


__all__ = ["get_core_pack", "get_standards_registry"]
