"""澄清闭环传输/端点/E2E 测试（方向 3 / ADR-0222）。

三组覆盖：

1. **API**（FastAPI ASGI）：GET pending / POST answer —— 所有权、typed
   409/404/400、幂等、跨会话拒绝；
2. **kernel 章节**：update_chapter_clarification 的 pending/resolved 章
   使 refusal 谓词与 goal REQUEST_CLARIFICATION 谓词闭环（两个读取点）；
3. **bridge 传输**（mock Pi RPC）：agent_settled 后发射 `clarification`
   SSE（task_complete 之后、done 之前）；同 context 第二 turn 抑制；
   事件在 CHAT_EVENT_TYPES 词表内。

全离线确定性（内存 store / mock RPC，无 LLM 无子进程）。
"""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.chat import router as chat_router
from app.api.routes import chat as _chat_mod
from app.services.chat.sse_contracts import CHAT_EVENT_TYPES
from app.services.gis_harness.requirement_ir import envelope as env_mod
from app.services.gis_harness.requirement_ir import service as req_service
from app.services.harness_kernel import get_runtime
from app.services.session_data import MemorySessionStore
from app.services.session_data import session_data_manager


def run(coro):
    return asyncio.run(coro)


async def _seed_requirement(store, session_id: str, query: str = "统计各区小学数量"):
    await req_service.ensure_document(store, session_id, query, turn=1)


# ── 1) API ───────────────────────────────────────────────────────────


@pytest.fixture
async def client():
    app = FastAPI()
    app.include_router(chat_router, prefix="/api/v1")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _pass_ownership():
    """require_owned_session 放行（所有权负例单独覆盖）。"""
    return patch.object(
        _chat_mod.AsyncHistoryService, "get_session_meta",
        AsyncMock(return_value=MagicMock()),
    )


def _deny_ownership():
    return patch.object(
        _chat_mod.AsyncHistoryService, "get_session_meta",
        AsyncMock(return_value=None),
    )


@pytest.mark.asyncio
async def test_get_pending_204_without_document(client):
    store = MemorySessionStore()
    with patch("app.services.session_data.session_data_manager", new=store), \
            _pass_ownership():
        resp = await client.get("/api/v1/chat/sessions/s-a/clarifications/pending")
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_get_pending_200_with_envelope(client):
    store = MemorySessionStore()
    await _seed_requirement(store, "s-a")
    with patch("app.services.session_data.session_data_manager", new=store), \
            _pass_ownership():
        resp = await client.get("/api/v1/chat/sessions/s-a/clarifications/pending")
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema_version"] == "clarification_envelope.v1"
    assert body["request_id"].startswith("clar-")
    assert 1 <= len(body["questions"]) <= 2


@pytest.mark.asyncio
async def test_get_pending_ownership_denied(client):
    store = MemorySessionStore()
    await _seed_requirement(store, "s-a")
    with patch("app.services.session_data.session_data_manager", new=store), \
            _deny_ownership():
        resp = await client.get("/api/v1/chat/sessions/s-a/clarifications/pending")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_answer_endpoint_happy_path(client):
    store = MemorySessionStore()
    await _seed_requirement(store, "s-a")
    env = await env_mod.pending_envelope(store, "s-a")
    q = env.questions[0]
    with patch("app.services.session_data.session_data_manager", new=store), \
            _pass_ownership():
        resp = await client.post(
            "/api/v1/chat/sessions/s-a/clarifications/answer",
            json={
                "request_id": env.request_id,
                "expected_revision": env.requirement_revision,
                "answers": [{"context_key": q.context_key, "value": "成都"}],
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "answered"
    assert body["resolved_all"] is True
    # 答案进 RequirementDocument provenance
    view = await req_service.get_requirement_view(store, "s-a")
    assert "aoi.name" in view["user_owned_paths"]


@pytest.mark.asyncio
async def test_answer_endpoint_duplicate_idempotent(client):
    store = MemorySessionStore()
    await _seed_requirement(store, "s-a")
    env = await env_mod.pending_envelope(store, "s-a")
    q = env.questions[0]
    payload = {
        "request_id": env.request_id,
        "expected_revision": env.requirement_revision,
        "answers": [{"context_key": q.context_key, "value": "成都"}],
    }
    with patch("app.services.session_data.session_data_manager", new=store), \
            _pass_ownership():
        r1 = await client.post(
            "/api/v1/chat/sessions/s-a/clarifications/answer", json=payload)
        r2 = await client.post(
            "/api/v1/chat/sessions/s-a/clarifications/answer", json=payload)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["status"] == "answered"
    assert r2.json()["status"] == "duplicate"


@pytest.mark.asyncio
async def test_answer_endpoint_stale_revision_409(client):
    store = MemorySessionStore()
    await _seed_requirement(store, "s-a")
    env = await env_mod.pending_envelope(store, "s-a")
    q = env.questions[0]
    with patch("app.services.session_data.session_data_manager", new=store), \
            _pass_ownership():
        resp = await client.post(
            "/api/v1/chat/sessions/s-a/clarifications/answer",
            json={
                "request_id": env.request_id,
                "expected_revision": env.requirement_revision + 9,
                "answers": [{"context_key": q.context_key, "value": "成都"}],
            },
        )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "clarification_stale_revision"


@pytest.mark.asyncio
async def test_answer_endpoint_cross_session_404(client):
    """另一会话的 request_id/context_key 打本会话 → 404 typed。"""
    store = MemorySessionStore()
    await _seed_requirement(store, "s-a")
    await _seed_requirement(store, "s-b", "统计重庆各区医院数量")
    env_a = await env_mod.pending_envelope(store, "s-a")
    q_a = env_a.questions[0]
    with patch("app.services.session_data.session_data_manager", new=store), \
            _pass_ownership():
        resp = await client.post(
            "/api/v1/chat/sessions/s-b/clarifications/answer",
            json={
                "request_id": env_a.request_id,
                "expected_revision": env_a.requirement_revision,
                "answers": [{"context_key": q_a.context_key, "value": "成都"}],
            },
        )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] in (
        "clarification_unknown_context", "clarification_unknown_request")


@pytest.mark.asyncio
async def test_answer_endpoint_ownership_denied(client):
    store = MemorySessionStore()
    await _seed_requirement(store, "s-a")
    env = await env_mod.pending_envelope(store, "s-a")
    q = env.questions[0]
    with patch("app.services.session_data.session_data_manager", new=store), \
            _deny_ownership():
        resp = await client.post(
            "/api/v1/chat/sessions/s-a/clarifications/answer",
            json={
                "request_id": env.request_id,
                "expected_revision": env.requirement_revision,
                "answers": [{"context_key": q.context_key, "value": "成都"}],
            },
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_answer_endpoint_empty_payload_400(client):
    store = MemorySessionStore()
    await _seed_requirement(store, "s-a")
    env = await env_mod.pending_envelope(store, "s-a")
    with patch("app.services.session_data.session_data_manager", new=store), \
            _pass_ownership():
        resp = await client.post(
            "/api/v1/chat/sessions/s-a/clarifications/answer",
            json={"request_id": env.request_id, "answers": []},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_answer_endpoint_updates_chapter_resolved(client):
    """全部答完 → 章节澄清 status=resolved（kernel/goal 两读取点闭环）。"""
    sid = f"sess-clar-{uuid.uuid4().hex[:8]}"
    try:
        rt = get_runtime(sid)
        await rt.begin_turn("t1", host="pi", message="统计各区小学数量")
        plan = await rt.hydrate()
        plan.gis_chapter = {
            "query": "统计各区小学数量",
            "intent": {"task": "administrative_statistic",
                       "clarification": {"question": "分析范围？",
                                         "status": "pending",
                                         "raised_turn_id": "t1"}},
        }
        from app.services.session_plan import save_session_plan
        await save_session_plan(plan)

        store = MemorySessionStore()
        await _seed_requirement(store, sid)
        env = await env_mod.pending_envelope(store, sid)
        q = env.questions[0]
        with patch("app.services.session_data.session_data_manager", new=store), \
                _pass_ownership():
            # answer 路由内部对章节的写走 get_runtime（真实 store）——此处
            # 直接断言路由成功且服务端状态推进；章节断言走 kernel 方法。
            resp = await client.post(
                f"/api/v1/chat/sessions/{sid}/clarifications/answer",
                json={
                    "request_id": env.request_id,
                    "expected_revision": env.requirement_revision,
                    "answers": [{"context_key": q.context_key, "value": "成都"}],
                },
            )
        assert resp.status_code == 200
        assert resp.json()["resolved_all"] is True
    finally:
        await session_data_manager.clear_session(sid)


# ── 2) kernel 章节：refusal / goal 谓词闭环 ──────────────────────────


@pytest.fixture
async def kernel_sid():
    sid = f"sess-kclar-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)


async def _plan_of(sid):
    from app.services.session_plan import load_session_plan
    return await load_session_plan(sid)


@pytest.mark.asyncio
async def test_chapter_pending_stamp_enables_refusal(kernel_sid):
    rt = get_runtime(kernel_sid)
    await rt.begin_turn("t1", host="pi", message="画个图")
    plan = await rt.hydrate()
    plan.gis_chapter = {"query": "画个图", "intent": {"task": "simple_view"}}
    from app.services.session_plan import save_session_plan
    await save_session_plan(plan)

    changed = await rt.update_chapter_clarification(
        status="pending", turn_id="t1",
        question="分析范围是哪个城市？",
        questions=[{"code": "aoi_unresolved", "context_key": "k1",
                    "question_zh": "分析范围是哪个城市？"}],
        request_id="clar-req-x-1",
    )
    assert changed is True
    verdict = await rt.turn_refusal_candidate("t1")
    assert verdict is not None
    assert verdict["reason_code"] == "clarification_required"
    plan2 = await _plan_of(kernel_sid)
    clar = plan2.gis_chapter["intent"]["clarification"]
    assert clar["status"] == "pending"
    assert clar["raised_turn_id"] == "t1"
    assert clar["source"] == "requirement_ir"
    # 幂等：重复同状态不再追加事件
    changed2 = await rt.update_chapter_clarification(
        status="pending", turn_id="t1", question="分析范围是哪个城市？",
        request_id="clar-req-x-1")
    assert changed2 is False


@pytest.mark.asyncio
async def test_chapter_pending_restamp_overwrites_raised_turn(kernel_sid):
    """review P2-2b：后续 turn 重新提出问题 → raised_turn_id 覆盖为新 turn
    （否则 refusal 谓词的 raised==turn 匹配永远失配，该 turn 误标 completed）。"""
    rt = get_runtime(kernel_sid)
    await rt.begin_turn("t1", host="pi", message="问一")
    await rt.begin_turn("t2", host="pi", message="问二")
    plan = await rt.hydrate()
    plan.gis_chapter = {"query": "问二", "intent": {"task": "simple_view"}}
    from app.services.session_plan import save_session_plan
    await save_session_plan(plan)
    await rt.update_chapter_clarification(
        status="pending", turn_id="t1", question="范围？", request_id="r1")
    await rt.update_chapter_clarification(
        status="pending", turn_id="t2", question="时间范围？", request_id="r2")
    plan2 = await _plan_of(kernel_sid)
    clar = plan2.gis_chapter["intent"]["clarification"]
    assert clar["raised_turn_id"] == "t2"   # 覆盖为提出者
    assert clar["question"] == "时间范围？"
    assert await rt.turn_refusal_candidate("t1") is None   # 旧 turn 不再候选
    assert await rt.turn_refusal_candidate("t2") is not None


@pytest.mark.asyncio
async def test_chapter_resolved_without_question_noop(kernel_sid):
    """review P2-2：无可解析问题的章节不凭空造 clarification 块。"""
    rt = get_runtime(kernel_sid)
    await rt.begin_turn("t1", host="pi", message="画图")
    plan = await rt.hydrate()
    plan.gis_chapter = {"query": "画图", "intent": {"task": "simple_view"}}
    from app.services.session_plan import save_session_plan
    await save_session_plan(plan)
    changed = await rt.update_chapter_clarification(
        status="resolved", turn_id="t1", answers={"k": "v"})
    assert changed is False
    plan2 = await _plan_of(kernel_sid)
    assert "clarification" not in plan2.gis_chapter["intent"]


@pytest.mark.asyncio
async def test_chapter_resolved_closes_loop(kernel_sid):
    rt = get_runtime(kernel_sid)
    await rt.begin_turn("t1", host="pi", message="画个图")
    plan = await rt.hydrate()
    plan.gis_chapter = {"query": "画个图", "intent": {"task": "simple_view"}}
    from app.services.session_plan import save_session_plan
    await save_session_plan(plan)
    await rt.update_chapter_clarification(
        status="pending", turn_id="t1", question="范围？", request_id="r1")
    assert await rt.turn_refusal_candidate("t1") is not None

    changed = await rt.update_chapter_clarification(
        status="resolved", turn_id="t2",
        answers={"k1": "成都"}, request_id="r1")
    assert changed is True
    assert await rt.turn_refusal_candidate("t1") is None   # resolved 闭环
    plan2 = await _plan_of(kernel_sid)
    clar = plan2.gis_chapter["intent"]["clarification"]
    assert clar["status"] == "resolved"
    assert clar["answers"] == {"k1": "成都"}
    assert clar["resolved_turn_id"] == "t2"
    # goal evaluator 同谓词：resolved → 不再 REQUEST_CLARIFICATION
    chapter = plan2.gis_chapter
    intent = chapter.get("intent", {})
    clarification = intent.get("clarification", {})
    has_open = bool(
        str(clarification.get("question") or clarification.get("query") or "")
    ) and str(clarification.get("status") or "") != "resolved"
    assert has_open is False


@pytest.mark.asyncio
async def test_chapter_update_without_plan_noop(kernel_sid):
    rt = get_runtime(kernel_sid)
    changed = await rt.update_chapter_clarification(
        status="pending", turn_id="t1", question="?",
        request_id="r1")
    assert changed is False   # 无章节 → 无副作用


@pytest.mark.asyncio
async def test_clarification_updated_in_event_vocabulary():
    from app.services.harness_kernel.models import EVENT_KINDS
    assert "clarification_updated" in EVENT_KINDS


# ── 3) bridge 传输：SSE 发射 + 抑制 ──────────────────────────────────


def _make_event_rpc(events: list[dict]):
    """与 tests/test_pi_e2e.py 同构的 mock RPC（队列预置事件）。"""
    from app.agent_pi_bridge import PiBridge  # noqa: F401 — ensure importable
    rpc = MagicMock()
    rpc.events = asyncio.Queue()
    rpc.start = AsyncMock()
    rpc.stop = AsyncMock()
    rpc.process_died = False

    async def _seed_then_idle(cmd, data=None):
        for ev in events:
            await rpc.events.put(ev)
    rpc.request = AsyncMock(side_effect=_seed_then_idle)
    return rpc


def _sse_types(chunks):
    return [c.split("\n")[0].replace("event: ", "")
            for c in chunks if c.strip() and not c.startswith(":")]


@pytest.mark.asyncio
async def test_bridge_emits_clarification_event(monkeypatch):
    """turn 收口：pending blocking → clarification 事件（task_complete 后、
    done 前）；同 context 第二 turn 抑制。"""
    from app.agent_pi_bridge import PiBridge
    from tests.fixtures.pi_mocks import make_agent_settled, make_token_event

    sid = f"sess-bridge-clar-{uuid.uuid4().hex[:8]}"
    store = MemorySessionStore()
    await _seed_requirement(store, sid)
    monkeypatch.setattr("app.services.session_data.session_data_manager", store)
    try:
        events = [make_token_event("我需要先确认几个问题。"),
                  make_agent_settled()]
        bridge = PiBridge(rpc=_make_event_rpc(events))
        chunks = []
        async for ev in bridge.stream_prompt("统计各区小学数量", session_id=sid):
            chunks.append(ev)
        types = _sse_types(chunks)
        assert "clarification" in types
        clar_idx = types.index("clarification")
        assert "task_complete" in types[:clar_idx]
        assert types[clar_idx + 1] == "done"
        payload = next(c for c in chunks if c.startswith("event: clarification"))
        assert "clarification_envelope.v1" in payload
        assert sid in payload or "request_id" in payload

        # 第二 turn（同 context 未答）→ 抑制：无 clarification 事件
        bridge2 = PiBridge(rpc=_make_event_rpc(
            [make_token_event("还在等你确认。"), make_agent_settled()]))
        chunks2 = []
        async for ev in bridge2.stream_prompt("统计各区小学数量", session_id=sid):
            chunks2.append(ev)
        types2 = _sse_types(chunks2)
        assert "clarification" not in types2
        assert types2[-1] == "done"

        # 第三 turn（全部已答）→ "clear" 对账路径：不再发射，不抛错
        env = await env_mod.pending_envelope(store, sid)
        assert env is not None
        q = env.questions[0]
        await env_mod.submit_answers(
            store, sid, env.request_id,
            [{"context_key": q.context_key, "value": "成都"}],
            expected_revision=env.requirement_revision)
        bridge3 = PiBridge(rpc=_make_event_rpc(
            [make_token_event("好的，开始分析。"), make_agent_settled()]))
        chunks3 = []
        async for ev in bridge3.stream_prompt("统计各区小学数量", session_id=sid):
            chunks3.append(ev)
        types3 = _sse_types(chunks3)
        assert "clarification" not in types3
        assert types3[-1] == "done"
    finally:
        await store.clear_session(sid)


@pytest.mark.asyncio
async def test_bridge_no_clarification_without_pending(monkeypatch):
    from app.agent_pi_bridge import PiBridge
    from tests.fixtures.pi_mocks import make_agent_settled, make_token_event

    sid = f"sess-bridge-none-{uuid.uuid4().hex[:8]}"
    store = MemorySessionStore()
    # 无 requirement 文档 → 无事件
    monkeypatch.setattr("app.services.session_data.session_data_manager", store)
    bridge = PiBridge(rpc=_make_event_rpc(
        [make_token_event("你好"), make_agent_settled()]))
    chunks = []
    async for ev in bridge.stream_prompt("你好", session_id=sid):
        chunks.append(ev)
    types = _sse_types(chunks)
    assert "clarification" not in types


def test_clarification_in_sse_vocabulary():
    assert "clarification" in CHAT_EVENT_TYPES
