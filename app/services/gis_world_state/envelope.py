"""MutationEnvelope —— 地图状态突变的统一元数据信封（方向 8 / ADR-0183）。

所有改图的写入（agent 工具、用户 UI、自愈修复、模板/autofill、系统对账）共享
同一张地图；本模块给每笔突变一个**可序列化、可观测、可归因**的身份层：

- ``mutation_id``：服务端幂等键（引擎锁内 dedup 的依据，见 lifecycle_engine）；
- ``producer_class``：优先级阶梯分类（USER_PINNED > USER_EXPLICIT > AGENT_EXPLICIT
  > REPAIR_AUTOFILL > TEMPLATE > SYSTEM_DEFAULT），裁决规则见 precedence.py；
- ``explicitness``：explicit（调用方显式声明的字段写入）vs derived（管线推导的
  附带写入，如 suggested_view）——derived 写入永远不得覆盖显式决策；
- ``client_optimistic_id``：前端乐观队列身份（前端 pending 代际 / 重放对账）；
- ``reason``：人类可读的变更动机（进 provenance / collab op journal）。

设计边界（D-01）：
- 信封是**元数据**，不承载 operations —— 操作语义仍是 MutationIntent 体系；
- 缺省全兼容：所有字段 Optional，engine/门面在信封缺席时行为与 master 一致；
- 不复制 Pi 的 tool loop / 不建第二套状态存储 —— 信封随既有
  ``apply_gis_mutation → engine.apply_mutation`` 链路透传。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, FrozenSet, Iterable, Optional

# 优先级阶梯（与 precedence.PRODUCER_PRECEDENCE 同源；此处仅声明名册）。
PRODUCER_USER_PINNED = "USER_PINNED"
PRODUCER_USER_EXPLICIT = "USER_EXPLICIT"
PRODUCER_AGENT_EXPLICIT = "AGENT_EXPLICIT"
PRODUCER_REPAIR_AUTOFILL = "REPAIR_AUTOFILL"
PRODUCER_TEMPLATE = "TEMPLATE"
PRODUCER_SYSTEM_DEFAULT = "SYSTEM_DEFAULT"

KNOWN_PRODUCER_CLASSES = (
    PRODUCER_USER_PINNED,
    PRODUCER_USER_EXPLICIT,
    PRODUCER_AGENT_EXPLICIT,
    PRODUCER_REPAIR_AUTOFILL,
    PRODUCER_TEMPLATE,
    PRODUCER_SYSTEM_DEFAULT,
)

EXPLICITNESS_EXPLICIT = "explicit"
EXPLICITNESS_DERIVED = "derived"

# 修复/收口管线的既有 actor 值（mutation.py 各生产调用点）——这些管线的写入
# 是 harness 推导的收口（非 agent 显式制图指令），归 REPAIR_AUTOFILL。
REPAIR_ACTORS: FrozenSet[str] = frozenset(
    {"runtime_repair", "map_finalizer", "finalize_display"}
)

# 模板/配方族 actor（webgis 工具名）：模板套用是"按配方填充"，规则是
# fill-undeclared（D-06），与显式 agent 指令区分。
TEMPLATE_ACTOR_PATTERNS = (
    re.compile(r"^tool:apply_template$"),
    re.compile(r"^tool:recipe_"),
)

_MUTATION_ID_MAX = 128
_REASON_MAX = 200


def new_mutation_id() -> str:
    """服务端铸造 mutation_id（客户端未携带时的兜底）。"""
    import uuid

    return uuid.uuid4().hex


def classify_producer(
    *,
    origin: str,
    actor: Optional[str] = None,
    targets_user_locked: bool = False,
    origin_is_user_pin: bool = False,
) -> str:
    """(origin, actor, flags) → producer_class（确定性纯函数，D-02）。

    - origin=user：目标命中 workbench 锁集 / 写锁集 → USER_PINNED（锁是既有
      durable pin 载体，guard_intent_locks 对 agent/system 整笔拒绝的那一面）；
      其余 → USER_EXPLICIT。
    - origin=agent：修复/收口管线 actor → REPAIR_AUTOFILL；模板/配方族 actor
      → TEMPLATE；其余 → AGENT_EXPLICIT。
    - origin=system → SYSTEM_DEFAULT。
    未知 origin 一律 SYSTEM_DEFAULT（fail-low：最低优先级，绝不冒充高阶写入者）。
    """
    actor_norm = str(actor or "").strip()
    if origin == "user":
        if origin_is_user_pin or targets_user_locked:
            return PRODUCER_USER_PINNED
        return PRODUCER_USER_EXPLICIT
    if origin == "agent":
        if actor_norm in REPAIR_ACTORS:
            return PRODUCER_REPAIR_AUTOFILL
        for pattern in TEMPLATE_ACTOR_PATTERNS:
            if pattern.match(actor_norm):
                return PRODUCER_TEMPLATE
        return PRODUCER_AGENT_EXPLICIT
    return PRODUCER_SYSTEM_DEFAULT


@dataclass
class MutationEnvelope:
    """一笔地图突变的统一身份（全部字段可缺省 —— 渐进接线，零破坏）。"""

    mutation_id: Optional[str] = None
    origin: str = "agent"
    actor: Optional[str] = None
    producer_class: Optional[str] = None
    explicitness: str = EXPLICITNESS_EXPLICIT
    client_optimistic_id: Optional[str] = None
    turn_id: Optional[str] = None
    reason: Optional[str] = None
    ts: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    # 目标是否命中 workbench 锁集（user 意图写锁集 → USER_PINNED 的判据）。
    targets_user_locked: bool = False

    def __post_init__(self) -> None:
        if self.mutation_id is not None:
            self.mutation_id = str(self.mutation_id)[:_MUTATION_ID_MAX]
        if self.client_optimistic_id is not None:
            self.client_optimistic_id = str(self.client_optimistic_id)[:_MUTATION_ID_MAX]
        if self.turn_id is not None:
            self.turn_id = str(self.turn_id)[:_MUTATION_ID_MAX]
        if self.reason is not None:
            self.reason = str(self.reason)[:_REASON_MAX]
        if self.producer_class is None:
            self.producer_class = classify_producer(
                origin=self.origin,
                actor=self.actor,
                targets_user_locked=self.targets_user_locked,
            )
        elif self.producer_class not in KNOWN_PRODUCER_CLASSES:
            # 拼错/伪造的分类不冒充高阶写入者：降级为 SYSTEM_DEFAULT。
            self.producer_class = PRODUCER_SYSTEM_DEFAULT
        if self.explicitness not in (EXPLICITNESS_EXPLICIT, EXPLICITNESS_DERIVED):
            self.explicitness = EXPLICITNESS_EXPLICIT

    def to_dict(self) -> Dict[str, Any]:
        """可序列化投影（只输出有值键，避免载荷膨胀/旧消费方破坏）。"""
        out: Dict[str, Any] = {
            "origin": self.origin,
            "producer_class": self.producer_class,
            "explicitness": self.explicitness,
            "ts": self.ts,
        }
        for key in (
            "mutation_id",
            "actor",
            "client_optimistic_id",
            "turn_id",
            "reason",
        ):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        return out

    @classmethod
    def from_request(
        cls,
        *,
        origin: str,
        actor: Optional[str] = None,
        mutation_id: Optional[str] = None,
        client_optimistic_id: Optional[str] = None,
        turn_id: Optional[str] = None,
        reason: Optional[str] = None,
        producer_class: Optional[str] = None,
        explicitness: str = EXPLICITNESS_EXPLICIT,
    ) -> "MutationEnvelope":
        """调用点入口：mutation_id 缺席时铸造（服务端兜底幂等键）。"""
        return cls(
            mutation_id=mutation_id or new_mutation_id(),
            origin=origin,
            actor=actor,
            explicitness=explicitness,
            client_optimistic_id=client_optimistic_id,
            turn_id=turn_id,
            reason=reason,
            producer_class=producer_class,
        )
