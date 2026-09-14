"""Precedence / ownership 阶梯 —— 突变冲突的确定性裁决（方向 8 / ADR-0183）。

阶梯（D-02，任务书初始思想按代码事实调整后落码）：

    USER_PINNED  >  USER_EXPLICIT  >  AGENT_EXPLICIT  >  REPAIR_AUTOFILL
                   >  TEMPLATE  >  SYSTEM_DEFAULT

四条硬规则（U2）：
1. agent 不得覆盖 user-pinned/user-explicit 的 presentation 决策 —— 落地于
   既有 UserPresentationGuard（spec 印记 + ring）与 ``_preserve_durable_presentation``；
   本模块提供 ``can_override`` 供分类面/测试/新调用点复用同一裁决语义。
2. user explicit 可以覆盖 agent（用户操作是唯一 override，引擎对 origin=user
   豁免锁 guard）。
3. repair 不得修改 user lock —— W15 ``guard_intent_locks`` 是唯一执行点；本模块
   的 ``resolve_field`` 在字段面给出同向裁决（locked → incumbent 不变）。
4. template/autofill 只能填未声明字段（``fill_undeclared``）；system reconciliation
   不得把 stale state 当新意图（``resolve_field`` 对 SYSTEM_DEFAULT 恒保留 incumbent，
   除非 incumbent 亦为 SYSTEM_DEFAULT）。

本模块是**纯函数层**：不读状态、不发 IO；执行仍由引擎/守卫承担（D-01）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple

from app.services.gis_world_state.envelope import (
    KNOWN_PRODUCER_CLASSES,
    PRODUCER_AGENT_EXPLICIT,
    PRODUCER_REPAIR_AUTOFILL,
    PRODUCER_SYSTEM_DEFAULT,
    PRODUCER_TEMPLATE,
    PRODUCER_USER_EXPLICIT,
    PRODUCER_USER_PINNED,
)

# 优先级全序（索引越大优先级越高）。KNOWN_PRODUCER_CLASSES 与之同册。
PRODUCER_PRECEDENCE: Tuple[str, ...] = (
    PRODUCER_SYSTEM_DEFAULT,
    PRODUCER_TEMPLATE,
    PRODUCER_REPAIR_AUTOFILL,
    PRODUCER_AGENT_EXPLICIT,
    PRODUCER_USER_EXPLICIT,
    PRODUCER_USER_PINNED,
)

_RANK = {name: rank for rank, name in enumerate(PRODUCER_PRECEDENCE)}

# 低阶写入者的"fill-undeclared 白名单下限"：TEMPLATE 及以下只许填空缺。
_FILL_ONLY_BELOW = _RANK[PRODUCER_AGENT_EXPLICIT]

# 需要显式声明才可写 incumbent 的下限：REPAIR_AUTOFILL 及以下对非 SYSTEM
# incumbent 只能保留（不得反转）。
_OVERRIDE_FLOOR = _RANK[PRODUCER_AGENT_EXPLICIT]


def rank(producer_class: Optional[str]) -> int:
    """producer_class → 序（未知/None = SYSTEM_DEFAULT 档：fail-low）。"""
    return _RANK.get(str(producer_class or ""), _RANK[PRODUCER_SYSTEM_DEFAULT])


def higher(a: Optional[str], b: Optional[str]) -> str:
    """两个分类中优先级更高者。"""
    return a if rank(a) >= rank(b) else b


def can_override(
    incoming: Optional[str],
    incumbent: Optional[str],
    *,
    incumbent_declared: bool = True,
) -> bool:
    """incoming 能否覆盖 incumbent 的**已声明**字段（确定性纯函数）。

    - incoming 严格高于 incumbent → 可覆盖（user 覆 agent、agent 覆 repair…）；
    - 同阶 → 不可覆盖（同阶重放走幂等路径，不做 last-wins 覆盖）；
    - incumbent 未声明（absent/None 值）→ 任何 incoming 可填（fill-undeclared）；
    - SYSTEM_DEFAULT incumbent 例外：它是"无主默认"，任何显式类可改。
    """
    if not incumbent_declared:
        return True
    inc_rank = rank(incumbent)
    if incumbent == PRODUCER_SYSTEM_DEFAULT:
        return rank(incoming) > _RANK[PRODUCER_TEMPLATE]
    if rank(incoming) > inc_rank:
        return True
    # REPAIR_AUTOFILL 及以下对已声明的更高阶 incumbent 一律不可覆盖；
    # AGENT_EXPLICIT 及以下对 USER_* 不可覆盖 —— 同秩也不行。
    return False


def resolve_field(
    incumbent_value: Any,
    incumbent_owner: Optional[str],
    incoming_value: Any,
    incoming_owner: Optional[str],
) -> Tuple[Any, str, str]:
    """字段级裁决：→ (value, owner, disposition)。

    disposition ∈ {"kept", "overridden", "filled"}（观测/审计词，写入
    provenance detail 或 reconciliation 报告；kept 时不产生写放大）。
    incoming_value 为 None 视为"本次未声明该字段" → 恒 kept。
    """
    if incoming_value is None:
        return incumbent_value, str(incumbent_owner or PRODUCER_SYSTEM_DEFAULT), "kept"
    declared = incumbent_value is not None
    if can_override(incoming_owner, incumbent_owner, incumbent_declared=declared):
        return (
            incoming_value,
            str(incoming_owner or PRODUCER_SYSTEM_DEFAULT),
            "filled" if not declared else "overridden",
        )
    return incumbent_value, str(incumbent_owner or PRODUCER_SYSTEM_DEFAULT), "kept"


def fill_undeclared(
    base: Mapping[str, Any],
    template: Mapping[str, Any],
    *,
    template_owner: str = PRODUCER_TEMPLATE,
) -> Tuple[Dict[str, Any], List[str]]:
    """模板/autofill 语义：只填 base 未声明（缺席或 None）的键，返回 (merged, filled)。

    已声明键（含显式 system default 之外的一切 owner 决策）一律保留 —— 模板
    永远不覆盖用户/agent/修复的显式值（U2/U6 的 fill-undeclared 规则）。
    值为 None 的模板键同样跳过（模板自己也没声明）。
    """
    merged: Dict[str, Any] = dict(base)
    filled: List[str] = []
    for key, value in template.items():
        if value is None:
            continue
        if key not in merged or merged[key] is None:
            merged[key] = value
            filled.append(key)
    return merged, filled


def reconcile_priority(anomaly_code: str) -> int:
    """reconciliation anomaly 的处置优先级（小者先处理；对账面排序用）。

    USER_HIDDEN_BUT_VISIBLE（user-wins 被破坏）> ZOMBIE > MISSING >
    VISIBILITY_MISMATCH（非 user-owned）> STALE_PENDING。
    """
    order = {
        "USER_HIDDEN_BUT_VISIBLE": 0,
        "ZOMBIE_RUNTIME_LAYER": 1,
        "SPEC_LAYER_MISSING_RUNTIME": 2,
        "VISIBILITY_MISMATCH": 3,
        "STALE_PENDING_REMOVED": 4,
    }
    return order.get(anomaly_code, 9)
