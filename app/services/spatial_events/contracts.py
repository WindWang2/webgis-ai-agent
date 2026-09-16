"""Spatial Events 契约（schema v1）——事件驱动空间操作控制平面的类型层。

纪律（与任务书对齐）：
- 事件是**控制平面事实**：inline payload ≤2KB canonical JSON；大内容只允许
  payload_ref（ref/ticket），绝不进 LLM context。
- kind/source/action 全部封闭词表；org_id 必填且由**受信上下文盖章**
  （ledger/API 层执行，契约层只保证非空）。
- webhook 是保留 source，仅 webhook seam（HMAC 验签后）可经 validation
  context 盖章，内部适配器不得伪造。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from pydantic_core.core_schema import ValidationInfo

SCHEMA_VERSION = "spatial-event.v1"

#: inline payload 的 canonical JSON 字节上限（大内容走 payload_ref）
MAX_INLINE_PAYLOAD_BYTES = 2048
#: 字符串字段边界
MAX_SUBJECT_KEY = 128
MAX_EVENT_ID = 64
MAX_SOURCE = 32
MAX_GOAL_TEMPLATE = 512


class EventKind(str, Enum):
    """封闭事件词表（v1）。扩词表 = 显式契约变更。"""

    MAP_MUTATION_APPLIED = "map.mutation_applied"
    ARTIFACT_REVISION_COMMITTED = "artifact.revision_committed"
    DATASET_VERSION_CHANGED = "dataset.version_changed"
    JOB_COMPLETED = "job.completed"
    JOB_FAILED = "job.failed"
    JOB_CANCELLED = "job.cancelled"
    SIMULATION_COMPLETED = "simulation.completed"
    MAPPRODUCT_VERSION_RECORDED = "mapproduct.version_recorded"
    OBSERVATION_SPATIAL = "observation.spatial"


EVENT_KINDS: Tuple[str, ...] = tuple(k.value for k in EventKind)


class SubjectType(str, Enum):
    SESSION = "session"
    DATASET = "dataset"
    ARTIFACT = "artifact"
    PROJECT = "project"
    JOB = "job"
    PRODUCT = "product"
    LAYER = "layer"
    MISSION = "mission"
    FEATURE = "feature"


class EventPriority(str, Enum):
    """与 governor ExecutionPriority 同语义（interactive=0 最先）。"""

    INTERACTIVE = "interactive"
    NORMAL = "normal"
    BATCH = "batch"


PRIORITY_ORDER: Dict[str, int] = {
    EventPriority.INTERACTIVE.value: 0,
    EventPriority.NORMAL.value: 1,
    EventPriority.BATCH.value: 2,
}


def canonical_payload_json(payload: Dict[str, Any]) -> str:
    """确定性 canonical JSON（键排序、无空格）——去重/派生 id 的唯一基准。"""
    return json.dumps(payload or {}, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def derive_event_id(
    *,
    source: str,
    kind: str,
    subject_key: str,
    occurred_at: datetime,
    payload: Dict[str, Any],
) -> str:
    """确定性 event_id：同 (source,kind,subject,occurred_at,payload) ⇒ 同 id。

    生产者可显式提供 event_id/dedupe_key 覆盖派生值（外部系统天然幂等键）。
    """
    basis = "|".join(
        (
            source[:MAX_SOURCE],
            kind,
            subject_key[:MAX_SUBJECT_KEY],
            occurred_at.isoformat(),
            hashlib.sha256(canonical_payload_json(payload).encode("utf-8"))
            .hexdigest(),
        )
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


class SpatialEventEnvelope(BaseModel):
    """事件信封（v1）。所有大 payload 只传 ref。"""

    schema_version: str = SCHEMA_VERSION
    event_id: str = Field(default="", max_length=MAX_EVENT_ID)
    kind: EventKind
    org_id: str = Field(min_length=1, max_length=64)
    source: str = Field(default="internal", max_length=MAX_SOURCE)
    subject_type: SubjectType
    subject_key: str = Field(min_length=1, max_length=MAX_SUBJECT_KEY)
    session_id: Optional[str] = Field(default=None, max_length=64)
    project_id: Optional[str] = Field(default=None, max_length=64)
    occurred_at: datetime
    dedupe_key: Optional[str] = Field(default=None, max_length=128)
    payload: Dict[str, Any] = Field(default_factory=dict)
    payload_ref: Optional[str] = Field(default=None, max_length=160)
    priority: EventPriority = EventPriority.NORMAL
    correlation_id: Optional[str] = Field(default=None, max_length=64)

    @field_validator("schema_version")
    @classmethod
    def _schema_pinned(cls, v: str) -> str:
        if v != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {v}")
        return v

    @field_validator("source")
    @classmethod
    def _webhook_reserved(cls, v: str, info: ValidationInfo) -> str:
        ctx = (info.context or {}) if info.context else {}
        if v == "webhook" and not ctx.get("trusted_webhook"):
            raise ValueError(
                "source='webhook' is reserved for the verified webhook seam"
            )
        return v

    @field_validator("occurred_at")
    @classmethod
    def _tz_required(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        return v

    @model_validator(mode="after")
    def _finalize(self) -> "SpatialEventEnvelope":
        size = len(canonical_payload_json(self.payload).encode("utf-8"))
        if size > MAX_INLINE_PAYLOAD_BYTES:
            raise ValueError(
                f"inline payload {size}B exceeds {MAX_INLINE_PAYLOAD_BYTES}B; "
                "use payload_ref for large content"
            )
        if not self.event_id:
            self.event_id = derive_id_for(self)
        return self

    @classmethod
    def webhook(
        cls, *, trusted: bool = True, **kwargs: Any
    ) -> "SpatialEventEnvelope":
        """webhook seam 专用构造：允许 source='webhook'（先 HMAC 验签）。"""
        kwargs["source"] = "webhook"
        return cls.model_validate(kwargs, context={"trusted_webhook": trusted})

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "kind": self.kind.value,
            "org_id": self.org_id,
            "source": self.source,
            "subject_type": self.subject_type.value,
            "subject_key": self.subject_key,
            "session_id": self.session_id,
            "project_id": self.project_id,
            "occurred_at": self.occurred_at.isoformat(),
            "dedupe_key": self.dedupe_key,
            "payload": self.payload,
            "payload_ref": self.payload_ref,
            "priority": self.priority.value,
            "correlation_id": self.correlation_id,
        }


def derive_id_for(env: "SpatialEventEnvelope") -> str:
    return derive_event_id(
        source=env.source,
        kind=env.kind.value,
        subject_key=env.subject_key,
        occurred_at=env.occurred_at,
        payload=env.payload,
    )


# ── Watch / Trigger 契约 ────────────────────────────────────────────


class TriggerAction(str, Enum):
    MISSION_CREATE = "mission_create"
    MISSION_REVISE = "mission_revise"
    MISSION_RESUME = "mission_resume"
    NOTIFY_ONLY = "notify_only"


TRIGGER_ACTIONS: Tuple[str, ...] = tuple(a.value for a in TriggerAction)

_MISSION_ACTIONS = {
    TriggerAction.MISSION_CREATE.value,
    TriggerAction.MISSION_REVISE.value,
    TriggerAction.MISSION_RESUME.value,
}


class MetricOp(str, Enum):
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    DELTA_PCT_GE = "delta_pct_ge"


class WatchCondition(BaseModel):
    """触发条件谓词（可组合；全部可选 = 恒真，仅按 kinds/subject 过滤）。

    - metric_name：payload 内点路径（如 "metric.ndvi"）
    - metric_op/metric_value：阈值比较；delta_pct_ge 相对 watch 状态里上次值
    - position_path：payload 内 [lon, lat] 点路径（AOI 谓词用，默认 "position"）
    - aoi_bbox：[minx, miny, maxx, maxy]；aoi_ref：GeoJSON ref（大 AOI 走 ref）
    - aoi_mode：enter/exit（AOI 谓词触发方向）
    - version_property：payload 内版本/修订路径（值变化才触发）
    - window_s + window_min_events：时间窗内事件计数
    - consecutive_n：连续达标次数
    """

    metric_name: Optional[str] = Field(default=None, max_length=64)
    metric_op: Optional[MetricOp] = None
    metric_value: Optional[float] = None
    position_path: Optional[str] = Field(default=None, max_length=64)
    aoi_bbox: Optional[List[float]] = Field(default=None, min_length=4, max_length=4)
    aoi_ref: Optional[str] = Field(default=None, max_length=160)
    aoi_mode: str = Field(default="enter", pattern="^(enter|exit)$")
    version_property: Optional[str] = Field(default=None, max_length=64)
    window_s: Optional[float] = Field(default=None, gt=0, le=86400)
    window_min_events: Optional[int] = Field(default=None, ge=1, le=1000)
    consecutive_n: Optional[int] = Field(default=None, ge=1, le=100)

    @field_validator("aoi_bbox")
    @classmethod
    def _bbox_ordered(cls, v: Optional[List[float]]) -> Optional[List[float]]:
        if v is None:
            return v
        minx, miny, maxx, maxy = v
        if minx > maxx or miny > maxy:
            raise ValueError("aoi_bbox must be [minx, miny, maxx, maxy]")
        return v

    @model_validator(mode="after")
    def _metric_needs_name_and_value(self) -> "WatchCondition":
        if self.metric_op is not None or self.metric_name is not None:
            if self.metric_op is None or self.metric_name is None:
                raise ValueError("metric predicate requires metric_name + metric_op")
            if self.metric_value is None:
                raise ValueError("metric predicate requires metric_value")
        return self


class WatchState(BaseModel):
    """watch 求值状态（持久化；重启后继续——连续计数/窗口/冷却不归零）。"""

    last_metric_value: Optional[float] = None
    consecutive_hits: int = 0
    last_version: Optional[str] = None
    inside_aoi: Optional[bool] = None
    last_fired_at: Optional[datetime] = None
    last_event_id: str = ""
    window_event_times: List[datetime] = Field(default_factory=list)


class SpatialWatch(BaseModel):
    watch_id: str = Field(min_length=1, max_length=64)
    org_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    enabled: bool = True
    kinds: List[str] = Field(min_length=1)
    subject_key_prefix: Optional[str] = Field(default=None, max_length=MAX_SUBJECT_KEY)
    session_id: Optional[str] = Field(default=None, max_length=64)
    project_id: Optional[str] = Field(default=None, max_length=64)
    condition: WatchCondition = Field(default_factory=WatchCondition)
    actions: List[str] = Field(min_length=1)
    cooldown_s: float = Field(default=60.0, ge=0, le=86400)
    mission_goal_template: Optional[str] = Field(default=None, max_length=MAX_GOAL_TEMPLATE)
    mission_project_id: Optional[str] = Field(default=None, max_length=64)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @field_validator("kinds")
    @classmethod
    def _kinds_in_vocab(cls, v: List[str]) -> List[str]:
        unknown = [k for k in v if k not in EVENT_KINDS]
        if unknown:
            raise ValueError(f"unknown event kinds: {unknown}")
        return v

    @field_validator("actions")
    @classmethod
    def _actions_in_vocab(cls, v: List[str]) -> List[str]:
        unknown = [a for a in v if a not in TRIGGER_ACTIONS]
        if unknown:
            raise ValueError(f"unknown trigger actions: {unknown}")
        return v

    @model_validator(mode="after")
    def _mission_actions_need_goal(self) -> "SpatialWatch":
        has_mission_action = any(a in _MISSION_ACTIONS for a in self.actions)
        if has_mission_action and not (self.mission_goal_template or "").strip():
            raise ValueError(
                "mission_* actions require a bounded mission_goal_template"
            )
        return self


class WatchOutcome(str, Enum):
    FIRED = "fired"
    COOLDOWN = "cooldown"
    NOT_MET = "not_met"
    REJECTED = "rejected"
    DEFERRED = "deferred"
    DUPLICATE = "duplicate"


class WatchFireRecord(BaseModel):
    """一次 watch 触发的持久化事实（(watch_id,event_id) 唯一 ⇒ 幂等）。"""

    watch_id: str
    org_id: str
    event_id: str
    fired_at: datetime
    action: str
    outcome: str
    detail: Dict[str, Any] = Field(default_factory=dict)

    def to_bounded_dict(self) -> Dict[str, Any]:
        d = self.detail or {}
        return {
            "watch_id": self.watch_id,
            "org_id": self.org_id,
            "event_id": self.event_id,
            "fired_at": self.fired_at.isoformat(),
            "action": self.action,
            "outcome": self.outcome,
            "detail": {
                k: (v if isinstance(v, (int, float, bool)) else str(v)[:120])
                for k, v in list(d.items())[:8]
            },
        }


class LedgerWatchConflict(RuntimeError):
    """watch_id 已被其他租户占用（防跨租户劫持）。"""


__all__ = [
    "SCHEMA_VERSION",
    "MAX_INLINE_PAYLOAD_BYTES",
    "EventKind",
    "EVENT_KINDS",
    "SubjectType",
    "EventPriority",
    "PRIORITY_ORDER",
    "SpatialEventEnvelope",
    "derive_event_id",
    "derive_id_for",
    "canonical_payload_json",
    "TriggerAction",
    "TRIGGER_ACTIONS",
    "WatchCondition",
    "WatchState",
    "SpatialWatch",
    "WatchOutcome",
    "WatchFireRecord",
    "LedgerWatchConflict",
    "ValidationError",
]
