"""Data Fusion Authoring HTTP schema（响应模型；有界投影）。

载荷主体为 service 的 dict 投影 —— 模型以 extra=allow 承载并锁定
顶层结构（真实 response_model 纪律 #2123 同款；词表/预算由域内
contracts 锁定，schema 不重复词表）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class _Loose(BaseModel):
    model_config = ConfigDict(extra="allow")


class FusionSourceCard(_Loose):
    side: str
    state: str


class FusionDescribeResponse(_Loose):
    session_id: str
    sources: List[Dict[str, Any]]


class FusionPlanView(_Loose):
    plan_id: str
    session_id: str
    status: str
    revision: int


class FusionPlanListResponse(_Loose):
    plans: List[Dict[str, Any]]
    cursor: Optional[int] = None
    has_more: bool = False


class FusionPlanPreviewResponse(_Loose):
    cached: bool
    plan_digest: Optional[str] = None


class FusionRunView(_Loose):
    run_id: str
    plan_id: str
    session_id: str
    status: str


class FusionRunListResponse(_Loose):
    runs: List[Dict[str, Any]]


class FusionRunResultsResponse(_Loose):
    run_id: str
    cursor: int
    total: int
    has_more: bool
    features: List[Dict[str, Any]]


class FusionRunCancelResponse(_Loose):
    run_id: str
    status: Optional[str] = None
    cancel_requested: Optional[bool] = None
    note: Optional[str] = None


class FusionReviewQueueResponse(_Loose):
    run_id: str
    total: int
    cursor: int
    has_more: bool
    items: List[Dict[str, Any]]


class FusionDecisionView(_Loose):
    decision_id: str
    plan_id: str
    pair_key: str
    action: str
    revision: int


class FusionDecisionListResponse(_Loose):
    plan_id: str
    decisions: List[Dict[str, Any]]
    cursor: Optional[int] = None
    has_more: bool = False


class FusionRenderRefResponse(_Loose):
    render_ref: str
    features: int


class FusionRefreshResponse(_Loose):
    plan_id: str
    base_run_id: Optional[str] = None
    changed: bool = False


class FusionRecoverResponse(_Loose):
    recovered: int
