"""网络请求工具模块"""
import asyncio
import ssl
import logging
import threading
import weakref
import aiohttp
import certifi

logger = logging.getLogger(__name__)

def get_ssl_context(verify: bool = True) -> ssl.SSLContext:
    """
    获取跨平台 SSL 上下文。
    
    Args:
        verify: 是否验证 SSL 证书。生产环境必须为 True；仅允许在显式配置
                ALLOW_INSECURE_SSL=true 时使用 verify=False。
    """
    if not verify:
        import os
        if os.environ.get("ALLOW_INSECURE_SSL", "").lower() not in ("1", "true", "yes"):
            raise ValueError(
                "SSL verification is disabled but ALLOW_INSECURE_SSL is not set. "
                "Refusing to create insecure SSL context. "
                "Set ALLOW_INSECURE_SSL=true explicitly if you really want this."
            )
        logger.warning("SSL verification DISABLED — ALLOW_INSECURE_SSL is set. This is insecure.")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
        
    ctx = ssl.create_default_context()
    try:
        # 使用 certifi 提供的 CA 证书，确保跨平台一致性
        ctx.load_verify_locations(certifi.where())
    except Exception as e:
        logger.warning(f"Failed to load certifi CA certs: {e}. Falling back to default system certs.")
        # 如果 certifi 失败，尝试系统默认路径（aiohttp 默认行为）
        pass
    return ctx

def get_base_headers() -> dict:
    """获取基础请求头，符合 Nominatim 等服务的 Usage Policy"""
    return {
        "User-Agent": "WebGIS-AI-Agent-V2/1.0 (https://github.com/WindWang2/webgis-ai-agent; contact@example.com)",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept": "application/json",
    }

async def create_client_session(**kwargs) -> aiohttp.ClientSession:
    """
    创建一个预配置的 aiohttp.ClientSession。
    会自动处理 Proxy 设置（如果配置了）。
    """
    headers = get_base_headers()
    if "headers" in kwargs:
        headers.update(kwargs.pop("headers"))

    # 注意：aiohttp.ClientSession 不直接在构造函数中接收 proxy，
    # 但我们可以在这层封装中处理通用的握手逻辑或默认设置。
    # 实际请求时推荐使用 session.get(..., proxy=settings.HTTP_PROXY)
    return aiohttp.ClientSession(headers=headers, **kwargs)


# ── 共享连接池（供 chinese_maps.py 的多 provider 共用）───────────────────────

# Per-event-loop registry (fix #933): the shared session and its lock must not
# be reused across worker-thread event loops created via asyncio.run().  A
# module-level asyncio.Lock / aiohttp.ClientSession binds to the loop that
# created it; reusing it on a different loop raises
# "Timeout context manager should be used inside a task" /
# "Future attached to a different loop".  We keep one session+lock per loop.
_sessions: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_locks: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_registry_lock = threading.Lock()

# Legacy aliases kept for backward compatibility (tests may import them).
# They always mirror the *calling* loop's entry after each public call.
_shared_session: aiohttp.ClientSession | None = None
_shared_session_loop: asyncio.AbstractEventLoop | None = None


def _get_loop_lock(loop: asyncio.AbstractEventLoop) -> asyncio.Lock:
    """Return the asyncio.Lock for *loop*, creating it atomically."""
    with _registry_lock:
        lock = _locks.get(loop)
        if lock is None:
            lock = asyncio.Lock()
            _locks[loop] = lock
        return lock


class _PerLoopLockProxy:
    """Proxy so legacy `async with _pool_lock:` is per-loop safe.

    Before #933, `_pool_lock = asyncio.Lock()` was instantiated at import and
    then awaited from worker-thread loops — violating asyncio's loop affinity.
    This proxy delegates every `async with _pool_lock` to the *current*
    running loop's lock, so old import sites remain correct without code
    changes.
    """

    async def __aenter__(self):
        loop = asyncio.get_running_loop()
        lock = _get_loop_lock(loop)
        await lock.acquire()
        self._entered_lock = lock  # type: ignore[attr-defined]
        return lock

    async def __aexit__(self, exc_type, exc, tb):
        lock = getattr(self, "_entered_lock", None)
        if lock is not None:
            lock.release()
            self._entered_lock = None  # type: ignore[attr-defined]
        return False

    async def acquire(self):
        loop = asyncio.get_running_loop()
        lock = _get_loop_lock(loop)
        await lock.acquire()
        self._entered_lock = lock  # type: ignore[attr-defined]
        return True

    def release(self):
        lock = getattr(self, "_entered_lock", None)
        if lock is not None and lock.locked():
            lock.release()
            self._entered_lock = None  # type: ignore[attr-defined]

    def locked(self):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        with _registry_lock:
            lock = _locks.get(loop)
        return lock.locked() if lock is not None else False


_pool_lock = _PerLoopLockProxy()


def _shared_session_usable() -> bool:
    """缓存 session 只有在「未关闭」且其事件循环仍存活时才可复用。

    session 是在创建它的那个 event loop 上工作的；若该 loop 已被关闭（例如前一个
    测试的函数级 loop），继续复用或 close 它都会在已关闭的 loop 上调度任务并抛出
    ``RuntimeError: Event loop is closed``。此时必须当作不可用并重新创建。

    Per-loop variant: checks the *current* running loop's session only; returns
    False when called outside a running loop.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    with _registry_lock:
        sess = _sessions.get(loop)
    if sess is None or sess.closed:
        return False
    return not loop.is_closed()


async def get_shared_client() -> aiohttp.ClientSession:
    """返回跨请求复用的 aiohttp ClientSession（TCP connector pool）。

    与每次 `async with aiohttp.ClientSession()` 比较：
    - 避免频繁 TCP 握手，降低延迟
    - 为 ProviderHealthTracker 提供统一的速率计数注入点
    注意：并发量由调用方的 Semaphore / rate limiter 另行约束，非此模块负责。
    """
    global _shared_session, _shared_session_loop
    loop = asyncio.get_running_loop()
    lock = _get_loop_lock(loop)
    async with lock:
        # Re-check under lock (double-checked) — another coroutine on the same
        # loop may have created the session while we waited for the lock.
        with _registry_lock:
            sess = _sessions.get(loop)
            if sess is not None and not sess.closed and not loop.is_closed():
                _shared_session = sess
                _shared_session_loop = loop
                return sess
            # Stale entry (closed session) — drop it; a new one will be created.
            if sess is not None and sess.closed:
                _sessions.pop(loop, None)

        conn = aiohttp.TCPConnector(ttl_dns_cache=300, limit=20, limit_per_host=10)
        new_sess = aiohttp.ClientSession(
            connector=conn,
            timeout=aiohttp.ClientTimeout(total=10),
            headers=get_base_headers(),
        )
        with _registry_lock:
            _sessions[loop] = new_sess
            _shared_session = new_sess
            _shared_session_loop = loop
        return new_sess


async def close_shared_client() -> None:
    """服务关闭时释放共享 session。应在 FastAPI lifespan shutdown 事件中调用。"""
    global _shared_session, _shared_session_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop (e.g. called from sync context after the loop was
        # closed). Best-effort: close all known sessions whose loops are still
        # open by scheduling closes on their own loops if possible; otherwise
        # just drop references — weak dict will GC them.
        with _registry_lock:
            items = list(_sessions.items())
            _sessions.clear()
            _locks.clear()
            _shared_session = None
            _shared_session_loop = None
        for sess_loop, sess in items:
            if sess is None or sess.closed:
                continue
            if sess_loop.is_closed():
                continue
            # Try to close on its own loop if that loop is still running in
            # another thread; otherwise ignore (GC will reap).
            try:
                if sess_loop.is_running():
                    fut = asyncio.run_coroutine_threadsafe(sess.close(), sess_loop)
                    fut.result(timeout=2)
                else:
                    # Loop exists but not running — spin a temporary run to close.
                    # aiohttp close must be awaited on the session's loop; if we
                    # can't, just drop.
                    pass
            except Exception:
                pass
        return

    lock = _get_loop_lock(loop)
    async with lock:
        with _registry_lock:
            sess = _sessions.pop(loop, None)
            # Update legacy aliases if they pointed at this loop's session.
            if _shared_session_loop is loop:
                _shared_session = None
                _shared_session_loop = None
        if sess is not None and not sess.closed:
            if not loop.is_closed():
                await sess.close()
        # Clean up the lock entry for this loop (no longer needed).
        with _registry_lock:
            _locks.pop(loop, None)
