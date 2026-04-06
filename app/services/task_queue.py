"""任务队列服务 (stub)"""
from typing import Optional


class TaskQueueService:
    @staticmethod
    def submit_task(task_type: str, params: dict) -> str:
        return f"task-{task_type}"
    
    @staticmethod
    def get_task_status(task_id: str) -> dict:
        return {"status": "pending", "progress": 0}


celery_app = None  # stub
