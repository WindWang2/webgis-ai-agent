"""User-approved visual repair 契约模型（F15，ADR-0214 决策四）。

两步提案/应用：plan（零突变预览，闭包 ⊆ healer 四类呈现面微变异）→
apply（显式 ``approved=true`` + CAS）。截图上传通道的请求面也在此
（ref-only 纪律：响应只含 ref/sha 摘要，永无字节回显）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

_PROPOSAL_ID_PATTERN = r'^[A-Za-z0-9._:-]{6,96}$'


class VisualRepairPlanRequest(BaseModel):
    """plan 请求：可选 finding_id 定向过滤（缺省 = 全部可修复候选）。"""

    finding_ids: Optional[List[str]] = Field(
        default=None, max_length=12,
        description="定向修复的 finding id 列表（map_product.visual_findings 的 finding_id）",
    )


class VisualRepairApplyRequest(BaseModel):
    """apply 请求：显式批准 + 乐观 CAS（expected_revision 漂移 → 409）。

    ``approved`` 缺省 False 且必须显式传 true —— 缺席/False 的 apply 一律
    400 ``approval_required``（用户批准是结构门槛，不是默认语义）。
    """

    proposal_id: str = Field(pattern=_PROPOSAL_ID_PATTERN)
    approved: bool = False
    expected_revision: int = Field(ge=0)


class VisualRepairPlanResponse(BaseModel):
    """plan 响应（可提案 = 闭包预览；不可提案 = 诚实缺席原因）。

    ``stale_count``（C13）：因证据新鲜度门被跳过的 finding 数 ——
    ``observed_revision`` ≠ 当前 mutation revision 的存储态 finding 不可
    修复（旧观测不得驱动新地图），只能等下一次终验重盖章。
    """

    session_id: str
    proposable: bool
    reason: str = ""
    proposal_id: str = ""
    base_revision: int = 0
    ops: List[Dict[str, Any]] = Field(default_factory=list)
    skipped: List[Dict[str, Any]] = Field(default_factory=list)
    finding_ids: List[str] = Field(default_factory=list)
    stale_count: int = 0


class VisualRepairApplyResponse(BaseModel):
    """apply 响应（applied=false 时必带机器可读 reason —— 硬停/锁/漂移）。"""

    session_id: str
    proposal_id: str
    applied: bool
    duplicate: bool = False
    reason: str = ""
    hard_stop: bool = False
    mutation_revision: int = 0
    correction_hint: str = ""
    reverify: str = ""


class VisualScreenshotUploadResponse(BaseModel):
    """截图入库回执（ref-only：ref+sha 摘要，永无字节回显）。"""

    session_id: str
    ref: str
    sha256: str
    size: int
    mapspec_revision: int
    mapspec_fingerprint: str = ""


class VisualRepairRejectRequest(BaseModel):
    """reject 请求：记录用户对提案的否决（拒绝记忆的输入面）。

    拒绝按缺陷指纹持久（会话内）—— 同一 patch 不再被 plan/自动通道
    反复索要；缺陷证据变化（指纹变化）后允许新提案。
    """

    proposal_id: str = Field(pattern=_PROPOSAL_ID_PATTERN)
    reason: str = Field(default="", max_length=200)


class VisualRepairRejectResponse(BaseModel):
    """reject 回执（幂等：重复拒绝同一提案返回相同结论）。"""

    session_id: str
    proposal_id: str
    rejected: bool
    defect_fingerprint: str = ""


__all__ = [
    "VisualRepairPlanRequest",
    "VisualRepairApplyRequest",
    "VisualRepairPlanResponse",
    "VisualRepairApplyResponse",
    "VisualScreenshotUploadResponse",
    "VisualRepairRejectRequest",
    "VisualRepairRejectResponse",
]
