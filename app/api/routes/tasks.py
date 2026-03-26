"""
T003 空间分析任务队列 - Celery 任务编排、进度上报、失败重试
支持异步任务提交、进度查询、SSE推送、失败自动重试3次
"""
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session as DbSession
import asyncio
import json
import logging
from datetime import datetime
from app.core.config import get_settings
from app.db.session import get_db
from app.models.api_response import ApiResponse
from app.services.task_queue import task_queue
from app.tasks.analysis import run_analysis_task
from app. services.celery_config import celery_app
router = APIRouter(prefix="/tasks", tags=["任务管理"])
settings = get_settings()
logger = logging.getLogger(__name__)
# ============ 任务提交 ============
@router.post("/submit", response_model=ApiResponse)
def submit_task(
    task_type: str,
    parameters: dict,
    layer_id: int,
    priority: int = 5,
    db: DbSession = Depends(get_db),
):
    """
    提交空间分析任务
    
    参数:
    - task_type: 分析类型(buffer/clip/intersect/dissolve/union/spatial_join/statistics)
    - parameters: 分析参数字典
    - layer_id: 输入图层ID
    - priority: 优先级1-10，默认5
    
    返回: {task_id, status, queue_position}
    """
    valid_types = {"buffer", "clip", "intersect", "dissolve", "union", "spatial_join", "statistics"}
    if task_type.lower() not in valid_types:
        return ApiResponse.fail(code="INVALID_TYPE", message=f"无效的分析类型: {task_type}")
    
    # 异步提交 Celery 任务
    task_result = run_analysis_task.apply_async(
        kwargs={
            "task_type": task_type,
            "parameters": parameters,
            "layer_id": layer_id,
        },
        queue="spatial_analysis",
        priority=priority,
    )
    
    # 记录到数据库
    from app.services.layer_service import TaskService
    svc = TaskService(db)
    db_task = svc.create_task(
        task_type=task_type,
        parameters=parameters,
        layer_id=layer_id,
        celery_task_id=task_result.id,
        priority=priority,
    )
    
    logger.info(f"任务已提交: {task_result.id}, 类型: {task_type}")
    return ApiResponse.ok(data={
        "task_id": task_result.id,
        "status": "queued",
        "created_at": datetime.utcnow().isoformat(),
    })
# ============ 任务状态查询 ============
@router.get("/{task_id}", response_model=ApiResponse)
def get_task(task_id: str, db: DbSession = Depends(get_db)):
    """获取任务详情和状态"""
    # 先查 Celery 状态
    status_info = task_queue.get_status(task_id)
    # 再查数据库记录
    from app.services.layer_service import TaskService
    svc = TaskService(db)
    db_task = svc.get_task(task_id)
    
    response_data = {
        "task_id": task_id,
        "status": status_info.get("status", "unknown"),
        "progress": status_info.get("progress", 0),
        "message": status_info.get("message", ""),
    }
    
    if db_task:
        response_data.update({
            "task_type": db_task.task_type,
            "layer_id": db_task.layer_id,
            "created_at": db_task.created_at.isoformat() if db_task.created_at else None,
            "started_at": db_task.started_at.isoformat() if db_task.started_at else None,
            "completed_at": db_task.completed_at.isoformat() if db_task.completed_at else None,
            "retry_count": db_task.retry_count,
            "error_message": db_task.error_trace,
        })
    
    if status_info.get("ready"):
        response_data["result"] = status_info.get("result")
        
    return ApiResponse.ok(data=response_data)
# ============ 任务进度(SSE推送) ============
@router.get("/{task_id}/stream")
async def stream_progress(task_id: str):
    """Server-Sent Events 实时推送任务进度"""
    async def generate():
        from app.services.task_queue import task_queue
        prev_progress = -1
        while True:
            status_info = task_queue.get_status(task_id)
            curr_progress = status_info.get("progress", 0)
            
            # 有新进度时推送
            if curr_progress != prev_progress:
                event = json.dumps({
                    "progress": curr_progress,
                    "status": status_info.get("status"),
                    "message": status_info.get("message", ""),
                })
                yield f"data: {event}\n\n"
                prev_progress = curr_progress
            
            # 任务完成或失败
            if status_info.get("ready"):
                yield f"data: {json.dumps({'done': True, 'success': status_info.get('successful', False)})}\n\n"
                break
            
            await asyncio.sleep(1)  # 每秒检查
    
    return StreamingResponse(generate(), media_type="text/event-stream")
# ============ 任务取消 ============
@router.post("/{task_id}/cancel", response_model=ApiResponse)
def cancel_task(task_id: str, db: DbSession = Depends(get_db)):
    """取消排队中的任务"""
    # 撤销 Celery 任务
    revoked = task_queue.revoke(task_id, terminate=True)
    if not revoked:
        return ApiResponse.fail(message="取消失败")
    
    # 更新数据库
    from app.services.layer_service import TaskService
    svc = TaskService(db)
    svc.update_status_only(task_id, "cancelled")
    
    return ApiResponse.ok(message="任务已取消")
# ============ 任务重试 ============
@router.post("/{task_id}/retry", response_model=ApiResponse)
def retry_task(task_id: str, db: DbSession = Depends(get_db)):
    """重试失败的任务(自动重试最多3次)"""
    from app.services.layer_service import TaskService
    svc = TaskService(db)
    db_task = svc.get_task(task_id)
    
    if not db_task:
        return ApiResponse.fail(code="NOT_FOUND", message="任务不存在")
    
    if db_task.status != "failed":
        return ApiResponse.fail(code="INVALID_STATE", message="仅失败任务可重试")
    
    if db_task.retry_count >= db_task.max_retries:
        return ApiResponse.fail(code="MAX_RETRY", message="已达最大重试次数")
    
    # 递增重试计数并重新提交
    svc.increment_retry(task_id)
    
    # 重新提交 Celery 任务
    task_result = run_analysis_task.apply_async(kwargs={
        "task_type": db_task.task_type,
        "parameters": db_task.parameters,
        "layer_id": db_task.layer_id,
        "original_task_id": task_id,
    })
    
    svc.update_celery_id(task_id, task_result.id)
    svc.update_status_only(task_id, "queued")
    
    return ApiResponse.ok(data={
        "new_task_id": task_result.id,
        "retry_count": db_task.retry_count + 1,
    })
# ============ 任务列表 ============
@router.get("", response_model=ApiResponse)
def list_tasks(
    limit: int = 50,
    offset: int = 0,
    status: str = None,
    db: DbSession = Depends(get_db)
):
    """获取任务列表，支持状态筛选"""
    from app.services.layer_service import TaskService
    svc = TaskService(db)
    tasks, total = svc.list_paginated(limit, offset, status)
    return ApiResponse.ok(data={
        "total": total,
        "items": [{
            "task_id": t.celery_task_id,
            "task_type": t.task_type,
            "status": t.status,
            "progress": t.progress,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        } for t in tasks]
    })
__all__ = ["router"]