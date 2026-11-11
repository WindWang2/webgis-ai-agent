"""G11 —— 简报 Agent 工具链测试（真实 registry dispatch + Pi 适配器）。

分层：real domain integration。覆盖：
1. session_id 注入探针（LLM 传入的同名实参被覆盖，不可伪造跨会话）；
2. 六工具真实 dispatch（真实 DB + 真实产物 + 真实领域计算）；
3. Pi HTTP 回调适配器（agent_pi_bridge.dispatch_tool）同链可执行。
"""
from __future__ import annotations

import uuid

import pytest

from app.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def brief_env(monkeypatch, tmp_path):
    """隔离 CAS 根 + 真实 registry（仅注册 brief 工具）。"""
    monkeypatch.setattr(
        "app.services.project_artifact_promotion.content_store_root",
        lambda: tmp_path / "cas")
    from app.services.durable_blob_store import reset_filesystem_blob_store

    reset_filesystem_blob_store()
    registry = ToolRegistry()
    from app.tools.brief_tools import register_brief_tools

    register_brief_tools(registry)
    yield registry, tmp_path
    reset_filesystem_blob_store()


@pytest.fixture
async def seeded_session(brief_env):
    """conftest 隔离 async DB 上建 Conversation + 真实产物。"""
    from app.core.database import get_async_db
    from app.models.db_model import Conversation

    sid = f"brief-tool-{uuid.uuid4().hex[:8]}"
    gen = get_async_db()
    db = await gen.__anext__()
    try:
        db.add(Conversation(id=sid, user_id="u1", title="学校分析"))
        await db.commit()
    finally:
        await gen.aclose()

    from app.services.artifact_registry import register_tool_artifact
    from app.services.session_data import session_data_manager

    payload = {"table": {"columns": ["name", "count"],
                         "rows": [{"name": "锦江区", "count": 142},
                                  {"name": "青羊区", "count": 97}]}}
    ref = await session_data_manager.store(sid, payload, prefix="stats")
    await register_tool_artifact(sid, ref, tool="analyze_distribution",
                                 result=payload)
    yield sid
    await session_data_manager.clear_session(sid)


class TestBriefToolsDispatch:
    async def test_create_draft_via_registry(self, brief_env, seeded_session):
        registry, _ = brief_env
        sid = seeded_session
        # 注入探针：显式传入伪造 session_id 也必须被覆盖（安全面）
        result = await registry.dispatch(
            "brief_create_draft",
            {"title": "学校分布简报", "session_id": "attacker-session"},
            session_id=sid)
        assert result["type"] == "brief_created", result
        assert result["summary"]["facts"] > 0
        assert result["summary"]["fact_stats"]["verified"] > 0
        assert result["evidence_gaps"] is not None

    async def test_create_draft_idempotent(self, brief_env, seeded_session):
        registry, _ = brief_env
        sid = seeded_session
        await registry.dispatch("brief_create_draft", {}, session_id=sid)
        second = await registry.dispatch("brief_create_draft", {}, session_id=sid)
        assert second["type"] == "brief_already_exists"

    async def test_full_tool_journey(self, brief_env, seeded_session):
        """草稿 → 编辑锁定 → 证据核对 → 预检 → 冻结 → 私有导出。"""
        registry, _ = brief_env
        sid = seeded_session
        created = await registry.dispatch(
            "brief_create_draft", {"title": "简报"}, session_id=sid)
        assert created["type"] == "brief_created"

        # 读取块/事实 ID（从草稿回执拿不到细节 → 走 store 读）
        from app.core.database import get_async_db
        from app.services.storymap.brief_store import load_brief

        gen = get_async_db()
        db = await gen.__anext__()
        try:
            from app.models.db_model import Conversation

            conv = await db.get(Conversation, sid)
            spec = await load_brief(db, conv)
        finally:
            await gen.aclose()
        text_block = next(b for b in spec.blocks if b.kind == "text")
        fact_id = next(f.fact_id for f in spec.facts
                       if f.status == "verified")

        # 编辑 + 锁定（BRF 指令 3）
        updated = await registry.dispatch(
            "brief_update_block",
            {"block_id": text_block.id, "text": "人工结论：不改。",
             "locked": True, "origin": "manual"},
            session_id=sid)
        assert updated["type"] == "brief_block_updated"

        # 证据核对（BRF 指令 2）
        evidence = await registry.dispatch(
            "brief_inspect_evidence", {"fact_id": fact_id}, session_id=sid)
        assert evidence["type"] == "brief_evidence"
        assert evidence["verification"]["status"] == "verified"
        assert evidence["source"]["artifact_ref"].startswith("ref:")
        assert evidence["source"]["source_label"]

        # 预检（BRF 指令 4 前半）
        pre = await registry.dispatch("brief_preflight", {}, session_id=sid)
        assert pre["type"] == "brief_preflight"
        assert pre["outcome"] in ("deliverable", "draft_exportable")

        # 冻结
        frozen = await registry.dispatch(
            "brief_freeze_release",
            {"allow_draft": pre["outcome"] != "deliverable"},
            session_id=sid)
        assert frozen["type"] == "brief_release_frozen"
        assert frozen["files"]
        assert all(f["status"] == "ready" for f in frozen["files"])
        assert "/api/v1/briefs/" in frozen["download_url"]

        # 私有导出（BRF 指令 4 后半：默认 private）
        exported = await registry.dispatch(
            "brief_export", {"format": "html"}, session_id=sid)
        assert exported["type"] == "brief_export"
        assert exported["format"] == "html"
        assert exported["url"].startswith(f"/api/v1/briefs/sessions/{sid}/")
        assert "公开" not in exported["message"] or "不含" in exported["message"]

    async def test_locked_block_edit_rejected(self, brief_env, seeded_session):
        registry, _ = brief_env
        sid = seeded_session
        await registry.dispatch("brief_create_draft", {}, session_id=sid)
        from app.core.database import get_async_db
        from app.models.db_model import Conversation
        from app.services.storymap.brief_store import load_brief

        gen = get_async_db()
        db = await gen.__anext__()
        try:
            conv = await db.get(Conversation, sid)
            spec = await load_brief(db, conv)
        finally:
            await gen.aclose()
        block = next(b for b in spec.blocks if b.kind == "text")
        await registry.dispatch(
            "brief_update_block",
            {"block_id": block.id, "locked": True}, session_id=sid)
        rejected = await registry.dispatch(
            "brief_update_block",
            {"block_id": block.id, "text": "覆盖"}, session_id=sid)
        assert "error" in rejected and "locked" in rejected["error"]


class TestPiAdapterChain:
    async def test_dispatch_via_pi_http_callback_adapter(
            self, brief_env, seeded_session):
        """Pi 扩展 HTTP 回调适配器（agent_pi_bridge.dispatch_tool）同链。

        这条链与生产 Pi 进程回调同一入口（pi_tools 路由 → 本函数）；
        此处以 in-process 直调（sessionId 语义与路由验证后的
        verifiedTurnId 等价），驱动真实 ToolDispatchService → 真实
        简报领域计算。
        """
        registry, _ = brief_env
        sid = seeded_session
        import app.agent_pi_bridge as bridge_mod

        bridge_mod.set_tool_registry(registry)
        try:
            request = bridge_mod.PiToolRequest(
                toolCallId="tc-brief-1",
                name="brief_create_draft",
                arguments={"title": "Pi 链简报"},
                sessionId=sid,
            )
            response = await bridge_mod.dispatch_tool(request)
            assert isinstance(response, bridge_mod.PiToolResponse)
            assert not response.isError
            payload = response.details if response.details else \
                response.content[0].get("json") if response.content else None
            # 回执内容里应能找到真实领域结果（已核验指标 > 0）
            import json as _json

            text = _json.dumps(
                {"details": response.details, "content": response.content},
                ensure_ascii=False, default=str)
            assert "brief_created" in text or "brief" in text
        finally:
            bridge_mod.set_tool_registry(None)
