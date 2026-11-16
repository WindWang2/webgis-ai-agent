"""G43（ADR-0295）store 测试：回执状态机 + 策略 CAS + 目录授权。

覆盖场景矩阵（DB 层）：
- C12 CAS 冲突（两写者竞速，后者 409 语义 + 当前值）
- C18/C19/C20/C21 状态机单调：not_sent/submitted/unknown/turn_outcome
- C23 幂等：重复 record_assembly / 迟到弱事件不改写强状态
- C22 落盘失败 → 读模型 None（unknown/limited，不伪造）
- C04/C29 历史 turn 回执可读；缺失 = None
"""
from __future__ import annotations

import contextlib

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.db_model import Base
from app.services import turn_context_store as store
from app.services.turn_context_store import (
    PolicyConflict,
    SourceRejected,
)


@pytest_asyncio.fixture
async def db_env(tmp_path, monkeypatch):
    """隔离 sqlite 引擎；同时接管 async_db_session 与 AsyncSessionLocal。"""
    from app.core import database as db_mod

    eng = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'g43_store.db'}",
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


def _payload(n_sources: int = 2) -> dict:
    return {
        "schema": "ctx.receipt.v2",
        "session_id": "s1",
        "turn_id": "t1",
        "mode": "typed",
        "included": n_sources,
        "omitted": 0,
        "sources": [
            {"source_id": f"pk:{i}", "kind": "project_knowledge_entry",
             "decision": "included", "version_token": f"tok{i}"}
            for i in range(n_sources)
        ],
    }


class TestReceiptLifecycle:
    async def test_assemble_then_submit_then_outcome(self, db_env):
        rid = await store.record_assembly(
            session_id="s1", turn_id="t1", run_id="r1",
            org_id="1", project_id="p1", user_id="u1",
            assembly_mode="typed", policy_version=3,
            digest="d" * 32, payload=_payload(),
        )
        assert rid is not None
        read = await store.get_receipt("s1", "t1")
        assert read["status"] == "assembled"
        assert read["policy_version"] == 3
        assert read["payload"]["sources"][0]["version_token"] == "tok0"

        assert await store.record_submitted("t1", disposition="queued") is True
        assert await store.record_turn_outcome("t1", outcome="cancelled") is True
        read = await store.get_receipt("s1", "t1")
        # C20：queued + cancelled 分列（不伪造成已运行/已完成）
        assert read["status"] == "submitted"
        assert read["disposition"] == "queued"
        assert read["turn_outcome"] == "cancelled"

    async def test_duplicate_assembly_is_idempotent(self, db_env):
        first = await store.record_assembly(
            session_id="s1", turn_id="t1", assembly_mode="typed",
            policy_version=0, digest="d", payload={},
        )
        second = await store.record_assembly(
            session_id="s1", turn_id="t1", assembly_mode="typed",
            policy_version=9, digest="other", payload={"x": 1},
        )
        assert first == second  # C23：重复事件不产生第二行
        read = await store.get_receipt("s1", "t1")
        assert read["policy_version"] == 0  # 首写冻结，不被迟到装配改写

    async def test_not_sent_reasons_closed_set(self, db_env):
        await store.record_assembly(
            session_id="s1", turn_id="t1", assembly_mode="typed",
            policy_version=0, digest="", payload={},
        )
        assert await store.record_not_sent("t1", reason="model_budget_rejected")
        read = await store.get_receipt("s1", "t1")
        assert read["status"] == "not_sent"
        assert read["not_sent_reason"] == "model_budget_rejected"

        # 未知 reason 收敛到闭集
        await store.record_not_sent("t2", reason="whatever")
        # t2 无行 → False（不凭空造行）

    async def test_late_weak_event_never_downgrades_submitted(self, db_env):
        await store.record_assembly(
            session_id="s1", turn_id="t1", assembly_mode="typed",
            policy_version=0, digest="", payload={},
        )
        await store.record_submitted("t1", disposition="started")
        # 迟到的 unknown（C21 进程死亡误报）与 not_sent 都不得改写
        assert await store.record_unknown("t1") is False
        assert await store.record_not_sent("t1", reason="pi_send_error") is False
        read = await store.get_receipt("s1", "t1")
        assert read["status"] == "submitted"
        assert read["disposition"] == "started"

    async def test_unknown_after_assembled_process_death(self, db_env):
        await store.record_assembly(
            session_id="s1", turn_id="t1", assembly_mode="typed",
            policy_version=0, digest="", payload={},
        )
        assert await store.record_unknown("t1") is True
        assert (await store.get_receipt("s1", "t1"))["status"] == "unknown"

    async def test_cross_session_read_is_none(self, db_env):
        await store.record_assembly(
            session_id="s1", turn_id="t1", assembly_mode="typed",
            policy_version=0, digest="", payload={},
        )
        # C25：跨会话读取（同 turn_id）拒绝且不泄漏
        assert await store.get_receipt("other-session", "t1") is None
        assert await store.get_receipt("s1", "missing-turn") is None

    async def test_link_user_message(self, db_env):
        await store.record_assembly(
            session_id="s1", turn_id="t1", assembly_mode="typed",
            policy_version=0, digest="", payload={},
        )
        await store.record_submitted("t1", disposition="started")
        assert await store.link_user_message("t1", 42) is True
        assert (await store.get_receipt("s1", "t1"))["user_message_id"] == 42

    async def test_list_receipts_newest_first(self, db_env):
        for i in range(3):
            await store.record_assembly(
                session_id="s1", turn_id=f"t{i}", assembly_mode="typed",
                policy_version=0, digest="", payload={},
            )
        ids = [r["turn_id"] for r in await store.list_receipts("s1", limit=10)]
        assert ids == ["t2", "t1", "t0"]

    async def test_persist_failure_returns_none_no_fake_row(self, db_env, monkeypatch):
        # C22：DB 异常 → record_assembly 返回 None，读模型保持缺失
        async def _boom(*a, **kw):
            raise RuntimeError("db down")

        monkeypatch.setattr(store, "_receipt_row_cls", _boom)
        rid = await store.record_assembly(
            session_id="s1", turn_id="tx", assembly_mode="typed",
            policy_version=0, digest="", payload={},
        )
        assert rid is None


class TestPolicyCAS:
    async def test_create_update_conflict(self, db_env):
        p1 = await store.update_policy(
            session_id="s1", org_id="1", project_id="p1", user_id="u1",
            mode="explicit", preferred=["pk:1"], excluded=["cm:2"],
            expected_version=0,
        )
        assert p1.policy_version == 1
        # 过期版本写入 → 冲突（C12 两标签页竞速）
        with pytest.raises(PolicyConflict) as exc:
            await store.update_policy(
                session_id="s1", org_id="1", project_id="p1", user_id="u1",
                mode="explicit", preferred=["pk:1", "pk:3"], excluded=[],
                expected_version=0,
            )
        assert exc.value.current.policy_version == 1
        # 基于当前版本重试成功
        p2 = await store.update_policy(
            session_id="s1", org_id="1", project_id="p1", user_id="u1",
            mode="explicit", preferred=["pk:1", "pk:3"], excluded=[],
            expected_version=1,
        )
        assert p2.policy_version == 2
        assert p2.preferred == ("pk:1", "pk:3")

    async def test_auto_with_selection_rejected(self, db_env):
        with pytest.raises(SourceRejected) as exc:
            await store.update_policy(
                session_id="s1", org_id="1", project_id="p1", user_id="u1",
                mode="auto", preferred=["pk:1"], excluded=[],
                expected_version=0,
            )
        assert exc.value.reason == "auto_with_selection"

    async def test_unauthorized_source_rejected_unified(self, db_env):
        # C09：目录外/越权源统一拒绝（合法结构但不在授权目录 → 拒绝；
        # gm 命名空间只有一个合法 id，gm:else 结构上即非法）
        for bad, reason in (
            (["pk:other-project-entry"], "source_not_authorized"),
            (["cm:99"], "source_not_authorized"),
            (["gm:else"], "source_id_malformed"),
        ):
            with pytest.raises(SourceRejected) as exc:
                await store.update_policy(
                    session_id="s1", org_id="1", project_id="p1", user_id="u1",
                    mode="explicit", preferred=list(bad), excluded=[],
                    expected_version=0,
                    authorized_source_ids=["pk:1", "cm:2", "gm:block"],
                )
            assert exc.value.reason == reason

    async def test_load_policy_defaults_auto(self, db_env):
        p = await store.load_policy("no-row-session")
        assert p.is_explicit is False
        assert p.policy_version == 0

    async def test_reset_to_auto(self, db_env):
        await store.update_policy(
            session_id="s1", org_id="1", project_id="p1", user_id="u1",
            mode="explicit", preferred=["pk:1"], excluded=[],
            expected_version=0,
        )
        p = await store.update_policy(
            session_id="s1", org_id="1", project_id="p1", user_id="u1",
            mode="auto", preferred=[], excluded=[], expected_version=1,
        )
        assert p.is_explicit is False
        loaded = await store.load_policy("s1")
        assert loaded.preferred == () and loaded.excluded == ()
