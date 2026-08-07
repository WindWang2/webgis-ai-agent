"""Regression tests: batched reference/alias resolution (goal §8).

Previously every string argument of a tool dispatch cost one Redis
resolve_alias round-trip (N strings = N serialized RTTs). Now the registry
collects all string leaves and resolves them in ONE resolve_aliases call
(single HMGET). These tests pin: exactly one batched call per dispatch,
preserved alias/ref:/plain-string semantics, skip-key exclusion, and the
no-session fast path.
"""
import pytest
from typing import Any

from app.tools.registry import ToolRegistry


def _make_registry():
    reg = ToolRegistry()

    def echo_tool(name: str, path: Any, layer_id: str = "", extra: Any = "x"):
        return {"name": name, "path": path, "layer_id": layer_id, "extra": extra}

    reg.register("echo_tool", "echoes string args", echo_tool)
    return reg


@pytest.mark.asyncio
async def test_batch_resolution_is_one_round_trip(monkeypatch):
    """N string args → exactly ONE resolve_aliases call (no per-string RTTs)."""
    reg = _make_registry()
    calls = []

    async def spy_resolve_aliases(session_id, strings):
        calls.append((session_id, list(strings)))
        return {s: s for s in strings}  # no aliases

    monkeypatch.setattr(
        "app.tools.registry.session_data_manager.resolve_aliases", spy_resolve_aliases
    )
    result = await reg.dispatch(
        "echo_tool",
        {"name": "n1", "path": "data/a.tif", "layer_id": "L1", "extra": "e1"},
        session_id="sess",
    )
    # layer_id is a skip key → excluded from the batch
    assert calls == [("sess", ["n1", "data/a.tif", "e1"])]
    assert result == {"name": "n1", "path": "data/a.tif", "layer_id": "L1", "extra": "e1"}


@pytest.mark.asyncio
async def test_no_session_skips_resolution_entirely(monkeypatch):
    """Without a session_id no Redis call happens at all."""
    reg = _make_registry()
    calls = []

    async def spy_resolve_aliases(session_id, strings):
        calls.append((session_id, list(strings)))
        return {s: s for s in strings}

    monkeypatch.setattr(
        "app.tools.registry.session_data_manager.resolve_aliases", spy_resolve_aliases
    )
    result = await reg.dispatch(
        "echo_tool", {"name": "n1", "path": "ref:data-9", "extra": "e1"}, session_id=None
    )
    assert calls == []
    # ref: strings stay literal when there is no session to resolve against
    assert result["path"] == "ref:data-9"


@pytest.mark.asyncio
async def test_alias_argument_resolves_to_data(monkeypatch):
    """A registered alias must still be replaced with the referenced data."""
    reg = _make_registry()

    async def spy_resolve_aliases(session_id, strings):
        return {s: ("ref:data-1" if s == "my-layer" else s) for s in strings}

    async def spy_get(session_id, ref_or_alias):
        if ref_or_alias == "my-layer":
            return {"type": "FeatureCollection", "features": []}
        return None

    async def spy_list_refs(session_id):
        return {"ref:data-1": "my-layer"}

    monkeypatch.setattr("app.tools.registry.session_data_manager.resolve_aliases", spy_resolve_aliases)
    monkeypatch.setattr("app.tools.registry.session_data_manager.get", spy_get)
    monkeypatch.setattr("app.tools.registry.session_data_manager.list_refs", spy_list_refs)

    result = await reg.dispatch(
        "echo_tool", {"name": "n1", "path": "my-layer", "layer_id": "L1", "extra": "e1"},
        session_id="sess",
    )
    assert result["path"] == {"type": "FeatureCollection", "features": []}


@pytest.mark.asyncio
async def test_ref_prefix_argument_resolves_to_data(monkeypatch):
    """ref:xxx arguments resolve even without an alias mapping."""
    reg = _make_registry()

    async def spy_resolve_aliases(session_id, strings):
        return {s: s for s in strings}  # no aliases — ref: handled via prefix

    async def spy_get(session_id, ref_or_alias):
        if ref_or_alias == "ref:data-1":
            return [1, 2, 3]
        return None

    monkeypatch.setattr("app.tools.registry.session_data_manager.resolve_aliases", spy_resolve_aliases)
    monkeypatch.setattr("app.tools.registry.session_data_manager.get", spy_get)

    result = await reg.dispatch(
        "echo_tool", {"name": "n1", "path": "ref:data-1", "layer_id": "L1", "extra": "e1"},
        session_id="sess",
    )
    assert result["path"] == [1, 2, 3]


@pytest.mark.asyncio
async def test_missing_ref_raises_helpful_error(monkeypatch):
    """A missing ref must still produce the self-healing error with available refs."""
    reg = _make_registry()

    async def spy_resolve_aliases(session_id, strings):
        return {s: s for s in strings}

    async def spy_get(session_id, ref_or_alias):
        return None

    async def spy_list_refs(session_id):
        return {"ref:data-1": "my-layer"}

    monkeypatch.setattr("app.tools.registry.session_data_manager.resolve_aliases", spy_resolve_aliases)
    monkeypatch.setattr("app.tools.registry.session_data_manager.get", spy_get)
    monkeypatch.setattr("app.tools.registry.session_data_manager.list_refs", spy_list_refs)

    result = await reg.dispatch(
        "echo_tool", {"name": "n1", "path": "ref:data-9", "layer_id": "L1", "extra": "e1"},
        session_id="sess",
    )
    assert result.get("success") is False
    message = str(result.get("summary") or result.get("message") or "")
    assert "无法找到引用数据或别名" in message
    assert "ref:data-1" in message  # available refs listed for self-healing


@pytest.mark.asyncio
async def test_nested_strings_are_batched(monkeypatch):
    """Strings nested in lists/dicts are collected and resolved in the same call."""
    reg = _make_registry()

    async def spy_resolve_aliases(session_id, strings):
        return {s: s for s in strings}

    monkeypatch.setattr("app.tools.registry.session_data_manager.resolve_aliases", spy_resolve_aliases)

    result = await reg.dispatch(
        "echo_tool",
        {"name": "n1", "path": "data/a.tif", "extra": ["x", {"deep": "y"}]},
        session_id="sess",
    )
    assert result["extra"] == ["x", {"deep": "y"}]
