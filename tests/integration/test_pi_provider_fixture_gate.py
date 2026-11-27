"""WP1/WP2（GOAL-20261127-01）：可控 provider fixture 驱动真实 vendored RPC。

任务书 WP1 验收语料的 fixture 门：mock HTTP provider（真实 HTTP，不 mock
dispatch）× 真实 v1.6.1 rpc-entry，产出 **route/attempt/usage 证据链对照**：

- F1 恢复链：429×2 → vendor auto-retry → 恢复成功 —— 结构化 provider-obs
  行 ×2（精确 status/retry_after）+ auto_retry 事件链 + message_end usage
  事实 + 宿主消费链（parse → 桥 → 健康表 rate_limit 位）逐环可核；
- F2 耗尽链：持续 429 → 重试预算耗尽的诚实失败 —— obs ×≥3 + finalError +
  健康表连续失败计数（cooldown 开启 → 下次路由自动规避的证据源）。

与既有 vendor contract 车道的关系：本文件**不重复**断言 wire 形状（那里
已锁 auto_retry/agent_settled 语义），只新增「结构化观测 → 路由健康权威」
的消费闭环证据（WP2 增量），并按任务书要求把 route-receipt/usage/重试链
证据对照落成机器可读报告（pytest -s 复核）。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def _load_contract_module():
    path = Path(__file__).parent / "test_pi_vendor_contract.py"
    spec = importlib.util.spec_from_file_location("_pi_provider_fixture_mod", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_C = _load_contract_module()
pytestmark = [
    _C.pytest.mark.heavy,
    _C.pytest.mark.skipif(bool(_C._needs_build()), reason=_C._needs_build() or "vendor built"),
]


@pytest.fixture()
def make_harness(tmp_path):
    """与 vendor contract 车道同款 harness 工厂（stub 环境须在 boot 前注入）。"""
    made = []

    def _make(**kwargs):
        h = _C._VendorHarness(0, tmp_path, **kwargs)  # 0 = OS 分配端口
        h.start_stub()
        made.append(h)
        return h

    try:
        yield _make
    finally:
        for h in made:
            h.stop_stub()


def _parse_obs_lines(provider_events):
    """stderr 行 → 结构化观测（宿主 parse_provider_observation 同款纯函数）。"""
    from app.services.chat.pi_rpc_client import parse_provider_observation

    parsed = []
    for line in provider_events or []:
        obs = parse_provider_observation(line)
        if obs is not None:
            parsed.append(obs)
    return parsed


def _extract_usage_fact(events):
    """message_end 事件 → 宿主账本记账事实（usage 链证据）。"""
    from app.services.chat.model_runtime.usage_ledger import extract_usage_fact

    facts = []
    for event in events:
        fact = extract_usage_fact(event)
        if fact is not None:
            facts.append(fact)
    return facts


def test_provider_fixture_recovery_chain(make_harness):
    """F1：429×2 → 重试恢复 —— 观测/事件/usage/健康四链对照（真实 RPC）。"""
    from app.services.chat.model_runtime.health import get_llm_provider_health
    from app.services.chat.model_runtime.provider import classify_status_failure

    harness = make_harness(mode="provider-fail-then-succeed")
    result = harness.run_turn("fixture recovery chain turn")
    events = _C._events(result)

    # 链1：结构化 provider-obs（每 attempt 精确信号）
    obs = _parse_obs_lines(result.get("providerEvents"))
    assert [o["status"] for o in obs] == [429, 429], obs
    assert all(o["retry_after"] is not None for o in obs), obs

    # 链2：auto_retry 事件链（结构化兜底通道）
    starts = [e for e in events if e.get("type") == "auto_retry_start"]
    assert [s.get("attempt") for s in starts] == [1, 2], starts
    ends = [e for e in events if e.get("type") == "auto_retry_end"]
    assert len(ends) == 1 and ends[0].get("success") is True, ends
    settled = [e for e in events if e.get("type") == "agent_settled"]
    assert len(settled) == 1 and settled[0].get("aborted") is not True, settled

    # 链3：usage 事实（message_end → 账本提炼，assistant 主链一次）
    facts = _extract_usage_fact(events)
    assistant_facts = [f for f in facts if f.kind == "assistant"]
    assert assistant_facts, [f.kind for f in facts]
    assert all(f.model for f in assistant_facts), assistant_facts

    # 链4：宿主消费闭环（桥侧 _on_provider_observation 同语义）→ 健康表
    health = get_llm_provider_health()
    health.reset()
    try:
        model = assistant_facts[0].model or ""
        provider = assistant_facts[0].provider or "webgis"
        for o in obs:
            failure = classify_status_failure(o["status"])
            health.record_failure(provider, model, kind=failure.value)
        snap = health.snapshot()
        key = f"{provider}/{model}"
        assert key in snap, snap
        assert snap[key]["rate_limited_recently"] is True, snap[key]
        assert snap[key]["total_failure"] == 2, snap[key]
    finally:
        health.reset()

    report = {
        "chain_obs": obs,
        "chain_retry": {"starts": len(starts), "end_success": ends[0].get("success")},
        "chain_usage": {
            "model": assistant_facts[0].model,
            "input": assistant_facts[0].usage.get("input"),
            "output": assistant_facts[0].usage.get("output"),
            "cost_source": (
                "provider" if assistant_facts[0].cost_from_provider
                else ("estimated" if assistant_facts[0].cost_total_micro_usd is not None else "unknown")
            ),
        },
        "chain_health": {"rate_limited_recently": True, "total_failure": 2},
    }
    print("FIXTURE_REPORT " + json.dumps(report, ensure_ascii=False, sort_keys=True))


def test_provider_fixture_exhaustion_chain(make_harness):
    """F2：持续 429 → 重试预算耗尽 → 诚实失败 + 健康表冷却证据。"""
    from app.services.chat.model_runtime.health import get_llm_provider_health
    from app.services.chat.model_runtime.provider import classify_status_failure

    harness = make_harness(mode="always-429")
    result = harness.run_turn("fixture exhaustion chain turn")
    events = _C._events(result)

    obs = _parse_obs_lines(result.get("providerEvents"))
    assert len(obs) >= 3, obs
    assert all(o["status"] == 429 for o in obs), obs

    ends = [e for e in events if e.get("type") == "auto_retry_end"]
    assert ends and ends[-1].get("success") is False, ends
    assert str(ends[-1].get("finalError") or ""), ends[-1]
    settled = [e for e in events if e.get("type") == "agent_settled"]
    assert len(settled) == 1, settled

    # 健康表：连续 429 → 退避计数达阈值 → 冷却开启（下次路由规避的证据源）
    health = get_llm_provider_health()
    health.reset()
    try:
        for o in obs:
            failure = classify_status_failure(o["status"])
            health.record_failure("webgis", "m-fixture", kind=failure.value)
        snap = health.snapshot()["webgis/m-fixture"]
        assert snap["total_failure"] >= 3
        assert snap["rate_limited_recently"] is True
        assert snap["available"] is False, snap  # 冷却开启
        assert snap["cooldown_remaining_s"] > 0, snap
    finally:
        health.reset()

    # 耗尽分支的 message_end 是诚实失败面：stop_reason='error'、金额 unknown
    # （vendor usage 零值不被宿主冒充为「确认免费」—— M29 语义）。
    facts = [f for f in _extract_usage_fact(events) if f.kind == "assistant"]
    assert all(f.stop_reason == "error" for f in facts), facts
    assert all(f.cost_total_micro_usd is None and f.cost_from_provider is not True
               for f in facts), facts
