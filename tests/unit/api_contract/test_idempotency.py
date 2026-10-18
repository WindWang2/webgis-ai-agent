"""V9 契约基石：Idempotency-Key 中间件测试（ADR-0138 / P5）。

四类门禁：
1. 重放一致性：同 key 二次请求返回首次响应 + Idempotent-Replay 头；
2. 并发同 key 单飞：N 并发只命中上游一次；
3. TTL 过期：记录过期后重新处理；
4. Redis 不可用 fail-open：请求正常处理，不 5xx。
"""
from __future__ import annotations

import asyncio
import threading
import time

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.core.idempotency import IdempotencyMiddleware, _LazyRedis


class FakeRedis:
    """异步 Redis 最小子集（get/set-nx/delete），键带过期时间戳。"""

    def __init__(self, broken: bool = False):
        self.store: dict[str, tuple[str, float]] = {}
        self.broken = broken
        self.calls: list[str] = []
        # #1664：TestClient 多线程并发下保证 set(nx=True) 的「检查-置位」原子性
        # （非原子的 check-then-set 会让两个线程同时拿到单飞锁，单飞断言失去
        # 确定性）。临界区内无 await，不阻塞 event loop。
        self._lock = threading.Lock()

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
        with self._lock:
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

    async def eval(self, _script: str, _numkeys: int, key: str, token: str):
        """_RELEASE_LOCK_LUA 的比较删除语义。"""
        await self._check()
        item = self.store.get(key)
        if item is not None and item[0] == token:
            self.store.pop(key, None)
            return 1
        return 0


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
        headers = {"Idempotency-Key": "k-1", "Authorization": "Bearer t"}
        r1 = client.post("/echo", json={"x": 1}, headers=headers)
        r2 = client.post("/echo", json={"x": 1}, headers=headers)
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["n"] == 1
        assert r2.json() == r1.json(), "重放必须返回首次响应体"
        assert r2.headers.get("Idempotent-Replay") == "true"
        assert counter["n"] == 1, "同 key 重放不得再次命中上游"

    def test_different_key_processes_again(self):
        redis = FakeRedis()
        client, counter = _client(redis)
        headers = {"Authorization": "Bearer t"}
        client.post("/echo", json={"x": 1}, headers={**headers, "Idempotency-Key": "k-a"})
        r2 = client.post("/echo", json={"x": 1}, headers={**headers, "Idempotency-Key": "k-b"})
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
        headers = {"Authorization": "Bearer t"}
        client.post("/echo", json={"x": 1}, headers={**headers, "Idempotency-Key": "same"})
        r2 = client.post("/echo", json={"x": 2}, headers={**headers, "Idempotency-Key": "same"})
        assert r2.json()["n"] == 2, "同 key 不同 body 必须视为不同请求"


class TestSingleflight:
    def test_concurrent_same_key_hits_upstream_once(self):
        redis = FakeRedis()
        app, counter = _build_app(redis)
        client = TestClient(app)

        results: list = []
        barrier = threading.Barrier(5)

        def worker():
            barrier.wait()
            results.append(
                client.post(
                    "/echo",
                    json={"x": 1},
                    headers={"Idempotency-Key": "conc", "Authorization": "Bearer t"},
                ).json()
            )

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 5
        # #1664：合同是「N 并发只命中上游一次」—— 恰好 1 次，不是「≤2 次」。
        # 锁释放与响应存储的「先存后放」排序（app/core/idempotency.py
        # _store_response）保证等待者在锁消失前必读到重放记录；<= 2 会把
        # 「单个等待者穿透」这一主要回归形态完全放绿。
        assert counter["n"] == 1, (
            f"并发同 key 单飞失效：上游命中 {counter['n']} 次（results={results}）"
        )
        bodies = {r["n"] for r in results}
        assert bodies == {1}, f"所有并发响应必须重放同一首次响应：{bodies}"


class TestTtl:
    def test_expired_record_reprocesses(self, monkeypatch):
        import app.core.idempotency as idem_mod

        redis = FakeRedis()
        client, counter = _client(redis)
        monkeypatch.setattr(idem_mod, "RESPONSE_TTL_S", 1)
        client.post("/echo", json={"x": 1}, headers={
            "Idempotency-Key": "ttl", "Authorization": "Bearer t"})
        # 人为过期
        for key, (value, _exp) in list(redis.store.items()):
            if key.startswith("idem:resp:"):
                redis.store[key] = (value, time.monotonic() - 0.01)
        r2 = client.post("/echo", json={"x": 1}, headers={
            "Idempotency-Key": "ttl", "Authorization": "Bearer t"})
        assert r2.headers.get("Idempotent-Replay") is None
        assert r2.json()["n"] == 2


class TestFailOpen:
    def test_redis_down_processes_normally(self):
        redis = FakeRedis(broken=True)
        client, counter = _client(redis)
        r = client.post("/echo", json={"x": 1}, headers={
            "Idempotency-Key": "down", "Authorization": "Bearer t"})
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


class TestReplayHeaders:
    def test_replay_restores_stored_headers(self):
        """任务书 P5：重放返回首次响应（含状态码与头）。"""
        redis = FakeRedis()
        app = FastAPI()
        app.add_middleware(IdempotencyMiddleware, redis=_InlineRedis(redis))

        @app.post("/hdr")
        async def hdr():
            return JSONResponse(
                {"ok": True}, headers={"X-Custom-Trace": "t-123", "X-Request-ID": "r-1"}
            )

        client = TestClient(app)
        r1 = client.post("/hdr", json={}, headers={
            "Idempotency-Key": "hk", "Authorization": "Bearer t"})
        r2 = client.post("/hdr", json={}, headers={
            "Idempotency-Key": "hk", "Authorization": "Bearer t"})
        assert r1.headers.get("X-Custom-Trace") == "t-123"
        assert r2.headers.get("X-Custom-Trace") == "t-123"
        assert r2.headers.get("Idempotent-Replay") == "true"
        # 逐跳/长度类头不参与重放（由框架重算）
        assert "transfer-encoding" not in {k.lower() for k in r2.headers}


class TestCallerScoping:
    """security F-05：重放缓存必须按调用方身份隔离。"""

    def test_other_caller_does_not_get_first_callers_response(self):
        redis = FakeRedis()
        client, counter = _client(redis)
        r1 = client.post("/echo", json={"x": 1}, headers={
            "Idempotency-Key": "shared", "Authorization": "Bearer alice"})
        r2 = client.post("/echo", json={"x": 1}, headers={
            "Idempotency-Key": "shared", "Authorization": "Bearer mallory"})
        r3 = client.post("/echo", json={"x": 1}, headers={"Idempotency-Key": "shared"})
        assert r1.json()["n"] == 1
        assert r2.headers.get("Idempotent-Replay") is None
        assert r2.json()["n"] == 2
        assert r3.headers.get("Idempotent-Replay") is None
        assert counter["n"] == 3
        # 同一调用方仍可重放
        r4 = client.post("/echo", json={"x": 1}, headers={
            "Idempotency-Key": "shared", "Authorization": "Bearer alice"})
        assert r4.headers.get("Idempotent-Replay") == "true"
        assert r4.json()["n"] == 1

    def test_auth_paths_never_cached(self):
        redis = FakeRedis()
        app = FastAPI()
        app.add_middleware(IdempotencyMiddleware, redis=_InlineRedis(redis))

        @app.post("/api/v1/auth/login")
        async def login(payload: dict):
            return {"access_token": "secret"}

        client = TestClient(app)
        client.post("/api/v1/auth/login", json={"u": 1}, headers={"Idempotency-Key": "k"})
        assert redis.store == {}

    def test_error_responses_not_cached(self):
        redis = FakeRedis()
        app = FastAPI()
        app.add_middleware(IdempotencyMiddleware, redis=_InlineRedis(redis))
        n = {"c": 0}

        @app.post("/fail")
        async def fail(payload: dict):
            n["c"] += 1
            return JSONResponse({"err": n["c"]}, status_code=409)

        client = TestClient(app)
        client.post("/fail", json={}, headers={
            "Idempotency-Key": "f", "Authorization": "Bearer t"})
        r2 = client.post("/fail", json={}, headers={
            "Idempotency-Key": "f", "Authorization": "Bearer t"})
        assert r2.headers.get("Idempotent-Replay") is None
        assert n["c"] == 2


class TestAnonymousCallers:
    """security F-05 加固（#1640）：无凭证调用方不读不存重放缓存。

    凭证摘要只混入 Authorization / X-Session-Token；两者均缺席时摘要恒定，
    匿名调用方之间零隔离 —— 同 key + 同 body 即可取回他人的首次响应
    （匿名会话 owner_token 即会话能力令牌，SEC-08）。此类调用方直接放行。
    """

    def test_anonymous_callers_share_no_replay_cache(self):
        redis = FakeRedis()
        client, counter = _client(redis)
        r1 = client.post("/echo", json={"x": 1}, headers={"Idempotency-Key": "anon"})
        r2 = client.post("/echo", json={"x": 1}, headers={"Idempotency-Key": "anon"})
        assert r1.status_code == 200
        assert r1.json()["n"] == 1
        # 第二个匿名调用方拿到的是新处理的响应，不是首个调用方的重放
        assert r2.headers.get("Idempotent-Replay") is None
        assert r2.json()["n"] == 2
        assert counter["n"] == 2
        assert not [k for k in redis.store if k.startswith("idem:resp:")], (
            "无凭证调用方的响应不得写入重放缓存"
        )

    def test_session_token_caller_still_replays(self):
        """#1640 验收 3：带 X-Session-Token 的匿名调用方行为不变（按凭证摘要隔离）。"""
        redis = FakeRedis()
        client, counter = _client(redis)
        headers = {"Idempotency-Key": "sess", "X-Session-Token": "s-1"}
        r1 = client.post("/echo", json={"x": 1}, headers=headers)
        r2 = client.post("/echo", json={"x": 1}, headers=headers)
        assert r1.json()["n"] == 1
        assert r2.headers.get("Idempotent-Replay") == "true"
        assert r2.json() == r1.json()
        # 不同 session token → 不同摘要 → 不重放（隔离语义不回归）
        r3 = client.post("/echo", json={"x": 1}, headers={
            "Idempotency-Key": "sess", "X-Session-Token": "s-2"})
        assert r3.headers.get("Idempotent-Replay") is None
        assert r3.json()["n"] == 2
        assert counter["n"] == 2
