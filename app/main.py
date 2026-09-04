"""FastAPI 应用入口

env 加载归启动器所有（#663-A）：根 main.py / manage.py 显式 load_dotenv，
裸 uvicorn 用 --env-file。本模块不得有 import 期环境副作用 —— 契约由
tests/unit/test_env_hygiene.py 锁定。
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.config import settings
from app.core.database import Engine
from app.core.exception import global_exception_handler
from app.core.rate_limiter import get_rate_limiter
from app.api.routes import health, map, chat, layer, report, task, upload, knowledge, ws, config, explorer, auth as auth_routes, static as static_routes, pi_tools, templates, raster as raster_routes, metrics, project as project_routes, data_fabric, jobs as jobs_routes, local_data, mapspec_mutations, analysis_graph as analysis_graph_routes
from app.tools.registry import ToolRegistry
from app.tools import init_tools
from app.services.chat_engine import ChatEngine
from app.services.tool_catalog import ToolCatalog

# F15-wiring：teardown 前排空 chat 侧 fire-and-forget 背景任务（标题生成 / ws 广播），
# 避免它们在资源关闭后继续写。drain_background_tasks 由 chat 执行引擎模块提供；
# 未提供时降级为 None，不阻塞启动。
try:
    from app.services.chat.execution_engine import drain_background_tasks
except ImportError:  # pragma: no cover - 旧版 execution_engine 没有该函数
    drain_background_tasks = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化工具注册中心 + DB schema 守卫迁移。"""
    # 测试阶段免登录：开着的每一秒都要在日志里可见，防止误带到生产。
    from app.core.auth import auth_bypass_enabled
    if auth_bypass_enabled():
        logger.warning(
            "[auth-bypass] AUTH_DISABLED=true — 所有受保护端点免登录放行"
            "（身份 test-admin / admin 角色）。仅限测试环境；生产请立即关闭。"
        )
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
    # v2(Phase 3, audit R1)：启动即编译 Compiled GIS Runtime Manifest 并做
    # cross-registry 校验 —— 此前 validate_gis_library 只有测试调用，悬空
    # 引用（孤儿工具/错绑 capability/dangling alias）在运行期静默降级。
    # fatal 议题 fail-fast（GIS_MANIFEST_STRICT=0 可逃生），warning/planned
    # 记日志。
    from app.lib.gis.runtime_manifest import (
        compile_runtime_manifest,
        validate_runtime_manifest_strict,
    )
    _manifest = compile_runtime_manifest(registry)
    import app.lib.gis.runtime_manifest as _rm
    _rm._cached_manifest = _manifest
    validate_runtime_manifest_strict(_manifest)
    logger.info(
        "[lifespan] compiled GIS runtime manifest fp=%s %s",
        _manifest.fingerprint[:12], _manifest.summary()["counts"],
    )
    # E-2（#893）：单例注入下沉 services 层（路由模块全局保留赋值兼容旧引用）
    from app.services.chat.engine_instance import set_app_registry
    set_app_registry(registry)
    chat.registry = registry
    # Inject the registry into the Pi bridge so tool dispatch
    # (called by the Pi extension via /pi-tools/execute) uses real GIS tools.
    from app.agent_pi_bridge import set_tool_registry as _set_pi_tool_registry
    _set_pi_tool_registry(registry)
    # 分层工具目录：按用户消息 + 会话粘性筛选 schema，cut token & 提升选择准确率
    catalog = ToolCatalog(registry)
    chat_engine = ChatEngine(registry, tool_catalog=catalog)
    # E-2（#893）：同 registry —— engine 单例注入 services 层持有器
    from app.services.chat.engine_instance import set_chat_engine
    set_chat_engine(chat_engine)
    chat.engine = chat_engine

    # 仓内 vendor/pi：API 启动即拉起 bundled RPC 子进程（不是用户全局 pi CLI）。
    from app.agent_pi_bridge import USE_NEW_AGENT, get_pi_bridge
    if USE_NEW_AGENT:
        try:
            from app.agent_pi_bridge import get_pi_bridge
            # 审计 AGENT-03：指向 .mjs 文件（Pi extension loader 需要可执行 JS，
            # 不是 .ts）。.mjs 是 ESM 格式，Node 原生支持无需编译。
            extension_path = str(Path(__file__).parent.parent / "app" / "extensions" / "webgis-tools" / "index.mjs")
            chat.pi_bridge = await get_pi_bridge(extension_paths=[extension_path])
            logger.info("[lifespan] bundled vendor/pi RPC agent started")
        except Exception as e:
            logger.error(
                f"[lifespan] bundled vendor/pi failed to start: {e}; "
                "falling back to ChatEngine"
            )
            chat.pi_bridge = None
    else:
        logger.info("[lifespan] USE_NEW_AGENT=false — ChatEngine path (bundled Pi not started)")
        chat.pi_bridge = None

    # 审计 S46：cleanup_idle_sessions 之前是死代码（定义在 session_data_manager
    # 但没人调）-> idle session 的 ref/event/state 永久堆积，Redis 内存缓慢增长。
    # 起一个后台任务每 10 分钟清理一次。被遗弃的匿名 session（无后续 chat 请求）
    # 通过 session_data 的 TTL 兜底，但 active 列表 + in-memory 单例需要主动扫。
    cleanup_task = asyncio.create_task(_periodic_session_cleanup())

    # ADR-0052：stale job 清扫。worker 崩溃/被杀时 DB 里的 job 会永远停在
    # running —— 用户面对一个永不结束的任务。这里周期性把心跳超时的 job
    # 收敛为 stale（终态但可 retry）。
    stale_sweep_task = asyncio.create_task(_periodic_stale_job_sweep())

    yield

    # 关闭后台清理任务
    for bg_task in (cleanup_task, stale_sweep_task):
        bg_task.cancel()
        try:
            await bg_task
        except asyncio.CancelledError:
            pass

    # F15-wiring：teardown 前排空 chat fire-and-forget 背景任务（标题生成、
    # ws 广播等），避免它们在 engine/http client 关闭后继续写已失效资源。
    if drain_background_tasks is not None:
        try:
            await drain_background_tasks()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[lifespan] drain_background_tasks failed: {e}")

    # 输出工具调用 digest（top 累计 / top p99 / 错误），便于运维定位最慢工具
    try:
        from app.services.tool_metrics import emit_digest
        emit_digest()
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning(f"[lifespan] emit_digest failed: {e}")

    from app.core.network import close_shared_client
    await close_shared_client()

    # Close the pooled LLM provider HTTP clients (httpx). They are reused across
    # LLM calls for keep-alive connection pooling and must be closed on shutdown
    # so sockets are released (consistent with close_shared_client above).
    # Idempotent; failure is best-effort and never blocks shutdown.
    try:
        from app.services.chat.llm_client import close_llm_http_clients

        await close_llm_http_clients()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[lifespan] LLM HTTP client close failed: {e}")

    # 审计 BUG-03/AGENT-04：Pi bridge subprocess 之前从未在 shutdown 时关闭
    # → 每次重启泄漏一个 node 进程 + reader task。现在显式关闭。
    if USE_NEW_AGENT:
        try:
            from app.agent_pi_bridge import shutdown_pi_bridge
            await shutdown_pi_bridge()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[lifespan] Pi bridge shutdown failed: {e}")

    # F11：之前只 dispose 同步 Engine，async 池仍绑定在已关闭的 event loop 上，
    # 跨 lifespan 周期复用时第一次 async DB 调用直接 'Event loop is closed'。
    # best-effort，与周围邻居一致；inline import 与上方 init_db 同款，便于测试 patch。
    from app.core.database import AsyncEngine
    if AsyncEngine is not None:
        try:
            await AsyncEngine.dispose()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[lifespan] async engine dispose failed: {e}")

    Engine.dispose()


async def _periodic_session_cleanup(interval_seconds: int = 600) -> None:
    """审计 S46：定期清理 idle session 数据，防内存/Redis 缓慢增长。

    session_data_manager.cleanup_idle_sessions 已存在但从未被调用。
    此任务每 interval_seconds 秒跑一次；失败仅 warning 不抛（不能让后台任务
    崩了影响主服务）。

    #470：cleanup_idle_sessions 之外还跑 sweep_expired_session_files ——
    Redis 的 4h TTL 是服务端静默过期（键消失无回调，clear_session 不会触发），
    没有这层兜底，TTL 过期会话的磁盘目录（mapspec revisions / checkpoints /
    raster PNGs）永远无人回收。清扫带 liveness 检查：store 里仍有状态的
    会话（TTL 被聊天活动续期）跳过。
    """
    import asyncio
    import logging
    from app.services.session_data import session_data_manager

    logger = logging.getLogger(__name__)
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            await session_data_manager.cleanup_idle_sessions()
            try:
                from app.services.mapspec.store import sweep_expired_session_files
                await sweep_expired_session_files(
                    liveness=getattr(session_data_manager, "is_session_active", None)
                )
            except Exception as sweep_error:  # noqa: BLE001
                logger.warning(f"[lifespan] session disk sweep failed: {sweep_error}")
            # audit #837: exports (.owner sidecars) / reports / orphaned
            # uploads get the same age-based reclamation as the mapspec
            # family — previously write-only directories.
            try:
                from app.services.artifact_lifecycle import sweep_aged_artifacts
                await sweep_aged_artifacts()
            except Exception as sweep_error:  # noqa: BLE001
                logger.warning(f"[lifespan] artifact sweep failed: {sweep_error}")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[lifespan] session cleanup tick failed: {e}")


async def _periodic_stale_job_sweep(interval_seconds: int = 60) -> None:
    """ADR-0052：把心跳超时的 durable job 标记为 stale（规范 §25）。

    最小可行方案：worker 在写进度时顺带刷新 heartbeat_at，本任务扫出
    ``running/cancelling 且心跳早于 cutoff`` 的行并原子迁移到 stale。没有引入
    分布式调度平台、lease 表或额外中间件 —— 只用已有的 DB 列。

    多副本 API 同时扫是安全的：迁移是条件更新（WHERE status IN (...)），只有一个
    副本能成功改到 stale，其余 rowcount=0。
    """
    import asyncio
    import logging

    from app.core.database import AsyncSessionLocal
    from app.services.jobs import DurableJobStore

    logger = logging.getLogger(__name__)
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            if AsyncSessionLocal is None:
                continue
            async with AsyncSessionLocal() as db:
                swept = await DurableJobStore.sweep_stale(db)
                # pending/queued 不会心跳，心跳预测器抓不到它们；提交路径在 create 与
                # apply_async 之间崩溃、或 broker 丢消息时，job 会永远停在那里。
                orphaned = await DurableJobStore.sweep_orphans(db)
                if swept or orphaned:
                    await db.commit()
                    logger.warning(
                        f"[lifespan] swept {swept} stale job(s), {orphaned} orphaned job(s)"
                    )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[lifespan] stale job sweep tick failed: {e}")



app = FastAPI(
    title=settings.PROJECT_NAME,
    description="WebGIS AI Agent - 智能地图分析与处理服务",
    version="0.1.3",
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


class RequestCorrelationMiddleware(BaseHTTPMiddleware):
    """#691：读 X-Request-ID 进 RuntimeContext，回显响应头。无则生成。

    进出均走同一 contextvar（RuntimeContext.request_id），与后续
    bind_runtime_context 合并；响应头始终回显（前端 transport.ts 也注入，
    现在服务端正式消费并关联日志）。
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        try:
            from app.lib.runtime.context import bind_runtime_context, new_request_id
        except Exception:  # noqa: BLE001
            return await call_next(request)
        incoming = request.headers.get("x-request-id")
        req_id = incoming.strip() if isinstance(incoming, str) and incoming.strip() else new_request_id()
        # 合并进 RuntimeContext，并保留 session_id（若已有，如 WebSocket）
        with bind_runtime_context(request_id=req_id):
            response = await call_next(request)
            # 回显（CORS 已 expose，跨域前端可读）
            try:
                response.headers["X-Request-ID"] = req_id
            except Exception:  # noqa: BLE001
                pass
            return response


# Rate limiting middleware (Redis with in-memory fallback)
class RateLimitMiddleware(BaseHTTPMiddleware):
    # 会话恢复一次并发拉取 8-15 个图层 geojson（restoreSessionMapLayers），
    # 60/min 的全局预算会被瞬间打爆，随后图层重试与制图观测全部 429 一整
    # 分钟，地图进入半残状态。两层缓解：
    #   1. 预算提到 240/min（本地/内网部署的合理值）；
    #   2. 豁免只读图层/瓦片数据 GET（有 session/owner_token 鉴权，且为
    #      恢复路径的重负载主体）。
    RATE_LIMIT_EXEMPT_PREFIXES = (
        "/api/v1/layers/data/",
        "/api/v1/health",
        "/api/v1/local-data/",
    )

    def __init__(self, app, max_requests: int = 240, window_seconds: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window = window_seconds

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith(("/docs", "/redoc", "/openapi.json")):
            return await call_next(request)

        path = request.url.path
        if path.startswith(self.RATE_LIMIT_EXEMPT_PREFIXES):
            return await call_next(request)

        # SEC-F4: behind nginx, request.client.host is the proxy IP — one
        # shared bucket for the whole platform. Use the forwarded real IP.
        from app.core.client_ip import client_ip_from

        client_ip = client_ip_from(request)
        limiter = await get_rate_limiter()
        allowed = await limiter.is_allowed(
            f"rate_limit:{client_ip}",
            self.max_requests,
            self.window,
        )
        if not allowed:
            return JSONResponse(status_code=429, content={"detail": "请求过于频繁，请稍后再试"})

        return await call_next(request)


app.add_middleware(RateLimitMiddleware, max_requests=240, window_seconds=60)

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
    # X-Session-Token: the anonymous session owner_token credential
    # (SEC-08). apiFetch sends it today, and MapLibre tile requests
    # (transformRequest, #514) attach it too — the header must be
    # preflight-legal for cross-origin dev setups.
    # X-Request-ID: transport.ts injects this on every request. Missing it from
    # allow_headers makes the browser OPTIONS preflight 400 ("Disallowed CORS
    # headers") and the chat UI surfaces TypeError "Failed to fetch".
    # Last-Event-ID: DUP-1 SSE resume header on chat stream reconnects.
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Accept",
        "X-Session-Token",
        "X-Request-ID",
        "Last-Event-ID",
    ],
    expose_headers=["X-Request-ID"],
)
# #691：X-Request-ID 关联与回显。add_middleware 是反序（最后注册最先执行），
# 故该 middleware 必须在 CORS 之后注册才能成为最外层——即使 CORS 直接处理
# OPTIONS 预检返回，也能回显 X-Request-ID。
app.add_middleware(RequestCorrelationMiddleware)

app.include_router(auth_routes.router, prefix="/api/v1", tags=["认证"])
app.include_router(health.router, prefix="/api/v1", tags=["健康检查"])
app.include_router(layer.router, prefix="/api/v1", tags=["图层管理"])
app.include_router(report.router, prefix="/api/v1", tags=["报告生成"])
app.include_router(chat.router, prefix="/api/v1", tags=["AI对话"])
app.include_router(mapspec_mutations.router, prefix="/api/v1", tags=["AI对话"])
# ADR-0097: 显式分析图 — SessionPlan/MapSpec/证据的只读派生投影端点。
app.include_router(analysis_graph_routes.router, prefix="/api/v1", tags=["Agent Workbench"])
app.include_router(map.router, prefix="/api/v1", tags=["地图管理"])
# ADR-0052: 统一任务中心必须先注册 —— task.router 的 GET /tasks/{task_id}
# 会把字面量 "jobs" 当成 task_id 匹配掉。
app.include_router(jobs_routes.router, prefix="/api/v1", tags=["任务管理"])
app.include_router(task.router, prefix="/api/v1", tags=["任务管理"])
app.include_router(upload.router, prefix="/api/v1", tags=["数据上传"])
app.include_router(knowledge.router, prefix="/api/v1", tags=["知识库管理"])
app.include_router(ws.router, prefix="/api/v1", tags=["WebSocket"])
app.include_router(config.router, prefix="/api/v1", tags=["系统配置"])
app.include_router(explorer.router, prefix="/api/v1", tags=["探索引擎"])
app.include_router(templates.router, prefix="/api/v1", tags=["地图制图模板"])
app.include_router(raster_routes.router, prefix="/api/v1", tags=["栅格图层"])
app.include_router(project_routes.router, prefix="/api/v1", tags=["项目工作区"])
app.include_router(data_fabric.router, prefix="/api/v1", tags=["Data Fabric / 数据织网"])
app.include_router(local_data.router, prefix="/api/v1/local-data", tags=["本地地理数据"])
app.include_router(metrics.router, prefix="/api/v1", tags=["性能遥测"])
app.include_router(pi_tools.router, tags=["PI工具"])

# 静态文件服务 — 用 FastAPI 路由替代原 StaticFiles mount（A4 修复）：
# 路径强校验 + 可选 HMAC 签名 + 访问日志 + JWT 鉴权或公共白名单。
if not os.path.exists(settings.DATA_DIR):
    os.makedirs(settings.DATA_DIR, exist_ok=True)
app.include_router(static_routes.router, prefix="/api/v1", tags=["静态文件"])
