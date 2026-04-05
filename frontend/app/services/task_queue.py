"""
Celery 任务队列配置
"""

from celery.schedules import crontab
from typing import Dict, Any, Optional
import logging

from app.services.celery_config import celery_app

logger = logging.getLogger(__name__)

# 补充定时任务配置
celery_app.conf.beat_schedule = {
    "cleanup-expired-tasks": {
        "task": "app.tasks.cleanup.cleanup_expired_tasks",
        "schedule": crontab(hour=2, minute=0),  # 每天凌晨 2 点
    },
}


class TaskQueueService:
    """任务队列服务"""
    
    def __init__(self):
        self.app = celery_app
    
    def submit_task(
        self,
        task_type: str,
        parameters: Dict[str, Any],
        layer_id: Optional[int] = None,
        task_id: Optional[str] = None,
        countdown: Optional[int] = None
    ) -> str:
        """
        提交分析任务
        
        Args:
            task_type: 任务类型
            parameters: 任务参数
            layer_id: 关联图层 ID
            task_id: 自定义任务 ID
            countdown: 延迟执行秒数
        
        Returns:
            任务 ID
        """
        from app.tasks.analysis import run_analysis
        
        if task_id:
            task = run_analysis.apply_async(
                args=[task_id, task_type, parameters, layer_id],
                countdown=countdown
            )
        else:
            task = run_analysis.apply_async(
                args=[task_type, parameters, layer_id],
                countdown=countdown
            )
        
        logger.info(f"任务已提交：{task.id}, 类型：{task_type}")
        return task.id
    
    def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """获取任务状态"""
        from celery.result import AsyncResult
        task = AsyncResult(task_id, app=self.app)
        
        return {
            "task_id": task_id,
            "status": task.status,
            "result": task.result if task.ready() else None,
            "traceback": task.traceback,
            "children": [str(child) for child in task.children]
        }
    
    def cancel_task(self, task_id: str) -> bool:
        """取消任务"""
        from celery.result import AsyncResult
        task = AsyncResult(task_id, app=self.app)
        task.revoke(terminate=True)
        return True
    
    def retry_task(
        self,
        task_id: str,
        task_type: str,
        parameters: Dict[str, Any],
        layer_id: Optional[int] = None,
        max_retries: int = 3
    ) -> str:
        """
        重试任务
        
        支持断点续传和超时重试
        """
        from app.tasks.analysis import run_analysis
        
        # 获取原任务信息
        from celery.result import AsyncResult
        original_task = AsyncResult(task_id, app=self.app)
        
        if original_task.retry_count >= max_retries:
            raise Exception(f"已超过最大重试次数：{max_retries}")
        
        # 重新提交任务
        new_task = run_analysis.retry(
            args=[task_type, parameters, layer_id],
            throw=False
        )
        
        return new_task.id
    
    def get_task_progress(self, task_id: str) -> Dict[str, Any]:
        """
        获取任务进度
        
        支持实时进度推送
        """
        from celery.result import AsyncResult
        task = AsyncResult(task_id, app=self.app)
        
        return {
            "task_id": task_id,
            "status": task.status,
            "progress": task.info.get("progress", 0) if task.info else 0,
            "result": task.result if task.ready() else None
        }


# 全局任务队列服务实例
task_queue = TaskQueueService()
