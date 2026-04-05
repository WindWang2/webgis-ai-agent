"""
T003 空间分析任务队列 - Celery任务编排、进度上报、状态查询、失败重试
支持异步任务提交、SSE推送、失败自动重试3次
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from fastapi.responses import StreamingResponse
from typing import Optional
import asyncio
import json
from datetime import datetime
from sqlalchemy.orm import Session as DbSession
from app.core.database import get_db
from app.models.api_response import ApiResponse
from app.services.task_queue import TaskQueueService
from app.tasks.analysis import run_analysis as run_spatial_analysis
router = APIRouter(prefix="/tasks", tags=["空间分析"])
# ============ 任务提交 ============
@router.post("/submit", response_model=ApiResponse)
def submit_task(
    task_type: str,
    layer_id: int,
    parameters: dict,
    db: DbSession = Depends(get_db),
):
    """
    提交空间分析任务
    
    task_type: buffer, clip, intersect, dissolve, union, spatial_join, statistics
    parameters: 详见各算子参数定义
    """
    allowed_types = {
        "buffer": {"distance": "float", "unit": "str", "dissolve": "bool"},
        "clip": {"boundary_layer_id": "int"},
        "intersect": {"intersect_layer_id": "int"},
        "dissolve": {"dissolve_field": "str (optional)"},
        "union": {"union_layer_id": "int"},
        "spatial_join": {"right_layer_id": "int", "join_type": "str", "predicate": "str"},
        "statistics": {"statistics_type": "str"}
    }
    
    if task_type.lower() not in allowed_types:
        return ApiResponse.fail(code="INVALID_TYPE", message=f"无效任务类型: {task_type}")
    
    # 验证参数
    required_params = allowed_types[task_type.lower()]
    for param, expected in required_params.items():
        if param.endswith("_id"):
            continue
        if param not in parameters:
            return ApiResponse.fail(code="MISSING_PARAM", message=f"缺少参数: {param}")
    
    # 存入数据库
    from app.services.layer_service import TaskService
    task_svc = TaskService(db)
    task = task_svc.create_task(
        task_type=task_type,
        layer_id=layer_id,
        parameters=parameters
    )
    
    # 触发 Celery 任务
    celery_task = run_spatial_analysis.apply_async(
        kwargs={
            "task_id": str(task.id),
            "task_type": task_type,
            "layer_id": layer_id,
            "parameters": parameters
        },
        task_id=str(task.celery_task_id)
    )
    
    task_svc.update_celery_id(str(task.id), celery_task.id)
    
    return ApiResponse.ok(data={
        "task_id": task.id,
        "celery_task_id": celery_task.id,
        "status": "queued"
    })
# ============ 任务查询 ============
@router.get("/{task_id}", response_model=ApiResponse)
def get_task_detail(task_id: int, db: DbSession = Depends(get_db)):
    """获取任务详情和状态"""
    from app.services.layer_service import TaskService
    svc = TaskService(db)
    task = svc.get_task(task_id)
    
    if not task:
        return ApiResponse.fail(code="NOT_FOUND", message="任务不存在")
    
    return ApiResponse.ok(data={
        "id": task.id,
        "task_type": task.task_type,
        "layer_id": task.layer_id,
        "status": task.status,
        "progress": task.progress,
        "progress_message": task.progress_message,
        "result_summary": task.result_summary,
        "error_trace": task.error_trace,
        "retry_count": task.retry_count,
        "created_at": task.created_at.isoformat(),
        "completed_at": task.completed_at.isoformat() if task.completed_at else None
    })
# ============ 进度查询(SSE) ============
@router.get("/{task_id}/progress")
async def stream_progress(task_id: int):
    """Server-Sent Events 实时进度推送"""
    async def event_generator():
        task_queue = TaskQueueService()
        
        prev_progress = -1
        while True:
            # 获取最新进度
            progress_info = task_queue.get_redis_progress(task_id)
            
            if progress_info["progress"] != prev_progress:
                prev_progress = progress_info["progress"]
                yield f"data: {json.dumps(progress_info)}\n\n"
            
            # 任务完成或失败则退出
            if progress_info["status"] in ["completed", "failed", "cancelled"]:
                break
            
            await asyncio.sleep(1)  # 1秒轮询
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")
# ============ 任务操作 ============
@router.post("/{task_id}/cancel", response_model=ApiResponse)
def cancel_task(task_id: int, db: DbSession = Depends(get_db)):
    """取消任务"""
    from app.services.layer_service import TaskService
    svc = TaskService(db)
    task = svc.get_task(task_id)
    
    if not task:
        return ApiResponse.fail(code="NOT_FOUND", message="任务不存在")
    
    if task.status in ["completed", "failed", "cancelled"]:
        return ApiResponse.fail(code="INVALID_STATE", message="任务已结束，无法取消")
    
    # 撤销 Celery 任务
    task_queue = TaskQueueService()
    task_queue.revoke(task.celery_task_id, terminate=True)
    
    svc.update_status(task_id, "cancelled")
    return ApiResponse.ok(message="任务已取消")
@router.post("/{task_id}/retry", response_model=ApiResponse)
def retry_task(task_id: int, db: DbSession = Depends(get_db)):
    """重试失败任务(自动重试3次)"""
    from app.services.layer_service import TaskService
    svc = TaskService(db)
    task = svc.get_task(task_id)
    
    if not task:
        return ApiResponse.fail(code="NOT_FOUND", message="任务不存在")
    
    if task.status != "failed":
        return ApiResponse.fail(code="INVALID_STATE", message="只有失败任务可重试")
    
    if task.retry_count >= task.max_retries:
        return ApiResponse.fail(code="MAX_RETRY", message="已达到最大重试次数")
    
    # 更新状态并重新触发
    svc.increment_retry(task_id)
    svc.update_status(task_id, "pending")
    
    celery_task = run_spatial_analysis.delay(kwargs={
        "task_id": str(task_id),
        "task_type": task.task_type,
        "layer_id": task.layer_id,
        "parameters": task.parameters
    })
    
    svc.update_celery_id(str(task_id), celery_task.id)
    
    return ApiResponse.ok(message=f"重试 {task.retry_count + 1}/{task.max_retries}")
# ============ 任务列表 ============
@router.get("", response_model=ApiResponse)
def list_tasks(
    limit: int = 50,
    offset: int = 0,
    status_filter: Optional[str] = None,
    db: DbSession = Depends(get_db),
):
    """获取任务列表，支持状态过滤"""
    from app.services.layer_service import TaskService
    svc = TaskService(db)
    tasks, total = svc.list_paginated(limit, offset, status_filter)
    
    return ApiResponse.ok(data={
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {"id": t.id, "task_type": t.task_type, "status": t.status, "progress": t.progress}
            for t in tasks
        ]
    })
__all__ = ["router"]