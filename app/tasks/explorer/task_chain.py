"""Explorer Celery task chain"""
import logging
import asyncio
import zlib
import json
from app.services.task_queue import celery_app
from app.services.explorer.models import SearchContext, RawContent
from app.adapters.gov.gov_data_adapter import GovDataAdapter
from app.adapters.base import DataSource

logger = logging.getLogger(__name__)

# 为 Celery prefork worker 提供持久事件循环，避免每次 asyncio.run() 创建新 loop
_celery_loop: asyncio.AbstractEventLoop | None = None

def _get_celery_loop() -> asyncio.AbstractEventLoop:
    """获取或创建当前 worker 进程的持久事件循环。"""
    global _celery_loop
    if _celery_loop is None or _celery_loop.is_closed():
        _celery_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_celery_loop)
    return _celery_loop


def _run_async(coro):
    """在 Celery worker 的持久事件循环中执行 async coroutine。"""
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if running_loop and running_loop.is_running():
        return asyncio.run_coroutine_threadsafe(coro, running_loop).result()

    loop = _get_celery_loop()
    if loop.is_running():
        return asyncio.run_coroutine_threadsafe(coro, loop).result()
    return loop.run_until_complete(coro)


def _store_ref(data: dict, task_id: str, prefix: str = "explorer") -> str:
    """存储数据到 session manager，返回 ref_id。"""
    from app.services.session_data import session_data_manager
    session_namespace = f"explorer:{task_id}"
    ref_id = _run_async(session_data_manager.store(session_namespace, data, prefix=prefix))
    return ref_id


def _load_ref(ref_id: str, task_id: str):
    """从 session manager 加载数据（按 task_id 命名空间）。"""
    from app.services.session_data import session_data_manager
    session_namespace = f"explorer:{task_id}"
    return _run_async(session_data_manager.get(session_namespace, ref_id))


@celery_app.task(bind=True, max_retries=2, soft_time_limit=30, time_limit=30)
def explorer_discover_task(self, task_id: str, query: str, context: dict):
    """数据发现阶段 — thin Celery adapter."""
    from app.services.explorer.discover_stage import run_discover_stage
    logger.info(f"[Explorer:{task_id}] Starting discover stage")

    def _on_progress(progress: int) -> None:
        self.update_state(state="PROGRESS", meta={"stage": "discover", "progress": progress})

    try:
        res = _run_async(run_discover_stage(
            task_id, query, context, on_progress=_on_progress
        ))
        return res.data
    except Exception as e:
        logger.error(f"[Explorer:{task_id}] Discover failed: {e}")
        raise self.retry(exc=e, countdown=2 ** self.request.retries)


@celery_app.task(bind=True, max_retries=1, soft_time_limit=55, time_limit=60)
def explorer_fetch_task(self, prev_result: dict):
    """内容抓取阶段 — thin Celery adapter."""
    from app.services.explorer.fetch_stage import run_fetch_stage
    task_id = prev_result["task_id"]
    logger.info(f"[Explorer:{task_id}] Starting fetch stage")

    def _on_progress(progress: int) -> None:
        self.update_state(state="PROGRESS", meta={"stage": "fetch", "progress": progress})

    res = _run_async(run_fetch_stage(
        task_id,
        prev_result.get("selected_sources", []),
        store_ref=lambda data, prefix: _store_ref(data, task_id=task_id, prefix=prefix),
        on_progress=_on_progress,
    ))
    if not res.success:
        error_msg = res.message if hasattr(res, "message") else str(res)
        raise RuntimeError(error_msg)
    return res.data


@celery_app.task(bind=True, soft_time_limit=55, time_limit=60)
def explorer_parse_task(self, prev_result: dict):
    """结构化解析阶段 — thin Celery adapter."""
    from app.services.explorer.parse_stage import run_parse_stage
    task_id = prev_result["task_id"]
    logger.info(f"[Explorer:{task_id}] Starting parse stage")

    def _on_progress(progress: int) -> None:
        self.update_state(state="PROGRESS", meta={"stage": "parse", "progress": progress})

    res = _run_async(run_parse_stage(
        task_id,
        prev_result.get("fetch_results", []),
        load_ref=lambda ref_id: _load_ref(ref_id, task_id=task_id),
        store_ref=lambda data, prefix: _store_ref(data, task_id=task_id, prefix=prefix),
        on_progress=_on_progress,
    ))
    return res.data


@celery_app.task(bind=True, max_retries=2, soft_time_limit=290, time_limit=300)
def explorer_geocode_task(self, prev_result: dict):
    """地理编码阶段 — thin Celery adapter over the pure :func:`geocode_stage`."""
    from app.tools.chinese_maps import batch_geocode_cn
    from app.services.explorer.geocode_stage import geocode_stage

    task_id = prev_result["task_id"]
    logger.info(f"[Explorer:{task_id}] Starting geocode stage")

    def _on_progress(progress: int) -> None:
        self.update_state(state="PROGRESS", meta={"stage": "geocode", "progress": progress})

    result = _run_async(geocode_stage(
        prev_result["parsed_results"],
        load_ref=lambda ref_id: _load_ref(ref_id, task_id=task_id),
        batch_geocode=batch_geocode_cn,
        on_progress=_on_progress,
    ))

    if result.rows or result.summary.total:
        geocoded_ref_id = _store_ref(
            {"rows": result.rows, "summary": result.summary.as_dict()},
            task_id=task_id,
            prefix="geocoded",
        )
    else:
        geocoded_ref_id = None

    return {
        "task_id": task_id,
        "geocoded_ref_id": geocoded_ref_id,
        "total_rows": result.summary.total,
        "success_rate": result.summary.success_rate,
    }


@celery_app.task(bind=True, soft_time_limit=25, time_limit=30)
def explorer_validate_task(self, prev_result: dict):
    """质量验证阶段 — thin Celery adapter."""
    from app.services.explorer.validate_stage import run_validate_stage
    task_id = prev_result["task_id"]
    logger.info(f"[Explorer:{task_id}] Starting validate stage")

    def _on_progress(progress: int) -> None:
        self.update_state(state="PROGRESS", meta={"stage": "validate", "progress": progress})

    res = _run_async(run_validate_stage(
        task_id,
        geocoded_ref_id=prev_result.get("geocoded_ref_id"),
        total_rows=prev_result.get("total_rows", 0),
        on_progress=_on_progress,
    ))
    return res.data



