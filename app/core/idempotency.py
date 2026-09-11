"""HTTP 幂等中间件（V9 契约基石，ADR-0138 / P5）。

语义：携带 ``Idempotency-Key`` 头的 POST 请求，在 key + method + path +
body hash 维度上 24h 内重放返回**首次响应**（状态码 + content-type + 体）；
并发同 key 单飞（Redis SET NX 锁，借鉴 app/lib/tool_cache.py）。

纪律：
- **无头零开销**：不带 ``Idempotency-Key`` 的请求直接放行，不读体、不碰 Redis。
- **fail-open**：Redis 不可用/超时 → 直接放行处理（幂等是尽力而为语义，
  与 RateLimiter 的 C-F10 同源）。绝不因幂等层故障拒绝请求。
- **只缓冲 JSON 响应**：SSE / 文件流不缓冲不重放（显式排除清单见 ADR-0138）。
- 锁 TTL 60s 覆盖单请求最坏处理时长；过期即退化为重复处理（有界重复，
  无分布式死锁 —— 与 tool_cache 同决策）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import settings

logger = logging.getLogger(__name__)

#: 重放窗口：首次响应的可重放时长（任务书：TTL 24h）。
RESPONSE_TTL_S = 24 * 3600
#: 单飞锁 TTL：过期即退化为重复处理。
LOCK_TTL_S = 60
#: 并发同 key 的等待上限与轮询间隔。
WAIT_TIMEOUT_S = 30.0
WAIT_INTERVAL_S = 0.25
#: 单请求缓冲上限（与上传面一致）；超限不缓冲不重放。
MAX_BUFFER_BYTES = 50 * 1024 * 1024

_INFLIGHT_PREFIX = "idem:inflight:"
_RESP_PREFIX = "idem:resp:"


def compute_idempotency_key(header_value: str, method: str, path: str, body: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(header_value.encode("utf-8", errors="replace"))
    digest.update(b"\x00")
    digest.update(method.encode())
    digest.update(b"\x00")
    digest.update(path.encode())
    digest.update(b"\x00")
    digest.update(hashlib.sha256(body or b"").digest())
    return digest.hexdigest()[:32]


class _LazyRedis:
    """与 RateLimiter 同款：客户端绑定首次使用的 event loop（TEST-13）。"""

    def __init__(self) -> None:
        self._redis = None
        self._bound_loop = None

    async def client(self):
        loop = asyncio.get_running_loop()
        if self._bound_loop is loop and self._redis is not None:
            return self._redis
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:  # noqa: BLE001
                pass
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=2,
            socket_timeout=1,
            decode_responses=True,
        )
        self._bound_loop = loop
        return self._redis


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """只对带 Idempotency-Key 的 JSON POST 生效；其余零干预。"""

    def __init__(self, app, redis: Optional[_LazyRedis] = None):
        super().__init__(app)
        self._redis = redis or _LazyRedis()

    async def dispatch(self, request: Request, call_next):
        header_key = request.headers.get("Idempotency-Key", "").strip()
        if not header_key or request.method != "POST":
            return await call_next(request)
        # 只处理 JSON 请求体（mutating API 面）；其他内容类型放行
        if "application/json" not in request.headers.get("content-type", ""):
            return await call_next(request)

        body = await request.body()
        if len(body) > MAX_BUFFER_BYTES:
            return await call_next(request)
        idem_key = compute_idempotency_key(header_key, request.method, request.url.path, body)

        try:
            redis = await self._redis.client()
            stored = await redis.get(f"{_RESP_PREFIX}{idem_key}")
            if stored is not None:
                return self._replay(stored)
            return await self._singleflight(redis, idem_key, request, call_next)
        except Exception as exc:  # noqa: BLE001 — fail-open（C-F10 同源）
            logger.warning(
                "[Idempotency] Redis unavailable (%s: %s); failing open",
                type(exc).__name__, exc,
            )
            return await call_next(request)

    # ── 单飞 ─────────────────────────────────────────────────────────
    async def _singleflight(self, redis, idem_key: str, request: Request, call_next):
        token = uuid.uuid4().hex
        lock_key = f"{_INFLIGHT_PREFIX}{idem_key}"
        acquired = False
        try:
            acquired = await redis.set(lock_key, token, nx=True, ex=LOCK_TTL_S)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Idempotency] lock probe failed (%s); fail-open", exc)
            redis = None
        if not acquired and redis is not None:
            replay = await self._wait_for_response(redis, idem_key)
            if replay is not None:
                return self._replay(replay)
            # 首个处理者异常退出（锁消失、响应缺席）→ 本请求兜底处理
        response = await call_next(request)
        if redis is not None:
            return await self._store_response(redis, idem_key, lock_key, token, response)
        return response

    async def _wait_for_response(self, redis, idem_key: str) -> Optional[str]:
        deadline = time.monotonic() + WAIT_TIMEOUT_S
        while time.monotonic() < deadline:
            stored = await redis.get(f"{_RESP_PREFIX}{idem_key}")
            if stored is not None:
                return stored
            inflight = await redis.get(f"{_INFLIGHT_PREFIX}{idem_key}")
            if inflight is None:
                return None
            await asyncio.sleep(WAIT_INTERVAL_S)
        return None

    # ── 响应缓冲与重放 ───────────────────────────────────────────────
    async def _store_response(self, redis, idem_key: str, lock_key: str, token: str, response):
        """缓冲 JSON 响应入 Redis；返回给客户端的是重组后的响应
        （body_iterator 已被消费，原响应不可复用）。非 JSON 不缓冲原样返回。"""
        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return response
        buffered: Optional[bytes] = None
        try:
            body = b""
            async for chunk in response.body_iterator:
                body += chunk
                if len(body) > MAX_BUFFER_BYTES:
                    break
            else:
                buffered = body
                record = json.dumps({
                    "status": response.status_code,
                    "content_type": content_type,
                    "body": body.decode("utf-8", errors="replace"),
                })
                await redis.set(f"{_RESP_PREFIX}{idem_key}", record, ex=RESPONSE_TTL_S)
        except Exception as exc:  # noqa: BLE001 — 缓冲失败不改变响应语义
            logger.warning("[Idempotency] store failed (%s)", exc)
        finally:
            try:
                # 只释放自己持有的锁（token 校验语义）
                current = await redis.get(lock_key)
                if current == token:
                    await redis.delete(lock_key)
            except Exception:  # noqa: BLE001
                pass
        if buffered is None:
            # 超限或缓冲异常：迭代器可能已部分消费，以保守空体重建不撒谎的响应
            return Response(
                status_code=502,
                content=b'{"code":"SERVER_ERROR","success":false,'
                        b'"message":"idempotency buffering failed","data":null}',
                media_type="application/json",
            )
        return Response(
            content=buffered,
            status_code=response.status_code,
            media_type=content_type,
        )

    def _replay(self, record_json: str) -> Response:
        try:
            record = json.loads(record_json)
        except (TypeError, ValueError):
            return Response(status_code=503, content=b"idempotency record corrupt")
        return Response(
            content=record.get("body", ""),
            status_code=int(record.get("status", 200)),
            media_type=record.get("content_type", "application/json"),
            headers={"Idempotent-Replay": "true"},
        )
