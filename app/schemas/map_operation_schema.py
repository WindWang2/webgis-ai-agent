"""Map Operation / Display Confirmation 路由 wire 模型（ADR-0278）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class MapOperationListResponse(BaseModel):
    """回执环只读面（有界，最新在前）。"""

    session_id: str
    operations: List[Dict[str, Any]] = Field(default_factory=list)


class MapOperationCommitRequest(BaseModel):
    """owner 批准 pending 敏感操作（expected_revision 缺省=批准时现读）。"""

    model_config = ConfigDict(extra="forbid")

    expected_revision: Optional[int] = Field(None, ge=0)


class MapOperationReceiptResponse(BaseModel):
    """单操作回执 / 批准结果（服务端构造的自由形状 + 信封字段）。"""

    session_id: str
    operation_id: str
    receipt: Dict[str, Any] = Field(default_factory=dict)


class DisplayConfirmationStatusResponse(BaseModel):
    """最终显示确认状态（GET）。

    - mode: auto（历史行为，恒 confirmed）/ required
    - pending: required 且已有终验裁决但未确认（前端确认卡的显示条件）
    - required_render_seq: 当前裁决依据的观察代次（ack 必须 ≥ 此值且
      等于最新观察代次才算新鲜）
    """

    session_id: str
    mode: str
    confirmed: bool
    pending: bool
    required_render_seq: int = 0
    latest_observation_seq: int = 0
    verdict_present: bool = False


class DisplayAckRequest(BaseModel):
    """owner 显式确认当前地图显示（render_seq=所确认的观察代次）。"""

    model_config = ConfigDict(extra="forbid")

    render_seq: int = Field(..., ge=0)


class DisplayAckResponse(BaseModel):
    accepted: bool
    confirmed: bool
    render_seq: int = 0
    reason: Optional[str] = None
    task_complete: Optional[bool] = None


__all__ = [
    "MapOperationListResponse",
    "MapOperationCommitRequest",
    "MapOperationReceiptResponse",
    "DisplayConfirmationStatusResponse",
    "DisplayAckRequest",
    "DisplayAckResponse",
]
