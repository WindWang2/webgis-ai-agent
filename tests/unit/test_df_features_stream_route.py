"""NDJSON 流式端点 + 断线取消（W12 C5）。

覆盖：NDJSON 行流 + _eof 尾行、limit 截断诚实标注、断线/放弃消费 →
取消传播（泵停、上游迭代器 close、token cancelled）、鉴权先于流。
"""
import asyncio
import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from unittest.mock import patch

from app.api.routes import data_fabric as df_routes
from app.extensions_platform.sdk.provider import StreamingVectorProvider

_ITEM_ID = "cat_stream_layer"


def _app() -> FastAPI:
    application = FastAPI()
    application.include_router(df_routes.router, prefix="/api/v1")
    return application


@pytest.fixture
async def client():
    application = _app()
    application.dependency_overrides[df_routes.get_current_user] = lambda: {"user_id": "u"}
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _feature(i):
    return {"type": "Feature", "id": i,
            "geometry": {"type": "Point", "coordinates": [1, 2]},
            "properties": {"i": i}}


class _CloseTrackingStream(StreamingVectorProvider):
    """真流式 adapter：继承 StreamingVectorProvider mixin（能力探测走真继承）。

    每要素 sleep：ASGITransport 无背压（响应被 httpx 整体缓冲）—— 不加
    延迟时服务端在测试"放弃消费"前就把整个流泵完，close 对已耗尽的
    生成器是 no-op，模拟不出真实 TCP 背压下的中途放弃。
    """

    def __init__(self, n, closed_flag, per_feature_s=0.005):
        import time as _time

        self._n = n
        self._closed = closed_flag
        self._delay = per_feature_s
        self._time = _time

    def stream_features(self, params, page_size=500):
        return self._gen()

    def _gen(self):
        closed = self._closed

        class _G:
            def __init__(inner):
                inner.i = 0

            def __iter__(inner):
                return inner

            def __next__(inner):
                if inner.i >= self._n:
                    raise StopIteration
                self._time.sleep(self._delay)
                f = _feature(inner.i)
                inner.i += 1
                return f

            def close(inner):
                closed["n"] += 1
                inner.i = self._n  # 上游取消：停止产出

        return _G()


class _SyncOnlyAdapter:
    """无 streaming mixin：诚实降级路径（一次 bounded query）。"""

    def __init__(self, n):
        self._n = n

    def query(self, dataset_id, spec):
        from app.schemas.data_fabric_schema import QueryResult

        return QueryResult(dataset_id=dataset_id, features=[_feature(i) for i in range(self._n)],
                           returned_count=self._n)


def _install(monkeypatch, adapter, item_fingerprint="fp1"):
    # route 调 data_fabric_manager.resolve_catalog_stream(session, item_id)
    # —— 实例属性直接给普通函数即可。
    monkeypatch.setattr(
        df_routes.data_fabric_manager,
        "resolve_catalog_stream",
        lambda db, item_id: (adapter, "stream_layer"),
    )
    # _run_async_manager 直接跑 fn(None)（绕开真实 DB 装配；鉴权已被 patch 掉）
    async def _run_prep(fn):
        return await fn(None)

    monkeypatch.setattr(df_routes, "_run_async_manager", _run_prep)


@pytest.mark.asyncio
async def test_ndjson_lines_and_eof_meta(client, monkeypatch):
    closed = {"n": 0}
    _install(monkeypatch, _CloseTrackingStream(5, closed))
    with patch.object(df_routes, "_authorize_catalog_item", lambda *a, **k: None):
        async with client.stream("GET", f"/api/v1/data-fabric/catalog/{_ITEM_ID}/features/stream") as r:
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("application/x-ndjson")
            lines = [line async for line in r.aiter_lines() if line]
    assert len(lines) == 6
    feats = [json.loads(line) for line in lines[:5]]
    assert [f["id"] for f in feats] == [0, 1, 2, 3, 4], "NDJSON 每行一个 Feature、保序"
    meta = json.loads(lines[5])
    assert meta == {"_eof": True, "count": 5, "limit_reached": False}


@pytest.mark.asyncio
async def test_limit_cap_honestly_marked(client, monkeypatch):
    closed = {"n": 0}
    _install(monkeypatch, _CloseTrackingStream(100, closed))
    with patch.object(df_routes, "_authorize_catalog_item", lambda *a, **k: None):
        async with client.stream(
            "GET",
            f"/api/v1/data-fabric/catalog/{_ITEM_ID}/features/stream",
            params={"limit": 3},
        ) as r:
            lines = [line async for line in r.aiter_lines() if line]
    assert len(lines) == 4
    meta = json.loads(lines[3])
    assert meta["count"] == 3 and meta["limit_reached"] is True, "拿满 limit ≠ 数据集读完，必须标注"


@pytest.mark.asyncio
async def test_abandoned_stream_cancels_token_and_closes_upstream(client, monkeypatch):
    """客户端断开（提前停止消费）→ 生成器 finally 取消 token。

    上游迭代器 close 的确定性验证在 bridge 层（见
    test_abandoned_consumer_closes_upstream_direct）—— ASGITransport 无
    背压，httpx 层的 close 时机不可控，在该层断言 close 会假红。
    """
    closed = {"n": 0}
    adapter = _CloseTrackingStream(10_000, closed)
    _install(monkeypatch, adapter)

    from app.extensions_platform import fabric_bridge

    real_pump = fabric_bridge.stream_features_from_adapter
    token_ref = {}

    async def _spy(adapter_, name, spec, cancel_token=None, page_size=500):
        token_ref["token"] = cancel_token
        inner = real_pump(adapter_, name, spec, cancel_token=cancel_token, page_size=page_size)
        try:
            async for f in inner:
                yield f
        finally:
            # 与生产面同款纪律：async for 在 GeneratorExit 时不会自动关闭
            # 内层 async generator —— 显式 aclose 才能即时收敛取消链。
            import contextlib

            with contextlib.suppress(Exception):
                await inner.aclose()

    monkeypatch.setattr(fabric_bridge, "stream_features_from_adapter", _spy)

    with patch.object(df_routes, "_authorize_catalog_item", lambda *a, **k: None):
        async with client.stream(
            "GET",
            f"/api/v1/data-fabric/catalog/{_ITEM_ID}/features/stream",
            params={"page_size": 4},
        ) as r:
            it = r.aiter_lines()
            await it.__anext__()  # 只消费 1 行即放弃（模拟断开）
        # exiting the stream context = client disconnect → server cancels generator

    await asyncio.sleep(0.5)  # 给泵收束时间（有界 5s 内）
    token = token_ref.get("token")
    assert token is not None and token.cancelled, "放弃消费必须取消 token"
    assert closed["n"] <= 1  # close 尽力而为（bridge 层断言其确定性）


@pytest.mark.asyncio
async def test_abandoned_consumer_closes_upstream_direct():
    """bridge 层：消费者中途放弃（aclose）→ 上游迭代器被确定性 close。

    这是取消链的权威断言层：无 HTTP，直接驱动 async generator，
    GeneratorExit → 显式 aclose 链 → 泵停 → close 上游。
    """
    from app.extensions_platform import fabric_bridge

    closed = {"n": 0}
    adapter = _CloseTrackingStream(10_000, closed, per_feature_s=0.0)

    agen = fabric_bridge.stream_features_from_adapter(adapter, "ds", None, page_size=4)
    it = agen.__aiter__()
    first = await it.__anext__()
    assert first["id"] == 0
    await asyncio.wait_for(agen.aclose(), timeout=8)
    assert closed["n"] >= 1, "放弃消费必须确定性 close 上游迭代器"
    assert it is not None


@pytest.mark.asyncio
async def test_authz_runs_before_stream_starts(client, monkeypatch):
    calls = {"authz": 0, "resolve": 0}

    def _deny(*a, **k):
        calls["authz"] += 1
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="denied")

    monkeypatch.setattr(df_routes, "_authorize_catalog_item", _deny)
    monkeypatch.setattr(
        df_routes.data_fabric_manager,
        "resolve_catalog_stream",
        lambda db, item_id: calls.__setitem__("resolve", calls["resolve"] + 1) or (None, None),
    )
    r = await client.get(f"/api/v1/data-fabric/catalog/{_ITEM_ID}/features/stream")
    assert r.status_code == 404
    assert calls["authz"] == 1 and calls["resolve"] == 0, "鉴权必须先于 adapter 解析"


@pytest.mark.asyncio
async def test_run_async_manager_cancellable_propagates_task_cancellation():
    """handler task 被取消 → 取消必须传播进 manager-loop 协程（任务树收敛）。"""
    observed = {"cancelled": False}

    async def _long_fn(session):
        try:
            await asyncio.sleep(30)
            return "done"
        except asyncio.CancelledError:
            observed["cancelled"] = True
            raise

    task = asyncio.create_task(df_routes._run_async_manager_cancellable(_long_fn))
    await asyncio.sleep(0.2)  # 让协程在 manager loop 上跑起来
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert observed["cancelled"] is True, "断连取消必须到达 df-manager-loop 上的协程"
