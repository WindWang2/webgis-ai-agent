"""Acquisition Alternatives V4 —— 数据获取备选声明（只声明，不抓取）。

compiler 对每个数据角色声明**有序获取备选**与可行性条件；实际获取由
数据面（data fabric / STAC 目录 / provider / 用户上传）执行。

红线：

- compiler 只产出声明（channels + feasibility + 披露），绝不直接 fetch；
- synthetic_demo 仅显式 opt-in（``allow_synthetic_demo=True``）——
  must_not_guess 红线的获取侧延伸：演示数据不得静默替代真实数据；
- 词表为 workflow_schema.ACQUISITION_CHANNELS 的**扩展投影**（拆分
  data_fabric 为 catalog/STAC，新增 provider_service 与 synthetic_demo；
  原四词保持原义 —— 旧消费方不受影响）；
- 全部确定性、有界、零 LLM / 零网络 I/O。
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence

from pydantic import BaseModel, Field

#: 获取备选词表（有序优先序：local 最优，synthetic_demo 兜底且仅显式）。
ACQUISITION_ALTERNATIVES = (
    "local",                # 本地/会话内已有数据（最优：已绑定）
    "derived",              # 由既有 capability 派生
    "data_fabric_catalog",  # 数据目录（data fabric）
    "data_fabric_stac",     # STAC 目录（遥感/栅格）
    "provider_service",     # 外部数据服务 provider
    "user_upload",          # 必须用户提供
    "synthetic_demo",       # 演示数据（仅显式 opt-in）
)

_MAX_ROLES = 16


class AcquisitionAlternative(BaseModel):
    """一个获取通道的声明（可行性 = 编译期事实裁决，不是网络探测）。"""
    channel: str                       # ⊆ ACQUISITION_ALTERNATIVES
    feasible: bool = False
    reason_code: str = ""              # 不可行/受限的稳定码
    disclosure: str = ""
    note: str = ""

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "channel": self.channel[:32],
            "feasible": self.feasible,
            "reason_code": self.reason_code[:64],
            "disclosure": self.disclosure[:200],
            "note": self.note[:120],
        }


class RoleAcquisitionPlan(BaseModel):
    """一个数据角色的获取备选声明（有序）。"""
    role: str
    resolution_status: str = "unresolved"   # ⊆ workflow_schema 角色解析态
    missing_policy: str = "block"
    alternatives: List[AcquisitionAlternative] = Field(default_factory=list)
    must_not_guess: bool = True

    @property
    def feasible_channels(self) -> List[str]:
        return [a.channel for a in self.alternatives if a.feasible]

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role[:32],
            "resolution_status": self.resolution_status[:24],
            "missing_policy": self.missing_policy[:16],
            "must_not_guess": self.must_not_guess,
            "feasible_channels": self.feasible_channels[:7],
            "alternatives": [
                a.to_bounded_dict() for a in self.alternatives[:7]],
        }


class AcquisitionPlan(BaseModel):
    """整工作流的获取声明（角色 × 有序备选）。"""
    roles: List[RoleAcquisitionPlan] = Field(default_factory=list)
    synthetic_demo_allowed: bool = False

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "synthetic_demo_allowed": self.synthetic_demo_allowed,
            "roles": [r.to_bounded_dict() for r in self.roles[:_MAX_ROLES]],
        }


def _alternative(
    channel: str, *, feasible: bool, reason_code: str = "",
    disclosure: str = "", note: str = "",
) -> AcquisitionAlternative:
    return AcquisitionAlternative(
        channel=channel, feasible=feasible, reason_code=reason_code,
        disclosure=disclosure, note=note,
    )


def plan_role_acquisition(
    role: str,
    resolution_status: str,
    *,
    missing_policy: str = "block",
    acquisition: str = "local",
    allow_synthetic_demo: bool = False,
) -> RoleAcquisitionPlan:
    """单角色的确定性获取备选声明。

    可行性规则（编译期事实，不探测）：
    - bound        → local 可行（数据已在）；
    - external     → 声明通道（data_fabric/user_upload）可行；
    - derived 可行 = 角色声明了派生通道；
    - unresolved   → 无通道可行：只声明候选 + 缺失策略（block/degrade）；
    - user_upload 恒为「可行候选」（用户总能提供，是否真实提供归 runtime）；
    - synthetic_demo 仅显式 opt-in 时进入声明，且恒带演示披露。
    """
    st = resolution_status
    alts: List[AcquisitionAlternative] = []
    if st == "bound":
        alts.append(_alternative("local", feasible=True))
    else:
        alts.append(_alternative(
            "local", feasible=False,
            reason_code=f"ACQ_LOCAL_NOT_BOUND:{role}"))
    if acquisition == "derived" or st == "bound":
        alts.append(_alternative(
            "derived", feasible=st == "bound",
            reason_code="" if st == "bound" else "ACQ_DERIVED_UNRESOLVED"))
    if acquisition in ("data_fabric", "data_fabric_catalog"):
        alts.append(_alternative(
            "data_fabric_catalog",
            feasible=st in ("external", "unresolved"),
            reason_code="" if st == "external" else "ACQ_CATALOG_UNCONFIRMED",
            note="数据目录命中与否由 data fabric 执行期确认"))
        alts.append(_alternative("data_fabric_stac", feasible=False,
                                 reason_code="ACQ_STAC_NOT_DECLARED"))
    elif acquisition in ("data_fabric_stac",):
        alts.append(_alternative(
            "data_fabric_stac",
            feasible=st in ("external", "unresolved"),
            reason_code="" if st == "external" else "ACQ_STAC_UNCONFIRMED"))
    else:
        alts.append(_alternative("data_fabric_catalog", feasible=False,
                                 reason_code="ACQ_CATALOG_NOT_DECLARED"))
    alts.append(_alternative(
        "provider_service", feasible=st == "external",
        reason_code="" if st == "external" else "ACQ_PROVIDER_NOT_DECLARED"))
    alts.append(_alternative(
        "user_upload",
        feasible=st in ("external", "unresolved", "degraded"),
        reason_code="" if st != "bound" else "ACQ_UPLOAD_REDUNDANT",
        disclosure=("必须由用户提供该数据；系统不得编造。"
                    if missing_policy == "block" and st != "bound" else "")))
    if allow_synthetic_demo:
        alts.append(_alternative(
            "synthetic_demo", feasible=True,
            reason_code="ACQ_SYNTHETIC_DEMO_EXPLICIT",
            disclosure="演示合成数据：仅用于产品演示，不得用于科学结论。"))
    else:
        alts.append(_alternative(
            "synthetic_demo", feasible=False,
            reason_code="ACQ_SYNTHETIC_NOT_ALLOWED",
            note="must_not_guess：合成数据未获显式授权"))
    return RoleAcquisitionPlan(
        role=role, resolution_status=st, missing_policy=missing_policy,
        alternatives=alts,
    )


def plan_acquisition(
    data_roles: Sequence[Any],
    *,
    allow_synthetic_demo: bool = False,
) -> AcquisitionPlan:
    """角色解析序列 → 获取备选声明（确定性，输入顺序保持）。"""
    roles: List[RoleAcquisitionPlan] = []
    for r in list(data_roles)[:_MAX_ROLES]:
        role = str(getattr(r, "role", "") or (r.get("role") if isinstance(r, dict) else ""))
        if not role:
            continue
        status = str(getattr(r, "status", "") or
                     (r.get("status") if isinstance(r, dict) else "unresolved"))
        acquisition = str(getattr(r, "acquisition", "") or
                          (r.get("acquisition") if isinstance(r, dict) else "local")
                          or "local")
        missing_policy = str(getattr(r, "missing_policy", "") or
                             (r.get("missing_policy") if isinstance(r, dict) else "block")
                             or "block")
        roles.append(plan_role_acquisition(
            role, status, missing_policy=missing_policy,
            acquisition=acquisition,
            allow_synthetic_demo=allow_synthetic_demo,
        ))
    return AcquisitionPlan(roles=roles,
                           synthetic_demo_allowed=allow_synthetic_demo)
