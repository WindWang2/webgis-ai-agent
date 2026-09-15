"""空间反幻觉守护引擎错误契约（ADR-0195 D-错误契约）。

约定：校验器内部校验函数直接抛错（单测按类型断言）；编排网关
`guardrail_middleware` 捕获后折算为 `GuardrailVerdict` 的 BLOCK issue，
两个挂载点据此返回各自的错误载体（ToolDispatchResult / MapSpecResult），
不向调用方泄栈。
"""
from __future__ import annotations

from app.services.spatial_guardrails.types import GuardrailIssue


class SpatialGuardrailError(Exception):
    """守护引擎错误基类：携带触发它的结构化 issue。"""

    def __init__(self, issue: GuardrailIssue) -> None:
        super().__init__(issue.message)
        self.issue = issue


class GeographicImpossibilityError(SpatialGuardrailError):
    """L2/L3 物理不可能：如陆上设施落在确信开阔水域。"""


class LatLonInversionError(SpatialGuardrailError):
    """L1 高危经纬度倒置且不可自动纠偏。"""


class FabricatedAdminDivisionError(SpatialGuardrailError):
    """虚构行政区划码（结构层第一道防线截获）。"""


class GeofenceRedlineViolationError(SpatialGuardrailError):
    """L2 红线围栏越界 / 抓取预算超限。"""


class TopologyImplausibilityError(SpatialGuardrailError):
    """L4 几何自洽性严重违背（瞬移线段、退化面）。"""
