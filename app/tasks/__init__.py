"""
Celery 任务模块
"""

from app.services.task_queue import celery_app

__all__ = ["celery_app"]
