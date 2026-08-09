"""
Unit tests for ChatContextAssembler deep module.
"""
import pytest
from app.services.chat.context_assembler import ChatContextAssembler, ContextAssemblyResult
from app.services.chat.context_builder import compose_request_messages
from app.services.session_data import SessionDataManager, session_data_manager


class _MetadataStore:
    """Fake store that returns a fixed metadata bundle and counts metadata reads.

    Used to prove RUN-04/PERF-08: assemble() already has map_state/list_refs/
    event_log from get_session_metadata, so it must NOT trigger redundant
    get_map_state/list_refs/get_event_log calls on the global session_data_manager
    via build_map_state_summary.
    """

    def __init__(self, metadata: dict):
        self._metadata = metadata
        self.metadata_reads = 0

    def has_get_session_metadata(self):  # noqa: D401
        return True

    async def get_session_metadata(self, session_id):
        self.metadata_reads += 1
        return self._metadata

    # The protocol/legacy fallbacks the assemble() else-branch could touch:
    async def get_map_state(self, session_id):
        return self._metadata.get("map_state", {})

    async def list_refs(self, session_id):
        return self._metadata.get("list_refs", {})

    async def get_event_log(self, session_id):
        return self._metadata.get("event_log", [])


@pytest.mark.asyncio
async def test_assemble_empty_messages():
    assembler = ChatContextAssembler()
    result = await assembler.assemble("test_session", [])
    assert result.messages == []
    assert result.estimated_tokens == 0
    assert result.history_turns_included == 0
    assert result.layer_count == 0
    assert result.to_messages() == []


@pytest.mark.asyncio
async def test_assemble_basic_prompt():
    assembler = ChatContextAssembler()
    messages = [
        {"role": "system", "content": "You are a WebGIS AI agent."},
        {"role": "user", "content": "Hello!"},
    ]
    result = await assembler.assemble("test_session_1", messages)
    assert isinstance(result, ContextAssemblyResult)
    assert len(result.messages) >= 2
    assert result.messages[0]["role"] == "system"
    assert "WebGIS AI agent" in result.messages[0]["content"]
    assert "环境感知" in result.messages[0]["content"]
    assert result.history_turns_included == 1
    assert result.estimated_tokens > 0
    assert result.to_messages() == result.messages


@pytest.mark.asyncio
async def test_dependency_injection():
    custom_store = SessionDataManager()
    assembler = ChatContextAssembler(store=custom_store)
    messages = [
        {"role": "system", "content": "System prompt."},
        {"role": "user", "content": "Show me points."},
    ]
    result = await assembler.assemble("test_session_di", messages)
    assert result.layer_count == 0
    assert isinstance(result.estimated_tokens, int)


@pytest.mark.asyncio
async def test_adapter_parity():
    session_id = "test_session_parity"
    messages = [
        {"role": "system", "content": "System prompt."},
        {"role": "user", "content": "Show me hospital buffer zone."},
    ]
    legacy_res = await compose_request_messages(session_id, messages)
    assembler = ChatContextAssembler()
    deep_res = await assembler.assemble(session_id, messages)
    assert legacy_res == deep_res.to_messages()


@pytest.mark.asyncio
async def test_assemble_does_not_refetch_map_state(monkeypatch):
    """RUN-04 / PERF-08: assemble() gets map_state/list_refs/event_log from
    get_session_metadata, so build_map_state_summary must reuse them instead of
    re-fetching from the global session_data_manager every round.
    """
    fixed_meta = {
        "map_state": {"viewport": {"center": [116.4, 39.9], "zoom": 11}, "layers": []},
        "list_refs": {"ref:data-aaa": "医院"},
        "event_log": [{"event": "tool_executed", "data": {"tool": "geocode_cn"}}],
        "started_at": None,
    }
    fake_store = _MetadataStore(fixed_meta)
    assembler = ChatContextAssembler(store=fake_store)

    # Spy on the GLOBAL session_data_manager methods that build_map_state_summary
    # calls when _fetched is False. If the bug is present, these fire (a redundant
    # Redis/L1 round-trip per round); after the fix they must NOT be called.
    refetch_calls = {"get_map_state": 0, "list_refs": 0, "get_event_log": 0}

    async def _spy_map_state(sid, _real=session_data_manager.get_map_state):
        refetch_calls["get_map_state"] += 1
        return await _real(sid)

    async def _spy_list_refs(sid, _real=session_data_manager.list_refs):
        refetch_calls["list_refs"] += 1
        return await _real(sid)

    async def _spy_event_log(sid, _real=session_data_manager.get_event_log):
        refetch_calls["get_event_log"] += 1
        return await _real(sid)

    import app.services.chat.context_builder as cb_mod

    monkeypatch.setattr(cb_mod.session_data_manager, "get_map_state", _spy_map_state)
    monkeypatch.setattr(cb_mod.session_data_manager, "list_refs", _spy_list_refs)
    monkeypatch.setattr(cb_mod.session_data_manager, "get_event_log", _spy_event_log)

    messages = [
        {"role": "system", "content": "System prompt."},
        {"role": "user", "content": "show hospitals"},
    ]
    result = await assembler.assemble("perf_session", messages)

    # The single metadata read supplied everything; no redundant refetches.
    assert fake_store.metadata_reads == 1
    assert refetch_calls == {"get_map_state": 0, "list_refs": 0, "get_event_log": 0}
    # The fetched inventory still makes it into the assembled context.
    assert "ref:data-aaa" in result.messages[0]["content"]
    assert "geocode_cn" in result.messages[0]["content"]
