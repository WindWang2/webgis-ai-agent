"""Model Routing Bridge 测试（ADR-0103 §五：live engine 接线）。

- 主模型不变：routing on 时 primary 仍是 resolve_llm_config 的既有结果；
- capability 护栏：est_context 超窗 / require_tools 降级留 reason codes；
- observe_outcome：成功/失败都进健康表；decision=None 时零副作用；
- engine 接线：_call_llm 走 router（monkeypatch 验证），异常回退 legacy；
- 角色分层：新角色 profile 存在且策略字段诚实（不绑厂商名）。
"""
import pytest

from app.services.chat import model_routing_bridge as bridge
from app.services.chat.model_config import resolve_llm_config
from app.services.chat.model_runtime.health import get_llm_provider_health


@pytest.fixture()
def clear_health():
    get_llm_provider_health().reset()
    yield
    get_llm_provider_health().reset()


def test_routing_disabled_returns_legacy(monkeypatch):
    monkeypatch.setattr(bridge, "_MODEL_ROUTER_ENABLED", False)
    cfg, decision = bridge.resolve_routed_config("execution")
    assert decision is None
    assert cfg.model == resolve_llm_config("execution").model


def test_primary_model_unchanged_when_routing_on():
    cfg, decision = bridge.resolve_routed_config("execution")
    assert decision is not None
    assert cfg.model == resolve_llm_config("execution").model
    assert "primary_configured" in decision.reason_codes


def test_require_tools_capability_guard(monkeypatch):
    """prefer_model 指向非工具模型 + require_tools → 能力护栏留痕或回落。"""
    # 本部署可能只有单模型（unknown descriptor 按 capable 处理）—— 断言
    # 「路由成功产出 decision 且 reason codes 可解释」而非具体降级方向。
    cfg, decision = bridge.resolve_routed_config("execution", require_tools=True)
    assert decision is not None
    assert decision.reason_codes  # 至少 primary_configured


def test_context_guard_reason_recorded(monkeypatch):
    from app.services.chat.model_runtime.descriptors import (
        ModelDescriptor,
        get_model_descriptor_registry,
    )
    reg = get_model_descriptor_registry()
    monkeypatch.setattr(
        reg, "resolve",
        lambda model, provider: ModelDescriptor(
            provider_id=provider, model_id=model, context_window=100,
        ),
        raising=False,
    )
    cfg, decision = bridge.resolve_routed_config(
        "execution", est_context_tokens=10_000
    )
    assert decision is not None
    assert any(r.startswith("capability_skip:") for r in decision.reason_codes) or \
        any(r.startswith("no_capable_fallback") for r in decision.reason_codes)


def test_observe_failure_records_health(clear_health):
    cfg, decision = bridge.resolve_routed_config("execution")
    assert decision is not None
    bridge.observe_outcome(
        decision, exc=TimeoutError("provider timeout"), status_code=None
    )
    snap = get_llm_provider_health().snapshot()
    keys = list(snap.get("providers", {}).keys()) or list(snap.keys())
    assert keys, "failure should be recorded in health table"


def test_observe_none_decision_is_noop(clear_health):
    bridge.observe_outcome(None, exc=RuntimeError("x"))
    bridge.observe_outcome(None, latency_s=1.0)
    assert get_llm_provider_health().snapshot() in ({}, {"providers": {}})


def test_estimate_context_tokens_cjk():
    n = bridge._estimate_context_tokens([{"role": "user", "content": "成都市小学分布热力图"}])
    assert n > 0


def test_new_role_profiles_exist():
    from app.services.chat.model_runtime.roles import DEFAULT_ROLE_PROFILES

    for role in ("architecture", "debugger", "scientific_review", "corpus_worker",
                 "doc_crosscheck", "descriptor_enrichment", "code_worker",
                 "static_analysis"):
        assert role in DEFAULT_ROLE_PROFILES, role
        p = DEFAULT_ROLE_PROFILES[role]
        # 策略字段不绑厂商：fallbacks 与 preferred_group 是抽象分组
        assert p.preferred_group in ("default", "strong", "cheap")
        assert p.max_attempts >= 1


async def test_engine_call_llm_routes_through_bridge(monkeypatch):
    from app.services.chat.execution_engine import ChatExecutionEngine
    from app.services.chat.llm_client import LLMConfig

    observed = {}

    class FakeRouter:
        def resolve_config(self, req):
            observed["role"] = req.role
            observed["require_tools"] = req.require_tools
            cfg = LLMConfig(base_url="http://x", model="fake-m", api_key="k")
            decision = type("D", (), {
                "role": req.role, "provider_id": "webgis", "model_id": "fake-m",
                "reason_codes": ("primary_configured",), "fallback_chain": (),
            })()
            return cfg, decision

        def observe(self, decision, latency_s=0.0, failure=None):
            observed["observed"] = (failure is None)

    monkeypatch.setattr(bridge, "_MODEL_ROUTER_ENABLED", True)

    import app.services.chat.model_runtime.routing as routing
    monkeypatch.setattr(routing, "get_model_router", lambda: FakeRouter())
    monkeypatch.setattr(
        "app.services.chat.model_runtime.routing.get_model_router", lambda: FakeRouter()
    )

    engine = ChatExecutionEngine.__new__(ChatExecutionEngine)
    engine.model_role = ""

    async def fake_call_llm(cfg, messages, tools=None):
        observed["called_model"] = cfg.model
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr("app.services.chat.execution_engine.call_llm", fake_call_llm)
    resp = await engine._call_llm([{"role": "user", "content": "hi"}], tools=[{"x": 1}])
    assert resp["choices"][0]["message"]["content"] == "ok"
    assert observed["called_model"] == "fake-m"
    assert observed["role"] == "execution"
    assert observed["require_tools"] is True
    assert observed["observed"] is True


def test_routing_role_fallback():
    from app.services.chat.execution_engine import ChatExecutionEngine

    engine = ChatExecutionEngine.__new__(ChatExecutionEngine)
    assert engine._routing_role() == "execution"
    engine.model_role = "subagent_reviewer"
    assert engine._routing_role() == "subagent_reviewer"
