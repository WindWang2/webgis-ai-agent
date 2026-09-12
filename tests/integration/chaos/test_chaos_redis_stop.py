"""Chaos — 停 Redis → API 降级路径（P7，#1225/#1229 隔离/降级谱系）。

redis 是依赖不是生命线：停掉后 API 必须仍然存活（健康检查 200），无
500 崩溃环；redis 回来后恢复。注入走 docker compose stop/start（真停）。
"""
from __future__ import annotations

import httpx
import pytest

try:  # 直接运行（pytest rootdir 导入）与包内导入两种姿势都兼容。
    from tests.integration.chaos.conftest import docker_compose, wait_redis
except ImportError:  # pragma: no cover
    from conftest import docker_compose, wait_redis  # type: ignore[no-redef]

pytestmark = pytest.mark.heavy


def test_redis_stop_api_survives_and_recovers(chaos_stack, chaos_api) -> None:
    base = chaos_api["base"]
    url = chaos_stack["redis_url"]

    # 基线：API 存活。
    with httpx.Client(timeout=10.0) as client:
        assert client.get(f"{base}/api/v1/health").status_code == 200

    # 真停（compose stop redis，非 fakeredis 模拟）。
    stop = docker_compose("stop")
    assert stop.returncode == 0, stop.stderr
    try:
        assert wait_redis(url, want=False, timeout=30), "redis 未真正停下"

        with httpx.Client(timeout=10.0) as client:
            health = client.get(f"{base}/api/v1/health")
            # 降级断言：不崩（2xx/503 都算诚实响应；5xx 内部崩溃即红）。
            assert health.status_code in (200, 503), (
                f"redis 停机时健康检查异常：{health.status_code}")
            live = client.get(f"{base}/api/v1/health/live")
            assert live.status_code == 200, "liveness 不允许被 redis 拖死"
    finally:
        start = docker_compose("start")
        assert start.returncode == 0, start.stderr
        assert wait_redis(url, want=True, timeout=60), "redis 未恢复"

    # 恢复断言：redis 回来后一切如常。
    with httpx.Client(timeout=10.0) as client:
        assert client.get(f"{base}/api/v1/health").status_code == 200
