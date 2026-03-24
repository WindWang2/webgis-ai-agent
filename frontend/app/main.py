"""
WebGIS AI Agent - 后端服务入口
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health, map, chat
from app.core.config import settings

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="WebGIS AI Agent - 智能地图分析与处理服务",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(health.router, prefix="/api/v1", tags=["健康检查"])
app.include_router(map.router, prefix="/api/v1", tags=["地图服务"])
app.include_router(chat.router, prefix="/api/v1/chat", tags=["AI 对话"])


@app.get("/", tags=["根路径"])
def root():
    """根路径"""
    return {
        "name": settings.PROJECT_NAME,
        "version": "0.1.0",
        "docs": "/docs"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
