"""Supply the real lifespan tool registry to catalog projection tests."""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def catalog_registry_instance(request):
    if not request.module.__name__.split(".")[-1].startswith("test_execution_catalog"):
        return None
    from app.tools.registry import ToolRegistry
    from app.tools import init_tools
    registry = ToolRegistry()
    init_tools(registry)
    return registry


@pytest.fixture(autouse=True)
def catalog_lifespan_registry(catalog_registry_instance, _reset_chat_engine_singletons, monkeypatch):
    if catalog_registry_instance is not None:
        from app import agent_pi_bridge
        monkeypatch.setattr(agent_pi_bridge, "_tool_registry", catalog_registry_instance)
