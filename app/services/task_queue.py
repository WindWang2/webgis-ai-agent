"""任务队列服务 - Celery 初始化"""
import logging
from collections import OrderedDict
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
    # #386：broker/backend 的 socket 超时压到 1-2s —— 前端每 3s 轮询任务状态，
    # Redis 不可达/慢响应时若长时间挂起，轮询请求会堆满事件循环线程池并卡住
    # 所有并发 SSE 流。2s 超时让 /tasks/status 在 backend 不可用时快速降级
    # 为 UNKNOWN（get_task_status 已捕获所有异常），而不是阻塞 120s。
    broker_connection_timeout=2,
    broker_transport_options={
        "socket_connect_timeout": 2,
        "socket_timeout": 2,
    },
    redis_socket_timeout=2,
    redis_socket_connect_timeout=2,
    result_backend_transport_options={
        "socket_connect_timeout": 2,
        "socket_timeout": 2,
    },
    # ADR-0052: worker 崩溃语义。
    #   acks_late=True             → 执行完成后才 ack
    #   reject_on_worker_lost=False→ 不主动 requeue：重投会重复执行不可逆的 GIS
    #                                操作。durable job 的入口守卫拒绝重复投递
    #                                （已终态直接跳过），stale 清扫负责把「worker
    #                                没了但状态仍是 running」的 job 收敛为 stale，
    #                                用户因此不会永远看到 running。
    task_acks_late=True,
    task_reject_on_worker_lost=False,
    # 软超时留出清理窗口：任务体能在 SoftTimeLimitExceeded 时删掉临时产物，
    # 而不是被硬杀后留下半个 GeoTIFF。
    task_soft_time_limit=3300,
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
    # P2 (F29): 该 dict 原本只增不减 —— 每次 Celery 提交都永久增长一个条目
    # （explorer orchestrator 按任务注册），进程生命周期内无界。改为 LRU 有界：
    # 超过 _OWNERS_MAX_ENTRIES 时逐出最旧条目。所有权校验在任务存续期内有效
    # （过期条目回退为「不属于该用户」→ 端点 404，与「从未注册」语义一致，
    # 因为 durable job 行才是任务中心的权威事实源）。
    _task_owners: "OrderedDict[str, str]" = OrderedDict()
    _OWNERS_MAX_ENTRIES = 20_000

    @classmethod
    def register_owner(cls, task_id: str, user_id: str) -> None:
        """记录 celery task_id 所属用户。仅在已知归属时调用。"""
        if user_id:
            cls._task_owners[task_id] = user_id
            cls._task_owners.move_to_end(task_id)
            while len(cls._task_owners) > cls._OWNERS_MAX_ENTRIES:
                cls._task_owners.popitem(last=False)

    @classmethod
    def verify_owner(cls, task_id: str, user_id: str) -> bool:
        """验证用户是否拥有该 task_id。不存在或不属于该用户均返回 False。"""
        owner = cls._task_owners.get(task_id)
        if owner is None:
            return False
        # 命中即刷新 recency，热任务不被 LRU 逐出。
        cls._task_owners.move_to_end(task_id)
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
        """查询任务状态（审计 S34：不返回 traceback 给客户端）。

        ADR-0052：结果后端不可用时优雅降级。没有 Redis 时 backend 是
        DisabledBackend，读 AsyncResult 会抛 AttributeError —— 之前这会让
        `/tasks/status/{id}` 直接 500。现在返回 UNKNOWN，让调用方（以及新任务中心）
        改用 durable job 行作为事实源。
        """
        try:
            result = celery_app.AsyncResult(task_id)
            info = result.info
            return {
                "task_id": task_id,
                "status": result.status,
                "result": result.result if result.ready() else None,
                "progress": info.get("progress", 0) if isinstance(info, dict) else 0,
            }
        except Exception as e:  # noqa: BLE001 —— 后端不可用不应让端点 500
            logger.warning(f"Celery result backend unavailable for {task_id}: {e}")
            return {"task_id": task_id, "status": "UNKNOWN", "result": None, "progress": 0}

    @staticmethod
    def revoke_task(task_id: str) -> bool:
        """撤销任务"""
        try:
            celery_app.control.revoke(task_id, terminate=True)
            return True
        except Exception as e:
            logger.error(f"Failed to revoke task {task_id}: {e}")
            return False
