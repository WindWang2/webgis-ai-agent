"""
Celery 应用配置
"""
from celery import Celery
from typing import Optional, Literal


# 全局Celery应用实例
celery_app = Celery("orchestration")

# 配置项
celery_app.conf.update(
    broker_url="redis://localhost:6379/0",
    result_backend="redis://localhost:6379/1",
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # 硬超时1小时
    task_soft_time_limit=2400,  # 软超时40分钟
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,
    # 任务队列配置
    task_default_queue="orchestration_tasks",
    task_default_exchange="orchestration",
    task_default_routing_key="orchestration.task",
)


def get_task_status(task_id: str) -> Optional[str]:
    """
    获取Celery任务状态
    
    Args:
        task_id: Celery任务ID
        
    Returns:
        任务状态字符串 (PENDING/STARTED/SUCCESS/FAILURE/RETRY) 或 None
    """
    try:
        from celery.result import AsyncResult
        result = AsyncResult(task_id, app=celery_app)
        return result.status
    except Exception:
        return None


def get_task_result(task_id: str) -> Optional[dict]:
    """
    获取Celery任务结果
    
    Args:
        task_id: Celery任务ID
        
    Returns:
        任务结果字典或None
    """
    try:
        from celery.result import AsyncResult
        result = AsyncResult(task_id, app=celery_app)
        if result.ready():
            return result.result
        return None
    except Exception:
        return None