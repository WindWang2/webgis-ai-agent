"""确定性模型路由（ADR-0102 Wave 4, §19/§20）。

路由是**策略求值**，不是「AI 选 AI」：输入（角色 profile + 描述符 + 健康
快照 + 请求约束）→ 确定性输出（选中模型 + 理由码 + 降级链 + 预算）。同
输入必同输出；默认路径永远是「operator 配置的主模型」。

降级链规则（§20 全部覆盖）：
- 只沿角色 profile.fallbacks 与同 fallback_group 的描述符顺序展开；
- 能力护栏：require_tools=True 时 tool_calling=False 的候选**跳过并留痕**
  （reason code capability_skip:<model>:tools）—— 绝不静默降到无工具模型；
- 健康护栏：冷却中的候选跳过并留痕（health_skip:<model>:cooldown）；
  近期限流的候选排后（不打 zero-out，只是排序劣后）；
- 能力不匹配（context_too_large / invalid_tool_schema / unsupported）标记
  的候选排后 —— 这类失败重试同模型无意义；
- 链有界：最多 1 主 + N 候选（profile.max_attempts 只管主模型内重试）；
  无可用候选时返回主模型 + no_capable_fallback 理由码（诚实地单次尝试，
  而不是无声跨能力降级）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from app.services.chat.llm_client import LLMConfig
from app.services.chat.model_config import ModelRole, resolve_llm_config
from app.services.chat.model_runtime.descriptors import (
    ModelDescriptor,
    get_model_descriptor_registry,
)
from app.services.chat.model_runtime.health import get_llm_provider_health
from app.services.chat.model_runtime.provider import FailureKind
from app.services.chat.model_runtime.roles import ModelRoleProfile, get_role_profile

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RouteDecision:
    """路由结果（trace/replay/debug bundle 的标准负载）。"""

    role: str
    provider_id: str
    model_id: str
    reason_codes: Tuple[str, ...]
    fallback_chain: Tuple[str, ...]
    profile: ModelRoleProfile
    descriptor: ModelDescriptor
    estimated_output_tokens: Optional[int] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "reason_codes": list(self.reason_codes),
            "fallback_chain": list(self.fallback_chain),
            "max_attempts": self.profile.max_attempts,
        }


@dataclass
class RouteRequest:
    role: str = "execution"
    require_tools: Optional[bool] = None       # None = 按 role profile
    require_json: Optional[bool] = None
    est_context_tokens: Optional[int] = None   # 估算上下文 → context window 过滤
    latency_budget_class: Optional[str] = None # fast|medium|soft 约束（可选）
    prefer_model: Optional[str] = None         # operator/会话级偏好（如 Pi set_model）


class ModelRouter:
    def __init__(
        self,
        registry=None,
        health=None,
        provider_id: str = "webgis",
    ) -> None:
        self._registry = registry or get_model_descriptor_registry()
        self._health = health or get_llm_provider_health()
        self._provider_id = provider_id

    # ------------------------------------------------------------------
    def route(self, req: RouteRequest) -> RouteDecision:
        profile = get_role_profile(req.role)
        require_tools = req.require_tools if req.require_tools is not None else profile.require_tools
        require_json = req.require_json if req.require_json is not None else profile.require_json

        # 主模型：既有单一解析点（runtime override > settings；角色级模型覆盖）
        role_enum = _legacy_role_enum(req.role)
        primary_cfg = resolve_llm_config(role_enum)
        primary_model = req.prefer_model or primary_cfg.model
        reasons: List[str] = []
        if req.prefer_model and req.prefer_model != primary_cfg.model:
            reasons.append("operator_preference")
        if primary_model == primary_cfg.model:
            reasons.append("primary_configured")

        candidates: List[str] = [primary_model]
        seen = {primary_model}
        for fb in profile.fallbacks:
            if fb not in seen:
                candidates.append(fb)
                seen.add(fb)
        for desc in self._registry.by_group(profile.preferred_group):
            if desc.model_id not in seen:
                candidates.append(desc.model_id)
                seen.add(desc.model_id)

        def _capable_skip(model: str) -> Optional[str]:
            desc = self._registry.resolve(model, self._provider_id)
            if require_tools and not desc.supports_tools():
                return f"capability_skip:{model}:tools"
            if require_json and desc.json_mode is False:
                return f"capability_skip:{model}:json"
            if (
                req.est_context_tokens
                and desc.context_window
                and req.est_context_tokens > int(desc.context_window * 0.9)
            ):
                return f"capability_skip:{model}:context"
            return None

        selected: Optional[str] = None
        healthy_chain: List[str] = []
        degraded_chain: List[str] = []
        for idx, model in enumerate(candidates):
            skip = _capable_skip(model)
            if skip:
                reasons.append(skip)
                continue
            if idx > 0:
                if not self._health.available(self._provider_id, model):
                    reasons.append(f"health_skip:{model}:cooldown")
                    continue
                # review R1 MAJOR：劣后语义真实实现 —— 限流/能力不匹配候选进
                # degraded 链，排在健康候选**之后**（此前同位追加，等于没排）。
                if self._health.has_capability_mismatch(self._provider_id, model):
                    reasons.append(f"health_deprioritize:{model}:mismatch")
                    degraded_chain.append(model)
                    continue
                if self._health.rate_limited_recently(self._provider_id, model):
                    reasons.append(f"health_deprioritize:{model}:rate_limit")
                    degraded_chain.append(model)
                    continue
                healthy_chain.append(model)
            else:
                # 主模型即使冷却也保留为选中（它就是 operator 决定的事实；
                # 冷却信息以 reason code 披露，由调用方决定是否快速失败）
                if not self._health.available(self._provider_id, model):
                    reasons.append(f"primary_cooldown:{int(self._health.cooldown_remaining(self._provider_id, model))}s")
                selected = model
        fallback_chain = healthy_chain + degraded_chain
        if selected is None:
            # 主模型被能力护栏跳过（如 prefer_model 非工具模型 + 要求工具）
            # → 沿链取第一个健康候选；整链不可用 → 回落主模型并如实留痕。
            if fallback_chain:
                selected = fallback_chain[0]
                reasons.append("fallback_first_capable")
            else:
                selected = primary_model
                reasons.append("no_capable_fallback")

        desc = self._registry.resolve(selected, self._provider_id)
        max_output = desc.max_output or profile.max_output_tokens
        return RouteDecision(
            role=req.role,
            provider_id=self._provider_id,
            model_id=selected,
            reason_codes=tuple(reasons),
            fallback_chain=tuple(fallback_chain),
            profile=profile,
            descriptor=desc,
            estimated_output_tokens=max_output,
        )

    # ------------------------------------------------------------------
    def resolve_config(self, req: RouteRequest) -> Tuple[LLMConfig, RouteDecision]:
        """路由 + 组装 LLMConfig（与 resolve_llm_config 输出同构，叠加
        角色 profile 的超时/采样/输出预算）。"""
        decision = self.route(req)
        role_enum = _legacy_role_enum(req.role)
        cfg = resolve_llm_config(role_enum)
        profile = decision.profile
        if decision.model_id != cfg.model:
            cfg = LLMConfig(
                base_url=cfg.base_url,
                model=decision.model_id,
                api_key=cfg.api_key,
                use_prompt_caching=cfg.use_prompt_caching,
                max_tokens=decision.estimated_output_tokens or cfg.max_tokens,
                temperature=profile.temperature if profile.temperature is not None else cfg.temperature,
                timeout_s=profile.timeout_s or cfg.timeout_s,
            )
        else:
            cfg = LLMConfig(
                base_url=cfg.base_url,
                model=cfg.model,
                api_key=cfg.api_key,
                use_prompt_caching=cfg.use_prompt_caching,
                max_tokens=profile.max_output_tokens or cfg.max_tokens,
                temperature=profile.temperature if profile.temperature is not None else cfg.temperature,
                timeout_s=profile.timeout_s or cfg.timeout_s,
            )
        return cfg, decision

    # ------------------------------------------------------------------
    def observe(
        self,
        decision: RouteDecision,
        *,
        latency_s: float = 0.0,
        failure: Optional[FailureKind] = None,
    ) -> None:
        """调用方回报结果（成功/失败）→ 健康表 + trace fallback 事件。"""
        if failure is not None:
            try:
                from app.lib.runtime.context import current_runtime_context
                from app.lib.runtime.trace import (
                    EVENT_FALLBACK,
                    get_trace_registry,
                )

                _rt = current_runtime_context()
                _turn = getattr(_rt, "turn_id", "") if _rt else ""
                if _turn:
                    get_trace_registry().emit(
                        _turn, EVENT_FALLBACK,
                        model=decision.model_id, failure=failure.value,
                        role=decision.role,
                    )
            except Exception:  # noqa: BLE001
                pass
        if failure is None:
            self._health.record_success(
                decision.provider_id, decision.model_id, latency_s=latency_s
            )
        else:
            self._health.record_failure(
                decision.provider_id, decision.model_id, kind=failure.value
            )


def _legacy_role_enum(role: str) -> ModelRole:
    """新角色名 → 既有 ModelRole（execution/planner/title/spatial 保持同映射；
    新角色缺省回落 EXECUTION 三元组）。"""
    try:
        return ModelRole(role)
    except ValueError:
        return ModelRole.EXECUTION


_router = ModelRouter()


def get_model_router() -> ModelRouter:
    return _router
