"""G37 会话模型运行配置 + G43 上下文资料工作台 HTTP 响应契约。

逐字段镜像 ``app.services.chat.model_runtime``（目录/偏好/用量）与
``app.services.turn_context_store`` / ``app.services.context_assembly``
（资料目录/策略/预览/回执）的运行时形状。所有模型 ``extra="allow"``：
未知字段原样透传，保证挂模前后响应体逐字节一致。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ── G37 会话模型视图 ──────────────────────────────────────────────────────


class SessionModelCatalogItem(BaseModel):
    """目录项公开投影（session_catalog.public_payload）。"""

    model_config = ConfigDict(extra="allow")

    item_id: str = ""
    model_id: Optional[str] = None
    label: Optional[str] = None
    provider: Optional[str] = None
    available: Optional[bool] = None
    unavailable_reason: Optional[str] = None


class SessionModelPreference(BaseModel):
    model_config = ConfigDict(extra="allow")

    mode: str
    model_id: Optional[str] = None
    thinking_level: Optional[str] = None
    session_token_cap: Optional[int] = None
    revision: int = 0


class SessionModelLastTurn(BaseModel):
    model_config = ConfigDict(extra="allow")

    turn_id: str = ""
    status: str = ""
    mode: str = ""
    requested_model: Optional[str] = None
    effective_model: Optional[str] = None
    thinking_requested: Optional[str] = None
    thinking_effective: Optional[str] = None
    response_model: Optional[str] = None
    catalog_generation: Optional[str] = None
    created_at: Optional[str] = None


class SessionModelViewResponse(BaseModel):
    """GET /sessions/{id}/model（目录 + preference + 目标解析 + 最近回执）。"""

    model_config = ConfigDict(extra="allow")

    session_id: str
    catalog_generation: str = ""
    catalog: List[Dict[str, Any]] = Field(default_factory=list)
    preference: SessionModelPreference
    target: Dict[str, Any] = Field(default_factory=dict)
    last_turn: Optional[SessionModelLastTurn] = None
    pending_next_turn: bool = False
    thinking_controllable: bool = False
    budget: Dict[str, Any] = Field(default_factory=dict)


class SessionModelPutResponse(BaseModel):
    """PUT /sessions/{id}/model（CAS 写入回执；生效在 turn 边界）。"""

    model_config = ConfigDict(extra="allow")

    session_id: str
    preference: SessionModelPreference
    effective_when: str
    pending_next_turn: bool = False
    thinking_controllable: bool = False


class SessionUsageTokenBlock(BaseModel):
    model_config = ConfigDict(extra="allow")

    input: int = 0
    output: int = 0
    cacheRead: int = 0
    cacheWrite: int = 0
    total: int = 0


class SessionUsageTurn(BaseModel):
    model_config = ConfigDict(extra="allow")

    turn_id: str = ""
    status: str = ""
    mode: str = ""
    requested_model: Optional[str] = None
    effective_model: Optional[str] = None
    thinking_requested: Optional[str] = None
    thinking_effective: Optional[str] = None
    response_model: Optional[str] = None
    tokens: SessionUsageTokenBlock = Field(default_factory=SessionUsageTokenBlock)
    cost_total_micro_usd: Optional[int] = None
    cost_source: Optional[str] = None
    complete: Optional[bool] = None
    records: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None


class SessionUsageResponse(BaseModel):
    """GET /sessions/{id}/usage（累计 + 最近 turn + coverage 分层）。"""

    model_config = ConfigDict(extra="allow")

    session_id: str
    totals: SessionUsageTokenBlock = Field(default_factory=SessionUsageTokenBlock)
    turns_recorded: int = 0
    turns_incomplete: int = 0
    cost_total_micro_usd: Optional[int] = None
    cost_source: str = "unknown"
    recent_turns: List[SessionUsageTurn] = Field(default_factory=list)
    coverage: Dict[str, Any] = Field(default_factory=dict)
    # WP3/B6（GOAL-20261127-01）：预算块（cap=生效上限；None=无预算控制；
    # used/remaining 仅在 cap 有界时出现）—— 与 budget_admission 同一权威。
    budget: Dict[str, Any] = Field(default_factory=dict)


class SessionRuntimeResponse(BaseModel):
    """GET /sessions/{id}/runtime（WP4 运行质量聚合投影，只读）。

    turns = 账本最近 turn（含 attempt 链）；provider_health = 路由健康表
    白名单投影（进程级，无用户内容）；vendor_session_stats = vendor 视角
    对账块（不可达时 available=false + reason，不伪造）。
    """

    model_config = ConfigDict(extra="allow")

    session_id: str
    turns: List[Dict[str, Any]] = Field(default_factory=list)
    totals: Dict[str, Any] = Field(default_factory=dict)
    budget: Dict[str, Any] = Field(default_factory=dict)
    provider_health: Dict[str, Any] = Field(default_factory=dict)
    vendor_session_stats: Dict[str, Any] = Field(default_factory=dict)


# ── G43 上下文资料视图 ────────────────────────────────────────────────────


class ContextSourcesResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    session_id: str
    project_id: str = ""
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    counts: Dict[str, int] = Field(default_factory=dict)


class SourcePolicyView(BaseModel):
    """SourcePolicy.as_bounded_dict 形状。"""

    model_config = ConfigDict(extra="allow")

    schema: str = ""
    session_id: str = ""
    mode: str = "auto"
    preferred: List[str] = Field(default_factory=list)
    excluded: List[str] = Field(default_factory=list)
    policy_version: int = 0


class ContextPolicyResponse(BaseModel):
    """GET/PUT /sessions/{id}/context-policy 共用。"""

    model_config = ConfigDict(extra="allow")

    session_id: str
    policy: SourcePolicyView
    effective_when: Optional[str] = None
    scope_note: str = ""


class ContextPreviewBlock(BaseModel):
    model_config = ConfigDict(extra="allow")

    domain: str = ""
    available: bool = False
    reason: str = ""
    est_tokens: int = 0
    chars: Optional[int] = None


class ContextPreviewResponse(BaseModel):
    """POST /sessions/{id}/context-preview（只读投影；非发送回执）。"""

    model_config = ConfigDict(extra="allow")

    schema: str
    session_id: str
    generated_at: str
    is_preview: bool
    disclaimer: str = ""
    policy: Optional[SourcePolicyView] = None
    blocks: List[ContextPreviewBlock] = Field(default_factory=list)
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    estimate: Dict[str, Any] = Field(default_factory=dict)
    not_previewed: List[str] = Field(default_factory=list)


class ContextReceiptsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    session_id: str
    receipts: List[Dict[str, Any]] = Field(default_factory=list)
    coverage_note: str = ""


class ContextReceiptResponse(BaseModel):
    """单 turn 完整回执（turn_context_store._receipt_read_model 形状）。"""

    model_config = ConfigDict(extra="allow")

    receipt_id: Optional[int] = None
    session_id: Optional[str] = None
    turn_id: Optional[str] = None
    run_id: Optional[str] = None
    assembly_mode: Optional[str] = None
    policy_version: Optional[int] = None
    digest: Optional[str] = None
    status: Optional[str] = None
    not_sent_reason: Optional[str] = None
    disposition: Optional[str] = None
    turn_outcome: Optional[str] = None
    user_message_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
