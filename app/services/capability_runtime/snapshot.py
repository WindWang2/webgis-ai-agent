"""CapabilityRuntimeSnapshot — capability 运行时合成投影(H05)。

F06/F07 铺完了静态面(situation 供给 / presence 闸 / catalog schema /
认证管线),但 Agent 规划时看到的仍是「注册了什么」,不是「此刻真正能跑
什么」。本模块把五个运行时事实合成到 capability 粒度的**只读投影**:

    capability → providers → health → credential_presence → policy
              → certification → resource class → runtime_status

- **单一投影,不写身份**:catalog 的 kind:id 身份与语义指纹不动
  (F07 P2-2 纪律);快照只读引用 entry 的 certification/resource_class。
- **解析权威不变**:provider 候选与资格判定走 ``capability_status``
  (capability_resolution);本模块只叠加运行时事实,不做第二套资格裁决。
- **词表即 DoD**:available / degraded / unavailable / credential_missing /
  policy_denied。provider 级与 capability 级各出一态;capability 级取其
  provider 中最优路径的态(best-wins,providers 列表全披露每 provider 的态)。
- **有界 + 确定性**:capability ≤16、provider ≤8/能力、hints ≤4;
  排序确定性;失败面 fail-open(单能力合成失败 → unknown 事实,不抛)。
- **无 secret**:presence/id/状态/digest 级事实;与 F06 纪律一致。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from app.lib.capability_policy import (
    CapabilityPolicyFact,
    REASON_PROVIDER_UNHEALTHY,
    evaluate_capability_policy,
)
from app.lib.tool_security import (
    CredentialPresence,
    resolve_credential_presence,
    resolve_granted_permissions,
)

logger = logging.getLogger(__name__)

__all__ = [
    "STATUS_AVAILABLE",
    "STATUS_DEGRADED",
    "STATUS_UNAVAILABLE",
    "STATUS_CREDENTIAL_MISSING",
    "STATUS_POLICY_DENIED",
    "MAX_SNAPSHOT_CAPABILITIES",
    "MAX_SNAPSHOT_PROVIDERS",
    "ProviderRuntimeFact",
    "CapabilityRuntimeFact",
    "CapabilityRuntimeSnapshot",
    "build_capability_runtime_snapshot",
]

#: runtime_status 词表(DoD;证据/快照/工具面共用)。
STATUS_AVAILABLE = "available"
STATUS_DEGRADED = "degraded"
STATUS_UNAVAILABLE = "unavailable"
STATUS_CREDENTIAL_MISSING = "credential_missing"
STATUS_POLICY_DENIED = "policy_denied"

#: 聚合序(best-wins:列表里出现的最优 provider 态即 capability 态)。
_STATUS_PRECEDENCE = (
    STATUS_AVAILABLE,
    STATUS_DEGRADED,
    STATUS_CREDENTIAL_MISSING,
    STATUS_POLICY_DENIED,
    STATUS_UNAVAILABLE,
)

MAX_SNAPSHOT_CAPABILITIES = 16
MAX_SNAPSHOT_PROVIDERS = 8


@dataclass(frozen=True)
class ProviderRuntimeFact:
    """单 provider 运行时事实(id 级;无参数无凭证无 secret)。"""

    kind: str
    id: str
    qualification_status: str = "unknown"
    health_state: str = ""           # closed/open/half_open/""(未咨询)
    latency_bucket: str = "unknown"
    consecutive_failures: int = 0
    last_failure_class: str = ""
    required_credentials: Tuple[str, ...] = ()
    credential_missing: Tuple[str, ...] = ()
    required_permission: str = ""
    policy_allowed: bool = True
    policy_reason_code: str = ""
    certification_state: str = ""    # valid/missing/stale/invalid/unknown/""(无目录证据)
    resource_class: Tuple[Tuple[str, str], ...] = ()
    runtime_status: str = STATUS_UNAVAILABLE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.id[:128],
            "qualification_status": self.qualification_status,
            "health_state": self.health_state,
            "latency_bucket": self.latency_bucket,
            "consecutive_failures": self.consecutive_failures,
            "last_failure_class": self.last_failure_class,
            "required_credentials": list(self.required_credentials[:8]),
            "credential_missing": list(self.credential_missing[:8]),
            "required_permission": self.required_permission[:64],
            "policy_allowed": self.policy_allowed,
            "policy_reason_code": self.policy_reason_code,
            "certification_state": self.certification_state,
            "resource_class": dict(self.resource_class[:8]),
            "runtime_status": self.runtime_status,
        }


@dataclass(frozen=True)
class CapabilityRuntimeFact:
    """单 capability 运行时合成事实。"""

    capability_id: str
    runtime_status: str = STATUS_UNAVAILABLE
    providers: Tuple[ProviderRuntimeFact, ...] = ()
    best_provider: str = ""          # "kind:id"(最优可用路径;空 = 无)
    why: str = ""
    hints: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability": self.capability_id[:128],
            "runtime_status": self.runtime_status,
            "providers": [p.to_dict() for p in self.providers[:MAX_SNAPSHOT_PROVIDERS]],
            "best_provider": self.best_provider[:96],
            "why": self.why[:200],
            "hints": [h[:160] for h in self.hints[:4]],
        }


@dataclass(frozen=True)
class CapabilityRuntimeSnapshot:
    """一次合成快照(有界;digest 可作证据键)。"""

    capabilities: Tuple[CapabilityRuntimeFact, ...] = ()
    status_summary: Tuple[Tuple[str, int], ...] = ()
    credential_presence_fingerprint: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capabilities": [c.to_dict() for c in self.capabilities[:MAX_SNAPSHOT_CAPABILITIES]],
            "status_summary": dict(self.status_summary[:8]),
            "credential_presence_fingerprint": self.credential_presence_fingerprint,
        }


def _catalog_entry(kind: str, entry_id: str) -> Any:
    """catalog 只读引用(缺席/失败 = None,不抛 —— 快照绝不因目录冷缓存失败)。"""
    try:
        from app.lib.gis.execution_catalog import get_execution_catalog

        return get_execution_catalog().entries.get((kind, entry_id))
    except Exception:  # noqa: BLE001 — 目录缺席不阻断快照
        return None


def _granted_scope_permissions() -> Tuple[str, ...]:
    """部署声明(env)∪ 会话授予(ContextVar)—— 快照是部署级规划视图。"""
    granted = set(resolve_granted_permissions())
    try:
        from app.tools.registry import granted_permissions as _ctx_granted

        granted.update(str(p) for p in (_ctx_granted() or ()) if p)
    except Exception:  # noqa: BLE001 — ContextVar 缺席 = 仅 env 声明
        pass
    return tuple(sorted(granted))


def _present_scope_credentials() -> Dict[str, CredentialPresence]:
    presences = dict(resolve_credential_presence())
    try:
        from app.tools.registry import present_credentials as _ctx_present

        for cid in (_ctx_present() or ()):
            cid = str(cid or "").strip()
            if cid and cid not in presences:
                presences[cid] = CredentialPresence(credential_id=cid, source="session")
    except Exception:  # noqa: BLE001 — ContextVar 缺席 = 仅部署声明
        pass
    return presences


def _provider_runtime_status(
    qual_status: str,
    health_state: str,
    policy_decision,
) -> str:
    """provider 级五态裁决(确定性;unavailable 是兜底态)。"""
    if not policy_decision.allowed:
        if policy_decision.reason_code in (
            "credential_missing",
        ):
            return STATUS_CREDENTIAL_MISSING
        if policy_decision.reason_code == REASON_PROVIDER_UNHEALTHY:
            return STATUS_UNAVAILABLE
        return STATUS_POLICY_DENIED
    if health_state == "open":
        return STATUS_UNAVAILABLE
    if health_state == "half_open":
        return STATUS_DEGRADED
    if qual_status == "degraded":
        return STATUS_DEGRADED
    return STATUS_AVAILABLE


def _build_provider_fact(
    cand: Any,
    *,
    present_ids: Tuple[str, ...],
    granted: Tuple[str, ...],
    registry: Any,
    with_health: bool,
) -> ProviderRuntimeFact:
    kind = str(getattr(cand, "kind", "tool"))
    pid = str(getattr(cand, "id", ""))
    qual_status = str(getattr(getattr(cand, "qualification", None), "status", "unknown"))
    health_state = ""
    latency_bucket = "unknown"
    consecutive = 0
    last_failure_class = ""
    if with_health:
        try:
            from app.services.capability_runtime.health import (
                get_provider_health_registry,
            )

            v = get_provider_health_registry().verdict(f"{kind}:{pid}")
            health_state = v.state
            latency_bucket = v.latency_bucket
            consecutive = v.consecutive_failures
            last_failure_class = v.last_failure_class
        except Exception:  # noqa: BLE001 — 健康面缺席中性
            pass

    required_creds: Tuple[str, ...] = ()
    required_perm = ""
    if kind == "tool" and registry is not None:
        try:
            meta = registry.metadata(pid) or {}
            required_creds = tuple(
                str(c)[:64] for c in (meta.get("requires_credentials") or ())[:8] if c
            )
            required_perm = str(meta.get("required_permission") or "")[:64]
        except Exception:  # noqa: BLE001 — metadata 缺席 = 无要求(既有闸语义)
            pass

    entry = _catalog_entry("tool" if kind == "tool" else kind, pid)
    certification_state = ""
    resource_class: Tuple[Tuple[str, str], ...] = ()
    if entry is not None:
        cert = getattr(entry, "certification", None) or {}
        certification_state = str(cert.get("state", "") or "")
        rc = getattr(entry, "resource_class", None) or {}
        resource_class = tuple(sorted((str(k)[:32], str(v)[:32]) for k, v in rc.items()))

    decision = evaluate_capability_policy(CapabilityPolicyFact(
        required_credentials=required_creds,
        credentials_present=present_ids,
        required_permission=required_perm,
        granted_permissions=granted,
        health_state=health_state,
    ))
    missing = tuple(
        c for c in required_creds if c not in present_ids
    )[:8]
    return ProviderRuntimeFact(
        kind=kind,
        id=pid,
        qualification_status=qual_status,
        health_state=health_state,
        latency_bucket=latency_bucket,
        consecutive_failures=consecutive,
        last_failure_class=last_failure_class,
        required_credentials=required_creds,
        credential_missing=missing,
        required_permission=required_perm,
        policy_allowed=decision.allowed,
        policy_reason_code=decision.reason_code,
        certification_state=certification_state[:32],
        resource_class=resource_class,
        runtime_status=_provider_runtime_status(qual_status, health_state, decision),
    )


def _aggregate_status(providers: Tuple[ProviderRuntimeFact, ...]) -> str:
    """capability 态 = provider 中最优路径的态(best-wins;全空 = unavailable)。"""
    present = {p.runtime_status for p in providers}
    for status in _STATUS_PRECEDENCE:
        if status in present:
            return status
    return STATUS_UNAVAILABLE


def build_capability_runtime_snapshot(
    capability_ids: Optional[List[str]] = None,
    *,
    situation: Any = None,
    session_id: str = "",
    registry: Any = None,
) -> CapabilityRuntimeSnapshot:
    """合成 capability 运行时快照(只读;fail-open;确定性排序)。

    ``capability_ids`` 缺席 = capability registry 全量(≤16,字典序)。
    ``situation`` 兼容 QualificationContext / dict(bind 同款容忍)。
    """
    from app.lib.tool_security import presence_fingerprint

    present = _present_scope_credentials()
    present_ids = tuple(sorted(present.keys()))
    granted = _granted_scope_permissions()
    with_health = True
    try:
        from app.services.capability_runtime.health import provider_health_enabled

        with_health = provider_health_enabled()
    except Exception:  # noqa: BLE001
        pass

    ids: List[str] = []
    if capability_ids:
        for c in capability_ids[:MAX_SNAPSHOT_CAPABILITIES]:
            s = str(c or "").strip()[:128]
            if s and s not in ids:
                ids.append(s)
    else:
        try:
            from app.lib.gis.capability_registry import get_capability_registry

            ids = sorted(get_capability_registry().all_ids)[:MAX_SNAPSHOT_CAPABILITIES]
        except Exception as exc:  # noqa: BLE001 — 词表缺席 = 空快照(诚实)
            logger.debug("[capability-runtime-snapshot] registry unavailable: %s", exc)
            ids = []

    facts: List[CapabilityRuntimeFact] = []
    for cap in ids:
        try:
            from app.services.gis_harness.capability_resolution import (
                capability_status,
            )

            status, ranked, rejected = capability_status(
                cap, situation if situation is not None else _empty_situation(),
                session_id=str(session_id or "")[:64],
            )
        except Exception as exc:  # noqa: BLE001 — 单能力失败不拖垮快照
            logger.debug("[capability-runtime-snapshot] %s resolve failed: %s", cap, exc)
            facts.append(CapabilityRuntimeFact(
                capability_id=cap,
                runtime_status=STATUS_UNAVAILABLE,
                why=f"resolution_unavailable: {type(exc).__name__}",
            ))
            continue

        provider_facts = tuple(
            _build_provider_fact(
                cand, present_ids=present_ids, granted=granted,
                registry=registry, with_health=with_health,
            )
            for cand in ranked[:MAX_SNAPSHOT_PROVIDERS]
        )
        cap_status = _aggregate_status(provider_facts)
        best = ""
        if provider_facts:
            for p in provider_facts:
                if p.runtime_status == cap_status:
                    best = f"{p.kind}:{p.id}"
                    break
        hints: List[str] = []
        for p in provider_facts:
            if p.credential_missing and p.runtime_status == STATUS_CREDENTIAL_MISSING:
                hints.append(
                    f"provide credentials {','.join(p.credential_missing[:4])} "
                    f"to enable {p.kind}:{p.id}")
            elif p.runtime_status == STATUS_UNAVAILABLE and p.health_state == "open":
                hints.append(
                    f"provider {p.kind}:{p.id} circuit open "
                    f"(last={p.last_failure_class or 'unknown'}); retry after cooldown")
            if len(hints) >= 4:
                break
        if not provider_facts:
            hints.append("no eligible provider for this capability")
        why = f"qualification={status}; providers={len(provider_facts)}"
        facts.append(CapabilityRuntimeFact(
            capability_id=cap,
            runtime_status=cap_status,
            providers=provider_facts,
            best_provider=best,
            why=why,
            hints=tuple(hints[:4]),
        ))

    summary: Dict[str, int] = {}
    for f in facts:
        summary[f.runtime_status] = summary.get(f.runtime_status, 0) + 1
    return CapabilityRuntimeSnapshot(
        capabilities=tuple(facts),
        status_summary=tuple(sorted(summary.items())),
        credential_presence_fingerprint=presence_fingerprint(present),
    )


def _empty_situation() -> Any:
    from app.services.gis_harness.qualification_v8 import QualificationContext

    return QualificationContext()
