"""报告子系统契约模型（V9 契约基石，ADR-0138）。

从 `app/api/routes/report.py` 内联迁出；路由文件只 import。
报告端点响应统一走 ApiResponse 信封；download/view 为文件流（排除清单）。
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class GenerateReportRequest(BaseModel):
    """POST /reports 请求体。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "session_id": "sess-123",
                    "format": "pdf",
                    "title": "流域分析报告",
                }
            ]
        }
    )

    session_id: str
    format: str = "pdf"
    title: Optional[str] = None


class ReportListResponse(BaseModel):
    """报告列表 DTO（当前列表端点走 ApiResponse 信封，本模型保留给
    summary/detail 分层演进使用）。"""

    total: int
    items: list[dict]


class ShareRequest(BaseModel):
    """POST /reports/{report_id}/share 请求体。"""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"ttl_days": 7}]}
    )

    ttl_days: int = 7
