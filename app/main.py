"""FastAPI 应用入口"""
import logging
import os
from contextlib import asynccontextmanager
from dotenv import load_dotenv

# 核心：确保 .env 被注入到 os.environ，供 MCP Adapter 的 os.path.expandvars 消费
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import time
from collections import defaultdict

from app.core.config import settings
from app.core.database import init_db, Engine
from app.core.exception import global_exception_handler
from app.api.routes import health, map, chat, layer, report, task, upload, knowledge, ws, config

logger = logging.getLogger(__name__)

mcp_adapter = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化数据库和 MCP 连接"""
    global mcp_adapter
    init_db()

    # 加载 MCP server 配置（项目根目录下的 mcp_servers.json）
    mcp_config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "mcp_servers.json")
    from app.services.mcp_adapter import MCPAdapter
    from app.api.routes.chat import registry as tool_registry
    mcp_config = MCPAdapter.load_config(mcp_config_path)
    if mcp_config.get("mcpServers"):
        logger.info(f"[MCP] loading config from {mcp_config_path}")
        mcp_adapter = await MCPAdapter.from_config(mcp_config, tool_registry)
    else:
        logger.info("[MCP] no mcp_servers.json found or empty, skipping MCP setup")

    yield

    if mcp_adapter:
        await mcp_adapter.close()
    Engine.dispose()



app = FastAPI(
    title=settings.PROJECT_NAME,
    description="WebGIS AI Agent - 智能地图分析与处理服务",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_exception_handler(Exception, global_exception_handler)


# Rate limiting middleware (in-memory, per-IP)
class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_requests: int = 60, window_seconds: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith(("/docs", "/redoc", "/openapi.json")):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        timestamps = self._requests[client_ip]
        self._requests[client_ip] = [t for t in timestamps if now - t < self.window]

        if len(self._requests[client_ip]) >= self.max_requests:
            return JSONResponse(status_code=429, content={"detail": "请求过于频繁，请稍后再试"})

        self._requests[client_ip].append(now)
        return await call_next(request)


app.add_middleware(RateLimitMiddleware, max_requests=60, window_seconds=60)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api/v1", tags=["健康检查"])
app.include_router(layer.router, prefix="/api/v1", tags=["图层管理"])
app.include_router(report.router, prefix="/api/v1", tags=["报告生成"])
app.include_router(chat.router, prefix="/api/v1", tags=["AI对话"])
app.include_router(map.router, prefix="/api/v1", tags=["地图管理"])
app.include_router(task.router, prefix="/api/v1", tags=["任务管理"])
app.include_router(upload.router, prefix="/api/v1", tags=["数据上传"])
app.include_router(knowledge.router, prefix="/api/v1", tags=["知识库管理"])
app.include_router(ws.router, prefix="/api/v1", tags=["WebSocket"])
app.include_router(config.router, prefix="/api/v1", tags=["系统配置"])

# 静态文件服务 - 用于访问导出的地图和分析后的 GeoTIFF
if not os.path.exists(settings.DATA_DIR):
    os.makedirs(settings.DATA_DIR, exist_ok=True)
app.mount("/api/v1/static", StaticFiles(directory=settings.DATA_DIR), name="static")
