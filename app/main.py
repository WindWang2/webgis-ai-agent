"""FastAPI 应用入口"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
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
from app.api.routes import health, map, chat, layer, report, task, upload, knowledge, ws, config, explorer, auth as auth_routes, static as static_routes, pi_tools, templates, raster as raster_routes, metrics
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
    # Inject the registry into the Pi bridge so tool dispatch
    # (called by the Pi extension via /pi-tools/execute) uses real GIS tools.
    from app.agent_pi_bridge import set_tool_registry as _set_pi_tool_registry
    _set_pi_tool_registry(registry)
    # 分层工具目录：按用户消息 + 会话粘性筛选 schema，cut token & 提升选择准确率
    catalog = ToolCatalog(registry)
    chat_engine = ChatEngine(registry, tool_catalog=catalog)
    chat.engine = chat_engine

    # Feature flag: 初始化 Pi agent (vendor/pi) 通过 RPC 调用
    from app.agent_pi_bridge import USE_NEW_AGENT, get_pi_bridge
    if USE_NEW_AGENT:
        try:
            from app.agent_pi_bridge import get_pi_bridge
            # 审计 AGENT-03：指向 .mjs 文件（Pi extension loader 需要可执行 JS，
            # 不是 .ts）。.mjs 是 ESM 格式，Node 原生支持无需编译。
            extension_path = str(Path(__file__).parent.parent / "app" / "extensions" / "webgis-tools" / "index.mjs")
            chat.pi_bridge = await get_pi_bridge(extension_paths=[extension_path])
            logger.info("[lifespan] Pi agent system enabled (USE_NEW_AGENT=true)")
        except Exception as e:
            logger.warning(f"[lifespan] Failed to initialize Pi bridge: {e}, falling back to ChatEngine")
            chat.pi_bridge = None
    else:
        chat.pi_bridge = None

    # 审计 S46：cleanup_idle_sessions 之前是死代码（定义在 session_data_manager
    # 但没人调）-> idle session 的 ref/event/state 永久堆积，Redis 内存缓慢增长。
    # 起一个后台任务每 10 分钟清理一次。被遗弃的匿名 session（无后续 chat 请求）
    # 通过 session_data 的 TTL 兜底，但 active 列表 + in-memory 单例需要主动扫。
    cleanup_task = asyncio.create_task(_periodic_session_cleanup())

    yield

    # 关闭后台清理任务
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass

    # 输出工具调用 digest（top 累计 / top p99 / 错误），便于运维定位最慢工具
    try:
        from app.services.tool_metrics import emit_digest
        emit_digest()
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning(f"[lifespan] emit_digest failed: {e}")

    from app.core.network import close_shared_client
    await close_shared_client()

    # 审计 BUG-03/AGENT-04：Pi bridge subprocess 之前从未在 shutdown 时关闭
    # → 每次重启泄漏一个 node 进程 + reader task。现在显式关闭。
    if USE_NEW_AGENT:
        try:
            from app.agent_pi_bridge import shutdown_pi_bridge
            await shutdown_pi_bridge()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[lifespan] Pi bridge shutdown failed: {e}")

    Engine.dispose()


async def _periodic_session_cleanup(interval_seconds: int = 600) -> None:
    """审计 S46：定期清理 idle session 数据，防内存/Redis 缓慢增长。

    session_data_manager.cleanup_idle_sessions 已存在但从未被调用。
    此任务每 interval_seconds 秒跑一次；失败仅 warning 不抛（不能让后台任务
    崩了影响主服务）。
    """
    import asyncio
    import logging
    from app.services.session_data import session_data_manager

    logger = logging.getLogger(__name__)
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            await session_data_manager.cleanup_idle_sessions()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[lifespan] session cleanup tick failed: {e}")



app = FastAPI(
    title=settings.PROJECT_NAME,
    description="WebGIS AI Agent - 智能地图分析与处理服务",
    version="0.1.2",
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_production() else None,
    redoc_url="/redoc" if not settings.is_production() else None,
)

app.add_exception_handler(Exception, global_exception_handler)


# Prometheus metrics — 审计 I11：之前 prometheus.yml 抓 /api/v1/metrics 但 app
# 从未暴露任何 metrics 端点 → 监控全是 up==0 / No data。instrumentator 在 /metrics
# 暴露 http_requests_total / http_request_duration_seconds 等，与 alerts-rules.json
# 对齐。
#
# SEC-11: /metrics 暴露内部流量/延迟分布，prometheus-fastapi-instrumentator
# 不原生支持鉴权钩子（expose 只是注册一个裸路由），强行加 BasicAuth 需要自己
# 包一层 Depends，且 Prometheus scraper 端配置凭据较繁琐。
# 因此推荐的网络层隔离方式（必须至少满足其一）：
#   1. NetworkPolicy 限制 /metrics 仅允许监控 namespace（如 prometheus）的 Pod 访问；
#   2. Ingress / 反向代理对 /metrics 做 IP 白名单或 mTLS；
#   3. 部署时让 Prometheus 与本服务同 namespace，直接走 ClusterIP，不经过 Ingress。
# 已设 include_in_schema=False，所以 /metrics 不会出现在公开的 OpenAPI 文档里，
# 但这并不能阻止直接 HTTP 探测，必须配合上面的网络隔离。
try:
    from prometheus_fastapi_instrumentator import Instrumentator
    # 不传 should_group_status_codes 等参数 —— 不同版本 API 不一致，使用默认最稳。
    # 健康检查端点产生的噪声由 Prometheus 端的 metric relabel 过滤即可。
    Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
except ImportError:
    logger.warning("prometheus-fastapi-instrumentator not installed — /metrics endpoint disabled")


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
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
    expose_headers=["X-Request-ID"],
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
app.include_router(templates.router, prefix="/api/v1", tags=["地图制图模板"])
app.include_router(raster_routes.router, prefix="/api/v1", tags=["栅格图层"])
app.include_router(metrics.router, prefix="/api/v1", tags=["性能遥测"])
app.include_router(pi_tools.router, tags=["PI工具"])

# 静态文件服务 — 用 FastAPI 路由替代原 StaticFiles mount（A4 修复）：
# 路径强校验 + 可选 HMAC 签名 + 访问日志 + JWT 鉴权或公共白名单。
if not os.path.exists(settings.DATA_DIR):
    os.makedirs(settings.DATA_DIR, exist_ok=True)
app.include_router(static_routes.router, prefix="/api/v1", tags=["静态文件"])
