"""G37（会话模型运行配置）单元/协议级测试。

覆盖：
- 目录构建/代际/凭据可见性（WP01-02，M07/M08）；
- preference CAS 与并发冲突（WP03，M11）；
- message_end usage 提炼/合计口径/费用来源（WP08-09，M20/M21/M24/M28）；
- 账本幂等（(turn_id, call_seq) 唯一 → 重放不重复计费，M23）；
- 能力门（M16/M17）与预算 admission（M30）；
- stream_prompt 全路径（协议 stub RPC）：应用→回读→model_status SSE→
  prompt 发出；预算拒绝 → prompt 零发送；应用失败 → 不假成功（M10/M09）。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.db_model import Base, ChatTurnRuntime, ChatUsageRecord, Conversation
from app.services.chat.model_runtime import session_catalog
from app.services.chat.model_runtime.session_model_prefs import (
    RevisionConflict,
    get_pref,
    set_pref,
)
from app.services.chat.model_runtime.session_model_service import (
    apply_session_model,
    budget_admission,
    capability_gate,
)
from app.services.chat.model_runtime.usage_ledger import (
    MessageUsageFact,
    TurnUsageCollector,
    estimate_cost_micro_usd,
    extract_usage_fact,
    persist_turn_records,
    reset_price_table_cache,
)
from tests.fixtures.pi_mocks import make_agent_settled, make_token_event


# ── 公共 fixture ──────────────────────────────────────────────────

@pytest_asyncio.fixture
async def db_factory(tmp_path, monkeypatch):
    """隔离 sqlite 引擎 + create_all；并把 ``async_db_session`` 指到它。"""
    from app.core import database as db_mod

    eng = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'g37_unit.db'}",
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    fac = async_sessionmaker(bind=eng, expire_on_commit=False)

    class _Factory:
        async def __aenter__(self):
            self._db = fac()
            return self._db

        async def __aexit__(self, *exc):
            await self._db.close()

    import contextlib

    @contextlib.asynccontextmanager
    async def _patched_session():
        async with fac() as db:
            yield db

    monkeypatch.setattr(db_mod, "async_db_session", _patched_session)
    yield fac
    await eng.dispose()


def _catalog_env(monkeypatch, *, base="gpt-base", extra="", key="k-test"):
    monkeypatch.setattr(
        "app.services.chat.model_config._runtime_overrides", {}, raising=False
    )
    monkeypatch.setattr(
        "app.core.config.settings.LLM_MODEL", base, raising=False
    )
    monkeypatch.setattr(
        "app.core.config.settings.SESSION_MODEL_CATALOG_EXTRA", extra, raising=False
    )
    monkeypatch.setattr(
        "app.core.config.settings.LLM_API_KEY", key, raising=False
    )


class _StubRpc:
    """协议级 RPC stub：记录命令、按脚本回 get_state。"""

    def __init__(self, state_model=("webgis", "gpt-base"), thinking="off",
                 fail_commands=()):
        self.calls: list[tuple[str, dict]] = []
        self.state_model = state_model
        self.thinking = thinking
        self.fail_commands = set(fail_commands)
        self.events = asyncio.Queue()
        self.process_died = False
        self.spawn_epoch = 1
        self.runtime_epoch = "test-epoch-1"

    async def request(self, cmd, data=None):
        self.calls.append((cmd, dict(data or {})))
        if cmd in self.fail_commands:
            raise RuntimeError(f"{cmd} failed (stub)")
        if cmd == "get_state":
            provider, model = self.state_model
            return {
                "model": ({"id": model, "provider": provider} if model else None),
                "thinkingLevel": self.thinking,
                "isStreaming": False,
            }
        return {}


def _message_end_event(role="assistant", model="gpt-base", response_model=None,
                       usage=None, stop="stop"):
    message = {
        "role": role,
        "provider": "webgis",
        "model": model,
        "usage": usage or {},
        "stopReason": stop,
    }
    if response_model:
        message["responseModel"] = response_model
    return {"type": "message_end", "message": message}


# ── WP01/02：目录 ─────────────────────────────────────────────────

def test_catalog_base_model_always_present(monkeypatch):
    _catalog_env(monkeypatch, base="m-base")
    items = session_catalog.build_catalog()
    assert [i.item_id for i in items] == ["m-base"]
    assert items[0].is_default and items[0].available


def test_catalog_extra_models_same_endpoint(monkeypatch):
    _catalog_env(monkeypatch, base="m-base", extra="m-fast, m-pro")
    items = session_catalog.build_catalog()
    assert [i.item_id for i in items] == ["m-base", "m-fast", "m-pro"]
    assert all(i.provider_id == "webgis" for i in items)


def test_catalog_duplicate_and_base_dedup(monkeypatch):
    _catalog_env(monkeypatch, base="m-base", extra="m-base, m-pro, m-pro")
    items = session_catalog.build_catalog()
    assert [i.item_id for i in items] == ["m-base", "m-pro"]


def test_catalog_credential_missing_marks_unavailable(monkeypatch):
    _catalog_env(monkeypatch, base="m-base", extra="m-pro", key="your-api-key-here")
    items = session_catalog.build_catalog()
    assert all(not i.available for i in items)
    assert {i.unavailable_reason for i in items} == {"credential_missing"}


def test_catalog_invalid_extra_id_rejected_not_hidden(monkeypatch):
    _catalog_env(monkeypatch, base="m-base", extra="../evil model, m-ok")
    items = session_catalog.build_catalog()
    by_id = {i.item_id: i for i in items}
    assert not by_id["../evil model"].available
    assert by_id["../evil model"].unavailable_reason == "invalid_id"
    assert by_id["m-ok"].available


def test_catalog_generation_changes_with_membership(monkeypatch):
    _catalog_env(monkeypatch, base="m-base", extra="")
    g1 = session_catalog.catalog_generation()
    _catalog_env(monkeypatch, base="m-base", extra="m-pro")
    g2 = session_catalog.catalog_generation()
    _catalog_env(monkeypatch, base="m-base", extra="m-pro")
    g3 = session_catalog.catalog_generation()
    assert g1 != g2
    assert g2 == g3  # 同目录同代
    assert len(g1) == 16


def test_validate_model_id_rejects_injection():
    for bad in ["a b", "", "x" * 200, "a\nb", "a\tb", '{"json":1}', "a;b"]:
        with pytest.raises(session_catalog.CatalogItemIdError):
            session_catalog.validate_model_id(bad)
    # vendor 风格 id（含 / 与 .）合法 —— 只进 JSON 载体，无文件路径语义
    assert session_catalog.validate_model_id("org/model.v2") == "org/model.v2"


# ── WP03：preference CAS ─────────────────────────────────────────

async def test_pref_defaults_to_inherit(db_factory):
    async with db_factory() as db:
        pref = await get_pref(db, "conv-1")
    assert pref.mode == "inherit" and pref.revision == 0


async def test_pref_cas_first_write_and_update(db_factory):
    async with db_factory() as db:
        p1 = await set_pref(db, "conv-1", mode="explicit",
                            catalog_item_id="m-pro", expected_revision=0)
        assert p1.revision == 1
        p2 = await set_pref(db, "conv-1", mode="inherit", expected_revision=1)
        assert p2.revision == 2 and p2.mode == "inherit"


async def test_pref_cas_conflict_is_explicit(db_factory):
    async with db_factory() as db:
        await set_pref(db, "conv-1", mode="explicit", catalog_item_id="a", expected_revision=0)
        # 两个标签都拿 revision=1 竞争：一个赢，一个明确冲突
        winner = await set_pref(db, "conv-1", mode="explicit", catalog_item_id="b", expected_revision=1)
        assert winner.catalog_item_id == "b"
        with pytest.raises(RevisionConflict):
            await set_pref(db, "conv-1", mode="explicit", catalog_item_id="c", expected_revision=1)


async def test_pref_concurrent_first_write_single_winner(db_factory):
    # 两个并发写入者各持独立会话（对应两个 HTTP 标签的真实形态）
    async def _write(item: str):
        async with db_factory() as db:
            return await set_pref(
                db, "conv-race", mode="explicit", catalog_item_id=item,
                expected_revision=0,
            )

    results = await asyncio.gather(_write("a"), _write("b"), return_exceptions=True)
    winners = [r for r in results if not isinstance(r, Exception)]
    conflicts = [r for r in results if isinstance(r, RevisionConflict)]
    assert len(winners) == 1 and len(conflicts) >= 1
    # 胜者写入了它自己的选择（无静默丢失）
    assert winners[0].catalog_item_id in ("a", "b")


async def test_pref_invalid_inputs_rejected(db_factory):
    async with db_factory() as db:
        with pytest.raises(ValueError):
            await set_pref(db, "conv-x", mode="explicit", expected_revision=0)
        with pytest.raises(ValueError):
            await set_pref(db, "conv-x", mode="weird", expected_revision=0)
        with pytest.raises(ValueError):
            await set_pref(db, "conv-x", mode="explicit", catalog_item_id="m",
                           thinking_level="ultra", expected_revision=0)


# ── WP08/09：usage 提炼与账本 ────────────────────────────────────

def test_extract_assistant_usage_keeps_dimensions():
    fact = extract_usage_fact(_message_end_event(
        model="m-pro", response_model="m-pro-2026",
        usage={"input": 100, "output": 40, "reasoning": 12, "cacheRead": 5,
               "cacheWrite": 7, "cacheWrite1h": 3, "totalTokens": 152,
               "cost": {"total": 0.001, "totalFromProvider": True}},
    ))
    assert fact.kind == "assistant"
    assert fact.usage["input"] == 100 and fact.usage["reasoning"] == 12
    assert fact.cost_total_micro_usd == 1000  # 0.001 USD → 1000 micro-USD
    assert fact.cost_from_provider is True
    assert fact.response_model == "m-pro-2026"
    assert fact.stop_reason == "stop"


def test_extract_tool_result_nested_usage_once():
    fact = extract_usage_fact({
        "type": "message_end",
        "message": {"role": "toolResult", "toolCallId": "t1", "usage": {"input": 10, "output": 2}},
    })
    assert fact.kind == "tool_result"
    assert fact.usage["input"] == 10


def test_extract_ignores_messages_without_usage():
    assert extract_usage_fact({"type": "message_end", "message": {"role": "user", "content": "hi"}}) is None
    assert extract_usage_fact({"type": "message_update", "message": {}}) is None
    # toolResult 无 usage（无 nested 模型工作）→ 不入账
    assert extract_usage_fact({"type": "message_end", "message": {"role": "toolResult"}}) is None


def test_collector_totals_do_not_double_count_reasoning():
    c = TurnUsageCollector("s", "t")
    c.observe(_message_end_event(usage={
        "input": 100, "output": 50, "reasoning": 20, "cacheRead": 0, "cacheWrite": 0,
    }))
    totals = c.totals
    # reasoning ⊂ output（vendor 语义）：合计不把 reasoning 再加一遍
    assert totals["total"] == 150
    assert totals["output"] == 50


def test_collector_two_identical_calls_count_twice():
    c = TurnUsageCollector("s", "t")
    usage = {"input": 10, "output": 5}
    c.observe(_message_end_event(usage=usage))
    c.observe(_message_end_event(usage=usage))  # 文本/数值完全相同 → 仍两次
    assert c.totals["input"] == 20 and c.expected_records == 2


def test_cost_source_unknown_when_no_price(monkeypatch):
    monkeypatch.delenv("SESSION_MODEL_PRICE_TABLE_JSON", raising=False)
    reset_price_table_cache()
    c = TurnUsageCollector("s", "t")
    c.observe(_message_end_event(usage={"input": 10, "output": 5}))  # 无 cost 字段
    micro, source = c.cost_view
    assert micro is None and source == "unknown"


def test_cost_source_estimated_with_price_table(monkeypatch):
    monkeypatch.setenv(
        "SESSION_MODEL_PRICE_TABLE_JSON",
        json.dumps({"m-pro": {"input_usd_per_1m": 1.0, "output_usd_per_1m": 2.0}}),
    )
    reset_price_table_cache()
    fact = MessageUsageFact(
        kind="assistant", provider="webgis", model="m-pro", response_model=None,
        usage={"input": 1_000_000, "output": 500_000, "cacheRead": 0, "cacheWrite": 0,
               "cacheWrite1h": None, "reasoning": None, "totalTokens": None},
        cost_total_micro_usd=None, cost_from_provider=None, stop_reason="stop",
    )
    est = estimate_cost_micro_usd(fact)
    assert est == 2_000_000  # 1M*1 + 0.5M*2 = 2 USD
    c = TurnUsageCollector("s", "t")
    c.facts.append(fact)
    assert c.cost_view == (2_000_000, "estimated")
    reset_price_table_cache()


def test_cost_zero_without_price_is_unknown_not_free(monkeypatch):
    """M29：runtime 无价目的默认零 → unknown，绝不显示 estimated $0/免费。"""
    monkeypatch.delenv("SESSION_MODEL_PRICE_TABLE_JSON", raising=False)
    reset_price_table_cache()
    c = TurnUsageCollector("s", "t")
    c.observe(_message_end_event(usage={
        "input": 10, "output": 5,
        "cost": {"total": 0, "totalFromProvider": False},
    }))
    micro, source = c.cost_view
    assert micro is None and source == "unknown"
    reset_price_table_cache()


def test_cost_source_provider_reported_preferred_over_estimate(monkeypatch):
    monkeypatch.setenv(
        "SESSION_MODEL_PRICE_TABLE_JSON",
        json.dumps({"m-pro": {"input_usd_per_1m": 1.0, "output_usd_per_1m": 2.0}}),
    )
    reset_price_table_cache()
    c = TurnUsageCollector("s", "t")
    c.observe(_message_end_event(model="m-pro", usage={
        "input": 100, "output": 50, "cost": {"total": 0.0005, "totalFromProvider": True},
    }))
    assert c.cost_view == (500, "provider")
    reset_price_table_cache()


async def test_persist_idempotent_on_replay(db_factory):
    """(turn_id, call_seq) 唯一：同一事件集合落盘两次只记一次（M23）。"""
    c = TurnUsageCollector("sess-1", "turn-1", "run-1")
    c.observe(_message_end_event(usage={"input": 10, "output": 5}))
    c.observe(_message_end_event(role="toolResult", usage={"input": 3, "output": 1}))
    assert await persist_turn_records(c, status="applied", requested_model="m-pro",
                                      effective_model="m-pro", finalize=True)
    # 重放（SSE 重连/重复回调语义）：再落一次不得重复计费
    assert await persist_turn_records(c, status="applied", requested_model="m-pro",
                                      effective_model="m-pro", finalize=True)
    async with db_factory() as db:
        turns = (await db.execute(select(ChatTurnRuntime))).scalars().all()
        records = (await db.execute(select(ChatUsageRecord))).scalars().all()
    assert len(turns) == 1 and turns[0].tokens_total == 19 and turns[0].complete
    assert len(records) == 2
    kinds = {r.kind for r in records}
    assert kinds == {"assistant", "tool_result"}


async def test_persist_incomplete_on_write_failure(db_factory, monkeypatch):
    """持久失败 → complete=False（面显示 incomplete，M32）。"""
    from app.core import database as db_mod

    async def _boom():
        raise RuntimeError("db down")

    @property
    def _broken(self):
        raise RuntimeError("db down")

    monkeypatch.setattr(db_mod, "async_db_session", lambda: _raise_ctx())

    def _raise_ctx():
        class _C:
            async def __aenter__(self):
                raise RuntimeError("db down")
            async def __aexit__(self, *a):
                return False
        return _C()

    c = TurnUsageCollector("sess-1", "turn-2")
    c.observe(_message_end_event(usage={"input": 10, "output": 5}))
    ok = await persist_turn_records(c, status="applied", finalize=True)
    assert ok is False and c.write_failures == 1


# ── WP06/WP10：能力门与预算 ───────────────────────────────────────

def test_capability_gate_rejects_no_tools():
    item = session_catalog.CatalogItem(
        item_id="m-raw", provider_id="webgis", is_default=False, available=True,
        tool_calling=False,
    )
    ok, code, _ = capability_gate(item, needs_tools=True, context_estimate_tokens=100)
    assert not ok and code == "tools_unsupported"


def test_capability_gate_unknown_tools_passes_conservative_default():
    item = session_catalog.CatalogItem(
        item_id="m-x", provider_id="webgis", is_default=False, available=True,
        tool_calling=None,
    )
    ok, code, _ = capability_gate(item, needs_tools=True, context_estimate_tokens=100)
    assert ok and code == ""


def test_capability_gate_context_window():
    item = session_catalog.CatalogItem(
        item_id="m-small", provider_id="webgis", is_default=False, available=True,
        context_window=1000,
    )
    ok, code, detail = capability_gate(item, needs_tools=True, context_estimate_tokens=2000)
    assert not ok and code == "context_window_insufficient"
    ok2, _, _ = capability_gate(item, needs_tools=True, context_estimate_tokens=500)
    assert ok2
    # 窗口未知（None）→ 不猜，放行（能力来源诚实 unknown）
    item_unknown = session_catalog.CatalogItem(
        item_id="m-unk", provider_id="webgis", is_default=False, available=True)
    ok3, _, _ = capability_gate(item_unknown, needs_tools=True, context_estimate_tokens=10**9)
    assert ok3


async def test_budget_admission_rejects_before_prompt(db_factory, monkeypatch):
    monkeypatch.setattr(
        "app.core.config.settings.SESSION_MODEL_TOKEN_CAP_MAX", 100000, raising=False
    )
    monkeypatch.setattr(
        "app.core.config.settings.SESSION_MODEL_TOKEN_CAP_DEFAULT", 1000, raising=False
    )
    async with db_factory() as db:
        db.add(ChatTurnRuntime(
            session_id="sess-b", turn_id="t-prior", status="applied",
            requested_model="m", tokens_total=900,
        ))
        await db.commit()
        verdict = await budget_admission(db, "sess-b", projected_tokens=200)
        assert not verdict["admitted"]
        assert verdict["reason_code"] == "session_token_budget_exceeded"
        assert verdict["cumulative"] == 900 and verdict["projected"] == 200
        verdict_ok = await budget_admission(db, "sess-b", projected_tokens=50)
        assert verdict_ok["admitted"] and verdict_ok["remaining"] == 100


async def test_budget_admission_no_cap_admits_and_discloses(db_factory, monkeypatch):
    monkeypatch.setattr(
        "app.core.config.settings.SESSION_MODEL_TOKEN_CAP_DEFAULT", 0, raising=False
    )
    async with db_factory() as db:
        verdict = await budget_admission(db, "sess-c", projected_tokens=10**9)
    assert verdict["admitted"] and verdict["cap"] is None


# ── WP05：应用编排（协议 stub）────────────────────────────────────

async def test_apply_explicit_model_set_model_and_readback(db_factory, monkeypatch):
    _catalog_env(monkeypatch, base="m-base", extra="m-pro")
    rpc = _StubRpc(state_model=("webgis", "m-pro"))
    async with db_factory() as db:
        await set_pref(db, "conv-a", mode="explicit", catalog_item_id="m-pro",
                       expected_revision=0)
        result = await apply_session_model(
            rpc, db, session_id="conv-a", turn_id="t1", bound_message="分析成都人口",
            force_apply=True,
        )
    assert result.ok and result.status == "applied"
    assert result.effective_model == "m-pro"
    commands = [c for c, _ in rpc.calls]
    assert "set_model" in commands and "get_state" in commands
    set_model_args = dict(rpc.calls[commands.index("set_model")][1])
    assert set_model_args == {"provider": "webgis", "modelId": "m-pro"}
    # prompt 前序：set_model 先于 get_state（回读确认后才允许 prompt）
    assert commands.index("set_model") < commands.index("get_state")


async def test_apply_readback_mismatch_refuses(db_factory, monkeypatch):
    """set_model 成功响应后 get_state 不匹配 → 拒绝伪生效（M10）。"""
    _catalog_env(monkeypatch, base="m-base", extra="m-pro")
    rpc = _StubRpc(state_model=("webgis", "m-base"))  # 仍是旧模型
    async with db_factory() as db:
        await set_pref(db, "conv-a", mode="explicit", catalog_item_id="m-pro",
                       expected_revision=0)
        result = await apply_session_model(
            rpc, db, session_id="conv-a", turn_id="t2", bound_message="x",
            force_apply=True,
        )
    assert not result.ok
    assert result.reason_code == "pi_readback_mismatch"
    assert result.effective_model == "m-base"  # 如实暴露现状


async def test_apply_explicit_set_model_failure_refuses(db_factory, monkeypatch):
    """explicit 选择 + set_model 失败 → 拒绝（不假成功、不静默旧模型）。"""
    _catalog_env(monkeypatch, base="m-base", extra="m-pro")
    rpc = _StubRpc(fail_commands={"set_model"})
    async with db_factory() as db:
        await set_pref(db, "conv-a", mode="explicit", catalog_item_id="m-pro",
                       expected_revision=0)
        result = await apply_session_model(
            rpc, db, session_id="conv-a", turn_id="t-fail", bound_message="x",
            force_apply=True,
        )
    assert not result.ok and result.reason_code == "pi_apply_failed"
    commands = [c for c, _ in rpc.calls]
    assert "set_model" in commands and "get_state" not in commands


async def test_apply_inherit_set_model_failure_degrades(db_factory, monkeypatch):
    """inherit + set_model 失败 → 不阻断；get_state 继续验证（M09 披露式降级）。"""
    _catalog_env(monkeypatch, base="m-base")
    rpc = _StubRpc(fail_commands={"set_model"})
    rpc.state_model = ("webgis", "m-base")
    async with db_factory() as db:
        result = await apply_session_model(
            rpc, db, session_id="conv-d", turn_id="t-deg", bound_message="x",
            force_apply=True,
        )
    assert result.ok and result.status == "inherit_default"
    commands = [c for c, _ in rpc.calls]
    assert "set_model" in commands and "get_state" in commands


async def test_apply_revoked_catalog_item_rejected(db_factory, monkeypatch):
    """目录撤销后 preference 指向不存在项 → 明确拒绝，不静默跨 provider（M12）。"""
    _catalog_env(monkeypatch, base="m-base", extra="")
    rpc = _StubRpc()
    async with db_factory() as db:
        # 模拟历史 preference（目录曾含 m-pro，现撤销）
        await set_pref(db, "conv-a", mode="explicit", catalog_item_id="m-pro",
                       expected_revision=0)
        result = await apply_session_model(
            rpc, db, session_id="conv-a", turn_id="t3", bound_message="x",
            force_apply=True,
        )
    assert not result.ok and result.reason_code == "catalog_item_unavailable"
    assert not any(c == "set_model" for c, _ in rpc.calls)  # 不发 set_model
    assert not any(c == "prompt" for c, _ in rpc.calls)


async def test_apply_inherit_uses_default_without_set_model_when_current(
    db_factory, monkeypatch,
):
    _catalog_env(monkeypatch, base="m-base")
    rpc = _StubRpc(state_model=("webgis", "m-base"))
    async with db_factory() as db:
        result = await apply_session_model(
            rpc, db, session_id="conv-fresh", turn_id="t4",
            bound_message="hi", force_apply=False,
        )
    assert result.ok and result.status == "inherit_default"
    assert not any(c == "set_model" for c, _ in rpc.calls)
    assert result.effective_model == "m-base"  # get_state 回读真值


# ── WP05/WP08：stream_prompt 全路径（协议 stub）───────────────────

def _bridge_with_stub_rpc(rpc, applied_state=None):
    from app.agent_pi_bridge import PiBridge

    bridge = PiBridge(rpc=rpc)
    if applied_state is not None:
        bridge._vendor_model_state = applied_state
    return bridge


async def _collect_sse(bridge, message="hi", session_id="conv-s"):
    out = []
    async for ev in bridge.stream_prompt(message, session_id=session_id):
        out.append(ev)
    return out


def _parse_events(raw_events):
    parsed = []
    for chunk in raw_events:
        lines = chunk.strip().split("\n")
        ev = None
        data = ""
        for ln in lines:
            if ln.startswith("event: "):
                ev = ln[7:]
            elif ln.startswith("data: "):
                data += ln[6:]
        if ev:
            try:
                parsed.append((ev, json.loads(data) if data else {}))
            except json.JSONDecodeError:
                parsed.append((ev, data))
    return parsed


async def test_stream_prompt_emits_model_status_and_applies(db_factory, monkeypatch):
    _catalog_env(monkeypatch, base="m-base", extra="m-pro")
    rpc = _StubRpc(state_model=("webgis", "m-pro"))

    async def _seed_prompt(cmd, data=None):
        if cmd == "prompt":
            await rpc.events.put(make_token_event("分析中"))
            await rpc.events.put(make_agent_settled())
        return {}

    # 无 preference → inherit 默认模型 m-base；get_state 必须回读同值。
    state_rpc = _StubRpc(state_model=("webgis", "m-base"))

    async def _request(cmd, data=None):
        state_rpc.calls.append((cmd, dict(data or {})))
        if cmd == "prompt":
            await rpc.events.put(make_token_event("分析中"))
            await rpc.events.put(_message_end_event(model="m-base", response_model="m-base-v2", usage={"input": 12, "output": 8}))
            await rpc.events.put(make_agent_settled())
            return {}
        if cmd == "get_state":
            provider, model = state_rpc.state_model
            return {"model": {"id": model, "provider": provider}, "thinkingLevel": "off"}
        return {}

    rpc.request = _request
    bridge = _bridge_with_stub_rpc(rpc)
    events = await _collect_sse(bridge, session_id="conv-s")
    parsed = _parse_events(events)
    types = [e for e, _ in parsed]
    assert "model_status" in types
    status_payload = next(p for e, p in parsed if e == "model_status")
    # inherit 模式（无 preference）→ 默认模型；无凭证信息泄漏
    assert status_payload["status"] in ("inherit_default", "degraded_inherit")
    payload_json = json.dumps(status_payload)
    assert "api_key" not in payload_json and "your-api-key" not in payload_json
    assert types[-1] == "done"


async def test_stream_prompt_budget_rejected_sends_no_prompt(db_factory, monkeypatch):
    """预算拒绝 → prompt 零发送（stub 请求计数不增加，M30 反向证明）。"""
    _catalog_env(monkeypatch, base="m-base")
    monkeypatch.setattr(
        "app.core.config.settings.SESSION_MODEL_TOKEN_CAP_DEFAULT", 50, raising=False
    )
    rpc = _StubRpc()
    prompt_calls = []

    async def _request(cmd, data=None):
        rpc.calls.append((cmd, dict(data or {})))
        if cmd == "prompt":
            prompt_calls.append(dict(data or {}))
            await rpc.events.put(make_agent_settled())
            return {}
        if cmd == "get_state":
            return {"model": {"id": "m-base", "provider": "webgis"}, "thinkingLevel": "off"}
        return {}

    rpc.request = _request
    # 预置超额累计
    async with db_factory() as db:
        db.add(ChatTurnRuntime(
            session_id="conv-budget", turn_id="t-old", status="applied",
            requested_model="m-base", tokens_total=10**6,
        ))
        await db.commit()

    bridge = _bridge_with_stub_rpc(rpc)
    events = await _collect_sse(bridge, session_id="conv-budget")
    parsed = _parse_events(events)
    types = [e for e, _ in parsed]
    assert prompt_calls == []  # prompt RPC 从未发出
    err = next(p for e, p in parsed if e == "task_error")
    assert err["error_class"] == "policy_reject"
    assert types[-1] == "done"
    model_status = next(p for e, p in parsed if e == "model_status")
    assert model_status["status"] == "budget_rejected"


async def test_stream_prompt_records_usage_on_settle(db_factory, monkeypatch):
    _catalog_env(monkeypatch, base="m-base")
    rpc = _StubRpc()

    async def _request(cmd, data=None):
        rpc.calls.append((cmd, dict(data or {})))
        if cmd == "prompt":
            await rpc.events.put(make_token_event("ok"))
            await rpc.events.put(_message_end_event(
                model="m-base", response_model="m-base-v2",
                usage={"input": 100, "output": 60, "reasoning": 10,
                       "cacheRead": 5, "cacheWrite": 0,
                       "cost": {"total": 0.002, "totalFromProvider": True}},
            ))
            await rpc.events.put(make_agent_settled())
            return {}
        if cmd == "get_state":
            return {"model": {"id": "m-base", "provider": "webgis"}, "thinkingLevel": "off"}
        return {}

    rpc.request = _request
    bridge = _bridge_with_stub_rpc(rpc)
    await _collect_sse(bridge, session_id="conv-u")
    # fire-and-forget 落盘：等后台任务完成
    for _ in range(50):
        await asyncio.sleep(0.05)
        async with db_factory() as db:
            row = (await db.execute(
                select(ChatTurnRuntime).where(ChatTurnRuntime.session_id == "conv-u")
            )).scalar_one_or_none()
            rec = (await db.execute(
                select(ChatUsageRecord).where(ChatUsageRecord.session_id == "conv-u")
            )).scalars().all()
            if row is not None and row.settled_at is not None:
                break
    assert row is not None
    assert row.status in ("inherit_default", "degraded_inherit")
    assert row.tokens_input == 100 and row.tokens_output == 60
    assert row.tokens_total == 165  # 100+60+5（reasoning ⊂ output 不另加）
    assert row.response_model == "m-base-v2"
    assert row.cost_total == 2000 and row.cost_source == "provider"
    assert row.complete
    assert len(rec) == 1 and rec[0].call_seq == 0


# ── WP03（路由语义）：目录外模型写入被拒（route 层规则复现）──────

async def test_put_preference_rejects_models_outside_catalog(monkeypatch):
    """PUT 语义：explicit 只接受目录内可选项（M07/M08）。"""
    from fastapi import HTTPException
    from app.api.routes.session_model_views import ModelPreferenceUpdate, put_session_model

    _catalog_env(monkeypatch, base="m-base")
    req = ModelPreferenceUpdate(mode="explicit", model_id="not-in-catalog",
                                expected_revision=0)
    conv = SimpleNamespace(id="conv-1")
    with pytest.raises(HTTPException) as exc_info:
        await put_session_model("conv-1", req, _conv=conv, db=MagicMock())
    assert exc_info.value.status_code == 404
