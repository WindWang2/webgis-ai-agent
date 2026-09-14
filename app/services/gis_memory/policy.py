"""记忆写策略（R2）：evidence-gated、fail-closed、偏好路由收敛。

「记忆永远滞后证据一个身位」（ADR-0069 决策 2）在本模块落成门槛：
- 证据源必须在 kind 的允许矩阵内（contract 校验）；
- 置信度达标（显式用户来源阈值低一档——人说的比猜的重）；
- TTL 解析（kind 默认 / 显式覆盖；失败记忆必须带过期）；
- 路由：项目作用域的用户制图偏好**不落新表**，改道 ADR-0069 账本
  （决策 D4——不建第二套 preference DB）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.services.gis_memory.contract import (
    KIND_USER_CARTO_PREF,
    RULE_MANUAL,
    RULE_TTL,
    SCOPE_PROJECT,
    SOURCE_USER_CORRECTION,
    SOURCE_USER_DECISION,
    kind_default_ttl_s,
    kind_base_weight,
    semantic_fingerprint,
    MemoryPolicyError,
    MemoryWriteRequest,
)

#: 普通生产证据的最低置信度。
MIN_CONFIDENCE = 0.6
#: 显式用户来源（纠正/决策）的最低置信度——人的显式表达天然高置信。
MIN_CONFIDENCE_EXPLICIT = 0.5

_EXPLICIT_SOURCES = (SOURCE_USER_CORRECTION, SOURCE_USER_DECISION)

ROUTE_GIS_MEMORY = "gis_memory"
ROUTE_CARTO_PROJECT_FACT = "carto_project_fact"


@dataclass(frozen=True)
class PolicyVerdict:
    """一次写入请求的策略裁决（允许时携带解析后的派生字段）。"""

    allowed: bool
    reason: str
    route: str = ROUTE_GIS_MEMORY
    ttl_s: Optional[int] = None
    invalidation_rule: str = RULE_MANUAL
    fingerprint: str = ""


def evaluate(req: MemoryWriteRequest) -> PolicyVerdict:
    """完整写入门（R2）。拒绝即返回 allowed=False（调用方记日志丢弃）。"""
    try:
        req.validate()
    except MemoryPolicyError as exc:
        return PolicyVerdict(allowed=False, reason=f"contract: {exc}")

    threshold = (
        MIN_CONFIDENCE_EXPLICIT
        if req.evidence.source in _EXPLICIT_SOURCES
        else MIN_CONFIDENCE
    )
    if float(req.confidence) < threshold:
        return PolicyVerdict(
            allowed=False,
            reason=(
                f"confidence {req.confidence:.2f} < 门槛 {threshold:.2f}"
                f"（source={req.evidence.source}）"
            ),
        )

    if req.ttl_s is not None and req.ttl_s <= 0:
        return PolicyVerdict(allowed=False, reason="ttl_s 必须为正或 None")

    ttl = req.ttl_s if req.ttl_s is not None else kind_default_ttl_s(req.kind)
    # 失效规则解析：显式声明优先；未声明时有 TTL → ttl 规则，无 TTL → manual
    # （由 dataset_version / scope_gone / 显式撤销收口，绝不「按 TTL 失效却永不过期」）。
    if req.invalidation_rule is not None:
        rule = req.invalidation_rule
    else:
        rule = RULE_TTL if ttl is not None else RULE_MANUAL
    if rule == RULE_TTL and ttl is None:
        return PolicyVerdict(
            allowed=False, reason="invalidation_rule=ttl 但 TTL 解析为 None"
        )

    route = ROUTE_GIS_MEMORY
    if req.kind == KIND_USER_CARTO_PREF and req.scope == SCOPE_PROJECT:
        # D4：项目制图偏好收敛到 ADR-0069 账本（conflict/expires/LRU 纪律已评审）。
        route = ROUTE_CARTO_PROJECT_FACT

    fingerprint = req.fingerprint or semantic_fingerprint(
        req.kind, req.subject, req.value
    )
    return PolicyVerdict(
        allowed=True,
        reason="ok",
        route=route,
        ttl_s=ttl,
        invalidation_rule=rule,
        fingerprint=fingerprint,
    )


__all__ = [
    "MIN_CONFIDENCE",
    "MIN_CONFIDENCE_EXPLICIT",
    "ROUTE_GIS_MEMORY",
    "ROUTE_CARTO_PROJECT_FACT",
    "PolicyVerdict",
    "evaluate",
    "kind_base_weight",
]
