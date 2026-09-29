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

import contextlib
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


def resolve_stream_target(db: Any, item_id: str) -> tuple[Any, str]:
    """目录条目 → (adapter, dataset_name)（同步 ORM，须在 manager-loop 上调）。

    W12 抽取：route 的流式预检（鉴权后）与既有 stream_catalog_item_features
    共用同一解析路径 —— adapter 构建/能力探测只有一个事实源。
    """
    from app.models.data_fabric import CatalogItemModel, DataSourceModel
    from app.services.data_fabric.manager import DataFabricManager

    item = db.query(CatalogItemModel).filter(CatalogItemModel.id == item_id).first()
    if item:
        ds_model = item.data_source or db.query(DataSourceModel).filter(
            DataSourceModel.id == item.source_id
        ).first()
    else:
        raise ValueError(f"Catalog item '{item_id}' not found")
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
    return adapter, item.name


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
    if cancel_token is not None:
        cancel_token.raise_if_cancelled()
    adapter, dataset_name = resolve_stream_target(db, item_id)
    if cancel_token is not None:
        cancel_token.raise_if_cancelled()
    async for feature in stream_features_from_adapter(
        adapter, dataset_name, query_spec, cancel_token=cancel_token, page_size=page_size
    ):
        yield feature


async def stream_features_from_adapter(
    adapter: Any,
    dataset_name: str,
    query_spec: Any = None,
    cancel_token: Optional[Any] = None,
    page_size: int = 500,
) -> AsyncIterator[dict[str, Any]]:
    """能力感知流式泵（route 的 NDJSON 消费面）。

    stream_features 的翻页发生在 adapter 内部（可能阻塞）→ to_thread 队列。
    """
    import asyncio

    iterator = iter_stream_features(adapter, dataset_name, query_spec, page_size)

    async def _gen() -> AsyncIterator[dict[str, Any]]:
        import concurrent.futures
        import contextlib
        import threading

        loop = asyncio.get_running_loop()
        # Round-2 C-1 修复（三合一）：
        # 1) **有界队列**（maxsize=64）+ 泵线程阻塞等 put 完成 → 真背压，
        #    宿主内存上界 = 64 条 feature（不再整体物化数据集）；
        # 2) 泵线程 try/finally 兜底入队哨兵/异常 → 消费方绝不悬挂；
        # 3) 收尾先 iterator.close()（触发上游 stream_cancel），pump_task
        #    用 stop 事件 + 有界等待收束（线程不泄漏、不无限等）。
        queue: "asyncio.Queue[Any]" = asyncio.Queue(maxsize=64)
        stop = threading.Event()
        _DONE = object()
        pump_error: list[BaseException] = []

        def _drain() -> None:
            try:
                for feature in iterator:
                    if stop.is_set():
                        return
                    fut = asyncio.run_coroutine_threadsafe(queue.put(feature), loop)
                    while not fut.done():
                        if stop.is_set():
                            fut.cancel()
                            return
                        try:
                            fut.result(timeout=0.25)
                        except concurrent.futures.TimeoutError:
                            continue
            except BaseException as exc:  # noqa: BLE001 - 异常转交消费方
                pump_error.append(exc)
            finally:
                drain_done.set()
                with contextlib.suppress(Exception):
                    loop.call_soon_threadsafe(queue.put_nowait, _DONE)

        drain_done = threading.Event()
        pump_task = asyncio.create_task(asyncio.to_thread(_drain))
        try:
            while True:
                if cancel_token is not None:
                    cancel_token.raise_if_cancelled()
                value = await queue.get()
                if value is _DONE:
                    if pump_error:
                        raise pump_error[0]
                    break
                yield value
        finally:
            # 收尾顺序（W12 确定性取消）：先 stop + **有界等待泵线程真正
            # 退出**（drain_done；to_thread 的 task.cancel 只取消包装协程，
            # 线程还在跑），再 close 上游迭代器 —— close 与 next() 并发会
            # 触发 "generator already executing"（被吞 → 上游协作取消退化
            # 成尽力而为）。线程的 stop 检查周期 0.25s，正常 <0.5s 收束；
            # adapter 卡死则 5s 上限后降级为尽力而为。
            stop.set()
            pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(asyncio.to_thread(drain_done.wait), timeout=5)
            close = getattr(iterator, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()

    # W12：显式 aclose —— `async for` 在 GeneratorExit（客户端断开 →
    # Starlette 取消响应生成器）时**不会**自动关闭内层 async generator，
    # _gen 的收尾（停泵/close 上游）会被拖到 GC/loop 关机才跑，上游协作
    # 取消退化成不确定行为。finally 里 await aclose 让取消链同步收敛。
    _inner = _gen()
    try:
        async for feature in _inner:
            yield feature
    finally:
        with contextlib.suppress(Exception):
            await _inner.aclose()
