"""Fabric bridge 分发语义（ADR-0119 / Wave 12）。

钉死：mixin 探测驱动的分发（有 → 流式/tile/raster；无 → 诚实降级或
typed UnsupportedSourceError）；既有查询路径不经过本模块（additive）。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.extensions_platform.fabric_bridge import (
    get_raster_window,
    get_tile,
    iter_stream_features,
)
from app.extensions_platform.sdk.provider import (
    RasterWindowProvider,
    StreamingVectorProvider,
    TileProvider,
)
from app.services.data_fabric.errors import UnsupportedSourceError


class _PlainAdapter:
    """无任何 mixin（V2 基线形态）。"""

    def query(self, dataset_id: str, query_spec: Any = None):
        result = type("R", (), {})()
        result.features = [{"properties": {"src": "sync"}}]
        return result


class _StreamingAdapter(StreamingVectorProvider):
    def stream_features(self, query: dict[str, Any], page_size: int = 500):
        for i in range(3):
            yield {"properties": {"src": "stream", "i": i}}


class _TileAdapter(TileProvider):
    def get_tile(self, z: int, x: int, y: int, **params: Any):
        from app.extensions_platform.sdk.provider import TilePayload

        return TilePayload(data=b"PNG", content_type="image/png")


class _RasterAdapter(RasterWindowProvider):
    def get_raster_window(self, bbox, crs, width, height):
        return {"data_b64": "AAAA", "shape": [height, width], "crs": crs}


def test_stream_features_with_mixin_is_real_stream():
    events = list(iter_stream_features(_StreamingAdapter(), "ds.x", None))
    assert [e["properties"]["i"] for e in events] == [0, 1, 2]


def test_stream_features_without_mixin_degrades_to_sync_query():
    """无 mixin → 诚实降级（sync query，逐 feature 产出；语义不变）。"""
    events = list(iter_stream_features(_PlainAdapter(), "ds.x", None))
    assert events == [{"properties": {"src": "sync"}}]


def test_tile_dispatch_without_mixin_typed_rejected():
    with pytest.raises(UnsupportedSourceError, match="TileProvider"):
        get_tile(_PlainAdapter(), 1, 2, 3)


def test_tile_dispatch_with_mixin():
    payload = get_tile(_TileAdapter(), 1, 2, 3)
    assert payload.data == b"PNG"


def test_raster_window_dispatch_without_mixin_typed_rejected():
    with pytest.raises(UnsupportedSourceError, match="RasterWindowProvider"):
        get_raster_window(_PlainAdapter(), (0, 0, 1, 1), "EPSG:4326", 4, 4)


def test_raster_window_dispatch_with_mixin():
    window = get_raster_window(_RasterAdapter(), (0, 0, 1, 1), "EPSG:4326", 4, 4)
    assert window["shape"] == [4, 4]


def test_worker_proxy_adapter_reaches_tile_dispatch():
    """动态代理类（Wave 10）能被本分发面消费——端到端形态闭环。"""
    from app.extensions_platform.worker.projection_v3 import make_worker_provider_class


    class _FakeWorker:
        _namespace = "demo"
        _call_timeout_s = 5.0

        def call(self, tool, args):
            assert tool == "provider:demo_src:get_tile"

            return {"data_hex": b"X".hex(), "content_type": "image/png", "metadata": {}}

    cls = make_worker_provider_class(
        _FakeWorker(),
        {"source_type": "demo_src", "description": "", "aliases": [], "mixins": ["tiles"]},
    )
    from app.schemas.data_fabric_schema import ConnectionProfile

    adapter = cls(ConnectionProfile(source_type="demo_src"))
    # issubclass 探测 → 分发面识别 tiles 能力。
    from app.extensions_platform.sdk.provider import extended_provider_capabilities

    assert extended_provider_capabilities(cls) == ["tiles"]
    payload = get_tile(adapter, 1, 2, 3)
    assert payload.data == b"X"
