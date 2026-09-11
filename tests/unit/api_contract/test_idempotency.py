"""V9 契约基石：Idempotency-Key 中间件测试（ADR-0138 / P5）。

四类门禁：
1. 重放一致性：同 key 二次请求返回首次响应 + Idempotent-Replay 头；
2. 并发同 key 单飞：N 并发只命中上游一次；
3. TTL 过期：记录过期后重新处理；
4. Redis 不可用 fail-open：请求正常处理，不 5xx。
"""
from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.idempotency import IdempotencyMiddleware, _LazyRedis


class FakeRedis:
    """异步 Redis 最小子集（get/set-nx/delete），键带过期时间戳。"""

    def __init__(self, broken: bool = False):
        self.store: dict[str, tuple[str, float]] = {}
        self.broken = broken
        self.calls: list[str] = []

    async def _check(self):
        if self.broken:
            raise ConnectionError("redis down")

    async def get(self, key: str):
        await self._check()
        item = self.store.get(key)
        if item is None:
            return None
        value, expires = item
        if expires < time.monotonic():
            self.store.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: str, nx: bool = False, ex: int = 0):
        await self._check()
        self.calls.append(key)
        if nx and key in self.store:
            v, exp = self.store[key]
            if exp >= time.monotonic():
                return False
        self.store[key] = (value, time.monotonic() + ex)
        return True

    async def delete(self, key: str):
        await self._check()
        self.store.pop(key, None)


class _InlineRedis(_LazyRedis):
    def __init__(self, client):
        self._client = client  # noqa: SLF001 — 测试注入

    async def client(self):
        return self._client


def _build_app(redis_client):
    app = FastAPI()
    app.add_middleware(IdempotencyMiddleware, redis=_InlineRedis(redis_client))
    counter = {"n": 0}

    @app.post("/echo")
    async def echo(payload: dict):
        counter["n"] += 1
        await asyncio.sleep(0.05)  # 给并发单飞留出竞态窗口
        return {"n": counter["n"], "echo": payload}

    return app, counter


def _client(redis_client):
    app, counter = _build_app(redis_client)
    return TestClient(app), counter


class TestReplay:
    def test_same_key_replays_first_response(self):
        redis = FakeRedis()
        client, counter = _client(redis)
        r1 = client.post("/echo", json={"x": 1}, headers={"Idempotency-Key": "k-1"})
        r2 = client.post("/echo", json={"x": 1}, headers={"Idempotency-Key": "k-1"})
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["n"] == 1
        assert r2.json() == r1.json(), "重放必须返回首次响应体"
        assert r2.headers.get("Idempotent-Replay") == "true"
        assert counter["n"] == 1, "同 key 重放不得再次命中上游"

    def test_different_key_processes_again(self):
        redis = FakeRedis()
        client, counter = _client(redis)
        client.post("/echo", json={"x": 1}, headers={"Idempotency-Key": "k-a"})
        r2 = client.post("/echo", json={"x": 1}, headers={"Idempotency-Key": "k-b"})
        assert r2.json()["n"] == 2
        assert counter["n"] == 2

    def test_no_header_zero_overhead_passthrough(self):
        redis = FakeRedis()
        client, counter = _client(redis)
        r = client.post("/echo", json={"x": 1})
        assert r.status_code == 200
        assert counter["n"] == 1
        assert redis.store == {}, "无头请求不得触碰幂等存储"

    def test_key_scoped_to_path_and_body(self):
        redis = FakeRedis()
        client, counter = _client(redis)
        client.post("/echo", json={"x": 1}, headers={"Idempotency-Key": "same"})
        r2 = client.post("/echo", json={"x": 2}, headers={"Idempotency-Key": "same"})
        assert r2.json()["n"] == 2, "同 key 不同 body 必须视为不同请求"


class TestSingleflight:
    def test_concurrent_same_key_hits_upstream_once(self):
        redis = FakeRedis()
        app, counter = _build_app(redis)
        client = TestClient(app)

        async def one_call():
            # TestClient 是同步的 —— 用线程池模拟并发到达
            await asyncio.sleep(0)

        import threading

        results: list = []
        barrier = threading.Barrier(5)

        def worker():
            barrier.wait()
            results.append(
                client.post("/echo", json={"x": 1}, headers={"Idempotency-Key": "conc"}).json()
            )

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 5
        assert counter["n"] <= 2, (
            f"并发同 key 单飞失效：上游命中 {counter['n']} 次（results={results}）"
        )
        bodies = {r["n"] for r in results}
        assert all(b in (1, 2) for b in bodies)


class TestTtl:
    def test_expired_record_reprocesses(self, monkeypatch):
        import app.core.idempotency as idem_mod

        redis = FakeRedis()
        client, counter = _client(redis)
        monkeypatch.setattr(idem_mod, "RESPONSE_TTL_S", 1)
        client.post("/echo", json={"x": 1}, headers={"Idempotency-Key": "ttl"})
        # 人为过期
        for key, (value, _exp) in list(redis.store.items()):
            if key.startswith("idem:resp:"):
                redis.store[key] = (value, time.monotonic() - 0.01)
        r2 = client.post("/echo", json={"x": 1}, headers={"Idempotency-Key": "ttl"})
        assert r2.headers.get("Idempotent-Replay") is None
        assert r2.json()["n"] == 2


class TestFailOpen:
    def test_redis_down_processes_normally(self):
        redis = FakeRedis(broken=True)
        client, counter = _client(redis)
        r = client.post("/echo", json={"x": 1}, headers={"Idempotency-Key": "down"})
        assert r.status_code == 200
        assert r.json()["n"] == 1

    def test_non_json_post_bypasses(self):
        redis = FakeRedis()
        app = FastAPI()
        app.add_middleware(IdempotencyMiddleware, redis=_InlineRedis(redis))

        @app.post("/upload-form")
        async def upload_form():
            return {"ok": True}

        client = TestClient(app)
        r = client.post(
            "/upload-form",
            content=b"binary",
            headers={"Idempotency-Key": "k", "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 200
        assert redis.store == {}


class TestLazyRedis:
    def test_lazy_redis_smoke(self):
        assert isinstance(_LazyRedis(), _LazyRedis)
