"""FastAPI 应用入口"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import init_db
from app.api.routes import health, map, chat, layer, report
>>>>>>> d91cac14354e0119e9e594df800d7ff6b23a5730


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化数据库"""
    init_db()
    yield



app = FastAPI(
    title=settings.PROJECT_NAME,
    description="WebGIS AI Agent - 智能地图分析与处理服务",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api/v1", tags=["健康检查"])
app.include_router(chat.router, prefix="/api/v1", tags=["对话"])
app.include_router(map.router, prefix="/api/v1", tags=["地图"])
app.include_router(layer.router, prefix="/api/v1", tags=["图层管理"])
app.include_router(tasks.router, prefix="/api/v1", tags=["任务管理"])
app.include_router(auth.router, prefix="/api/v1", tags=["认证"])
app.include_router(report.router, prefix="/api/v1", tags=["报告生成"])
app.include_router(webhook.router, prefix="/api/v1", tags=["Webhook"])
