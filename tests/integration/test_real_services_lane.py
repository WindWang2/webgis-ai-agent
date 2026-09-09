"""Real-services lane（Quality V3 W12，opt-in）。

Redis 瞬时故障恢复（§15 Redis loss/recovery）：真实 REDIS_URL 下
连接中断 → 客户端恢复 → 语义正确（锁释放/重取）。缺省 skip
（资源纪律：默认 quick lane 不启动任何服务）。

进程内等价物（chaos V3 的 fakeredis 序列）在 tests/quality 的 chaos
套件覆盖；本文件只验证**真实服务**行为。
"""
from __future__ import annotations

import os

import pytest


@pytest.mark.real_services()
def test_redis_transient_disconnect_recovery():
    url = os.environ.get("REDIS_URL", "")
    if not url:
        pytest.skip("REDIS_URL 未设置（real-services lane 专属）")
    import redis

    client = redis.from_url(url, socket_connect_timeout=2,
                            socket_timeout=2, health_check_interval=1)
    client.ping()

    key = "webgis:real-lane:probe"
    client.set(key, "v1", ex=60)

    # 瞬时故障：强制断连（连接池关闭 → 下一条命令重连）
    pool = client.connection_pool
    client.disconnect()

    assert client.get(key) == b"v1", "断连恢复后必须读到写入值"

    # 分布式锁语义在瞬时故障下的行为
    lock = client.lock("webgis:real-lane:lock", timeout=5)
    assert lock.acquire(blocking=False)
    client.disconnect()  # 持锁瞬间断连
    assert client.get("webgis:real-lane:lock") is not None
    lock.release()
    assert client.get("webgis:real-lane:lock") is None
    pool.disconnect()


@pytest.mark.real_services()
def test_postgres_smoke_via_sqlalchemy():
    url = os.environ.get("TEST_POSTGRES_URL", "")
    if not url:
        pytest.skip("TEST_POSTGRES_URL 未设置（real-services lane 专属）")
    import sqlalchemy

    engine = sqlalchemy.create_engine(url)
    with engine.connect() as conn:
        version = conn.execute(sqlalchemy.text("SELECT version()")).scalar()
    assert "PostgreSQL" in (version or "")
