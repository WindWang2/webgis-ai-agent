"""FastAPI 应用入口"""
import logging
import os
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.config import settings
from app.core.database import Engine
from app.core.exception import global_exception_handler
from app.core.rate_limiter import get_rate_limiter
from app.api.routes import health, map, chat, layer, report, task, upload, knowledge, ws, config, explorer, auth as auth_routes, static as static_routes
from app.tools.registry import ToolRegistry
from app.tools import init_tools
from app.services.chat_engine import ChatEngine
from app.services.tool_catalog import ToolCatalog

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化工具注册中心 + DB schema 守卫迁移。"""
    # 守卫式 SQLite 迁移（_apply_runtime_migrations 内部已做 SQLite 检测）；
    # 没这一行新增/重命名字段就只能靠手动 ALTER，跑久了必出 "no such column"。
    try:
        from app.core.database import init_db
        init_db()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"[lifespan] init_db skipped: {e}")

    registry = ToolRegistry()
    init_tools(registry)
    chat.registry = registry
    # 分层工具目录：按用户消息 + 会话粘性筛选 schema，cut token & 提升选择准确率
    catalog = ToolCatalog(registry)
    chat.engine = ChatEngine(registry, tool_catalog=catalog)

    yield

    # 输出工具调用 digest（top 累计 / top p99 / 错误），便于运维定位最慢工具
    try:
        from app.services.tool_metrics import emit_digest
        emit_digest()
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning(f"[lifespan] emit_digest failed: {e}")

    from app.core.network import close_shared_client
    await close_shared_client()
    Engine.dispose()



app = FastAPI(
    title=settings.PROJECT_NAME,
    description="WebGIS AI Agent - 智能地图分析与处理服务",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_production() else None,
    redoc_url="/redoc" if not settings.is_production() else None,
)

app.add_exception_handler(Exception, global_exception_handler)


# Rate limiting middleware (Redis with in-memory fallback)
class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_requests: int = 60, window_seconds: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window = window_seconds

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith(("/docs", "/redoc", "/openapi.json")):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        limiter = await get_rate_limiter()
        allowed = await limiter.is_allowed(
            f"rate_limit:{client_ip}",
            self.max_requests,
            self.window,
        )
        if not allowed:
            return JSONResponse(status_code=429, content={"detail": "请求过于频繁，请稍后再试"})

        return await call_next(request)


app.add_middleware(RateLimitMiddleware, max_requests=60, window_seconds=60)

# CORS
# THREAT MODEL: CORS_ORIGINS=["*"] + allow_credentials=True causes the middleware
# to echo the request Origin header back as Access-Control-Allow-Origin. Any site
# can therefore initiate credentialed requests against this API. This is
# accepted because:
#   1. The API is deployed behind a trusted gateway / not publicly exposed, OR
#   2. Auth-protected endpoints rely on non-cookie credentials (Authorization
#      header bearer tokens) which browsers do NOT auto-attach cross-origin.
# If either assumption changes (cookie auth introduced, public deployment),
# tighten CORS_ORIGINS to an explicit allow-list before shipping.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_routes.router, prefix="/api/v1", tags=["认证"])
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
app.include_router(explorer.router, prefix="/api/v1", tags=["探索引擎"])

# 静态文件服务 — 用 FastAPI 路由替代原 StaticFiles mount（A4 修复）：
# 路径强校验 + 可选 HMAC 签名 + 访问日志 + JWT 鉴权或公共白名单。
if not os.path.exists(settings.DATA_DIR):
    os.makedirs(settings.DATA_DIR, exist_ok=True)
app.include_router(static_routes.router, prefix="/api/v1", tags=["静态文件"])
