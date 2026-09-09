"""Presence registry —— 瞬态参与者状态（Workbench V6 / ADR-0119）。

记录（隐私最小集 —— 无坐标级 cursor、无 email/token/user_id 原文）::

    clientId → {"label": str, "kind": "user"|"agent", "color": str,
                "viewport": {...}?, "selectionIds": [...]≤50,
                "editingLayerId"?: str, "editingGroupId"?: str,
                "exp": <unix 秒>}

有界：每会话参与者 ≤ 32（Lua 原子 check-cap-join，评审 NIT 采纳）；
记录 TTL 30s（心跳续期）；hash key TTL 1h（死会话 GC）。
过期成员在读路径惰性清扫。无 Redis = 进程内有界降级注册表。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PRESENCE_TTL_S = 30
MAX_PARTICIPANTS = 32
#: selection id 快照上限（与 world_state 的既有选择描述预算一致）。
MAX_SELECTION_IDS = 50
#: 降级注册表的总会话上限（防畸形会话量放大；每个会话内另有 32 上限）。
_FALLBACK_MAX_SESSIONS = 256
#: presence 字段白名单（服务端裁剪客户端 patch —— 防载荷放大/字段注入）。
_ALLOWED_FIELDS = frozenset(
    {"label", "kind", "color", "viewport", "selectionIds", "editingLayerId", "editingGroupId"}
)
_MAX_STR = 120

_KEY = "collab:presence:{sid}"

# 原子 join：清扫过期 → cap 检查 → 写入 → key 续期（单 RTT，无竞态窗口）。
_JOIN_SCRIPT = """
local key = KEYS[1]
local field = ARGV[1]
local value = ARGV[2]
local now = tonumber(ARGV[3])
local cap = tonumber(ARGV[4])
local ttl_ms = tonumber(ARGV[5])
local members = redis.call('HVALS', key)
local alive = 0
for _, m in ipairs(members) do
  local ok, exp = pcall(string.match, m, '"exp":(-?%d+)')
  local expn = tonumber(exp) or 0
  if expn < now then
    local okd, f = pcall(string.match, m, '"cid":"([^"]+)"')
    if okd and f then redis.call('HDEL', key, f) end
  else
    alive = alive + 1
  end
end
local existing = redis.call('HEXISTS', key, field)
if alive >= cap and existing == 0 then
  return 0
end
redis.call('HSET', key, field, value)
redis.call('PEXPIRE', key, ttl_ms)
return 1
"""

# 快照 = 清扫过期 + 全量读（≤32+ 条，单 RTT）。
_SNAPSHOT_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local ttl_ms = tonumber(ARGV[2])
local members = redis.call('HGETALL', key)
local alive = {}
local expired = {}
for i = 1, #members, 2 do
  local m = members[i + 1]
  local ok, exp = pcall(string.match, m, '"exp":(-?%d+)')
  if (tonumber(exp) or 0) < now then
    table.insert(expired, members[i])
  else
    table.insert(alive, m)
  end
end
if #expired > 0 then
  redis.call('HDEL', key, unpack(expired))
  redis.call('PEXPIRE', key, ttl_ms)
end
return alive
"""


def _sanitize_patch(patch: Dict[str, Any]) -> Dict[str, Any]:
    """白名单 + 长度裁剪（服务端权威：载荷可信后才进广播/存储）。"""
    out: Dict[str, Any] = {}
    for key, value in patch.items():
        if key not in _ALLOWED_FIELDS:
            continue
        if key in ("viewport",):
            if isinstance(value, dict) and len(value) <= 12:
                out[key] = {
                    str(k)[:32]: v
                    for k, v in list(value.items())[:12]
                    if isinstance(v, (int, float, str, bool))
                }
        elif key == "selectionIds":
            if isinstance(value, list):
                out[key] = [str(v)[:_MAX_STR] for v in value[:MAX_SELECTION_IDS]]
        elif key in ("label", "color", "editingLayerId", "editingGroupId", "kind"):
            if isinstance(value, str) and value:
                out[key] = value[:_MAX_STR]
    return out


class PresenceRegistry:
    """Redis hash（Lua 原子操作）+ 进程内降级。全部调用 fail-open。"""

    def __init__(self, *, ttl_s: int = PRESENCE_TTL_S) -> None:
        self._ttl_s = ttl_s
        self._local: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._client: Optional[Any] = None
        self._client_failed_at: float = -30.0  # 首探立即放行（评审 m-7 同型）
        self._scripts: Dict[str, Any] = {}

    # ── Redis 客户端（懒探 + 退避）─────────────────────────────────────
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
                "join": self._client.register_script(_JOIN_SCRIPT),
                "snapshot": self._client.register_script(_SNAPSHOT_SCRIPT),
            }
            return self._client
        except Exception:  # noqa: BLE001
            self._client_failed_at = now
            return None

    def _drop(self) -> None:
        self._client = None
        self._client_failed_at = time.monotonic()
        self._scripts = {}

    # ── 记录构造 ────────────────────────────────────────────────────────
    def _record(self, client_id: str, patch: Dict[str, Any], now: float) -> str:
        record = {"cid": str(client_id), **_sanitize_patch(patch), "exp": int(now) + self._ttl_s}
        return json.dumps(record, ensure_ascii=False, default=str)

    # ── API ─────────────────────────────────────────────────────────────
    async def join(self, session_id: str, client_id: str, info: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        """加入会话；满员返回 None（调用方以 4408 拒绝）。"""
        now = time.time()
        client = self._redis()
        if client is not None:
            try:
                granted = await self._scripts["join"](
                    keys=[_KEY.format(sid=str(session_id))],
                    args=[
                        str(client_id),
                        self._record(client_id, info, now),
                        int(now),
                        MAX_PARTICIPANTS,
                        3600_000,
                    ],
                )
                if not granted:
                    return None
                return await self.snapshot(session_id)
            except Exception as exc:  # noqa: BLE001
                logger.debug("[collab-presence] redis join failed, local fallback: %s", exc)
                self._drop()
        return self._join_local(session_id, client_id, info, now)

    def _join_local(self, session_id: str, client_id: str, info: Dict[str, Any], now: float) -> Optional[List[Dict[str, Any]]]:
        bucket = self._local.get(session_id)
        if bucket is None:
            if len(self._local) >= _FALLBACK_MAX_SESSIONS:
                return None
            bucket = {}
            self._local[session_id] = bucket
        self._sweep_local(bucket, now)
        if client_id not in bucket and len(bucket) >= MAX_PARTICIPANTS:
            return None
        bucket[client_id] = json.loads(self._record(client_id, info, now))
        return self.snapshot_local(session_id)

    async def heartbeat(self, session_id: str, client_id: str, patch: Dict[str, Any]) -> None:
        """续期 + 合并字段（服务端白名单裁剪）。"""
        now = time.time()
        client = self._redis()
        if client is not None:
            try:
                clean = _sanitize_patch(patch)
                key = _KEY.format(sid=str(session_id))
                current = await client.hget(key, str(client_id))
                if current:
                    record = json.loads(current)
                else:
                    record = {"cid": str(client_id)}
                record.update(clean)
                record["exp"] = int(now) + self._ttl_s
                await client.hset(key, str(client_id), json.dumps(record, ensure_ascii=False))
                await client.pexpire(key, 3600_000)
                return
            except Exception as exc:  # noqa: BLE001
                logger.debug("[collab-presence] redis heartbeat failed: %s", exc)
                self._drop()
        bucket = self._local.get(session_id)
        if bucket is None:
            return
        record = bucket.get(client_id)
        if record is None:
            return
        record.update(_sanitize_patch(patch))
        record["exp"] = int(now) + self._ttl_s

    async def leave(self, session_id: str, client_id: str) -> bool:
        client = self._redis()
        if client is not None:
            try:
                removed = await client.hdel(_KEY.format(sid=str(session_id)), str(client_id))
                return removed > 0
            except Exception as exc:  # noqa: BLE001
                logger.debug("[collab-presence] redis leave failed: %s", exc)
                self._drop()
        bucket = self._local.get(session_id)
        if bucket is None:
            return False
        existed = bucket.pop(client_id, None) is not None
        return existed

    async def snapshot(self, session_id: str) -> List[Dict[str, Any]]:
        client = self._redis()
        if client is not None:
            try:
                raw = await self._scripts["snapshot"](
                    keys=[_KEY.format(sid=str(session_id))],
                    args=[int(time.time()), 3600_000],
                )
                out = []
                for item in raw or []:
                    try:
                        record = json.loads(item)
                    except (ValueError, TypeError):
                        continue
                    record.pop("exp", None)
                    out.append(record)
                return out
            except Exception as exc:  # noqa: BLE001
                logger.debug("[collab-presence] redis snapshot failed: %s", exc)
                self._drop()
        return self.snapshot_local(session_id)

    def snapshot_local(self, session_id: str) -> List[Dict[str, Any]]:
        bucket = self._local.get(session_id)
        if not bucket:
            return []
        self._sweep_local(bucket, time.time())
        out = []
        for record in bucket.values():
            clean = {k: v for k, v in record.items() if k != "exp"}
            out.append(clean)
        return out

    @staticmethod
    def _sweep_local(bucket: Dict[str, Dict[str, Any]], now: float) -> None:
        expired = [cid for cid, rec in bucket.items() if rec.get("exp", 0) < now]
        for cid in expired:
            bucket.pop(cid, None)


presence_registry = PresenceRegistry()
