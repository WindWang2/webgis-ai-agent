"""Atomic replay-cache admission, bounded independently from session Redis data."""
from __future__ import annotations

import inspect
import time
from typing import Any

MAX_REPLAY_RECORD_BYTES = 256 * 1024
MAX_REPLAY_TOTAL_BYTES = 16 * 1024 * 1024
MAX_REPLAY_PRINCIPAL_BYTES = 2 * 1024 * 1024
MAX_REPLAY_RECORDS = 512

# All side-index work is bounded by MAX_REPLAY_RECORDS. Entries expire together
# with the replay record; owner fields are removed when their last entry expires.
_ADMIT = """
local expired = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, ARGV[7])
local total = tonumber(redis.call('GET', KEYS[5]) or '0')
for _, key in ipairs(expired) do
  local size = tonumber(redis.call('HGET', KEYS[2], key) or '0')
  local owner = redis.call('HGET', KEYS[3], key)
  total = math.max(0, total - size)
  if owner then
    local amount = redis.call('HINCRBY', KEYS[4], owner, -size)
    if amount <= 0 then redis.call('HDEL', KEYS[4], owner) end
  end
  redis.call('ZREM', KEYS[1], key)
  redis.call('HDEL', KEYS[2], key)
  redis.call('HDEL', KEYS[3], key)
end
redis.call('SET', KEYS[5], total, 'EX', ARGV[3] + 60)
if redis.call('EXISTS', KEYS[6]) == 1 then return 1 end
local size = string.len(ARGV[4])
local owned = tonumber(redis.call('HGET', KEYS[4], ARGV[2]) or '0')
if total + size > tonumber(ARGV[5]) or owned + size > tonumber(ARGV[6]) or redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[7]) then
  return 0
end
redis.call('SET', KEYS[6], ARGV[4], 'EX', ARGV[3])
redis.call('ZADD', KEYS[1], ARGV[1] + ARGV[3], KEYS[6])
redis.call('HSET', KEYS[2], KEYS[6], size)
redis.call('HSET', KEYS[3], KEYS[6], ARGV[2])
redis.call('HINCRBY', KEYS[4], ARGV[2], size)
redis.call('SET', KEYS[5], total + size, 'EX', ARGV[3] + 60)
for i=1,4 do redis.call('EXPIRE', KEYS[i], ARGV[3] + 60) end
return 1
"""


async def store_replay(redis: Any, key: str, record: str, ttl: int, principal: str) -> bool:
    """Refuse cache admission without changing the already-executed response."""
    if len(record.encode("utf-8")) > MAX_REPLAY_RECORD_BYTES:
        return False
    result = redis.eval(_ADMIT, 6, "idem:budget:expiry", "idem:budget:sizes",
        "idem:budget:owners", "idem:budget:ownerbytes", "idem:budget:total", key,
        time.time(), principal, ttl, record, MAX_REPLAY_TOTAL_BYTES,
        MAX_REPLAY_PRINCIPAL_BYTES, MAX_REPLAY_RECORDS)
    if inspect.isawaitable(result):
        result = await result
    return bool(result)
