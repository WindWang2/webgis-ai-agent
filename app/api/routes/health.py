"""
健康检查路由
"""

from fastapi import APIRouter
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.orm import Session as DbSession

router = APIRouter()


def get_db_for_check():
    """获取数据库连接（仅用于健康检查）"""
    from app.core.database import Engine as engine, SessionLocal
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@router.get("/health")
def health_check():
    """
    健康检查接口
    
    返回服务状态信息
    """
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "WebGIS AI Agent",
        "version": "0.1.0"
    }


@router.get("/ready")
def readiness_check(db: DbSession = None):
    """
    就绪检查接口
    
    检查服务是否准备好接收请求，包括数据库连接
    """
    db_ready = False
    try:
        db.execute(text("SELECT 1"))
        db_ready = True
    except Exception:
        pass
    
    return {
        "ready": db_ready,
        "database": "connected" if db_ready else "disconnected",
        "timestamp": datetime.utcnow().isoformat()
    }