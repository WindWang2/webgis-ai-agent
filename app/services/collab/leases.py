"""Lease locks —— 瞬态编辑租约（Workbench V6 / ADR-0119）。

语义边界（架构 §6 / R1-NIT 修订）：租约是**用户间的编辑协调（advisory）**；
agent 的硬闸是 doc 内持久意图锁 ``workbench.lockedLayerIds``（服务端引擎
守卫强制）+ user-wins 守卫。租约**不**约束 agent —— agent 不持有 clientId，
不参与租约协议；该取舍在 ADR 中披露（租约 TTL 60s 会让单浏览器短时饿死
agent 编辑，属可接受的产品语义）。

lockKey 词表：``layer:{layerId}`` | ``group:{groupId}``。

Redis：hash ``collab:lease:{sid}`` field=lockKey → JSON {client,label,exp}；
Lua 原子 acquire/renew/release（token=clientId 校验，模式同
distributed_lock 的 token-checked release）。TTL 60s，续期 20s；
每 clientId ≤ 16 把租约；过期惰性清扫；hash key TTL 1h。
无 Redis = 进程内有界降级注册表。无永久孤儿：TTL 过期即失效。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

LEASE_TTL_S = 60
RENEW_INTERVAL_S = 20
MAX_LEASES_PER_CLIENT = 16
_FALLBACK_MAX_SESSIONS = 256

_KEY = "collab:lease:{sid}"

# acquire：清扫过期 → 每-client 计数上限 → 未被他人持有才授予。
_ACQUIRE_SCRIPT = """
local key = KEYS[1]
local lock_key = ARGV[1]
local client = ARGV[2]
local label = ARGV[3]
local now = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])
local max_per_client = tonumber(ARGV[6])
local ttl_ms = tonumber(ARGV[7])
local members = redis.call('HGETALL', key)
local expired = {}
local mine = 0
local holder = nil
for i = 1, #members, 2 do
  local f = members[i]
  local v = members[i + 1]
  local ok, exp = pcall(string.match, v, '"exp":(-?%d+)')
  local expn = tonumber(exp) or 0
  if expn < now then
    table.insert(expired, f)
  else
    local okc, c = pcall(string.match, v, '"client":"([^"]+)"')
    if okc and c == client then mine = mine + 1 end
    if f == lock_key and expn >= now then holder = v end
  end
end
if #expired > 0 then redis.call('HDEL', key, unpack(expired)) end
if holder then
  return {0, holder}
end
if mine >= max_per_client then
  return {0, 'client_limit'}
end
local value = '{"client":"' .. client .. '","label":"' .. label .. '","exp":' .. tostring(math.floor(now + ttl)) .. '}'
redis.call('HSET', key, lock_key, value)
redis.call('PEXPIRE', key, ttl_ms)
return {1, value}
"""

# renew/release：仅持有人可操作。
_RENEW_SCRIPT = """
local key = KEYS[1]
local lock_key = ARGV[1]
local client = ARGV[2]
local now = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])
local ttl_ms = tonumber(ARGV[5])
local v = redis.call('HGET', key, lock_key)
if not v then return 0 end
local ok, c = pcall(string.match, v, '"client":"([^"]+)"')
if not ok or c ~= client then return 0 end
local ok2, _ = pcall(string.match, v, '"exp":(-?%d+)')
if (tonumber(string.match(v, '"exp":(-?%d+)')) or 0) < now then
  redis.call('HDEL', key, lock_key)
  return 0
end
local newv = string.gsub(v, '"exp":%-?%d+', '"exp":' .. tostring(math.floor(now + ttl)))
redis.call('HSET', key, lock_key, newv)
redis.call('PEXPIRE', key, ttl_ms)
return 1
"""

_RELEASE_SCRIPT = """
local key = KEYS[1]
local lock_key = ARGV[1]
local client = ARGV[2]
local v = redis.call('HGET', key, lock_key)
if not v then return 0 end
local ok, c = pcall(string.match, v, '"client":"([^"]+)"')
if not ok or c ~= client then return 0 end
redis.call('HDEL', key, lock_key)
return 1
"""


def normalize_lock_key(raw: str, *, max_len: int = 260) -> Optional[str]:
    """lockKey 白名单：``layer:{id}`` | ``group:{id}``；其余 None（拒绝）。"""
    if not isinstance(raw, str) or len(raw) > max_len:
        return None
    for prefix in ("layer:", "group:"):
        if raw.startswith(prefix) and len(raw) > len(prefix):
            return raw
    return None


class LeaseRegistry:
    def __init__(self, *, ttl_s: int = LEASE_TTL_S) -> None:
        self._ttl_s = ttl_s
        self._local: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._client: Optional[Any] = None
        self._client_failed_at: float = 0.0
        self._scripts: Dict[str, Any] = {}

    def _redis(self) -> Optional[Any]:
        if self._client is not None:
            return self._client
        now = time.monotonic()
        if now - self._client_failed_at < 30.0:
            return None
        try:
            from app.core.config import settings

            if not getattr(settings, "USE_REDIS", False):
                self._client_failed_at = now
                return None
            import redis.asyncio as aioredis

            self._client = aioredis.from_url(
                getattr(settings, "REDIS_URL", "redis://localhost:6379/0"),
                socket_connect_timeout=1,
                socket_timeout=2,
                decode_responses=True,
            )
            self._scripts = {
                "acquire": self._client.register_script(_ACQUIRE_SCRIPT),
                "renew": self._client.register_script(_RENEW_SCRIPT),
                "release": self._client.register_script(_RELEASE_SCRIPT),
            }
            return self._client
        except Exception:  # noqa: BLE001
            self._client_failed_at = now
            return None

    def _drop(self) -> None:
        self._client = None
        self._client_failed_at = time.monotonic()
        self._scripts = {}

    async def acquire(
        self, session_id: str, lock_key: str, client_id: str, label: str = ""
    ) -> Dict[str, Any]:
        """返回 {"granted": bool, "holder"?: {...}, "reason"?: str}。"""
        key = normalize_lock_key(lock_key)
        if key is None:
            return {"granted": False, "reason": "invalid_lock_key"}
        now = time.time()
        # Lua 内拼接 JSON（acquire 脚本）：字段先去引义字符，防注入破坏 JSON。
        safe_client = str(client_id).replace('"', "").replace("\\", "")[:120]
        safe_label = str(label).replace('"', "").replace("\\", "")[:120]
        client = self._redis()
        if client is not None:
            try:
                granted, payload = await self._scripts["acquire"](
                    keys=[_KEY.format(sid=str(session_id))],
                    args=[
                        key,
                        safe_client,
                        safe_label,
                        now,
                        self._ttl_s,
                        MAX_LEASES_PER_CLIENT,
                        3600_000,
                    ],
                )
                if granted:
                    return {"granted": True}
                if payload == "client_limit":
                    return {"granted": False, "reason": "client_limit"}
                try:
                    holder = json.loads(payload)
                    holder.pop("exp", None)
                except (ValueError, TypeError):
                    holder = {"client": "unknown"}
                return {"granted": False, "holder": holder, "reason": "held"}
            except Exception as exc:  # noqa: BLE001
                logger.debug("[collab-lease] redis acquire failed: %s", exc)
                self._drop()
        return self._acquire_local(str(session_id), key, str(client_id), str(label)[:120], now)

    def _acquire_local(
        self, session_id: str, key: str, client_id: str, label: str, now: float
    ) -> Dict[str, Any]:
        bucket = self._sweep_local(session_id, now)
        if bucket is None:
            if len(self._local) >= _FALLBACK_MAX_SESSIONS:
                return {"granted": False, "reason": "capacity"}
            bucket = {}
            self._local[session_id] = bucket
        existing = bucket.get(key)
        if existing is not None and existing.get("exp", 0) >= now and existing.get("client") != client_id:
            holder = {k: v for k, v in existing.items() if k != "exp"}
            return {"granted": False, "holder": holder, "reason": "held"}
        mine = sum(1 for v in bucket.values() if v.get("client") == client_id and v.get("exp", 0) >= now)
        if existing is None and mine >= MAX_LEASES_PER_CLIENT:
            return {"granted": False, "reason": "client_limit"}
        bucket[key] = {"client": client_id, "label": label, "exp": now + self._ttl_s}
        return {"granted": True}

    async def renew(self, session_id: str, lock_key: str, client_id: str) -> bool:
        key = normalize_lock_key(lock_key)
        if key is None:
            return False
        now = time.time()
        client = self._redis()
        if client is not None:
            try:
                return bool(
                    await self._scripts["renew"](
                        keys=[_KEY.format(sid=str(session_id))],
                        args=[key, str(client_id), now, self._ttl_s, 3600_000],
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("[collab-lease] redis renew failed: %s", exc)
                self._drop()
        bucket = self._sweep_local(session_id, now)
        record = (bucket or {}).get(key)
        if record is None or record.get("client") != client_id:
            return False
        record["exp"] = now + self._ttl_s
        return True

    async def release(self, session_id: str, lock_key: str, client_id: str) -> bool:
        key = normalize_lock_key(lock_key)
        if key is None:
            return False
        client = self._redis()
        if client is not None:
            try:
                return bool(
                    await self._scripts["release"](
                        keys=[_KEY.format(sid=str(session_id))],
                        args=[key, str(client_id)],
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("[collab-lease] redis release failed: %s", exc)
                self._drop()
        bucket = self._local.get(session_id)
        record = (bucket or {}).get(key)
        if record is None or record.get("client") != client_id:
            return False
        bucket.pop(key, None)
        return True

    async def release_all(self, session_id: str, client_id: str) -> int:
        """断连清理：释放该 client 的全部租约。返回释放数。"""
        released = 0
        for record in await self.snapshot(session_id):
            if record.get("client") == client_id and record.get("lockKey"):
                if await self.release(session_id, str(record["lockKey"]), client_id):
                    released += 1
        return released

    async def snapshot(self, session_id: str) -> List[Dict[str, Any]]:
        now = time.time()
        client = self._redis()
        if client is not None:
            try:
                raw = await self._client.hgetall(_KEY.format(sid=str(session_id)))
                out: List[Dict[str, Any]] = []
                for field, value in (raw or {}).items():
                    try:
                        record = json.loads(value)
                    except (ValueError, TypeError):
                        continue
                    if record.get("exp", 0) < now:
                        continue
                    record.pop("exp", None)
                    record["lockKey"] = field
                    out.append(record)
                return out
            except Exception as exc:  # noqa: BLE001
                logger.debug("[collab-lease] redis snapshot failed: %s", exc)
                self._drop()
        bucket = self._sweep_local(session_id, now)
        if not bucket:
            return []
        return [
            {**{k: v for k, v in record.items() if k != "exp"}, "lockKey": field}
            for field, record in bucket.items()
        ]

    def _sweep_local(self, session_id: str, now: float) -> Optional[Dict[str, Dict[str, Any]]]:
        bucket = self._local.get(session_id)
        if bucket is None:
            return None
        expired = [k for k, v in bucket.items() if v.get("exp", 0) < now]
        for k in expired:
            bucket.pop(k, None)
        return bucket


lease_registry = LeaseRegistry()
