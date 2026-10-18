"""Interaction IR V1 wire 契约模型（Goal 02 / ADR-0224）。

请求/响应信封；kind 词表与有界常量的权威在
``app/services/gis_harness/interaction_ir/contracts.py``（单一权威，本模块
只做引用，杜绝两套词表漂移）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.services.gis_harness.interaction_ir.contracts import (
    MAX_EVENTS_PER_BATCH,
    SCHEMA_VERSION,
)

#: 客户端可见 actor 词表；**客户端端点只放行 user**（服务端校验，防伪造
#: agent/system 事件 —— ADR-0224 G）。
ActorLiteral = Literal["user", "agent", "system"]
MutationOutcomeLiteral = Literal["committed", "superseded", "failed"]

_ID_PATTERN = r'^[A-Za-z0-9._:-]+$'


class InteractionEventV1(BaseModel):
    """一条用户地图交互事件（observation/event layer，非状态真相）。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["interaction_ir.v1"] = SCHEMA_VERSION
    event_id: str = Field(min_length=8, max_length=64, pattern=_ID_PATTERN)
    actor: ActorLiteral = "user"
    #: per-tab 稳定实例 id（sessionStorage 维持跨刷新连续性）。
    client_instance_id: str = Field(
        min_length=8, max_length=64, pattern=_ID_PATTERN,
    )
    #: per-(session, client_instance_id) 严格单调，从 1 起（切会话由前端
    #: 重置）。服务端对新实例的首个 seq 不要求从 1 起（刷新续号/复制
    #: 标签页合法）—— gap 只对已知实例披露（ADR-0224 review P2-1）。
    client_seq: int = Field(ge=1, le=2**53)
    kind: str = Field(min_length=1, max_length=48)
    #: 引发本事件的上一事件/mutation id（causality 链）。
    causation_id: Optional[str] = Field(default=None, max_length=64)
    correlation_id: Optional[str] = Field(default=None, max_length=64)
    turn_id: Optional[str] = Field(default=None, max_length=64)

    # ── authority 关联（只引用，不塞大 payload）───────────────────────
    client_mutation_id: Optional[str] = Field(
        default=None, min_length=8, max_length=128, pattern=_ID_PATTERN,
    )
    expected_revision: Optional[int] = Field(default=None, ge=0)
    #: 客户端申报的 mutation 结局（服务端对 _mutation_dedup 交叉校验）。
    mutation_revision: Optional[int] = Field(default=None, ge=0)
    mutation_outcome: Optional[MutationOutcomeLiteral] = None
    mapspec_fingerprint: Optional[str] = Field(default=None, max_length=64)
    viewport_seq: Optional[int] = Field(default=None, ge=0)
    render_observation_seq: Optional[int] = Field(default=None, ge=0)

    #: 有界 payload（服务端按 kind 白名单二次规范化）。
    payload: Dict[str, Any] = Field(default_factory=dict)
    #: 客户端时间戳（advisory —— 排序/投影/指纹一律不用 wall-clock）。
    created_at: Optional[str] = Field(default=None, max_length=32)


class InteractionEventAck(BaseModel):
    """单条事件的摄入裁决（ack 信封，客户端据此同步水印/去重）。"""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    client_seq: int
    #: accepted / accepted_with_gap / duplicate / stale_seq / rejected
    verdict: Literal[
        "accepted", "accepted_with_gap", "duplicate", "stale_seq", "rejected",
    ]
    reason: Optional[str] = None
    server_seq: Optional[int] = None
    gap_from: Optional[int] = None


class InteractionEventBatchRequest(BaseModel):
    """批量摄入请求（outbox 批量发送，≤ MAX_EVENTS_PER_BATCH 条）。"""

    model_config = ConfigDict(extra="forbid")

    events: List[InteractionEventV1] = Field(min_length=1, max_length=MAX_EVENTS_PER_BATCH)


class InteractionIngestResponse(BaseModel):
    """批量摄入响应：逐条裁决 + 批摘要 + per-instance 水印。"""

    model_config = ConfigDict(extra="forbid")

    accepted: int
    duplicates: int
    stale: int
    rejected: int
    acks: List[InteractionEventAck]
    #: client_instance_id → 服务端已见最大 client_seq（客户端对齐本地水印，
    #: 避免 ack 丢失后的无限重发）。
    watermarks: Dict[str, int] = Field(default_factory=dict)
    #: ledger 环淘汰/预算丢弃累计（诚实披露，仅非零出现）。
    dropped: Optional[int] = None
    #: 持久化降级披露（分类已给但存储失败 —— 客户端重试安全：水印/查重
    #: 会把重放收敛为 stale/duplicate，不双计）。
    persist_degraded: Optional[bool] = None


class InteractionProjectionResponse(BaseModel):
    """有界交互投影（agent context / 诊断只读面）。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    session_id: str
    projection: Dict[str, Any]
