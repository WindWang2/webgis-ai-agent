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
    include=["app.services.spatial_tasks", "app.tasks.explorer.task_chain"]
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
    DEFAULT_RETRY_POLICY = {
        'max_retries': 3,
        'interval_start': 5,
        'interval_step': 10,
        'interval_max': 60,
    }

    # 审计 S34：celery task_id → user_id 映射，用于状态/撤销端点的所有权校验
    _task_owners: dict[str, str] = {}

    @classmethod
    def register_owner(cls, task_id: str, user_id: str) -> None:
        """记录 celery task_id 所属用户。仅在已知归属时调用。"""
        if user_id:
            cls._task_owners[task_id] = user_id

    @classmethod
    def verify_owner(cls, task_id: str, user_id: str) -> bool:
        """验证用户是否拥有该 task_id。不存在或不属于该用户均返回 False。"""
        owner = cls._task_owners.get(task_id)
        if owner is None:
            return False
        return owner == user_id

    @staticmethod
    def submit_task(
        task_name: str,
        *args,
        retry: bool = False,
        retry_policy: dict = None,
        callback: str = None,
        user_id: str = None,
        **kwargs
    ) -> str:
        """提交任务到队列"""
        try:
            send_kwargs = {}
            if retry:
                policy = retry_policy if retry_policy is not None else TaskQueueService.DEFAULT_RETRY_POLICY
                send_kwargs['retry'] = True
                send_kwargs['retry_policy'] = policy
            if callback:
                send_kwargs['link'] = celery_app.signature(callback)
            result = celery_app.send_task(task_name, args=args, kwargs=kwargs, **send_kwargs)
            if user_id:
                TaskQueueService.register_owner(result.id, user_id)
            return result.id
        except Exception as e:
            logger.error(f"Failed to submit task {task_name}: {e}")
            raise

    @staticmethod
    def get_task_status(task_id: str) -> dict:
        """查询任务状态（审计 S34：不返回 traceback 给客户端）"""
        result = celery_app.AsyncResult(task_id)
        info = result.info
        return {
            "task_id": task_id,
            "status": result.status,
            "result": result.result if result.ready() else None,
            "progress": info.get("progress", 0) if isinstance(info, dict) else 0,
        }

    @staticmethod
    def revoke_task(task_id: str) -> bool:
        """撤销任务"""
        try:
            celery_app.control.revoke(task_id, terminate=True)
            return True
        except Exception as e:
            logger.error(f"Failed to revoke task {task_id}: {e}")
            return False
