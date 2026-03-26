"""
WebGIS AI Agent - 主应用入口
T001 后端基础架构搭建 - FastAPI框架、数据库连接、Celery配置、基础中间件
"""
import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
# 配置必须在导入前设置
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@db:5432/webgis")
os.environ.setdefault("REDIS_URL", "redis://redis:6379/0")

from app.core.config import Settings, get_settings
from app.db.session import engine, SessionLocal, init_db
from app.api.routes import health, layers, analysis, tasks
from app.services.celery_config import celery_app

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)
settings = get_settings()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """生命周期管理：启动和关闭时的初始化/清理"""
    logger.info("🚀 应用启动中...")
    
    # 初始化数据库
    try:
        init_db()
        logger.info("✅ 数据库初始化完成")
    except Exception as e:
        logger.error(f"❌ 数据库初始化失败: {e}")
    
    # Celery 应用配置检查
    try:
        from app.services.task_queue import task_queue
        logger.info(f"✅ Celery 任务队列已配置, Broker: {settings.CELERY_BROKER_URL}")
    except Exception as e:
        logger.warning(f"⚠️ Celery 配置异常: {e}")
    
    logger.info("🎯 应用启动完成")
    yield
    
    logger.info("🛑 应用关闭中...")
    logger.info("👋 应用已关闭")

app = FastAPI(
    title="WebGIS AI Agent",
    description="智能地图分析与处理服务 - 后端API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan
)

# ============ 中间件配置 ============
# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
)
# GZIP压缩
app.add_middleware(GZipMiddleware, minimum_size=1000)

# ============ 路由注册 ============
app.include_router(health.router, prefix="/api/v1", tags=["健康检查"])
app.include_router(layers.router, prefix="/api/v1", tags=["图层管理"])
app.include_router(analysis.router, prefix="/api/v1", tags=["空间分析"])
app.include_router(tasks.router, prefix="/api/v1", tags=["任务管理"])

# ============ 根路径 ============
@app.get("/", tags=["根路径"])
async def root():
    return {
        "name": "WebGIS AI Agent API",
        "version": "1.0.0",
        "docs": "/docs",
        "status": "running"
    }

# ============ 全局异常处理 ============
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"🔥 全局异常: {exc}", exc_info=True)
    return {
        "code": "SERVER_ERROR",
        "success": False,
        "message": f"服务器内部错误: {str(exc)[:100]}",
        "data": None
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=os.environ.get("RELOAD", "false").lower() == "true"
    )

__all__ = ["app"]