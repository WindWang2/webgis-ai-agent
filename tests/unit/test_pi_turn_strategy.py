"""WP2（GOAL-20261127-01）unit：Pi inherit 运行时模型选择策略。

覆盖：
- RouteRequest.candidate_models 候选池语义（显式池替代 fallbacks/描述符组池；
  授权边界不逃逸 —— D7）；
- turn_strategy.resolve_inherit_target：策略关闭 / 全健康取默认 / 冷却降级 /
  降级链排序 / 无可用候选诚实回落 / 路由器异常回落 / 不可用目录项不入池；
- apply_session_model inherit 接线：非默认目标必须 set_model + 回读；
  explicit 路径零策略介入（行为不变）；strategy_codes 有界进 SSE。
"""
from __future__ import annotations


import pytest
import pytest_asyncio

from app.services.chat.model_runtime import routing as rt_mod
from app.services.chat.model_runtime import turn_strategy
from app.services.chat.model_runtime.routing import RouteDecision, RouteRequest


# ── 假目录项（与 session_catalog.CatalogItem 同字段面） ─────────────


def _item(mid, *, is_default=False, available=True, tools=None, window=None):
    from types import SimpleNamespace

    return SimpleNamespace(
        item_id=mid,
        provider_id="webgis",
        is_default=is_default,
        available=available,
        unavailable_reason="" if available else "credential_missing",
        context_window=window,
        max_output=None,
        tool_calling=tools,
        reasoning=None,
        vision=None,
        cost_class="unknown",
        capability_source="defaults",
    )


class _FakeRouter:
    """脚本化路由器：捕获请求，返回预置决策。"""

    def __init__(self, decision):
        self.decision = decision
        self.requests: list[RouteRequest] = []

    def route(self, req):
        self.requests.append(req)
        return self.decision


def _decision(model_id, codes=(), chain=()):
    return RouteDecision(
        role="execution",
        provider_id="webgis",
        model_id=model_id,
        reason_codes=tuple(codes),
        fallback_chain=tuple(chain),
        profile=None,
        descriptor=None,
    )


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("PI_MODEL_HEALTH_ROUTING", raising=False)
    monkeypatch.delenv("MODEL_ROUTER_ENABLED", raising=False)


# ── RouteRequest.candidate_models（S2a） ────────────────────────────


def test_route_request_candidate_models_is_additive_default_none(clean_env):
    req = RouteRequest(role="execution")
    assert req.candidate_models is None
    req2 = RouteRequest(role="execution", candidate_models=("a", "b"))
    assert req2.candidate_models == ("a", "b")


# ── resolve_inherit_target（S2b） ───────────────────────────────────


def test_strategy_off_returns_default(monkeypatch, clean_env):
    monkeypatch.setenv("PI_MODEL_HEALTH_ROUTING", "0")
    catalog = [_item("m-base", is_default=True), _item("m-alt")]
    d = turn_strategy.resolve_inherit_target(catalog, needs_tools=True)
    assert d.selected_model == "m-base"
    assert not d.used_fallback
    assert "strategy_off" in d.reason_codes


def test_router_error_falls_back_to_default(monkeypatch, clean_env):
    class _Boom:
        def route(self, req):
            raise RuntimeError("router down")

    monkeypatch.setattr(rt_mod, "get_model_router", lambda: _Boom())
    catalog = [_item("m-base", is_default=True), _item("m-alt")]
    d = turn_strategy.resolve_inherit_target(catalog, needs_tools=True)
    assert d.selected_model == "m-base"
    assert "strategy_error" in d.reason_codes


def test_unavailable_items_excluded_from_candidate_pool(monkeypatch, clean_env):
    fake = _FakeRouter(_decision("m-base", codes=("primary_configured",)))
    monkeypatch.setattr(rt_mod, "get_model_router", lambda: fake)
    catalog = [
        _item("m-base", is_default=True),
        _item("m-dead", available=False),
        _item("m-alt"),
    ]
    d = turn_strategy.resolve_inherit_target(catalog, needs_tools=True)
    assert d.selected_model == "m-base"
    pool = fake.requests[0].candidate_models
    assert pool == ("m-base", "m-alt")  # 不可用项不入池（授权边界）
    assert "primary_configured" in d.reason_codes


def test_codes_bounded(monkeypatch, clean_env):
    fake = _FakeRouter(_decision("m-base", codes=tuple(f"c{i}" for i in range(20))))
    monkeypatch.setattr(rt_mod, "get_model_router", lambda: fake)
    d = turn_strategy.resolve_inherit_target([_item("m-base", is_default=True)])
    codes = d.codes_bounded()
    assert len(codes) == 8
    assert all(len(c) <= 64 for c in codes)


def test_no_default_item_reports_reason(clean_env):
    d = turn_strategy.resolve_inherit_target([_item("m-alt")])
    assert d.item is None
    assert "strategy_no_default" in d.reason_codes


# ── 真实 ModelRouter 语义（候选池=目录） ────────────────────────────


def test_real_router_cooldown_selects_healthy_extra(monkeypatch, clean_env):
    """默认项冷却 → 健康的目录内候选被选中（used_fallback=True）。"""
    monkeypatch.setattr("app.core.config.settings.LLM_MODEL", "m-base", raising=False)
    health = rt_mod.get_model_router()._health
    health.reset()
    try:
        for _ in range(3):
            health.record_failure("webgis", "m-base", kind="provider_unavailable")
        assert not health.available("webgis", "m-base")
        fake_registry = type("R", (), {"resolve": staticmethod(
            lambda mid, provider_id="webgis": type("D", (), {
                "model_id": mid,
                "context_window": 128000,
                "max_output": 4096,
                "support_tools_result": True,
                "supports_tools": lambda self, assume_for_unknown=True: True,
                "json_mode": None,
            })(),
        ), "by_group": lambda self, group: []})()
        router = rt_mod.ModelRouter(registry=fake_registry, health=health)
        monkeypatch.setattr(rt_mod, "get_model_router", lambda: router)
        catalog = [_item("m-base", is_default=True, window=128000), _item("m-alt", window=128000)]
        d = turn_strategy.resolve_inherit_target(catalog, needs_tools=True)
        assert d.selected_model == "m-alt"
        assert d.used_fallback
        # 主模型（候选 0）冷却 → primary_cooldown 留痕；整链无健康主选 →
        # fallback_first_capable 取首个健康候选（路由权威既有词表）。
        assert any(c.startswith("primary_cooldown") for c in d.reason_codes)
        assert "fallback_first_capable" in d.reason_codes
    finally:
        health.reset()


def test_real_router_no_capable_fallback_is_honest(monkeypatch, clean_env):
    """整链硬资格不过（require_tools + 非 tool 模型）→ 默认项 + no_capable_fallback。"""
    monkeypatch.setattr("app.core.config.settings.LLM_MODEL", "m-base", raising=False)
    health = rt_mod.get_model_router()._health
    health.reset()
    try:
        fake_registry = type("R", (), {"resolve": staticmethod(
            lambda mid, provider_id="webgis": type("D", (), {
                "model_id": mid,
                "context_window": 128000,
                "max_output": 4096,
                "supports_tools": lambda self, assume_for_unknown=True: False,
                "json_mode": None,
            })(),
        ), "by_group": lambda self, group: []})()
        router = rt_mod.ModelRouter(registry=fake_registry, health=health)
        monkeypatch.setattr(rt_mod, "get_model_router", lambda: router)
        catalog = [_item("m-base", is_default=True, tools=False, window=128000),
                   _item("m-alt", tools=False, window=128000)]
        d = turn_strategy.resolve_inherit_target(catalog, needs_tools=True)
        assert d.selected_model == "m-base"
        assert not d.used_fallback
        assert "no_capable_fallback" in d.reason_codes
        assert any(c.startswith("capability_skip:m-alt") for c in d.reason_codes)
    finally:
        health.reset()


# ── apply_session_model 接线（inherit 非默认目标必须 set_model） ────


class _StubRpc:
    def __init__(self, state_model=("webgis", "m-base")):
        self.calls = []
        self.state_model = state_model

    async def request(self, cmd, data=None):
        self.calls.append((cmd, dict(data or {})))
        if cmd == "get_state":
            provider, model = self.state_model
            return {"model": {"id": model, "provider": provider}, "thinkingLevel": "off"}
        return {}


@pytest_asyncio.fixture
async def g37_db(tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core import database as db_mod
    from app.models.db_model import Base

    eng = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'strategy.db'}",
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


def _patch_catalog(monkeypatch, base="m-base", extra="m-alt"):
    monkeypatch.setattr(
        "app.services.chat.model_config._runtime_overrides", {}, raising=False)
    monkeypatch.setattr("app.core.config.settings.LLM_MODEL", base, raising=False)
    monkeypatch.setattr(
        "app.core.config.settings.SESSION_MODEL_CATALOG_EXTRA", extra, raising=False)
    monkeypatch.setattr("app.core.config.settings.LLM_API_KEY", "k-test", raising=False)


@pytest.mark.asyncio
async def test_apply_inherit_fallback_sets_model_and_reports_codes(
    monkeypatch, clean_env, g37_db,
):
    """策略选中非默认目录项 → set_model 实际发出 + strategy_codes 进 SSE。"""
    _patch_catalog(monkeypatch)
    fake = _FakeRouter(_decision("m-alt", codes=("health_skip:m-base:cooldown",)))
    monkeypatch.setattr(rt_mod, "get_model_router", lambda: fake)
    rpc = _StubRpc(state_model=("webgis", "m-alt"))

    from app.services.chat.model_runtime.session_model_service import apply_session_model

    async with g37_db() as db:
        result = await apply_session_model(
            rpc, db, session_id="s1", turn_id="t1", bound_message="hi",
        )
    assert result.ok
    assert result.mode == "inherit"
    assert result.requested_model == "m-alt"
    assert result.status == "inherit_routed"  # 非默认降级应用 ≠ 继承默认
    set_calls = [c for c in rpc.calls if c[0] == "set_model"]
    assert set_calls and set_calls[0][1] == {"provider": "webgis", "modelId": "m-alt"}
    assert result.strategy_codes == ["health_skip:m-base:cooldown"]
    payload = result.sse_payload("s1", "t1")
    assert payload["route"]["codes"] == ["health_skip:m-base:cooldown"]
    assert payload["route"]["selected"] == "m-alt"


@pytest.mark.asyncio
async def test_apply_explicit_never_uses_strategy(monkeypatch, clean_env, g37_db):
    """explicit 偏好：策略零介入（目录重验证 + 回读，行为与现状一致）。"""
    _patch_catalog(monkeypatch)
    from app.services.chat.model_runtime.session_model_prefs import set_pref

    calls = {"n": 0}

    class _Boom:
        def route(self, req):
            calls["n"] += 1
            raise AssertionError("strategy must not run on explicit path")

    monkeypatch.setattr(rt_mod, "get_model_router", lambda: _Boom())
    rpc = _StubRpc(state_model=("webgis", "m-alt"))
    async with g37_db() as db:
        await set_pref(
            db, "s2", mode="explicit",
            catalog_item_id="m-alt", expected_revision=0,
        )
        from app.services.chat.model_runtime.session_model_service import (
            apply_session_model,
        )

        result = await apply_session_model(
            rpc, db, session_id="s2", turn_id="t2", bound_message="hi",
        )
    assert result.ok and result.mode == "explicit"
    assert result.requested_model == "m-alt"
    assert result.strategy_codes == []
    assert calls["n"] == 0


@pytest.mark.asyncio
async def test_apply_inherit_default_skips_set_model(monkeypatch, clean_env, g37_db):
    """全健康 → 选中默认项 → 与现状一致（不 set_model，status=inherit_default）。"""
    _patch_catalog(monkeypatch)
    fake = _FakeRouter(_decision("m-base", codes=("primary_configured",)))
    monkeypatch.setattr(rt_mod, "get_model_router", lambda: fake)
    rpc = _StubRpc(state_model=("webgis", "m-base"))
    from app.services.chat.model_runtime.session_model_service import apply_session_model

    async with g37_db() as db:
        result = await apply_session_model(
            rpc, db, session_id="s3", turn_id="t3", bound_message="hi",
        )
    assert result.ok and result.status == "inherit_default"
    assert not [c for c in rpc.calls if c[0] == "set_model"]
    assert result.strategy_codes == ["primary_configured"]
    payload = result.sse_payload("s3", "t3")
    assert "route" in payload
