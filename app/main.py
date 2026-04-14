"""FastAPI 应用入口"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import init_db
from app.api.routes import health, map, chat, layer, report, task, upload, knowledge, ws

logger = logging.getLogger(__name__)

_mcp_adapter = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化数据库和 MCP 连接"""
    global _mcp_adapter
    init_db()

    # 加载 MCP server 配置（项目根目录下的 mcp_servers.json）
    mcp_config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "mcp_servers.json")
    from app.services.mcp_adapter import MCPAdapter
    from app.api.routes.chat import registry as tool_registry
    mcp_config = MCPAdapter.load_config(mcp_config_path)
    if mcp_config.get("mcpServers"):
        logger.info(f"[MCP] loading config from {mcp_config_path}")
        _mcp_adapter = await MCPAdapter.from_config(mcp_config, tool_registry)
    else:
        logger.info("[MCP] no mcp_servers.json found or empty, skipping MCP setup")

    yield

    if _mcp_adapter:
        await _mcp_adapter.close()



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
app.include_router(layer.router, prefix="/api/v1", tags=["图层管理"])
app.include_router(report.router, prefix="/api/v1", tags=["报告生成"])
app.include_router(chat.router, prefix="/api/v1", tags=["AI对话"])
app.include_router(map.router, prefix="/api/v1", tags=["地图管理"])
app.include_router(task.router, prefix="/api/v1", tags=["任务管理"])
app.include_router(upload.router, prefix="/api/v1", tags=["数据上传"])
app.include_router(knowledge.router, prefix="/api/v1", tags=["知识库管理"])
app.include_router(ws.router, prefix="/api/v1", tags=["WebSocket"])
