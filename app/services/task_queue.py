"""任务队列服务 - Celery 初始化"""
import logging
from celery import Celery
from app.core.config import settings

logger = logging.getLogger(__name__)

# 初始化 Celery
# 当不使用 Redis 时，使用 memory 代理以避免连接错误
broker_url = settings.CELERY_BROKER_URL if settings.USE_REDIS else "memory://"
result_backend = settings.CELERY_RESULT_BACKEND if settings.USE_REDIS else None

celery_app = Celery(
    "webgis_tasks",
    broker=broker_url,
    backend=result_backend,
    include=["app.services.spatial_tasks"]
)


# 常规配置
celery_app.conf.update(
    task_always_eager=not settings.USE_REDIS,  # 如果不使用 Redis，则同步执行任务
    task_eager_propagates=True,                # Eager 模式下抛出异常
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # 1小时超时
)

# 自动发现任务
celery_app.autodiscover_tasks(["app.services"])

class TaskQueueService:
    @staticmethod
    def submit_task(task_name: str, *args, **kwargs) -> str:
        """提交任务到队列"""
        try:
            result = celery_app.send_task(task_name, args=args, kwargs=kwargs)
            return result.id
        except Exception as e:
            logger.error(f"Failed to submit task {task_name}: {e}")
            raise

    @staticmethod
    def get_task_status(task_id: str) -> dict:
        """查询任务状态"""
        result = celery_app.AsyncResult(task_id)
        return {
            "task_id": task_id,
            "status": result.status,
            "result": result.result if result.ready() else None,
            "progress": result.info.get("progress", 0) if isinstance(result.info, dict) else 0
        }
