"""Model Runtime → Live Engine 路由桥（ADR-0103 §五）。

ADR-0102 交付了完整的模型运行时（descriptors / roles / health / 确定性
router），但 engine 一直直走 ``resolve_llm_config`` 单点 —— router 是孤岛。
本桥把 live 路由接上，同时保持向后兼容：

- **主模型不变**：router 的 primary 就是 ``resolve_llm_config(role)`` 的
  既有结果（operator 配置 > runtime override）；router 只在其上加能力护栏
  （context window / tool calling / json）与健康降级链（fallback_group +
  role profile fallbacks），并对每次调用产出可留痕的 reason codes；
- **失败即回退**：任何 router 异常 → 既有 ``resolve_llm_config`` 路径
  （老部署零破坏）；``MODEL_ROUTER_ENABLED=0`` 整体关闭；
- **结果必须回报**：``observe_outcome`` 把 provider 失败分类写入健康表
  （breaker / cooldown / capability_mismatch）与 trace —— 无观测不降级。
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.services.chat.llm_client import LLMConfig
from app.services.chat.model_config import ModelRole

logger = logging.getLogger(__name__)

#: live 路由总开关（默认开；router 异常自动回退 legacy 单点解析）
_MODEL_ROUTER_ENABLED = os.getenv("MODEL_ROUTER_ENABLED", "1") != "0"


def routing_enabled() -> bool:
    return _MODEL_ROUTER_ENABLED


def _estimate_context_tokens(messages: Sequence[Dict[str, Any]]) -> int:
    """粗估 prompt token 数（CJK-aware，与 context_budget 同一估算语义）。"""
    try:
        from app.services.chat.context.history_compression import _estimate_tokens

        total = 0
        for m in messages:
            content = m.get("content") if isinstance(m, dict) else None
            if isinstance(content, str):
                total += _estimate_tokens(content)
            elif isinstance(content, list):
                # 多模态/分段结构：文本分段照常估算，非文本段按固定开销计
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        total += _estimate_tokens(part["text"])
                    else:
                        total += 16
        return total
    except Exception:  # noqa: BLE001 — 估算失败返回 0（router 视为未知）
        return 0


def resolve_routed_config(
    role: ModelRole | str,
    *,
    messages: Optional[Sequence[Dict[str, Any]]] = None,
    require_tools: Optional[bool] = None,
    require_json: Optional[bool] = None,
    est_context_tokens: Optional[int] = None,
) -> Tuple[LLMConfig, Optional[Any]]:
    """路由解析：返回 (LLMConfig, RouteDecision|None)。

    decision=None 表示走了 legacy 路径（router 关闭或异常）—— 调用方无需
    观察（无 decision 就无健康表更新，行为与历史一致）。
    """
    if est_context_tokens is None and messages:
        est_context_tokens = _estimate_context_tokens(messages)
    if not _MODEL_ROUTER_ENABLED:
        return _legacy_config(role), None
    try:
        from app.services.chat.model_runtime.routing import RouteRequest, get_model_router

        req = RouteRequest(
            role=str(getattr(role, "value", role)),
            require_tools=require_tools,
            require_json=require_json,
            est_context_tokens=est_context_tokens,
        )
        cfg, decision = get_model_router().resolve_config(req)
        _log_reasons(decision)
        return cfg, decision
    except Exception:  # noqa: BLE001 — 路由是增强，失败回退 legacy
        logger.debug("[model-routing] route failed; legacy path", exc_info=True)
        return _legacy_config(role), None


def _legacy_config(role: ModelRole | str) -> LLMConfig:
    from app.services.chat.model_config import resolve_llm_config

    return resolve_llm_config(role)


def _log_reasons(decision: Any) -> None:
    try:
        reasons = list(getattr(decision, "reason_codes", ()) or ())
        interesting = [r for r in reasons if not r.startswith("primary_configured")]
        if interesting:
            logger.info(
                "[model-routing] role=%s model=%s reasons=%s",
                getattr(decision, "role", "?"),
                getattr(decision, "model_id", "?"),
                interesting,
            )
    except Exception:  # noqa: BLE001
        pass
    # ADR-0103 §十：证据链 MODEL_ROUTING 阶段（有当前 runtime turn 才记）。
    try:
        from app.lib.runtime.context import current_runtime_context
        from app.lib.runtime.gis_trace import Stage, record_stage

        _rt = current_runtime_context()
        _turn = getattr(_rt, "turn_id", "") if _rt else ""
        if _turn:
            record_stage(
                _turn, Stage.MODEL_ROUTING,
                role=getattr(decision, "role", ""),
                model=getattr(decision, "model_id", ""),
                reasons=list(getattr(decision, "reason_codes", ()) or ())[:8],
                fallback_chain=list(getattr(decision, "fallback_chain", ()) or ())[:8],
            )
    except Exception:  # noqa: BLE001
        pass


def observe_outcome(
    decision: Optional[Any],
    *,
    latency_s: float = 0.0,
    exc: Optional[BaseException] = None,
    status_code: Optional[int] = None,
    finish_reason: Optional[str] = None,
) -> None:
    """调用结果回报（成功/失败 → 健康表 + trace）。绝不抛出。"""
    if decision is None:
        return
    try:
        from app.services.chat.model_runtime.provider import (
            classify_exception,
            classify_status_failure,
        )
        from app.services.chat.model_runtime.routing import get_model_router

        failure = None
        if exc is not None:
            failure = classify_exception(exc)
        elif status_code is not None and status_code >= 400:
            failure = classify_status_failure(status_code)
        get_model_router().observe(
            decision, latency_s=latency_s, failure=failure
        )
    except Exception:  # noqa: BLE001 — 观测绝不影响主流程
        logger.debug("[model-routing] observe failed", exc_info=True)


def health_snapshot() -> Dict[str, Any]:
    """当前健康表快照（评测/诊断用；只读）。"""
    try:
        from app.services.chat.model_runtime.health import get_llm_provider_health

        return get_llm_provider_health().snapshot()
    except Exception:  # noqa: BLE001
        return {}


class LatencyTimer:
    """简易时延计（observe_outcome 的 latency_s 来源）。"""

    def __init__(self) -> None:
        self.start = time.perf_counter()

    def elapsed(self) -> float:
        return time.perf_counter() - self.start
