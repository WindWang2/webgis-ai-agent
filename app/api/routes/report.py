"""
报告生成 API - 从会话历史生成 PDF/HTML/Markdown 报告
支持报告列表、下载、分享等功能
"""
import os
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.api_response import ApiResponse, ErrCode
from app.models.report import Report
from app.models.db_model import Conversation, Message
from app.services.report_service import ReportService, REPORT_DIR

import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/reports", tags=["报告生成"])

ALLOWED_FORMATS = {"pdf", "html", "markdown", "md"}


# ── Schemas ──────────────────────────────────────────────────────────


class GenerateReportRequest(BaseModel):
    session_id: str
    format: str = "pdf"
    title: Optional[str] = None


class ReportListResponse(BaseModel):
    total: int
    items: list[dict]


class ShareRequest(BaseModel):
    ttl_days: int = 7


# ── Helpers ──────────────────────────────────────────────────────────


def _serialize_report(r: Report) -> dict:
    return {
        "id": r.id,
        "session_id": r.session_id,
        "title": r.title,
        "format": r.format,
        "status": r.status,
        "file_size": r.file_size,
        "share_code": r.share_code,
        "share_expires_at": (
            r.share_expires_at.isoformat() if r.share_expires_at else None
        ),
        "error_message": r.error_message,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "download_url": f"/api/v1/reports/{r.id}/download" if r.status == "completed" else None,
    }


def _media_type(fmt: str) -> str:
    if fmt == "pdf":
        return "application/pdf"
    if fmt == "html":
        return "text/html"
    return "text/markdown"


def _file_ext(fmt: str) -> str:
    if fmt in ("markdown", "md"):
        return "md"
    return fmt


# ── Endpoints ────────────────────────────────────────────────────────


@router.post("", response_model=ApiResponse)
async def create_report(
    request: GenerateReportRequest,
    db: Session = Depends(get_db),
):
    """从会话历史生成报告"""
    fmt = request.format.lower()
    if fmt not in ALLOWED_FORMATS:
        return ApiResponse.fail(code=ErrCode.VALIDATE_ERROR, message=f"不支持的格式: {fmt}，可选: {', '.join(sorted(ALLOWED_FORMATS))}")

    # 验证会话存在
    conversation = db.get(Conversation, request.session_id)
    if not conversation:
        return ApiResponse.fail(code=ErrCode.NOT_FOUND, message="会话不存在")

    messages = (
        db.query(Message)
        .filter(Message.conversation_id == request.session_id)
        .order_by(Message.created_at.asc())
        .all()
    )

    if not messages:
        return ApiResponse.fail(code=ErrCode.VALIDATE_ERROR, message="会话中暂无消息，无法生成报告")

    # 创建报告记录
    report_id = str(uuid.uuid4())
    title = request.title or conversation.title or "分析报告"
    file_name = f"{report_id}.{_file_ext(fmt)}"
    file_path = os.path.join(REPORT_DIR, file_name)

    report = Report(
        id=report_id,
        session_id=request.session_id,
        title=title,
        format=fmt,
        status="generating",
        file_path=file_path,
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    # 异步生成报告
    svc = ReportService()
    try:
        msg_dicts = [
            {
                "role": m.role,
                "content": m.content or "",
                "tool_calls": m.tool_calls,
                "tool_result": m.tool_result,
            }
            for m in messages
        ]

        success = await svc.generate_report(
            session_id=request.session_id,
            session_title=conversation.title,
            messages=msg_dicts,
            output_path=file_path,
            format=fmt,
        )

        if success and os.path.exists(file_path):
            report.status = "completed"
            report.file_size = os.path.getsize(file_path)
        else:
            report.status = "failed"
            report.error_message = "报告生成失败"
    except Exception as e:
        logger.error(f"Report generation error: {e}", exc_info=True)
        report.status = "failed"
        report.error_message = str(e)

    db.commit()
    db.refresh(report)

    if report.status == "failed":
        return ApiResponse.fail(
            code=ErrCode.SERVER_ERROR,
            message=f"报告生成失败: {report.error_message}",
            data=_serialize_report(report),
        )

    return ApiResponse.ok(data=_serialize_report(report), message="报告生成成功")


@router.get("", response_model=ApiResponse)
async def list_reports(
    session_id: Optional[str] = Query(None, description="按会话 ID 筛选"),
    db: Session = Depends(get_db),
):
    """列出报告"""
    q = db.query(Report).order_by(Report.created_at.desc())
    if session_id:
        q = q.filter(Report.session_id == session_id)

    items = q.limit(100).all()
    return ApiResponse.ok(data={
        "total": len(items),
        "items": [_serialize_report(r) for r in items],
    })


@router.get("/shared/{share_code}", response_model=ApiResponse)
async def get_shared_report_info(share_code: str, db: Session = Depends(get_db)):
    """通过分享码获取报告信息"""
    report = db.query(Report).filter(Report.share_code == share_code).first()
    if not report:
        return ApiResponse.fail(code=ErrCode.NOT_FOUND, message="分享链接不存在")

    if report.share_expires_at and report.share_expires_at < datetime.now(timezone.utc):
        return ApiResponse.fail(code=ErrCode.NOT_FOUND, message="分享链接已过期")

    return ApiResponse.ok(data=_serialize_report(report))


@router.get("/shared/{share_code}/view")
async def view_shared_report(share_code: str, db: Session = Depends(get_db)):
    """通过分享码查看/下载报告文件"""
    report = db.query(Report).filter(Report.share_code == share_code).first()
    if not report:
        raise HTTPException(status_code=404, detail="分享链接不存在")

    if report.share_expires_at and report.share_expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=404, detail="分享链接已过期")

    if report.status != "completed" or not report.file_path or not os.path.exists(report.file_path):
        raise HTTPException(status_code=404, detail="报告文件不可用")

    if report.format == "html":
        with open(report.file_path, "r", encoding="utf-8") as f:
            return Response(content=f.read(), media_type="text/html")

    return FileResponse(
        report.file_path,
        media_type=_media_type(report.format),
        filename=f"report_{report.id[:8]}.{_file_ext(report.format)}",
    )


@router.get("/{report_id}", response_model=ApiResponse)
async def get_report(report_id: str, db: Session = Depends(get_db)):
    """获取报告详情"""
    report = db.get(Report, report_id)
    if not report:
        return ApiResponse.fail(code=ErrCode.NOT_FOUND, message="报告不存在")
    return ApiResponse.ok(data=_serialize_report(report))


@router.get("/{report_id}/download")
async def download_report(report_id: str, db: Session = Depends(get_db)):
    """下载报告文件"""
    report = db.get(Report, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    if report.status != "completed":
        raise HTTPException(status_code=400, detail="报告未生成完成")
    if not report.file_path or not os.path.exists(report.file_path):
        raise HTTPException(status_code=404, detail="报告文件不存在")

    return FileResponse(
        report.file_path,
        media_type=_media_type(report.format),
        filename=f"report_{report.id[:8]}.{_file_ext(report.format)}",
    )


@router.post("/{report_id}/share", response_model=ApiResponse)
async def create_share_link(
    report_id: str,
    body: ShareRequest = ShareRequest(),
    db: Session = Depends(get_db),
):
    """生成分享链接"""
    report = db.get(Report, report_id)
    if not report:
        return ApiResponse.fail(code=ErrCode.NOT_FOUND, message="报告不存在")
    if report.status != "completed":
        return ApiResponse.fail(code=ErrCode.VALIDATE_ERROR, message="报告未生成完成，无法分享")

    ttl_days = max(1, min(body.ttl_days, 30))
    share_code = secrets.token_urlsafe(12)
    report.share_code = share_code
    report.share_expires_at = datetime.now(timezone.utc) + timedelta(days=ttl_days)
    db.commit()
    db.refresh(report)

    return ApiResponse.ok(data={
        "share_code": share_code,
        "share_url": f"/api/v1/reports/shared/{share_code}",
        "expires_at": report.share_expires_at.isoformat(),
        "ttl_days": ttl_days,
    })


__all__ = ["router"]
