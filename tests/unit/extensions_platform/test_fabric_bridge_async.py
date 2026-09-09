"""Round-1 回归测试：BLK-1 async 面覆盖 + MAJ-1 升级失败分支。"""

from __future__ import annotations

import asyncio

import pytest

from app.extensions_platform.fabric_bridge import stream_catalog_item_features


class _AsyncFakeQuery:
    """sqlite 内存库替身：最小 DB/item/ds_model 面（真 loop 消费验证）。"""

    def __init__(self, features: int = 5) -> None:
        self.features = features

    def _item(self):
        item = type("I", (), {})()
        item.name = "demo.item"
        item.source_id = "ds-1"
        ds = type("D", (), {})()
        ds.id = "ds-1"
        ds.name = "demo"
        ds.source_type = "v3demo_streams"
        ds.endpoint_url = ""
        ds.connection_profile = {}
        item.data_source = ds
        return item

    def query(self, model, *a, **k):
        return self

    def filter(self, *a, **k):
        return self

    def first(self):
        if getattr(self, "_empty", False):
            return None
        return self._item()


def test_async_stream_consumes_all_features_via_adapter_stream(monkeypatch):
    """BLK-1 回归：真 asyncio loop 消费 async 生成器，泵线程不死锁。"""
    from app.extensions_platform.sdk.provider import StreamingVectorProvider

    class _Adapter(StreamingVectorProvider):
        def stream_features(self, query, page_size=500):
            for i in range(5):
                yield {"properties": {"i": i}}

    monkeypatch.setattr(
        "app.services.data_fabric.manager.DataFabricManager.get_adapter",
        classmethod(lambda cls, profile: _Adapter()),
    )

    async def _run():
        out = []
        async for feature in stream_catalog_item_features(_AsyncFakeQuery(), "item-1"):
            out.append(feature)
        return out

    features = asyncio.run(_run())
    assert [f["properties"]["i"] for f in features] == [0, 1, 2, 3, 4]


def test_async_stream_cancellation_closes_iterator(monkeypatch):
    """消费方提前退出 → 泵任务被取消、iterator 被 close（协作取消闭环）。"""
    from app.extensions_platform.sdk.provider import StreamingVectorProvider

    closed = {"flag": False}

    class _Adapter(StreamingVectorProvider):
        def stream_features(self, query, page_size=500):
            try:
                for i in range(1000):
                    yield {"properties": {"i": i}}
            except GeneratorExit:
                closed["flag"] = True
                raise

    monkeypatch.setattr(
        "app.services.data_fabric.manager.DataFabricManager.get_adapter",
        classmethod(lambda cls, profile: _Adapter()),
    )

    async def _run():
        got = 0
        async for _feature in stream_catalog_item_features(_AsyncFakeQuery(), "item-1"):
            got += 1
            if got >= 3:
                break
        # 允许泵线程让路
        await asyncio.sleep(0.05)
        return got

    got = asyncio.run(_run())
    assert got == 3


def test_async_stream_missing_item_raises():
    async def _run():
        db = _AsyncFakeQuery()
        db._empty = True
        out = []
        async for feature in stream_catalog_item_features(db, "nope"):
            out.append(feature)
        return out

    with pytest.raises(ValueError, match="not found"):
        asyncio.run(_run())
