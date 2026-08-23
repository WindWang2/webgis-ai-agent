"""Cartography verdict turn-injection: harness → Pi Agent 的被动回流。

覆盖两层：
- bridge 层：``cartography_context`` 以「用户消息 → verdict 块 → turn marker」
  的顺序附着（marker 必须最后，扩展的 currentTurnToken 取最后匹配）；
- chat 助手层：``_build_cartography_turn_context`` 只读组合存储 verdict 与
  当前 MapSpec 指纹，跨代/无活动/未通过守卫不通过时返回空串。
"""
import asyncio
import shutil
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent_pi_bridge import PiBridge
from app.lib.cartography.quality_loop import cartographic_fingerprint
from app.services.mapspec.store import BASE_STORAGE_DIR, mapspec_store_instance
from app.services.mapspec_store import mapspec_store
from app.services.session_data import session_data_manager


VERDICT_BLOCK = (
    "[CARTOGRAPHY_VERDICT]\n"
    '{"status": "failed_repairable"}\n'
    "Server-verified cartography harness verdict."
)


def _make_bridge():
    rpc = MagicMock()
    rpc.events = asyncio.Queue()
    rpc.start = AsyncMock()
    rpc.stop = AsyncMock()
    captured: list = []

    async def fake_request(cmd, data=None):
        if cmd == "prompt":
            captured.append(data)
            await rpc.events.put({
                "type": "message_update",
                "message": {"role": "assistant", "content": [
                    {"type": "text", "text": "ok"}
                ]},
            })
            await rpc.events.put({"type": "agent_end", "willRetry": False})
            await rpc.events.put({"type": "agent_settled"})

    rpc.request = AsyncMock(side_effect=fake_request)
    bridge = PiBridge(rpc=rpc)
    return bridge, captured


# ─── bridge 层：附着顺序 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prompt_attaches_verdict_between_message_and_marker():
    bridge, captured = _make_bridge()
    await bridge.prompt("hello", session_id="sess-1", cartography_context=VERDICT_BLOCK)

    assert len(captured) == 1
    message = captured[0]["message"]
    i_msg = message.index("hello")
    i_verdict = message.index("[CARTOGRAPHY_VERDICT]")
    i_marker = message.index("[WEBGIS_TURN_CONTEXT:")
    assert i_msg < i_verdict < i_marker
    # marker 段是最后一块服务端附着的上下文（其后仅剩 do-not-quote 说明行）。
    assert message.rstrip().endswith("(Internal routing context; do not quote or modify this marker.)")


@pytest.mark.asyncio
async def test_prompt_without_context_keeps_legacy_shape():
    bridge, captured = _make_bridge()
    await bridge.prompt("hello", session_id="sess-1")
    message = captured[0]["message"]
    assert "CARTOGRAPHY_VERDICT" not in message
    assert "[WEBGIS_TURN_CONTEXT:" in message


@pytest.mark.asyncio
async def test_stream_prompt_attaches_verdict_in_same_order():
    bridge, captured = _make_bridge()
    events = []
    async for sse in bridge.stream_prompt(
        "hello", session_id="sess-1", cartography_context=VERDICT_BLOCK
    ):
        events.append(sse)

    assert events, "stream must yield SSE events"
    message = captured[0]["message"]
    assert message.index("hello") < message.index("[CARTOGRAPHY_VERDICT]") < message.index(
        "[WEBGIS_TURN_CONTEXT:"
    )


# ─── chat 助手层：只读组合与守卫 ─────────────────────────────────────────


@pytest.fixture
async def carto_session():
    sid = f"carto-inject-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    session_dir = BASE_STORAGE_DIR / sid
    if session_dir.exists():
        shutil.rmtree(session_dir, ignore_errors=True)


async def _current_fingerprint(sid: str) -> str:
    mapspec = await mapspec_store_instance.get_mapspec(sid)
    assert isinstance(mapspec, dict), "init_project must produce a mapspec"
    return cartographic_fingerprint(mapspec)


def _failed_review(sid: str, fingerprint: str) -> dict:
    return {
        "session_id": sid,
        "cartography": {
            "status": "failed_repairable",
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


@pytest.mark.asyncio
async def test_helper_injects_current_generation_failure(carto_session):
    from app.api.routes.chat import _build_cartography_turn_context

    await mapspec_store.init_project(carto_session)
    fingerprint = await _current_fingerprint(carto_session)
    await session_data_manager.set_map_state(
        carto_session, "_cartographic_review", _failed_review(carto_session, fingerprint)
    )

    block = await _build_cartography_turn_context(carto_session)
    assert block.startswith("[CARTOGRAPHY_VERDICT]")
    assert '"failed_repairable"' in block
    assert '"desired_quality_failed"' in block


@pytest.mark.asyncio
async def test_helper_injects_current_generation_pass_as_tiny_token(carto_session):
    """#657: pass / passed_with_warnings 也注入微型 pass token——沉默不再是 pass。"""
    from app.api.routes.chat import _build_cartography_turn_context

    await mapspec_store.init_project(carto_session)
    fingerprint = await _current_fingerprint(carto_session)
    review = _failed_review(carto_session, fingerprint)
    review["cartography"]["status"] = "passed_with_warnings"
    review["cartography"]["checks"] = []
    review["overall_passed"] = True
    await session_data_manager.set_map_state(
        carto_session, "_cartographic_review", review
    )

    block = await _build_cartography_turn_context(carto_session)
    assert block.startswith("[CARTOGRAPHY_VERDICT]")
    assert '"verdict": "pass"' in block
    assert "overall_passed" not in block
    assert "passed_with_warnings" not in block


@pytest.mark.asyncio
async def test_helper_skips_stale_generation_review(carto_session):
    from app.api.routes.chat import _build_cartography_turn_context

    await mapspec_store.init_project(carto_session)
    await session_data_manager.set_map_state(
        carto_session,
        "_cartographic_review",
        _failed_review(carto_session, "carto-sha256:stale"),
    )
    assert await _build_cartography_turn_context(carto_session) == ""


@pytest.mark.asyncio
async def test_helper_skips_session_without_cartography_activity(carto_session):
    from app.api.routes.chat import _build_cartography_turn_context

    assert await _build_cartography_turn_context(carto_session) == ""
    assert await _build_cartography_turn_context("") == ""
    assert await _build_cartography_turn_context(None) == ""


# ─── 同 turn content 守卫：harness verdict 不进 mutation 结果 ────────────


@pytest.mark.asyncio
async def test_same_turn_mutation_content_is_not_harness_verdict(monkeypatch):
    """#657 AC: 同 turn mutation 的 ``content`` 是 MapSpec 生命周期 payload，
    不是 harness Cartography Verdict——即使 dispatch 后 evaluate 已持久化
    评审，返回给模型的文本也保持评估前的 llm_payload 不变。"""
    import app.agent_pi_bridge as bridge
    from app.services.tool_dispatch_service import ToolDispatchResult

    sid = f"carto-content-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)

    lifecycle_payload = (
        '{"success": true, "cartographic_review": {"stage": "desired_state"}}'
    )
    result = ToolDispatchResult(
        status="ok",
        llm_payload=lifecycle_payload,
        slim_event={},
        geojson_ref=None,
        raw_result={
            "success": True,
            "is_compiled": True,
            "mapspec_fingerprint": "fp-content",
            "mutation_revision": 1,
        },
        error_msg=None,
        map_actions=[],
    )
    fake_service = MagicMock()
    fake_service.dispatch = AsyncMock(return_value=result)
    monkeypatch.setattr(bridge, "ToolDispatchService", lambda **kw: fake_service)

    fake_registry = MagicMock()
    fake_registry.list_tools = MagicMock(return_value=["webgis_layer_upsert"])
    fake_registry.metadata = MagicMock(return_value={"tier": 1})
    monkeypatch.setattr(bridge, "_tool_registry", fake_registry)

    async def _fake_eval(session_id):
        # evaluate 在 HTTP 返回前持久化评审；content 不得因此改写。
        await session_data_manager.set_map_state(session_id, "_cartographic_review", {
            "session_id": session_id,
            "cartography": {
                "status": "passed",
                "mapspec_fingerprint": "fp-content",
            },
            "gate": {},
            "overall_passed": True,
        })

    async def _fake_persist(session_id, event, map_actions):
        return True

    monkeypatch.setattr(bridge, "evaluate_cartographic_session", _fake_eval)
    monkeypatch.setattr(bridge, "_persist_cartographic_harness_context", _fake_persist)

    request = bridge.PiToolRequest(
        toolCallId="tc-content",
        name="webgis_layer_upsert",
        arguments={},
        sessionId=sid,
    )
    try:
        resp = await bridge.dispatch_tool(request)
        text = "".join(c.get("text", "") for c in resp.content)
        assert text == lifecycle_payload
        assert "CARTOGRAPHY_VERDICT" not in text
        assert "overall_passed" not in text
    finally:
        await session_data_manager.clear_session(sid)
        bridge._session_executed_sets.pop(sid, None)
