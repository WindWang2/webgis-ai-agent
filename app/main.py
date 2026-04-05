"""FastAPI 应用入口"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import init_db
from app.api.routes import health, map, chat, layer, report


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

# 路由注册
app.include_router(health.router, prefix=f"{settings.API_V1_STR}/v1", tags=["健康检查"])
app.include_router(map.router, prefix=f"{settings.API_V1_STR}/v1", tags=["地图服务"])
app.include_router(chat.router, prefix=f"{settings.API_V1_STR}/v1", tags=["对话"])
app.include_router(layer.router, prefix=f"{settings.API_V1_STR}/v1", tags=["图层"])
app.include_router(report.router, prefix=f"{settings.API_V1_STR}/v1", tags=["报告"])


@app.get("/", tags=["根路径"])
def root():
    return {
        "name": settings.PROJECT_NAME,
        "version": "0.1.0",
        "docs": "/docs",
    }
