"""健康检查路由"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import text
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

_LLM_CACHE_TTL = 30.0
_llm_last_check = 0.0
_llm_last_result = False


def _check_db():
    """检查数据库连接"""
    from app.core.database import Engine
    try:
        with Engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.warning("Health check failed: database unreachable: %s", e)
        return False


def _check_llm():
    """检查 LLM API 连通性（3 秒超时，30 秒缓存）"""
    global _llm_last_check, _llm_last_result
    now = time.monotonic()
    if now - _llm_last_check < _LLM_CACHE_TTL:
        return _llm_last_result
    try:
        import httpx
        base_url = settings.LLM_BASE_URL.rstrip("/")
        resp = httpx.head(f"{base_url}/models", timeout=3.0)
        _llm_last_result = resp.status_code < 500
    except Exception as e:
        _llm_last_result = False
        logger.warning("Health check failed: LLM unreachable: %s", e)
    _llm_last_check = now
    return _llm_last_result


def _check_redis():
    """检查 Redis 连通性"""
    try:
        import redis
        r = redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        return r.ping()
    except Exception as e:
        logger.warning("Health check failed: Redis unreachable: %s", e)
        return False


def _check_celery():
    """检查 Celery Worker 是否在线"""
    try:
        from celery import Celery
        app = Celery(broker=settings.CELERY_BROKER_URL)
        inspect = app.control.inspect(timeout=2.0)
        active = inspect.active()
        return active is not None
    except Exception as e:
        logger.warning("Health check failed: Celery unreachable: %s", e)
        return False


def _live_agent_runtime() -> str:
    """Pi if the bundled subprocess is up; ChatEngine otherwise.

    Fail-closed: an unreadable bridge state must not claim "pi" off the flag
    alone — that is the hardcoded-badge lie #1032 kills. If the probe itself
    errors, report ChatEngine (the safe fallback that serves when the bridge
    cannot be verified)."""
    try:
        from app.api.routes.chat import _use_pi_bridge
        if _use_pi_bridge():
            return "pi"
        return "chatengine"
    except Exception:
        logger.warning("agent_runtime probe failed; reporting chatengine", exc_info=True)
        return "chatengine"


@router.get("/health")
def health_check():
    """基础存活检查"""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "WebGIS AI Agent",
        "version": "0.1.3",
        "agent_runtime": _live_agent_runtime(),
        # V5-B: any-worker-alive is a service average — with a pool >1 some
        # workers can be down (sessions on them degrade to ChatEngine) while
        # the badge still says "pi". Disclose the per-worker split so the
        # badge can be reconciled against reality.
        "pi_workers_alive": _pi_workers_alive(),
    }


def _pi_workers_alive() -> Optional[str]:
    """"alive/total" for the bridge pool (None when no pool/single worker)."""
    try:
        from app.agent_pi_bridge import get_bridge_pool
        from app.api.routes.chat import _bridge_alive

        pool = get_bridge_pool()
        if pool is None or pool.size <= 1:
            return None
        alive = sum(1 for b in pool.bridges if _bridge_alive(b))
        return f"{alive}/{pool.size}"
    except Exception:
        return None


@router.get("/health/live")
def liveness_check():
    """轻量存活检查 — 仅确认进程可响应，不做依赖检查。

    专供 k8s livenessProbe / Docker HEALTHCHECK 使用：失败应直接杀进程，
    所以这里不能因 DB/Redis/Celery 抖动而失败。
    """
    return {"status": "alive"}


@router.get("/ready")
def readiness_check():
    """就绪检查：数据库 + LLM + Redis + Celery 连通性。

    任一依赖不可达时返回 HTTP 503，让 k8s readinessProbe 暂停把流量打过来；
    全部就绪时返回 HTTP 200。

    SEC-11：响应体只返回极简状态（不附带 DB/Redis/Celery 的具体连通细节），
    因为 /ready 是无鉴权端点——之前的 body 会把内部依赖拓扑（哪个挂了、
    哪个连着）泄露给任意调用方，便于攻击者做侦察。详细连通信息只在服务端
    日志里保留，运维仍可定位故障点。
    """
    db_ready = _check_db()
    llm_ready = _check_llm()
    redis_ready = _check_redis()
    celery_ready = _check_celery()

    all_ready = db_ready and llm_ready and redis_ready and celery_ready

    # 详细连通信息写日志，不进响应体
    logger.info(
        "readiness: ready=%s db=%s llm=%s redis=%s celery=%s",
        all_ready, db_ready, llm_ready, redis_ready, celery_ready,
    )

    # k8s readinessProbe 只看 HTTP 状态码；body 仅返回极简状态，避免信息泄露。
    return JSONResponse(
        status_code=200 if all_ready else 503,
        content={"ready": all_ready},
    )
