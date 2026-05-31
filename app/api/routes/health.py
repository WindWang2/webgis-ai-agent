"""健康检查路由"""

import logging
import time
from datetime import datetime, timezone
from sqlalchemy import text
from fastapi import APIRouter

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
    except Exception:
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
    except Exception:
        _llm_last_result = False
    _llm_last_check = now
    return _llm_last_result


def _check_redis():
    """检查 Redis 连通性"""
    try:
        import redis
        r = redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        return r.ping()
    except Exception:
        return False


def _check_celery():
    """检查 Celery Worker 是否在线"""
    try:
        from celery import Celery
        app = Celery(broker=settings.CELERY_BROKER_URL)
        inspect = app.control.inspect(timeout=2.0)
        active = inspect.active()
        return active is not None
    except Exception:
        return False


@router.get("/health")
def health_check():
    """基础存活检查"""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "WebGIS AI Agent",
        "version": "0.1.2"
    }


@router.get("/ready")
def readiness_check():
    """就绪检查：数据库 + LLM + Redis + Celery 连通性"""
    db_ready = _check_db()
    llm_ready = _check_llm()
    redis_ready = _check_redis()
    celery_ready = _check_celery()

    all_ready = db_ready and llm_ready and redis_ready and celery_ready

    return {
        "ready": all_ready,
        "database": "connected" if db_ready else "disconnected",
        "llm": "reachable" if llm_ready else "unreachable",
        "redis": "connected" if redis_ready else "disconnected",
        "celery": "active" if celery_ready else "inactive",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
