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


# ── #788 (F-A-8): legacy path injects the cartography harness verdict ─────


@pytest.fixture
async def legacy_verdict_session():
    """A session with a real MapSpec generation (for fingerprint matching)."""
    import shutil
    import uuid

    from app.services.mapspec.store import BASE_STORAGE_DIR
    from app.services.mapspec_store import mapspec_store

    sid = f"legacy-verdict-{uuid.uuid4().hex[:8]}"
    await mapspec_store.init_project(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    shutil.rmtree(BASE_STORAGE_DIR / sid, ignore_errors=True)


async def _current_fingerprint(sid: str) -> str:
    from app.lib.cartography.quality_loop import cartographic_fingerprint
    from app.services.mapspec.store import mapspec_store_instance

    mapspec = await mapspec_store_instance.get_mapspec(sid)
    assert isinstance(mapspec, dict), "init_project must produce a mapspec"
    return cartographic_fingerprint(mapspec)


def _review(sid: str, fingerprint: str, status: str) -> dict:
    review = {
        "session_id": sid,
        "cartography": {
            "status": status,
            "termination_reason": "desired_quality_failed",
            "mapspec_fingerprint": fingerprint,
            "checks": [
                {"rule": "PAINT_LEGEND_EQUIVALENCE", "status": "fail",
                 "message": "legend labels diverge from paint domain"},
            ],
            "repair_attempts": [],
        },
        "gate": {},
        "overall_passed": False,
    }
    if status in ("passed", "passed_with_warnings"):
        review["cartography"]["checks"] = []
        review["overall_passed"] = True
    return review


def _system_blocks(result) -> str:
    return "\n".join(
        m["content"] for m in result.messages if m.get("role") == "system"
    )


@pytest.mark.asyncio
async def test_legacy_path_injects_fail_verdict(legacy_verdict_session):
    """#788: a stored failing review of the CURRENT generation must reach the
    legacy engine's composed request messages — previously the
    [CARTOGRAPHY_VERDICT] injection existed only on the Pi path."""
    sid = legacy_verdict_session
    fingerprint = await _current_fingerprint(sid)
    await session_data_manager.set_map_state(
        sid, "_cartographic_review", _review(sid, fingerprint, "failed_repairable")
    )

    result = await ChatContextAssembler().assemble(sid, [
        {"role": "system", "content": "System prompt."},
        {"role": "user", "content": "换个颜色"},
    ])

    blocks = _system_blocks(result)
    assert "CARTOGRAPHY_VERDICT" in blocks
    assert '"verdict": "fail"' in blocks
    assert "failed_repairable" in blocks


@pytest.mark.asyncio
async def test_legacy_path_renders_pass_as_micro_token(legacy_verdict_session):
    """#788 + #657: a passing review renders ONLY the micro-token — no status,
    checks, or overall_passed fields leak into the legacy context."""
    sid = legacy_verdict_session
    fingerprint = await _current_fingerprint(sid)
    await session_data_manager.set_map_state(
        sid, "_cartographic_review",
        _review(sid, fingerprint, "passed_with_warnings"),
    )

    result = await ChatContextAssembler().assemble(sid, [
        {"role": "system", "content": "System prompt."},
        {"role": "user", "content": "谢谢"},
    ])

    blocks = _system_blocks(result)
    assert "CARTOGRAPHY_VERDICT" in blocks
    assert '"verdict": "pass"' in blocks
    assert "overall_passed" not in blocks
    assert "passed_with_warnings" not in blocks


@pytest.mark.asyncio
async def test_legacy_path_skips_stale_generation_review(legacy_verdict_session):
    """#788 guard: a review of a stale MapSpec generation is not injected
    (same fingerprint guard as the Pi route helper)."""
    sid = legacy_verdict_session
    await session_data_manager.set_map_state(
        sid, "_cartographic_review", _review(sid, "carto-sha256:stale", "failed_repairable")
    )

    result = await ChatContextAssembler().assemble(sid, [
        {"role": "system", "content": "System prompt."},
        {"role": "user", "content": "你好"},
    ])

    assert "CARTOGRAPHY_VERDICT" not in _system_blocks(result)
