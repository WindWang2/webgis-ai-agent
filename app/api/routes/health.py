"""健康检查路由"""

import logging
from datetime import datetime, timezone
from sqlalchemy import text
from fastapi import APIRouter

from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


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
    """检查 LLM API 连通性（3 秒超时）"""
    try:
        import httpx
        base_url = settings.LLM_BASE_URL.rstrip("/")
        resp = httpx.head(f"{base_url}/models", timeout=3.0)
        return resp.status_code < 500
    except Exception:
        return False


@router.get("/health")
def health_check():
    """基础存活检查"""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "WebGIS AI Agent",
        "version": "0.1.0"
    }


@router.get("/ready")
def readiness_check():
    """就绪检查：数据库 + LLM 连通性"""
    db_ready = _check_db()
    llm_ready = _check_llm()

    return {
        "ready": db_ready and llm_ready,
        "database": "connected" if db_ready else "disconnected",
        "llm": "reachable" if llm_ready else "unreachable",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
