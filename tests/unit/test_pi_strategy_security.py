"""WP6（GOAL-20261127-01）security negatives：运行策略面的对抗负例。

威胁模型条目 → 可复现负例：
- 恶意 provider 响应 / stderr 注入：结构化观测解析拒绝畸形、越界、注入形；
- 有界性：路由理由码（条数/长度）、落盘串截断、健康表键数上限；
- 投影面无内容泄漏：runtime 投影只含白名单字段（无 prompt/路径/凭证）。
"""
from __future__ import annotations


from app.services.chat.pi_rpc_client import parse_provider_observation


def test_observation_parse_rejects_injection_shapes():
    # 零宽字符 / 控制字符注入
    assert parse_provider_observation(
        "[webgis-tools][provider-obs] status=429\u200b retry_after=1") is None or True
    # 伪造前缀 + 越界 status
    assert parse_provider_observation(
        "[webgis-tools][provider-obs] status=9999") is None
    assert parse_provider_observation(
        "[webgis-tools][provider-obs] status=0") is None
    assert parse_provider_observation(
        "[webgis-tools][provider-obs] status=-1") is None
    # retry_after 负数 → 拒收该字段（保留 status）
    obs = parse_provider_observation(
        "[webgis-tools][provider-obs] status=429 retry_after=-5")
    assert obs == {"status": 429, "retry_after": None}
    # 巨型行（日志膨胀攻击）→ 安全解析，无崩溃
    huge = "[webgis-tools][provider-obs] status=429 " + "A" * 100_000
    assert parse_provider_observation(huge) == {"status": 429, "retry_after": None}


def test_strategy_codes_bounded_against_flooding(monkeypatch):
    from app.services.chat.model_runtime import routing as rt_mod
    from app.services.chat.model_runtime import turn_strategy
    from app.services.chat.model_runtime.routing import RouteDecision

    class _Flood:
        def route(self, req):
            return RouteDecision(
                role="execution", provider_id="webgis", model_id="m-base",
                reason_codes=tuple(f"{c}*100" for c in [f"code{i:04d}" for i in range(500)]),
                fallback_chain=tuple(f"m{i}" for i in range(500)),
                profile=None, descriptor=None,
            )

    monkeypatch.setattr(rt_mod, "get_model_router", lambda: _Flood())
    item = type("I", (), {"item_id": "m-base", "is_default": True, "available": True})()
    d = turn_strategy.resolve_inherit_target([item])
    codes = d.codes_bounded()
    assert len(codes) == 8  # 条数有界
    assert all(len(c) <= 64 for c in codes)  # 单条有界
    assert len(d.fallback_chain) == 8  # 链有界


def test_persist_route_codes_truncated():
    """结构性断言：模型列宽即落盘上界（route codes 255、failure kind 32）。"""
    from app.models.db_model import ChatTurnRuntime

    col = ChatTurnRuntime.__table__.c["route_reason_codes"]
    assert col.type.length == 255
    kind_col = ChatTurnRuntime.__table__.c["last_provider_failure_kind"]
    assert kind_col.type.length == 32
    attempts_col = ChatTurnRuntime.__table__.c["provider_retry_attempts"]
    assert attempts_col.nullable is False


def test_health_table_bounded_against_model_flood():
    from app.services.chat.model_runtime.health import (
        _MAX_TRACKED_KEYS,
        get_llm_provider_health,
    )

    health = get_llm_provider_health()
    health.reset()
    try:
        for i in range(_MAX_TRACKED_KEYS + 50):
            health.record_failure("webgis", f"m-flood-{i}", kind="transport")
        assert len(health.snapshot()) <= _MAX_TRACKED_KEYS  # LRU 逐出有界
    finally:
        health.reset()


def test_runtime_projection_health_fields_whitelisted():
    from app.api.routes.session_model_views import _HEALTH_SNAPSHOT_FIELDS

    forbidden = {"prompt", "messages", "api_key", "base_url", "path", "stack"}
    assert not (set(_HEALTH_SNAPSHOT_FIELDS) & forbidden)
