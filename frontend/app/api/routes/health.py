"""
健康检查路由
"""

from fastapi import APIRouter
from datetime import datetime

router = APIRouter()


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
def readiness_check():
    """
    就绪检查接口
    
    检查服务是否准备好接收请求
    """
    return {
        "ready": True,
        "timestamp": datetime.utcnow().isoformat()
    }
