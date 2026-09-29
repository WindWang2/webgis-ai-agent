"""统一 capability policy 入口(H05)。

内置工具执行闸(#1402:CREDENTIALS_REQUIRED / PERMISSION_DENIED)与扩展
broker 授权(broker.py default-deny)此前各自内联布尔判定 —— 同一个
「capability 现在能不能跑」的问题存在两个裁决点。本模块把它们收敛为
**单一纯函数评估入口**:

- 输入是 facts(调用方自己收集的 presence/granted/certification/health
  快照),输出是 typed 决策 —— 本模块不做任何 I/O、不读 env、不读
  ContextVar(facts 收集面各归各家:lib tool_security / registry /
  extensions_platform / capability_runtime.health);
- reason 词表即 DoD 词表:credential_missing / permission_denied /
  certification_stale / provider_unhealthy / policy_denied;
- error_code 保持既有闸面词(CREDENTIALS_REQUIRED / PERMISSION_DENIED),
  registry 闸重构后消息与码逐位不变(回归网 = 既有测试)。

纪律:缺席面不裁决 —— required 缺席(空 required 列表/空权限声明)恒
allowed;certification/health 缺席(unknown/空)不参与裁决(不虚构)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

__all__ = [
    "POLICY_OK",
    "REASON_CREDENTIAL_MISSING",
    "REASON_PERMISSION_DENIED",
    "REASON_CERTIFICATION_STALE",
    "REASON_PROVIDER_UNHEALTHY",
    "REASON_POLICY_DENIED",
    "CODE_CREDENTIALS_REQUIRED",
    "CODE_PERMISSION_DENIED",
    "CapabilityPolicyFact",
    "CapabilityPolicyDecision",
    "evaluate_capability_policy",
]


#: 决策 reason 词表(证据/快照/测试共用;id 级,无参数无凭证)。
POLICY_OK = "ok"
REASON_CREDENTIAL_MISSING = "credential_missing"
REASON_PERMISSION_DENIED = "permission_denied"
REASON_CERTIFICATION_STALE = "certification_stale"
REASON_PROVIDER_UNHEALTHY = "provider_unhealthy"
REASON_POLICY_DENIED = "policy_denied"

#: 既有执行闸错误码(逐位保持 —— registry 闸的对外契约)。
CODE_CREDENTIALS_REQUIRED = "CREDENTIALS_REQUIRED"
CODE_PERMISSION_DENIED = "PERMISSION_DENIED"

#: certification state 词表(pack_catalog.certification_status_for 投影;
#: unknown = 证据缺席,不参与裁决)。
_CERT_BLOCKING = frozenset({"stale", "invalid"})


@dataclass(frozen=True)
class CapabilityPolicyFact:
    """单次 capability 使用裁决的输入事实(全部由调用方收集)。"""

    #: 工具/操作声明的凭证 id(空 = 无凭证要求)。
    required_credentials: Tuple[str, ...] = ()
    #: 当前作用域已 present 的凭证 id。
    credentials_present: Tuple[str, ...] = ()
    #: 声明的单一权限(空 = 无权限要求;registry required_permission 语义)。
    required_permission: str = ""
    #: 当前作用域已授予的权限集合。
    granted_permissions: Tuple[str, ...] = ()
    #: certification 状态投影(valid/missing/stale/invalid/unknown)。
    certification_state: str = ""
    #: provider 健康投影(closed/open/half_open;"" = 未咨询,不裁决)。
    health_state: str = ""
    #: 扩展/worker 域 fail-closed 语义(扩展面 unknown 也拒绝;核心面 unknown 放行)。
    fail_closed: bool = False
    #: bounded 补充披露(只进 reasons/detail,id 级)。
    detail: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CapabilityPolicyDecision:
    """policy 裁决(immutable;可进证据 —— 只含 id 级事实)。"""

    allowed: bool
    #: 粗粒度结果码(POLICY_OK / REASON_* 词表)。
    reason_code: str = POLICY_OK
    #: 既有闸面错误码(CREDENTIALS_REQUIRED / PERMISSION_DENIED;allowed 时空)。
    error_code: str = ""
    #: 全部命中原因(有界;first = 主因)。
    reasons: Tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason_code": self.reason_code,
            "error_code": self.error_code,
            "reasons": list(self.reasons[:4]),
        }


def evaluate_capability_policy(fact: CapabilityPolicyFact) -> CapabilityPolicyDecision:
    """单一裁决点:凭证 → 权限 → certification → 健康的确定性合取。

    顺序即披露顺序(第一个命中 = 主因)。certification/health 缺席面
    (unknown/空)不裁决;``fail_closed=True``(扩展/worker 域)时
    certification unknown 同样拒绝 —— 默认最小权限,显式失败。
    """
    reasons = []
    missing = [
        c for c in fact.required_credentials
        if c and c not in fact.credentials_present
    ]
    if missing:
        reasons.append(REASON_CREDENTIAL_MISSING)
    if fact.required_permission and fact.required_permission not in fact.granted_permissions:
        reasons.append(REASON_PERMISSION_DENIED)
    cert_state = (fact.certification_state or "").strip().lower()
    if cert_state in _CERT_BLOCKING:
        reasons.append(REASON_CERTIFICATION_STALE)
    elif cert_state == "unknown" and fact.fail_closed:
        reasons.append(REASON_CERTIFICATION_STALE)
    if fact.health_state == "open":
        reasons.append(REASON_PROVIDER_UNHEALTHY)

    if not reasons:
        return CapabilityPolicyDecision(allowed=True)

    first = reasons[0]
    error_code = ""
    if first == REASON_CREDENTIAL_MISSING:
        error_code = CODE_CREDENTIALS_REQUIRED
    elif first == REASON_PERMISSION_DENIED:
        error_code = CODE_PERMISSION_DENIED
    return CapabilityPolicyDecision(
        allowed=False,
        reason_code=first,
        error_code=error_code,
        reasons=tuple(reasons[:4]),
    )
