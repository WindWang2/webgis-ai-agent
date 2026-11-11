"""Pi 工具面的**稳定只读消费者适配器**（G06 / CAP-02）。

边界纪律（G01）：本模块**不修改** ``pi_native_surface.py`` / ``agent_pi_bridge.py``
/ ``pi_rpc_client.py`` / Pi Extension 宿主生命周期。它把
``CapabilityAvailabilitySnapshot`` 投影成 Pi 可消费的有界工具描述 + 资格结果，
跨模块变更被限制在这个 adapter 内。

生产语义：
* 模型只看到**有界候选**（默认 8，硬上限 64）；
* 只投影 ``native_verified`` / ``available_unverified`` 的真实注册工具；
* ``declared_only`` / ``temporarily_unavailable`` / ``forbidden`` 一律不可见；
* 每个候选带 schema 指纹与未验证原因，供服务端严格校验对拍；
* 拒绝 hallucinated 工具名 / 参数多余字段（复用 ``validate_pi_call``）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from app.lib.gis.capability_availability import CapabilityAvailabilitySnapshot

ADAPTER_SCHEMA_VERSION = "pi-availability-projection/v1"
VISIBLE_STATUSES = ("native_verified", "available_unverified")
DEFAULT_BOUND = 8
MAX_BOUND = 64


def project_pi_tool_surface(
    snapshot: CapabilityAvailabilitySnapshot,
    *,
    registry: Any = None,
    status_filter: Sequence[str] = ("native_verified",),
    bound: int = DEFAULT_BOUND,
) -> dict[str, Any]:
    """只读投影：把可用性快照转成有界、可对拍的 Pi 工具描述面。"""
    limit = max(1, min(int(bound), MAX_BOUND))
    allowed = set(status_filter) & set(VISIBLE_STATUSES)
    if not allowed:
        allowed = {"native_verified"}

    tools: list[dict[str, Any]] = []
    for rec in snapshot.records:
        if not rec.tool_id or rec.availability_status not in allowed:
            continue
        args_model = None
        required: list[str] = []
        if registry is not None and hasattr(registry, "args_model"):
            try:
                args_model = registry.args_model(rec.tool_id)
            except Exception:  # noqa: BLE001
                args_model = None
        if args_model is not None:
            required = sorted(
                n for n, f in getattr(args_model, "model_fields", {}).items()
                if f.is_required())
        tools.append({
            "name": rec.tool_id,
            "availability_status": rec.availability_status,
            "required": required,
            "unit_semantics": list(rec.units),
            "crs_semantics": rec.crs_semantics,
            "side_effect": rec.side_effect,
            "permission": rec.permission,
            "unverified_reasons": list(rec.unverified_reasons),
            "registry_generation": rec.registry_generation,
        })
        if len(tools) >= limit:
            break

    return {
        "schema_version": ADAPTER_SCHEMA_VERSION,
        "tools": tools,
        "returned": len(tools),
        "bound": limit,
        "truncated": len([
            r for r in snapshot.records
            if r.tool_id and r.availability_status in allowed
        ]) > len(tools),
        "snapshot_fingerprint": snapshot.content_fingerprint,
        "registry_generation": snapshot.registry_generation,
        "catalog_generation": snapshot.catalog_generation,
        "disclosure": (
            "本面只含真实注册且当前可用的工具；声明态/停用/越权能力不可见。"
            "服务端仍必须用 validate_pi_call 做工具名与参数严格校验。"),
    }


def validate_projected_call(
    tool_name: str, arguments: Mapping[str, Any],
    *, projection: Mapping[str, Any], snapshot: CapabilityAvailabilitySnapshot,
    registry: Any = None,
) -> tuple[bool, str]:
    """投影面 + 快照 + registry 三方严格校验（服务端唯一入口）。"""
    from app.lib.gis.availability_rerank import validate_pi_call

    names = {t["name"] for t in projection.get("tools", [])}
    if tool_name not in names:
        return False, (
            "tool_not_in_bounded_projection: 拒绝 hallucinated 工具名 / "
            "不可用工具 / 超出有界候选面")
    return validate_pi_call(
        tool_name, arguments, snapshot=snapshot, registry=registry)


# ── G13 / WP02：统一资格消费契约（组合投影，不新做科学判定） ─────────

QUALIFICATION_VIEW_SCHEMA = "pi-tool-qualification/v1"


def summarize_tool_qualification(
    tool_name: str,
    *,
    snapshot: Optional[CapabilityAvailabilitySnapshot] = None,
    registry: Any = None,
    permissions: Optional[frozenset[str]] = None,
) -> dict[str, Any]:
    """单工具有界资格摘要（WP02 统一消费契约；诊断/测试/后续消费方共用）。

    责任表（每层由谁决定、何时失效）：
    - ``structural_availability``：快照（编译期结构事实；同代缓存，
      注册/schema/扩展状态变化即换代）；
    - ``permission``：**请求侧**对拍 —— 只有调用方显式传 ``permissions``
      才投影（None = 无隔离上下文，不虚构通过/拒绝）；
    - ``scientific_profile``：AlgorithmDescriptor/MethodUseContract 的只读
      在场性（缺席 = ``not_evaluated``，不是 eligible）；
    - ``provider``：descriptor 声明的 provider 依赖（真实健康探测属
      provider_eligibility，本视图不发起探测、不虚构 healthy）；
    - ``generations``：registry/catalog 代 + 快照指纹（判定可归因到层）。

    快照获取失败/退避 → 结构层诚实 ``snapshot_unavailable:not_evaluated``
    （与其余消费方同一 fail-open 纪律），不抛错。
    """
    name = str(tool_name or "")
    if snapshot is None:
        from app.lib.gis.capability_availability_cache import (
            get_availability_snapshot,
        )

        try:
            snapshot = get_availability_snapshot(registry=registry)
        except Exception:  # noqa: BLE001 — 证据缺席诚实留白
            snapshot = None
    if snapshot is None:
        return {
            "schema_version": QUALIFICATION_VIEW_SCHEMA,
            "tool": name,
            "in_snapshot": False,
            "structural_availability": {
                "status": "not_evaluated",
                "reason_codes": ["snapshot_unavailable:not_evaluated"],
                "unverified_reasons": [],
            },
            "generations": {},
            "scientific_profile": "not_evaluated",
            "permission": "not_evaluated",
            "provider": {"declared": "unknown",
                         "health": "not_evaluated"},
        }
    rec = snapshot.for_tool(name)
    out: dict[str, Any] = {
        "schema_version": QUALIFICATION_VIEW_SCHEMA,
        "tool": name,
        "in_snapshot": rec is not None,
        "structural_availability": {
            "status": rec.availability_status if rec else "not_in_snapshot",
            "reason_codes": list(rec.reason_codes)[:6] if rec else [],
            "unverified_reasons": list(rec.unverified_reasons)[:6] if rec else [],
        },
        "generations": {
            "registry": snapshot.registry_generation,
            "catalog": snapshot.catalog_generation,
            "snapshot_fingerprint": snapshot.content_fingerprint,
            "tool_version_id": rec.tool_version_id if rec else "",
        },
        "scientific_profile": "not_evaluated",
        "permission": "not_evaluated",
        "provider": {"declared": rec.provider if rec else "unknown",
                     "health": "not_evaluated"},
    }
    if rec is not None and rec.algorithm_id:
        try:
            from app.lib.gis.method_eligibility import preflight_method

            pre = preflight_method(rec.algorithm_id)
            out["scientific_profile"] = pre.verdict
            out["scientific_profile_reasons"] = list(pre.reason_codes)[:4]
        except Exception:  # noqa: BLE001 — 预检缺席诚实留白
            out["scientific_profile"] = "not_evaluated"
    if permissions is not None:
        required = rec.permission if rec else ""
        if not required:
            out["permission"] = "allowed_no_permission_required"
        elif required in permissions:
            out["permission"] = "allowed"
        else:
            out["permission"] = "forbidden"
            out["permission_required"] = required
    return out


# ── G13 / WP05：执行前资格复核（可恢复拒绝） ────────────────────────

#: dispatch 复核 kill switch：GIS_AVAILABILITY_DISPATCH_RECHECK=0 关闭
#: （行为回退 master：capability bind 仍是唯一既有闸）。
RECHECK_ENV = "GIS_AVAILABILITY_DISPATCH_RECHECK"
CODE_CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
CODE_STALE_TOOL_SCHEMA = "STALE_TOOL_SCHEMA"
#: 拒绝时给出的同 capability 候选上限（喂 LLM 重试；有界）。
_MAX_ALTERNATIVES = 4


def dispatch_recheck_enabled() -> bool:
    raw = (os.getenv(RECHECK_ENV) or "1").strip().lower()
    return raw not in ("0", "false", "off", "no")


@dataclass(frozen=True)
class DispatchAvailabilityDenial:
    """执行前复核的 typed 拒绝（可恢复：补数据/换代后同任务可重试）。"""

    code: str
    reason: str
    hint: str
    alternatives: tuple = ()
    tool_version_id: str = ""
    registry_generation: str = ""

    def denial_text(self) -> str:
        alts = ", ".join(str(a) for a in self.alternatives[:3]) or "(none)"
        return (
            f"Tool dispatch refused before execution: {self.code}. "
            f"{self.reason} {self.hint} Current alternatives: {alts}. "
            "Retry the same task after fixing the cause; the correction "
            "does not require a new session.")


def _capability_alternatives(
    capability_id: str, snapshot: CapabilityAvailabilitySnapshot,
) -> tuple:
    """同 capability 下当前结构可用的候选（有界、按名稳定序）。"""
    if not capability_id:
        return ()
    blocked = {"declared_only", "temporarily_unavailable", "forbidden"}
    alts = sorted(
        r.tool_id for r in snapshot.records
        if r.tool_id and r.capability_id == capability_id
        and r.availability_status not in blocked)
    return tuple(alts[:_MAX_ALTERNATIVES])


def preflight_dispatch_availability(
    tool_name: str,
    *,
    registry: Any = None,
    snapshot: Optional[CapabilityAvailabilitySnapshot] = None,
) -> Optional[DispatchAvailabilityDenial]:
    """执行前可用性复核：只在**阳性结构事实**上拒绝，其余放行。

    阳性事实（来自同代快照 + 活注册表对拍）：
    1. 快照状态 ``declared_only``（planned/hidden）或
       ``temporarily_unavailable``（provider 依赖缺席 / external 状态）；
    2. ``tool_version_id`` 漂移：选中时的快照代与当前注册表版本不一致
       （同名工具换代 —— 旧 schema 调用不得被默许）。

    快照缺席 / 工具不在快照 / 复核自身故障 → ``None``（放行；证据缺席
    不等于通过 —— 后置 registry/schema/权限闸与执行结果面仍是权威）。
    本函数**不做**权限判定（权限属路由/owner 门禁，不建第二权限系统）、
    **不发起** provider 在线探测（缺事实 = not_evaluated 披露）。
    """
    if not dispatch_recheck_enabled():
        return None
    name = str(tool_name or "").strip()
    if not name:
        return None
    if snapshot is None:
        from app.lib.gis.capability_availability_cache import (
            get_availability_snapshot,
        )

        try:
            snapshot = get_availability_snapshot(registry=registry)
        except Exception:  # noqa: BLE001 — 复核面故障放行（闸不成为新故障面）
            return None
    rec = snapshot.for_tool(name)
    if rec is None:
        return None

    if rec.availability_status in ("declared_only", "temporarily_unavailable"):
        reasons = ", ".join(list(rec.unverified_reasons)[:3]) or "not_evaluated"
        if rec.availability_status == "temporarily_unavailable":
            reason = (
                f"structural availability = temporarily_unavailable "
                f"(provider dependency absent or tool status external; "
                f"reasons: {reasons})")
            hint = ("Wait for the provider/extension to be restored, or pick "
                    "an alternative below; re-discovery stays available.")
        else:
            reason = (
                f"structural availability = declared_only "
                f"(planned/hidden tool; reasons: {reasons})")
            hint = ("This capability is declared but not executable in the "
                    "current environment; pick an alternative below.")
        return DispatchAvailabilityDenial(
            code=CODE_CAPABILITY_UNAVAILABLE,
            reason=reason,
            hint=hint,
            alternatives=_capability_alternatives(rec.capability_id, snapshot),
            tool_version_id=rec.tool_version_id,
            registry_generation=snapshot.registry_generation,
        )

    # 版本代对拍：同名工具换版本/契约 → 旧 schema 调用必须显式换代。
    if registry is not None and rec.tool_version_id:
        try:
            live_id = str(registry.descriptor(name).tool_id or "")
        except Exception:  # noqa: BLE001 — registry 缺席该工具由存在性闸负责
            live_id = ""
        if live_id and live_id != rec.tool_version_id:
            return DispatchAvailabilityDenial(
                code=CODE_STALE_TOOL_SCHEMA,
                reason=(
                    f"tool schema generation drifted: selected="
                    f"{rec.tool_version_id} live={live_id}"),
                hint=(
                    "The tool was re-registered with a new schema between "
                    "selection and dispatch; re-discover tools and retry "
                    "with the new schema."),
                alternatives=_capability_alternatives(rec.capability_id, snapshot),
                tool_version_id=rec.tool_version_id,
                registry_generation=snapshot.registry_generation,
            )
    return None
