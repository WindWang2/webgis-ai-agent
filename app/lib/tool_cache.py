"""工具结果缓存层 — Redis-backed, opt-in per tool.

入口：make_cache_key(name, args)、cached_tool(...) 装饰器（后续 Task 加入）。
键命名空间 tool_cache:v1:<sha256[:16]>，全量失效一条 SCAN | DEL 即可。

singleflight：cache miss 时先尝试 SET NX 锁（带 token + TTL）；拿到锁的
caller 计算并写回，其余 caller 轮询等待结果。锁丢失/过期/Redis 故障时
退化为直接计算（有界重复，等价于无 singleflight），无分布式死锁风险。
"""
import asyncio
import hashlib
import json
import uuid
from typing import Optional, Any, Callable
import logging
import time
import functools
import inspect
from contextvars import ContextVar

import redis as _redis

from app.core.config import settings

logger = logging.getLogger(__name__)

# singleflight 锁参数：锁 TTL 必须覆盖最坏计算时长（过期即退化为重复计算，
# 不会死锁）；等待方轮询直到值出现或锁窗口结束。
_DEFAULT_LOCK_TTL_S = 120
# 同步路径的等待预算：等待占用 threadpool 线程，30s 上限避免拥塞其它工具。
_SYNC_WAIT_BUDGET_S = 30
_POLL_MIN_S = 0.05
_POLL_MAX_S = 1.0


def make_cache_key(tool_name: str, args: dict) -> Optional[str]:
    """构造确定性缓存键。

    args 内任一叶子值是 'ref:' 开头的字符串时返回 None — 调用方据此跳过缓存。
    （ref:xxx 是会话内可变数据引用，同一引用不同时刻解析结果不同。）
    """
    if _contains_ref(args):
        return None
    canonical = json.dumps(args, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(f"{tool_name}::{canonical}".encode()).hexdigest()[:16]
    return f"tool_cache:v1:{digest}"


def _contains_ref(value) -> bool:
    """递归检查任一叶子是否是 'ref:' 开头的字符串。"""
    if isinstance(value, str):
        return value.startswith("ref:")
    if isinstance(value, dict):
        return any(_contains_ref(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_ref(v) for v in value)
    return False


# 进程级单例。lazy 初始化使得 import 期间 Redis 不可达也不会炸 import。
_redis_client: Optional["_redis.Redis"] = None
_last_warning_ts: float = 0.0
_WARN_THROTTLE_SEC = 60.0


def _get_redis_client() -> "_redis.Redis":
    global _redis_client
    if _redis_client is None:
        _redis_client = _redis.Redis.from_url(
            settings.REDIS_URL,
            decode_responses=False,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
        )
    return _redis_client


def _reset_redis_client_for_tests() -> None:
    """仅供测试使用：清空单例 + warning 时间戳。"""
    global _redis_client, _last_warning_ts
    _redis_client = None
    _last_warning_ts = 0.0


def _warn_throttled(msg: str) -> None:
    global _last_warning_ts
    now = time.monotonic()
    if now - _last_warning_ts >= _WARN_THROTTLE_SEC:
        logger.warning(msg)
        _last_warning_ts = now


def get_cached(key: str) -> Optional[Any]:
    """Redis 读。失败/未命中均返回 None — 调用方据此走未命中路径。"""
    try:
        raw = _get_redis_client().get(key)
    except _redis.RedisError as e:
        _warn_throttled(f"[tool_cache] Redis GET failed, bypassing cache: {type(e).__name__}: {e}")
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"[tool_cache] Corrupt cache value at {key}: {e}")
        return None


def set_cached(key: str, value: Any, ttl: int) -> None:
    """Redis 写。失败时仅 warning，绝不抛 — 不能因缓存写失败导致用户请求失败。"""
    try:
        payload = json.dumps(value, default=str).encode("utf-8")
        _get_redis_client().setex(key, ttl, payload)
    except (_redis.RedisError, TypeError, ValueError) as e:
        _warn_throttled(f"[tool_cache] Redis SET failed, dropping cache write: {type(e).__name__}: {e}")


# ─── singleflight (cache stampede protection) ────────────────────────────────

def _lock_key(key: str) -> str:
    return f"{key}:lock"


def _acquire_lock(key: str, token: str, lock_ttl_s: int) -> bool:
    """SET NX PX — 拿到锁的 caller 负责计算。Redis 故障时返回 False → 直接计算。"""
    try:
        return bool(_get_redis_client().set(_lock_key(key), token, nx=True, px=lock_ttl_s * 1000))
    except _redis.RedisError as e:
        _warn_throttled(f"[tool_cache] lock acquire failed, computing without singleflight: {type(e).__name__}: {e}")
        return False


def _release_lock(key: str, token: str) -> None:
    """Compare-and-delete：只有锁持有者（token 匹配）才能释放，防止误删他人锁。"""
    try:
        _get_redis_client().eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
            1,
            _lock_key(key),
            token,
        )
    except _redis.RedisError as e:
        _warn_throttled(f"[tool_cache] lock release failed (TTL will expire it): {type(e).__name__}: {e}")


def _lock_exists(key: str) -> bool:
    """Lock 是否仍被持有。Redis 故障时假定存活（等 deadline 兜底），不抛。"""
    try:
        return bool(_get_redis_client().exists(_lock_key(key)))
    except _redis.RedisError:
        return True


def _wait_for_cached(key: str, budget_s: float, sleep: Callable[[float], None]) -> Optional[Any]:
    """轮询直到值出现或锁消失（winner 完成/失败释放）或预算耗尽（指数退避）。

    返回 None 表示放弃等待（锁丢失/过期）→ 调用方退化为直接计算。
    """
    deadline = time.monotonic() + budget_s
    interval = _POLL_MIN_S
    while time.monotonic() < deadline:
        sleep(interval)
        cached = get_cached(key)
        if cached is not None:
            return cached
        if not _lock_exists(key):
            return None  # winner 释放了锁但没发布值（如计算失败）→ 立即接手
        interval = min(interval * 2, _POLL_MAX_S)
    return None


async def _singleflight_async(key: str, ttl: int, lock_ttl_s: int, compute: Callable[[], Any]) -> Any:
    """async 版：持有锁者计算并写回；等待者轮询（在线程中，不阻塞事件循环）。

    锁过期/丢失/Redis 故障时退化为直接计算 — 有界重复，无死锁。
    """
    token = uuid.uuid4().hex
    if _acquire_lock(key, token, lock_ttl_s):
        try:
            result = await compute()
            set_cached(key, result, ttl)
            return result
        finally:
            _release_lock(key, token)
    cached = await asyncio.to_thread(_wait_for_cached, key, float(lock_ttl_s), time.sleep)
    if cached is not None:
        return cached
    result = await compute()
    set_cached(key, result, ttl)
    return result


def _singleflight_sync(key: str, ttl: int, lock_ttl_s: int, compute: Callable[[], Any]) -> Any:
    """sync 版：等待预算 _SYNC_WAIT_BUDGET_S，避免长时间占用 threadpool 线程。"""
    token = uuid.uuid4().hex
    if _acquire_lock(key, token, lock_ttl_s):
        try:
            result = compute()
            set_cached(key, result, ttl)
            return result
        finally:
            _release_lock(key, token)
    cached = _wait_for_cached(key, min(float(lock_ttl_s), _SYNC_WAIT_BUDGET_S), time.sleep)
    if cached is not None:
        return cached
    result = compute()
    set_cached(key, result, ttl)
    return result


# Registry 的 timing wrapper 读此变量判断当前 dispatch 是否命中缓存。
cache_hit_var: ContextVar[bool] = ContextVar("tool_cache_hit", default=False)

# BUG-08: 嵌套深度计数。当某个 cached_tool 在另一个 cached_tool 的函数体内被
# 调用时（嵌套 dispatch），内层 set 会覆盖外层 cache_hit_var 的值，导致外层
# registry timing 误报。用深度计数区分：最外层（depth==0）的 cached_tool 不
# reset —— 它的值留给 registry 读；嵌套层（depth>0）在 finally 里 reset 回外层
# 值，保证内层状态不泄漏到外层。
_cache_depth_var: ContextVar[int] = ContextVar("tool_cache_depth", default=0)


def cached_tool(ttl: int = 3600, skip_if: Optional[Callable[[dict], bool]] = None,
                singleflight: bool = True, lock_ttl: Optional[int] = None):
    """工具函数装饰器：Redis 命中即返回，未命中调内层 + 写回。

    Args:
        ttl: 缓存生存时间（秒）。默认 1 小时。
        skip_if: 谓词函数 (kwargs_dict) -> bool。返回真值时双向旁路缓存。
        singleflight: cache miss 时抑制并发重复计算（SET NX 锁 + 等待者轮询）。
            默认开启；锁过期/丢失/Redis 故障时退化为直接计算，无死锁。
        lock_ttl: 单飞锁 TTL（秒）。默认 120s；计算可能超过该时长时按需调大。

    内层函数可以是 sync 也可以是 async；通过 inspect.iscoroutinefunction 分支。
    """
    effective_lock_ttl = lock_ttl or _DEFAULT_LOCK_TTL_S

    def decorator(func: Callable) -> Callable:
        is_async = inspect.iscoroutinefunction(func)
        tool_name = getattr(func, "__name__", "anonymous_tool")

        if is_async:
            @functools.wraps(func)
            async def async_wrapper(**kwargs):
                if skip_if is not None and skip_if(kwargs):
                    return await func(**kwargs)
                key = make_cache_key(tool_name, kwargs)
                if key is None:
                    return await func(**kwargs)
                # BUG-08: 进入嵌套层。最外层 is_outermost=True（depth 进入前为 0）。
                # 注意：不能用 token.old_value 判定——ContextVar 的 default 值不算
                # "已 set"，old_value 会是 Token.MISSING 而非 0。先 get() 再 set。
                prev_depth = _cache_depth_var.get()
                depth_token = _cache_depth_var.set(prev_depth + 1)
                is_outermost = prev_depth == 0
                try:
                    cached = get_cached(key)
                    if cached is not None:
                        # 审计 M10：cache hit 显式 set(True)。
                        hit_token = cache_hit_var.set(True)
                        if is_outermost:
                            return cached  # 最外层：留给 registry 读，不 reset
                        try:
                            return cached
                        finally:
                            cache_hit_var.reset(hit_token)
                    # 审计 M10：cache miss 必须显式 set(False)，否则 ContextVar
                    # 会保留前一次 hit 的 True 值 -> registry timing 误报 cache_hit。
                    hit_token = cache_hit_var.set(False)
                    try:
                        if singleflight:
                            return await _singleflight_async(
                                key, ttl, effective_lock_ttl, lambda: func(**kwargs)
                            )
                        result = await func(**kwargs)
                        set_cached(key, result, ttl)
                        return result
                    finally:
                        if not is_outermost:
                            cache_hit_var.reset(hit_token)
                finally:
                    _cache_depth_var.reset(depth_token)
            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(**kwargs):
            if skip_if is not None and skip_if(kwargs):
                return func(**kwargs)
            key = make_cache_key(tool_name, kwargs)
            if key is None:
                return func(**kwargs)
            # BUG-08: 同 async_wrapper，用深度计数区分最外层与嵌套层。
            prev_depth = _cache_depth_var.get()
            depth_token = _cache_depth_var.set(prev_depth + 1)
            is_outermost = prev_depth == 0
            try:
                cached = get_cached(key)
                if cached is not None:
                    # 审计 M10：cache hit 显式 set(True)。
                    hit_token = cache_hit_var.set(True)
                    if is_outermost:
                        return cached  # 最外层：留给 registry 读，不 reset
                    try:
                        return cached
                    finally:
                        cache_hit_var.reset(hit_token)
                # 审计 M10：同 async_wrapper，miss 时显式 set(False)
                hit_token = cache_hit_var.set(False)
                try:
                    if singleflight:
                        return _singleflight_sync(
                            key, ttl, effective_lock_ttl, lambda: func(**kwargs)
                        )
                    result = func(**kwargs)
                    set_cached(key, result, ttl)
                    return result
                finally:
                    if not is_outermost:
                        cache_hit_var.reset(hit_token)
            finally:
                _cache_depth_var.reset(depth_token)
        return sync_wrapper

    return decorator
