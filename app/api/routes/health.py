"""健康检查路由"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import text
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

_LLM_CACHE_TTL = 30.0
_llm_last_check = 0.0
_llm_last_result = False


def _check_db():
    """检查数据库连接"""
    from app.core.database import Engine
    try:
        with Engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.warning("Health check failed: database unreachable: %s", e)
        return False


def _check_llm():
    """检查 LLM API 连通性（3 秒超时，30 秒缓存）"""
    global _llm_last_check, _llm_last_result
    now = time.monotonic()
    if now - _llm_last_check < _LLM_CACHE_TTL:
        return _llm_last_result
    try:
        import httpx
        base_url = settings.LLM_BASE_URL.rstrip("/")
        resp = httpx.head(f"{base_url}/models", timeout=3.0)
        _llm_last_result = resp.status_code < 500
    except Exception as e:
        _llm_last_result = False
        logger.warning("Health check failed: LLM unreachable: %s", e)
    _llm_last_check = now
    return _llm_last_result


def _check_redis():
    """检查 Redis 连通性"""
    try:
        import redis
        r = redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        return r.ping()
    except Exception as e:
        logger.warning("Health check failed: Redis unreachable: %s", e)
        return False


def _check_celery():
    """检查 Celery Worker 是否在线"""
    try:
        from celery import Celery
        app = Celery(broker=settings.CELERY_BROKER_URL)
        inspect = app.control.inspect(timeout=2.0)
        active = inspect.active()
        return active is not None
    except Exception as e:
        logger.warning("Health check failed: Celery unreachable: %s", e)
        return False


def _live_agent_runtime() -> str:
    """Pi if the bundled subprocess is up; ChatEngine otherwise.

    Fail-closed: an unreadable bridge state must not claim "pi" off the flag
    alone — that is the hardcoded-badge lie #1032 kills. If the probe itself
    errors, report ChatEngine (the safe fallback that serves when the bridge
    cannot be verified)."""
    try:
        from app.api.routes.chat import _use_pi_bridge
        if _use_pi_bridge():
            return "pi"
        return "chatengine"
    except Exception:
        logger.warning("agent_runtime probe failed; reporting chatengine", exc_info=True)
        return "chatengine"


@router.get("/health")
def health_check():
    """基础存活检查"""
    # Platform V4（ADR-0131 D7）：version 从 build_info（VERSION 文件）取——
    # 修复硬编码 "0.1.3" 与 VERSION 文件（0.1.0.0）的漂移。
    from app.core.build_info import version_string

    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "WebGIS AI Agent",
        "version": version_string(),
        "agent_runtime": _live_agent_runtime(),
        # V5-B: any-worker-alive is a service average — with a pool >1 some
        # workers can be down (sessions on them degrade to ChatEngine) while
        # the badge still says "pi". Disclose the per-worker split so the
        # badge can be reconciled against reality.
        "pi_workers_alive": _pi_workers_alive(),
    }


def _pi_workers_alive() -> Optional[str]:
    """"alive/total" for the bridge pool (None when no pool/single worker)."""
    try:
        from app.agent_pi_bridge import get_bridge_pool
        from app.api.routes.chat import _bridge_alive

        pool = get_bridge_pool()
        if pool is None or pool.size <= 1:
            return None
        alive = sum(1 for b in pool.bridges if _bridge_alive(b))
        return f"{alive}/{pool.size}"
    except Exception:
        return None


@router.get("/health/live")
def liveness_check():
    """轻量存活检查 — 仅确认进程可响应，不做依赖检查。

    专供 k8s livenessProbe / Docker HEALTHCHECK 使用：失败应直接杀进程，
    所以这里不能因 DB/Redis/Celery 抖动而失败。
    """
    return {"status": "alive"}


@router.get("/ready")
def readiness_check():
    """就绪检查：数据库 + LLM + Redis + Celery 连通性。

    任一依赖不可达时返回 HTTP 503，让 k8s readinessProbe 暂停把流量打过来；
    全部就绪时返回 HTTP 200。

    SEC-11：响应体只返回极简状态（不附带 DB/Redis/Celery 的具体连通细节），
    因为 /ready 是无鉴权端点——之前的 body 会把内部依赖拓扑（哪个挂了、
    哪个连着）泄露给任意调用方，便于攻击者做侦察。详细连通信息只在服务端
    日志里保留，运维仍可定位故障点。
    """
    db_ready = _check_db()
    llm_ready = _check_llm()
    redis_ready = _check_redis()
    celery_ready = _check_celery()

    all_ready = db_ready and llm_ready and redis_ready and celery_ready

    # 详细连通信息写日志，不进响应体
    logger.info(
        "readiness: ready=%s db=%s llm=%s redis=%s celery=%s",
        all_ready, db_ready, llm_ready, redis_ready, celery_ready,
    )

    # k8s readinessProbe 只看 HTTP 状态码；body 仅返回极简状态，避免信息泄露。
    return JSONResponse(
        status_code=200 if all_ready else 503,
        content={"ready": all_ready},
    )


# ── SRE 组件健康分类学（Quality V3 W11，Epic 10）──────────────────────────
#
# 安全边界（Subagent-A B-1 修订）：
# - 路径 /api/v1/status/detailed 不在 RATE_LIMIT_EXEMPT_PREFIXES（那里只豁免
#   /api/v1/health 前缀）→ 继承全局限流，不可被无限抓取；
# - get_current_user 鉴权依赖（JWT bearer）→ 未鉴权 401，拓扑细节不外泄
#   （SEC-11 同款关切；/ready 的教训）；
# - 探测是同步阻塞 IO → asyncio.to_thread 下放线程池，不阻塞事件循环；
# - 缓存 = 不可变快照 + 锁原子换入（m-1：不照抄 _llm 裸全局的混合纪元）。

import asyncio as _asyncio
import threading as _threading
import time as _time

from fastapi import Depends as _Depends
from pydantic import BaseModel as _BaseModel

from app.core.auth import get_current_user as _get_current_user


class SreComponentStatus(_BaseModel):
    status: str                 # ok | degraded | down | not_configured
    latency_ms: Optional[float] = None
    detail: Optional[str] = None


class SreStatusReport(_BaseModel):
    status: str                 # ok | degraded | down
    components: dict
    stuck_jobs: Optional[int] = None
    refresh_age_s: float = 0.0


_SRE_COMPONENTS = ("db", "redis", "llm", "worker", "object_store")

_CACHE_TTL_S = 10.0
_cache_lock = _threading.Lock()
_cache_snapshot: Optional[dict] = None
_cache_written_at: float = 0.0
_refresh_guard = _asyncio.Lock()


#: R1-m2：延迟降级阈值（ms）——超过即 degraded（探测成功但逼近不可用）
_DEGRADED_LATENCY_MS = {"llm": 1500.0, "worker": 1500.0, "db": 500.0,
                        "redis": 500.0}


def _probe_component(name: str) -> tuple:
    """单组件探测（同步阻塞；调用方保证在 to_thread 中执行）。

    返回 (status_str, latency_ms, detail)。词表封闭：ok|degraded|down|
    not_configured。degraded = 可达但延迟超阈值（R1-m2：否则 0.5 是
    死词表、SRE_Component_Degraded 告警永不着火）。
    """
    t0 = _time.monotonic()
    try:
        if name == "db":
            ok = _check_db()
        elif name == "redis":
            ok = _check_redis()
        elif name == "llm":
            # R2-m6：_check_llm 自带 30s TTL —— latency 在缓存窗口内
            # 近似 0，降级判据只在缓存过期那次刷新生效（窗口 ≤30s，
            # 可接受；绕开缓存会放大对 LLM 的探测流量）
            ok = _check_llm()
        elif name == "worker":
            ok = _check_celery()
        elif name == "object_store":
            return _probe_object_store()
        else:  # pragma: no cover — 词表封闭，防御性分支
            return ("down", None, f"unknown component {name}")
    except Exception as exc:  # noqa: BLE001 — 探测故障按 down 诚实上报
        return ("down", None, f"probe error: {type(exc).__name__}")
    latency = round((_time.monotonic() - t0) * 1000, 1)
    if not ok:
        return ("down", latency, None)
    threshold = _DEGRADED_LATENCY_MS.get(name)
    if threshold is not None and latency > threshold:
        return ("degraded", latency,
                f"latency {latency}ms > {threshold}ms threshold")
    return ("ok", latency, None)


def _probe_object_store() -> tuple:
    """对象存储探测：未配置 → not_configured；配置了但不可达 → down。

    配置面 = s3_blob_store.s3_config_from_env()（WEBGIS_S3_* env，与
    生产消费方同一事实源）。
    """
    from app.services.s3_blob_store import s3_config_from_env

    cfg = s3_config_from_env()
    base = cfg.get("endpoint_url") or ""
    if not base or not cfg.get("bucket"):
        return ("not_configured", None, "WEBGIS_S3_ENDPOINT_URL/BUCKET 未配置")
    t0 = _time.monotonic()
    try:
        import httpx

        resp = httpx.head(base.rstrip("/") + "/minio/health/live", timeout=2.0)
        latency = round((_time.monotonic() - t0) * 1000, 1)
        if resp.status_code == 404:
            # R1-m3：非 MinIO 的 S3 兼容端点没有该健康路径 —— 端点可达
            # 但健康探针缺席，报 degraded（不得假阳性 ok）
            return ("degraded", latency,
                    "no /minio/health/live (non-MinIO S3?)")
        if resp.status_code < 300:
            return ("ok", latency, None)
        # R2-m5：3xx（http→https 误配等）与 4xx/5xx 都不是健康
        return ("down", latency, f"health probe {resp.status_code}")
    except Exception as exc:  # noqa: BLE001
        return ("down", round((_time.monotonic() - t0) * 1000, 1),
                f"probe error: {type(exc).__name__}")


def _collect_sre_snapshot() -> dict:
    """全组件探测 → 快照 dict + 指标刷新（线程内执行；M-4 刷新点）。"""
    from app.core import sre_metrics

    components: dict[str, SreComponentStatus] = {}
    for name in _SRE_COMPONENTS:
        status_str, latency, detail = _probe_component(name)
        components[name] = SreComponentStatus(
            status=status_str, latency_ms=latency, detail=detail)
        value = {"ok": sre_metrics.STATUS_OK,
                 "degraded": sre_metrics.STATUS_DEGRADED,
                 "down": sre_metrics.STATUS_DOWN,
                 "not_configured": None}.get(status_str)
        if value is not None:
            sre_metrics.set_component_status(name, value)
    values = [c.status for c in components.values()]
    if "down" in values:
        overall = "down"
    elif "degraded" in values:
        overall = "degraded"
    else:
        overall = "ok"
    # stuck_jobs 由端点的 async 侧补齐（find_stale 是 AsyncSession API）
    return {"components": components, "stuck_jobs": None,
            "overall": overall}


async def _count_stuck_jobs() -> Optional[int]:
    """stuck job 有界计数（find_stale limit=100 上限；探测不到 = None
    —— 诚实缺失，不伪造 0）。"""
    try:
        from app.core.database import AsyncSessionLocal
        from app.services.jobs.store import DurableJobStore

        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as db:
            rows = await DurableJobStore.find_stale(db, limit=100)
            return len(rows)
    except Exception:  # noqa: BLE001 — jobs store 不可用时不阻塞组件面
        logger.warning("stuck job count unavailable", exc_info=True)
        return None


@router.get("/status/detailed")
async def sre_status_detailed(
    _current_user: dict = _Depends(_get_current_user),
) -> JSONResponse:
    """鉴权 SRE 组件健康分类学（Epic 10 W11）。

    安全模型见文件头注释：不在限流豁免前缀内 + JWT 鉴权 + 组件拓扑
    细节只在鉴权面暴露（B-1）。TTL 10s 缓存，快照原子换入；k8s probe
    不得指向本端点（/health/live、/ready 语义不变）。整体 down → 503
    （与 /ready 的探测语义对齐，M-3），degraded 保持 200。
    """
    global _cache_snapshot, _cache_written_at
    now = _time.monotonic()
    if _cache_snapshot is None or now - _cache_written_at >= _CACHE_TTL_S:
        async with _refresh_guard:
            if _cache_snapshot is None or \
                    _time.monotonic() - _cache_written_at >= _CACHE_TTL_S:
                fresh = await _asyncio.to_thread(_collect_sre_snapshot)
                stuck = await _count_stuck_jobs()
                fresh["stuck_jobs"] = stuck
                from app.core import sre_metrics as _sm
                if stuck is not None:
                    # m-1（R1）：探测不可用 = None，保持序列缺席 +
                    # staleness 告警讲真话，不伪造 0
                    _sm.set_stuck_jobs(stuck)
                _sm.set_refresh_timestamp()
                with _cache_lock:
                    _cache_snapshot = fresh
                    _cache_written_at = _time.monotonic()
    with _cache_lock:
        snap = _cache_snapshot
        written = _cache_written_at
    report = SreStatusReport(
        status=snap["overall"],
        components={k: v.model_dump() for k, v in snap["components"].items()},
        stuck_jobs=snap["stuck_jobs"],
        refresh_age_s=round(_time.monotonic() - written, 3),
    )
    status_code = 503 if report.status == "down" else 200
    return JSONResponse(status_code=status_code, content=report.model_dump())
