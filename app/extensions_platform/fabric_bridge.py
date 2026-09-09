"""Data Fabric bridge：V2/V3 provider mixin 的生产分发面（ADR-0119 / Wave 12）。

V2 给 SDK 增加了 ``StreamingVectorProvider`` / ``TileProvider`` /
``RasterWindowProvider`` mixin，但分发未接（limitation）。本模块是
**additive** 的分发层：

- 探测：``extended_provider_capabilities``（issubclass，真继承才报告）；
- ``iter_stream_features``：有 mixin → 流式；无 mixin → 诚实降级为
  既有 sync ``query`` 翻页（degraded 标注，不虚假宣称流式）；
- ``get_tile`` / ``get_raster_window``：有 mixin → 分发；无 → typed
  UnsupportedSourceError（绝不静默回退成别的行为）。

入口：``DataFabricManager.stream_catalog_item_features``（additive 方法，
既有查询路径逐字节不变）。
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Iterator, Optional

from .diagnostics import ExtensionPlatformError

logger = logging.getLogger(__name__)


class FabricBridgeError(ExtensionPlatformError):
    """bridge 分发失败（typed）。"""


def _adapter_capabilities(adapter: Any) -> list[str]:
    from .sdk.provider import extended_provider_capabilities

    return extended_provider_capabilities(adapter)


def iter_stream_features(
    adapter: Any,
    dataset_id: str,
    query_spec: Any = None,
    page_size: int = 500,
) -> Iterator[dict[str, Any]]:
    """按 adapter 能力流式产出 GeoJSON Feature。

    - ``StreamingVectorProvider``：真流式（adapter 内部翻页，消费方可
      提前 close = 协作取消）；
    - 无 mixin：降级为一次 sync ``query``（bounded），逐 feature 产出，
      并在日志中标注 degraded——语义仍正确，只是非流式。
    """
    if "streaming_vector" in _adapter_capabilities(adapter):
        query = _query_payload(query_spec)
        yield from adapter.stream_features({"dataset_id": dataset_id, **query}, page_size)
        return
    # 诚实降级：一次 bounded query，逐 feature 产出。
    logger.info(
        "fabric_bridge: adapter %s lacks streaming_vector; degrading to sync query",
        type(adapter).__name__,
    )
    result = adapter.query(dataset_id, query_spec) if query_spec is not None else _bare_query(
        adapter, dataset_id
    )
    features = getattr(result, "features", None) or []
    for feature in features:
        yield feature


def _query_payload(query_spec: Any) -> dict[str, Any]:
    if query_spec is None:
        return {}
    if hasattr(query_spec, "model_dump"):
        return {"query_spec": query_spec.model_dump(mode="json")}
    if isinstance(query_spec, dict):
        return {"query_spec": query_spec}
    return {}


def _bare_query(adapter: Any, dataset_id: str) -> Any:
    from app.schemas.data_fabric_schema import QuerySpec

    return adapter.query(dataset_id, QuerySpec())


def get_tile(adapter: Any, z: int, x: int, y: int, **params: Any):
    """瓦片分发：无 TileProvider mixin → typed UnsupportedSourceError。"""
    from app.services.data_fabric.errors import UnsupportedSourceError

    if "tiles" not in _adapter_capabilities(adapter):
        raise UnsupportedSourceError(
            f"adapter {type(adapter).__name__} does not implement TileProvider"
        )
    return adapter.get_tile(z, x, y, **params)


def get_raster_window(
    adapter: Any,
    bbox: tuple[float, float, float, float],
    crs: str,
    width: int,
    height: int,
) -> dict[str, Any]:
    """栅格窗口分发：无 RasterWindowProvider mixin → typed 拒绝。"""
    from app.services.data_fabric.errors import UnsupportedSourceError

    if "raster_window" not in _adapter_capabilities(adapter):
        raise UnsupportedSourceError(
            f"adapter {type(adapter).__name__} does not implement RasterWindowProvider"
        )
    return dict(adapter.get_raster_window(bbox, crs, width, height) or {})


async def stream_catalog_item_features(
    db: Any,
    item_id: str,
    query_spec: Any = None,
    cancel_token: Optional[Any] = None,
    page_size: int = 500,
) -> AsyncIterator[dict[str, Any]]:
    """目录条目 → 能力感知流式取数（DataFabricManager 的 additive 委托）。

    DB 查询在调用协程（session 非线程安全）；adapter 构建与首个能力探测
    走既有 ``get_adapter``（同步、快）。取消语义与 query_catalog_item_async
    一致：每个产出点之间由消费方驱动，取消在 fetch 前后检查。
    """
    from app.models.data_fabric import CatalogItemModel, DataSourceModel
    from app.services.data_fabric.manager import DataFabricManager

    if cancel_token is not None:
        cancel_token.raise_if_cancelled()
    item = db.query(CatalogItemModel).filter(CatalogItemModel.id == item_id).first()
    if not item:
        raise ValueError(f"Catalog item '{item_id}' not found")
    ds_model = item.data_source or db.query(DataSourceModel).filter(
        DataSourceModel.id == item.source_id
    ).first()
    if not ds_model:
        raise ValueError(f"Parent data source for item '{item_id}' not found")
    from app.schemas.data_fabric_schema import ConnectionProfile

    profile = ConnectionProfile(
        id=ds_model.id,
        name=ds_model.name,
        source_type=ds_model.source_type,
        url=ds_model.endpoint_url,
        options=ds_model.connection_profile.get("options", {}),
        allow_private=ds_model.connection_profile.get("allow_private", False),
    )
    adapter = DataFabricManager.get_adapter(profile)
    if cancel_token is not None:
        cancel_token.raise_if_cancelled()
    import asyncio

    # stream_features 的翻页发生在 adapter 内部（可能阻塞）→ to_thread 队列。
    iterator = iter_stream_features(adapter, item.name, query_spec, page_size)

    async def _gen() -> AsyncIterator[dict[str, Any]]:
        import contextlib

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=64)
        _DONE = object()

        async def _pump() -> None:
            def _drain() -> None:
                for feature in iterator:
                    asyncio.run_coroutine_threadsafe(queue.put(feature), loop).result()

            with contextlib.suppress(Exception):
                _drain()
            await queue.put(_DONE)

        pump_task = asyncio.create_task(_pump())
        try:
            while True:
                if cancel_token is not None:
                    cancel_token.raise_if_cancelled()
                value = await queue.get()
                if value is _DONE:
                    break
                yield value
        finally:
            pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pump_task
            close = getattr(iterator, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()

    async for feature in _gen():
        yield feature
