"""Regression tests for review-swarm P3 findings in session_state_atomic.py.

Covers:
- atomic_update_state (Redis path): a corrupt (non-JSON) state field must be
  reported as None per the "storage failure returns None" contract instead of
  leaking json.JSONDecodeError past the WatchError/RedisError handlers.
"""
import json

import fakeredis.aioredis
import pytest

from app.services.session_data_redis import RedisSessionStore
from app.services.session_state_atomic import atomic_update_state


def _store() -> RedisSessionStore:
    return RedisSessionStore(
        redis_url="redis://unused",
        redis=fakeredis.aioredis.FakeRedis(decode_responses=False),
    )


@pytest.mark.parametrize(
    "corrupt_value",
    [b"not-json{{{", b"\xff\xfe invalid utf-8"],
    ids=["malformed-json", "invalid-utf8"],
)
async def test_atomic_update_state_corrupt_field_returns_none(corrupt_value):
    """损坏状态字段必须按"存储失败"契约返回 None，不得逃逸 JSONDecodeError。

    旧行为：hmget 读到的腐败值直接喂 json.loads，JSONDecodeError(ValueError)
    逃逸 except WatchError/RedisError，把契约内的降级路径炸成 500。
    """
    store = _store()
    sid = "sess-corrupt-atomic-state"
    state_key = store._state_key(sid)
    await store._r.hset(state_key, "plan", corrupt_value)

    result = await atomic_update_state(store, sid, ["plan"], lambda snap: {"plan": {"ok": True}})

    assert result is None, "损坏字段必须降级为 None（存储失败契约），而非抛出异常"
    # 降级路径不得顺带覆盖掉现场：腐败值原样保留，等维护路径处理。
    assert await store._r.hget(state_key, "plan") == corrupt_value


async def test_atomic_update_state_valid_field_still_commits():
    """正常字段的读-改-写契约不受腐败降级影响。"""
    store = _store()
    sid = "sess-valid-atomic-state"
    state_key = store._state_key(sid)
    await store._r.hset(state_key, "plan", json.dumps({"v": 1}))

    seen = {}

    def mutate(snapshot):
        seen.update(snapshot)
        return {"plan": {"v": 2}}

    result = await atomic_update_state(store, sid, ["plan"], mutate)

    assert seen == {"plan": {"v": 1}}, "mutate 必须收到解码后的快照"
    assert result == {"plan": {"v": 2}}
    assert json.loads(await store._r.hget(state_key, "plan")) == {"v": 2}
