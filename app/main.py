"""
WebGIS AI Agent - 主应用入口
T001 后端基础架构搭建 - FastAPI框架、数据库连接、Celery配置、基础中间件
"""
import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
# 配置必须在导入前设置（保持向后兼容）
os.environ.setdefault("REDIS_URL", "redis://redis:6379/0")

# 数据库配置：通过 config 模块的环境变量读取
# 若设置了独立的数据库组件环境变量，会自动组合，若设置了 DATABASE_URL 直接使用

from app.core.config import Settings, settings
from app.core.exception import global_exception_handler
from app.db.session import engine, SessionLocal, init_db
from app.api.routes import health, layer, tasks, auth
# 增加 webhook 导入
try:
    from app.api.routes import webhook
except ImportError:
    webhook = None

# 注册路由时排除空的 analysis 路由（功能已在 tasks 中实现）
from app.services.celery_config import celery_app

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


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
    allow_origins=settings.get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# GZIP压缩
app.add_middleware(GZipMiddleware, minimum_size=1000)

# ============ 路由注册 ============
app.include_router(health.router, prefix="/api/v1", tags=["健康检查"])
app.include_router(layer.router, prefix="/api/v1", tags=["图层管理"])
app.include_router(tasks.router, prefix="/api/v1", tags=["任务管理"])
app.include_router(auth.router, prefix="/api/v1", tags=["认证"])
app.include_router(webhook.router, prefix="/api/v1", tags=["Webhook"])

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
# 使用统一的异常处理器，根据环境自动处理
app.add_exception_handler(Exception, global_exception_handler)

# ============ 开发环境测试路由 ============
@app.get("/test-error", tags=["测试"])
async def trigger_error(error_type: str = "generic"):
    """测试异常处理的端点"""
    if error_type == "generic":
        raise ValueError("这是测试用的通用错误消息 - Generic Error")
    elif error_type == "runtime":
        raise RuntimeError("这是测试用的运行时错误 - Runtime Error")
    elif error_type == "not_found":
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="这是测试用的404错误")
    else:
        raise Exception(f"未知错误类型: {error_type}")

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
