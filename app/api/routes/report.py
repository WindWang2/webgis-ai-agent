"""
T005 报告生成API - 支持PDF/HTML/Markdown格式导出空间分析结果
创建时间: 2026-04-03
更新时间: 2026-04-04 新增Markdown格式支持
"""
import os
import tempfile
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
import uuid
import logging

from app.core.database import get_db
from app.models.api_response import ApiResponse
from app.services.report_service import ReportService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/reports", tags=["报告生成"])

# ======= Schema 定义 =======
class GenerateReportRequest(BaseModel):
    """生成报告请求"""
    task_id: int
    format: str = "pdf"  # pdf/html/markdown/md
    include_map_screenshot: bool = True
    template: str = "default"


class ReportInfo(BaseModel):
    """报告信息"""
    report_id: str
    task_id: int
    format: str
    status: str  # pending/completed/failed
    download_url: str
    created_at: int
    file_size: int | None = None


# ======= 内存存储（生产环境使用数据库 + Redis） =======
_reports: dict[str, dict] = {}
_shared_reports: dict[str, dict] = {}  # 分享码 -> 报告ID + 过期时间
_report_dir = tempfile.gettempdir() + "/webgis_reports"
os.makedirs(_report_dir, exist_ok=True)

# ======= API 实现 =======
@router.post("/generate", response_model=ApiResponse)
async def generate_report(
    request: GenerateReportRequest, 
    db: Session = Depends(get_db)
):
    """
    生成空间分析结果报告
    """
    # 验证参数
    allowed_formats = ["pdf", "html", "markdown", "md"]
    if request.format.lower() not in allowed_formats:
        return ApiResponse.fail(code="INVALID_FORMAT", message=f"仅支持以下格式: {', '.join(allowed_formats)}")
    
    # 验证任务存在
    from app.services.layer_service import TaskService
    task_svc = TaskService(db)
    task = task_svc.get_task_by_id(request.task_id)

    
    if not task:
        return ApiResponse.fail(code="TASK_NOT_FOUND", message="任务不存在")
    
    if task.status != "completed":
        return ApiResponse.fail(code="INVALID_TASK_STATE", message="仅支持已完成的分析任务生成报告")
    
    # 创建报告记录
    report_id = str(uuid.uuid4())
    report_file = f"{_report_dir}/{report_id}.{request.format.lower()}"
    
    _reports[report_id] = {
        "id": report_id,
        "task_id": request.task_id,
        "format": request.format.lower(),
        "status": "pending",
        "file_path": report_file,
        "created_at": int(datetime.now().timestamp() * 1000),
        "include_map_screenshot": request.include_map_screenshot
    }
    
    # 异步生成报告
    report_svc = ReportService()
    try:
        success = await report_svc.generate_report(
            task=task,
            report_id=report_id,
            output_path=report_file,
            format=request.format.lower(),
            include_screenshot=request.include_map_screenshot
        )
        
        if success:
            _reports[report_id]["status"] = "completed"
            _reports[report_id]["file_size"] = os.path.getsize(report_file)
        else:
            _reports[report_id]["status"] = "failed"
            return ApiResponse.fail(code="GENERATE_FAILED", message="报告生成失败")
            
    except Exception as e:
        logger.error(f"报告生成失败: {e}")
        _reports[report_id]["status"] = "failed"
        return ApiResponse.fail(code="GENERATE_ERROR", message=f"报告生成错误: {str(e)}")
    
    return ApiResponse.success(data={
        "report_id": report_id,
        "format": request.format,
        "download_url": f"/api/v1/reports/{report_id}/download",
        "status": "completed"
    })


@router.get("/{report_id}", response_model=ApiResponse)
async def get_report_info(report_id: str):
    """获取报告状态和信息"""
    if report_id not in _reports:
        raise HTTPException(status_code=404, detail="报告不存在")
    
    report = _reports[report_id]
    return ApiResponse.success(data={
        "report_id": report_id,
        "task_id": report["task_id"],
        "format": report["format"],
        "status": report["status"],
        "created_at": report["created_at"],
        "file_size": report.get("file_size"),
        "download_url": f"/api/v1/reports/{report_id}/download" if report["status"] == "completed" else None
    })


@router.get("/{report_id}/download")
async def download_report(report_id: str):
    """下载生成好的报告"""
    if report_id not in _reports:
        raise HTTPException(status_code=404, detail="报告不存在")
    
    report = _reports[report_id]
    
    if report["status"] != "completed":
        raise HTTPException(status_code=400, detail="报告未生成完成")
    
    file_path = report["file_path"]
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="报告文件不存在")
    
    format_ext = report['format']
    if format_ext == "md":
        format_ext = "markdown"
    filename = f"analysis_report_{report['task_id']}.{format_ext}"
    
    if report["format"] == "pdf":
        media_type = "application/pdf"
    elif report["format"] in ["html"]:
        media_type = "text/html"
    else:  # markdown/md
        media_type = "text/markdown"
    
    return FileResponse(
        file_path,
        media_type=media_type,
        filename=filename
    )


@router.post("/{report_id}/share", response_model=ApiResponse)
async def create_share_link(report_id: str, ttl_days: int = 7):
    """
    创建报告分享链接，默认有效期7天
    """
    if report_id not in _reports:
        raise HTTPException(status_code=404, detail="报告不存在")
    
    report = _reports[report_id]
    if report["status"] != "completed":
        return ApiResponse.fail(code="REPORT_NOT_READY", message="报告还未生成完成，无法分享")
    
    # 生成唯一分享码
    import secrets
    share_code = secrets.token_urlsafe(12)
    
    # 保存分享记录
    expire_at = int(datetime.now().timestamp()) + (ttl_days * 24 * 3600)
    _shared_reports[share_code] = {
        "report_id": report_id,
        "created_at": int(datetime.now().timestamp()),
        "expire_at": expire_at,
        "ttl_days": ttl_days,
        "access_count": 0
    }
    
    # 生成分享链接
    share_url = f"/api/v1/reports/shared/{share_code}"
    
    return ApiResponse.success(data={
        "share_code": share_code,
        "share_url": share_url,
        "expire_at": expire_at,
        "ttl_days": ttl_days
    })


@router.get("/shared/{share_code}")
async def get_shared_report(share_code: str):
    """访问公开分享的报告"""
    # 校验分享码是否存在
    if share_code not in _shared_reports:
        raise HTTPException(status_code=404, detail="分享链接不存在或已过期")
    
    share_info = _shared_reports[share_code]
    current_time = int(datetime.now().timestamp())
    
    # 校验是否过期
    if current_time > share_info["expire_at"]:
        del _shared_reports[share_code]
        raise HTTPException(status_code=404, detail="分享链接已过期")
    
    # 增加访问计数
    share_info["access_count"] += 1
    
    # 获取报告信息
    report_id = share_info["report_id"]
    if report_id not in _reports:
        raise HTTPException(status_code=404, detail="报告不存在")
    
    report = _reports[report_id]
    if report["status"] != "completed":
        raise HTTPException(status_code=400, detail="报告已被删除")
    
    # 重定向到下载或预览
    if report["format"] == "html":
        # HTML格式直接预览
        with open(report["file_path"], "r", encoding="utf-8") as f:
            return Response(content=f.read(), media_type="text/html")
    else:
        # 其他格式直接下载
        return FileResponse(
            report["file_path"],
            media_type="application/octet-stream",
            filename=f"shared_report_{report['task_id']}.{report['format']}"
        )


@router.get("/shared/{share_code}/info", response_model=ApiResponse)
async def get_shared_report_info(share_code: str):
    """获取分享报告的基本信息（无需下载文件）"""
    if share_code not in _shared_reports:
        raise HTTPException(status_code=404, detail="分享链接不存在或已过期")
    
    share_info = _shared_reports[share_code]
    current_time = int(datetime.now().timestamp())
    
    if current_time > share_info["expire_at"]:
        del _shared_reports[share_code]
        raise HTTPException(status_code=404, detail="分享链接已过期")
    
    report_id = share_info["report_id"]
    report = _reports.get(report_id, {})
    
    return ApiResponse.success(data={
        "share_code": share_code,
        "expire_at": share_info["expire_at"],
        "access_count": share_info["access_count"],
        "created_at": share_info["created_at"],
        "report_id": report_id,
        "task_id": report.get("task_id"),
        "format": report.get("format")
    })


__all__ = ["router"]
