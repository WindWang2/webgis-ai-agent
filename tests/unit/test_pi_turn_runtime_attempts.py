"""WP3（GOAL-20261127-01）unit：attempt 链持久化 + governor 真实 usage 回流
+ budget 投影（B6 契约修复）。

覆盖：
- persist_turn_records 新元数据（attempts/route codes/failure kind）落行；
- finalize 结算 → governor.ledger.record_output_tokens 回流（fail-open）；
- session_usage_summary.budget：cap 有界 → used/remaining；无 cap →
  token_cap=None 如实 unlimited（前端契约缺陷闭合）；
- governor record_output_tokens 全 scope 求和语义（与 context 的
  session keep-latest 语义分叉有据）。
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.db_model import Base, ChatTurnRuntime
from app.services.chat.model_runtime.usage_ledger import (
    TurnUsageCollector,
    persist_turn_records,
    session_usage_summary,
)


@pytest_asyncio.fixture
async def db_factory(tmp_path, monkeypatch):
    from app.core import database as db_mod

    eng = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'wp3.db'}",
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    fac = async_sessionmaker(bind=eng, expire_on_commit=False)

    import contextlib

    @contextlib.asynccontextmanager
    async def _patched():
        async with fac() as db:
            yield db

    monkeypatch.setattr(db_mod, "async_db_session", _patched)
    yield fac
    await eng.dispose()


def _message_end_event(model="m-base", usage=None):
    return {
        "type": "message_end",
        "message": {
            "role": "assistant", "provider": "webgis", "model": model,
            "usage": usage or {"input": 30, "output": 20},
            "stopReason": "stop",
        },
    }


@pytest.mark.asyncio
async def test_persist_writes_attempt_chain_metadata(db_factory):
    collector = TurnUsageCollector("s-wp3", "t-wp3", "run-wp3")
    collector.observe(_message_end_event())
    ok = await persist_turn_records(
        collector,
        status="inherit_routed",
        provider_retry_attempts=2,
        route_reason_codes="primary_cooldown:30s|fallback_first_capable",
        last_provider_failure_kind="rate_limit",
        finalize=True,
    )
    assert ok
    async with db_factory() as db:
        row = (await db.execute(
            select(ChatTurnRuntime).where(ChatTurnRuntime.turn_id == "t-wp3")
        )).scalar_one()
    assert row.provider_retry_attempts == 2
    assert row.route_reason_codes == "primary_cooldown:30s|fallback_first_capable"
    assert row.last_provider_failure_kind == "rate_limit"
    assert row.status == "inherit_routed"


@pytest.mark.asyncio
async def test_finalize_feeds_governor_output_tokens(db_factory, monkeypatch):
    fed = []

    class _FakeGovernor:
        class ledger:  # noqa: N801 — 桩形状对齐 governor.ledger 属性
            @staticmethod
            def record_output_tokens(session_id, turn_id, tokens):
                fed.append((session_id, turn_id, tokens))

    from app.services.governor import governor as gov_mod

    monkeypatch.setattr(gov_mod, "get_governor", lambda: _FakeGovernor())
    collector = TurnUsageCollector("s-gov", "t-gov", "run-gov")
    collector.observe(_message_end_event(usage={"input": 10, "output": 7}))
    ok = await persist_turn_records(collector, finalize=True)
    assert ok
    assert fed == [("s-gov", "t-gov", 7)]


@pytest.mark.asyncio
async def test_finalize_governor_failure_is_fail_open(db_factory, monkeypatch):
    from app.services.governor import governor as gov_mod

    def _boom():
        raise RuntimeError("governor down")

    monkeypatch.setattr(gov_mod, "get_governor", _boom)
    collector = TurnUsageCollector("s-gov2", "t-gov2", "run-gov2")
    collector.observe(_message_end_event())
    ok = await persist_turn_records(collector, finalize=True)
    assert ok  # 账本主事务已提交；回流失败不影响结算
    async with db_factory() as db:
        row = (await db.execute(
            select(ChatTurnRuntime).where(ChatTurnRuntime.turn_id == "t-gov2")
        )).scalar_one()
    assert row.complete is True


@pytest.mark.asyncio
async def test_usage_summary_budget_block_with_cap(db_factory):
    collector = TurnUsageCollector("s-cap", "t-cap", "run")
    collector.observe(_message_end_event(usage={"input": 100, "output": 40}))
    await persist_turn_records(collector, finalize=True)
    async with db_factory() as db:
        from app.services.chat.model_runtime.session_model_prefs import set_pref

        await set_pref(
            db, "s-cap", mode="inherit",
            session_token_cap=1000, expected_revision=0,
        )
        summary = await session_usage_summary(db, "s-cap")
    assert summary["budget"]["cap"] == 1000
    assert summary["budget"]["token_cap"] == 1000
    assert summary["budget"]["used"] == 140
    assert summary["budget"]["remaining"] == 860


@pytest.mark.asyncio
async def test_usage_summary_budget_block_unlimited(db_factory):
    collector = TurnUsageCollector("s-nocap", "t-nocap", "run")
    collector.observe(_message_end_event())
    await persist_turn_records(collector, finalize=True)
    async with db_factory() as db:
        summary = await session_usage_summary(db, "s-nocap")
    assert summary["budget"]["cap"] is None
    assert summary["budget"]["token_cap"] is None
    assert "remaining" not in summary["budget"]


@pytest.mark.asyncio
async def test_usage_summary_recent_turns_attempt_projection(db_factory):
    collector = TurnUsageCollector("s-proj", "t-proj", "run")
    collector.observe(_message_end_event())
    await persist_turn_records(
        collector,
        provider_retry_attempts=1,
        route_reason_codes="strategy_off",
        last_provider_failure_kind="timeout",
        finalize=True,
    )
    async with db_factory() as db:
        summary = await session_usage_summary(db, "s-proj")
    turn = summary["recent_turns"][-1]
    assert turn["provider_retry_attempts"] == 1
    assert turn["route_reason_codes"] == ["strategy_off"]
    assert turn["last_provider_failure_kind"] == "timeout"


def test_governor_record_output_tokens_sums_all_scopes():
    from app.services.governor.session_budget import SessionBudgetLedger

    led = SessionBudgetLedger(budgets={})
    led.record_output_tokens("s1", "t1", 100)
    led.record_output_tokens("s1", "t2", 70)
    snap = led.snapshot("s1")
    assert snap["session"]["cumulative"]["output_tokens"] == 170
    snap_t1 = led.snapshot("s1", turn_id="t1")
    assert snap_t1["turn"]["cumulative"]["output_tokens"] == 100
