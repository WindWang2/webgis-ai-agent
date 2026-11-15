"""G43（ADR-0295）@tool 测试：注册 + 只读回执查询 + 语义诚实面。

覆盖场景矩阵（工具层）：
- 真实 ToolRegistry 注册（init_tools 路径可发现）
- WP10：当前/历史 turn 回执读取；legacy → coverage limited
- 红线：无任何策略修改参数（只读）
"""
from __future__ import annotations

import contextlib

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.db_model import Base


@pytest_asyncio.fixture
async def db_env(tmp_path, monkeypatch):
    from app.core import database as db_mod

    eng = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'g43_tool.db'}",
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    fac = async_sessionmaker(bind=eng, expire_on_commit=False)

    @contextlib.asynccontextmanager
    async def _patched_session():
        async with fac() as db:
            yield db

    monkeypatch.setattr(db_mod, "async_db_session", _patched_session)
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", fac)
    yield fac
    await eng.dispose()


def _registry():
    from app.tools.registry import ToolRegistry

    return ToolRegistry()


class TestRegistration:
    def test_tool_registered_readonly(self):
        registry = _registry()
        from app.tools.turn_context_tools import register_turn_context_tools

        register_turn_context_tools(registry)
        meta = registry._metadata["webgis_turn_context_receipt"]
        assert meta.get("side_effect") == "pure"
        fn = registry.get("webgis_turn_context_receipt")
        assert fn is not None
        # 红线：签名只有只读参数（无策略修改）
        import inspect

        params = set(inspect.signature(fn).parameters)
        assert params <= {"session_id", "turn_id", "limit"}

    async def test_dispatch_reads_receipt_via_registry(self, db_env):
        """经生产 dispatch 面调用（session_id 注入；WP10 的 dispatch 真值）。"""
        from app.services.turn_context_store import record_assembly
        from app.tools.turn_context_tools import register_turn_context_tools

        await record_assembly(
            session_id="s-disp", turn_id="t-disp", assembly_mode="typed",
            policy_version=1, digest="", payload={"sources": []})
        registry = _registry()
        register_turn_context_tools(registry)
        result = await registry.dispatch(
            "webgis_turn_context_receipt",
            {"turn_id": "t-disp"},
            session_id="s-disp",
        )
        body = result.get("result") if isinstance(result, dict) and "result" in result else result
        assert body["turn_id"] == "t-disp"
        assert body["session_id"] == "s-disp"


class TestToolBehavior:
    async def test_current_turn_receipt(self, db_env):
        from app.services.turn_context_store import record_assembly, record_submitted
        from app.tools.turn_context_tools import query_turn_context_receipt

        await record_assembly(
            session_id="s1", turn_id="t1", assembly_mode="typed",
            policy_version=5, digest="d" * 32,
            payload={
                "sources": [
                    {"source_id": "pk:1", "kind": "project_knowledge_entry",
                     "decision": "included", "version_token": "tok1"},
                    {"source_id": "cm:2", "kind": "carto_memory_fact",
                     "decision": "excluded_by_policy"},
                ],
                "included": 3, "sources_total": 2,
            },
        )
        await record_submitted("t1", disposition="queued")
        result = await query_turn_context_receipt("s1", "t1")
        assert result["status"] == "submitted"
        assert result["disposition"] == "queued"
        assert result["policy_version"] == 5
        assert result["sources"][0]["version_token"] == "tok1"
        assert result["coverage"] == "full"
        assert "≠" in result["semantics"]  # queued≠已消费 的诚实语义披露

    async def test_latest_receipt_when_turn_omitted(self, db_env):
        from app.services.turn_context_store import record_assembly
        from app.tools.turn_context_tools import query_turn_context_receipt

        await record_assembly(
            session_id="s1", turn_id="t2", assembly_mode="typed",
            policy_version=1, digest="", payload={})
        result = await query_turn_context_receipt("s1")
        assert result["turn_id"] == "t2"

    async def test_missing_turn_honest_not_found(self, db_env):
        from app.tools.turn_context_tools import query_turn_context_receipt

        result = await query_turn_context_receipt("s1", "t-none")
        assert result["error"] == "receipt_not_found"
        assert result["coverage"] == "limited"
        assert "不补造" in result["note"]

    async def test_legacy_row_reports_limited(self, db_env):
        from app.services.turn_context_store import record_assembly
        from app.tools.turn_context_tools import query_turn_context_receipt

        await record_assembly(
            session_id="s1", turn_id="t-leg", assembly_mode="legacy",
            policy_version=0, digest="", payload={})
        result = await query_turn_context_receipt("s1", "t-leg")
        assert result["coverage"] == "limited"

    async def test_no_session_error(self):
        from app.tools.turn_context_tools import query_turn_context_receipt

        result = await query_turn_context_receipt("", "")
        assert result == {"error": "session context unavailable"}
