"""G42 目标补全（goal repair）HTTP 响应契约（ADR-0138 response_model 基线）。

逐字段镜像 ``app.services.gis_harness.goal_repair_execution`` 预览回执与
attempt 视图（store._row_to_view）的运行时形状；动作行/摘要等自由子文档
以 Dict[str, Any] 透传。所有模型 ``extra="allow"``：未知字段原样透传，
保证挂模前后响应体逐字节一致。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class GoalRepairPreviewResponse(BaseModel):
    """版本化补全预览（build_repair_preview 形状；零副作用）。"""

    model_config = ConfigDict(extra="allow")

    schema_version: str
    session_id: str
    goal_id: str
    verdict: str
    signal: str
    plan_version: str
    goal_fingerprint: str
    mapspec_revision: int
    observation_seq: int
    evidence_signature: str
    actions: List[Dict[str, Any]] = Field(default_factory=list)
    blockers: List[str] = Field(default_factory=list)
    progress_rule: str
    active_attempt: Optional[Dict[str, Any]] = None
    attempts: List[Dict[str, Any]] = Field(default_factory=list)


class GoalRepairAttemptView(BaseModel):
    """attempt 视图（store._row_to_view 形状）+ start/cancel 附加键。

    selected_action_ids / budget_denied 仅 start 回填；cancel_requested /
    note 仅 cancel 回填。
    """

    model_config = ConfigDict(extra="allow")

    schema_version: str
    attempt_id: str
    status: str
    active: bool
    cancellable: bool
    session_id: str
    goal_id: str
    plan_version: str
    entry: str
    parent_attempt_id: str
    turn_id: str
    created_at: Optional[str] = None
    heartbeat_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None
    actions: List[Dict[str, Any]] = Field(default_factory=list)
    outcome: Optional[str] = None
    final_verdict_before: Optional[str] = None
    final_verdict_after: Optional[str] = None
    final_changed: bool = False
    blockers: List[str] = Field(default_factory=list)
    selected_action_ids: Optional[List[str]] = None
    budget_denied: Optional[List[str]] = None
    cancel_requested: Optional[bool] = None
    note: Optional[str] = None
