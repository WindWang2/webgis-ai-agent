"""V3 worker 投影：算法 / 数据 provider / cartography / recipe（ADR-0119 / Wave 10-11）。

宿主侧把握手申报的可序列化声明投影进**权威 registry**（与 in-process
同一投影管线与台账回滚语义）：

- **算法**：描述符 dict → ``AlgorithmDescriptor``（元数据面；执行体 =
  worker 内工具，``tool_candidates`` 引用已投影的 worker 工具）；
- **数据 provider**：动态代理类（继承核心 ABC 七方法契约 + 按握手申报
  动态继承 streaming/tile/raster mixin——``extended_provider_capabilities``
  探测依赖 issubclass，静态类探测不到动态能力）。每个方法一次 RPC，
  pydantic 返回值宿主侧重校验；per-worker 串行锁把 data_fabric 的线程
  池并发**排队**（绝不向消费端泄漏 operation_in_flight，C-6）；
- **cartography / recipe**：payload 本就是 JSON dict → 经
  ``ExtensionContext`` 既有投影路径（零活对象跨进程）。

错误映射（M-7）：worker 侧异常 `{code,message}` → ``DataFabricError``
子类，保住 data_fabric 消费端的熔断与「fetch failed ≠ empty」语义。
"""

from __future__ import annotations

import threading
from typing import Any

from ..diagnostics import DiagnosticCode, ExtensionDiagnostic, ExtensionPlatformError


def _fabric_error_from(exc: ExtensionPlatformError) -> Exception:
    """worker RPC 错误 → DataFabricError 子类（错误映射表，M-7）。"""
    from app.services.data_fabric.errors import (
        DataFabricError,
        SourceUnreachableError,
        UnsupportedSourceError,
    )

    code = exc.diagnostic.code
    message = exc.diagnostic.message
    if code in (DiagnosticCode.WORKER_CALL_TIMEOUT, DiagnosticCode.WORKER_CRASHED):
        return SourceUnreachableError(f"extension worker unreachable: {message}")
    if code == DiagnosticCode.OUTPUT_LIMIT_EXCEEDED:
        return SourceUnreachableError(f"extension worker result over budget: {message}")
    if code in (
        DiagnosticCode.DECLARED_BUT_UNREGISTERED,
        DiagnosticCode.WORKER_MODE_INVALID,
    ):
        return UnsupportedSourceError(message)
    if code == DiagnosticCode.BROKER_DENIED:
        return SourceUnreachableError(f"provider broker channel denied: {message}")
    return DataFabricError(message)


def project_worker_algorithms(
    manifest: Any,
    ledger: Any,
    worker: Any,
    host: Any,
) -> list[str]:
    """握手申报的算法描述符 → AlgorithmRegistry（经台账）。"""
    from app.lib.gis.algorithm_registry import AlgorithmDescriptor, get_algorithm_registry

    projected: list[str] = []
    registry = get_algorithm_registry()
    for descriptor in sorted(worker.algorithms, key=lambda d: d.get("id", "")):
        algo_id = str(descriptor.get("id", ""))
        namespaced = manifest.namespaced_algorithm_id(algo_id)
        if registry.has(namespaced):
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.REGISTRY_PROJECTION_COLLISION,
                    f"algorithm {namespaced!r} already registered",
                    extension_id=manifest.id,
                )
            )
        payload = dict(descriptor)
        # tool_candidates 引用本 worker 投影的工具：本地名规约为宿主投影名
        # （worker.tools 里的名字已是命名空间化形态）。
        offered = {str(t.get("name")) for t in worker.tools}
        candidates = []
        for candidate in payload.get("tool_candidates") or []:
            namespaced_candidate = manifest.namespaced_tool_name(str(candidate))
            candidates.append(namespaced_candidate if namespaced_candidate in offered else candidate)
        payload["tool_candidates"] = candidates
        payload["id"] = namespaced
        payload.setdefault("runtime_status", "native")
        try:
            algo = AlgorithmDescriptor.model_validate(payload)
        except Exception as exc:  # noqa: BLE001 - 归一为投影失败（fail closed）
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.REGISTRY_PROJECTION_FAILED,
                    f"worker algorithm {algo_id!r} descriptor invalid: {exc}",
                    extension_id=manifest.id,
                )
            ) from exc
        try:
            registry.register(algo)
        except Exception as exc:  # noqa: BLE001
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.REGISTRY_PROJECTION_FAILED,
                    f"algorithm {namespaced!r} rejected by AlgorithmRegistry: {exc}",
                    extension_id=manifest.id,
                )
            ) from exc
        ledger.record("algorithm", namespaced, lambda n=namespaced: registry.unregister(n))
        projected.append(namespaced)
    return projected


def make_worker_provider_class(worker: Any, entry: dict[str, Any]) -> type:
    """为握手申报的 worker provider 构造动态代理适配器类。

    - 继承 ``GeospatialDataSourceAdapter``（七方法契约）；
    - 按申报 mixins 动态继承 ``StreamingVectorProvider``/``TileProvider``/
      ``RasterWindowProvider``（``extended_provider_capabilities`` 依赖
      issubclass，必须真继承才能被 data_fabric 探测到，M-7e）；
    - per-instance 串行锁：data_fabric 线程池并发在本类**排队**（C-6），
      绝不向消费端抛 operation_in_flight；
    - 返回值逐方法重校验（pydantic → model_validate）；异常按映射表转
      ``DataFabricError`` 子类。
    """
    from app.extensions_platform.sdk.provider import (
        RasterWindowProvider,
        StreamingVectorProvider,
        TileProvider,
    )
    from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter

    source_type = str(entry["source_type"])
    mixins = set(entry.get("mixins") or [])
    bases: list[type] = [GeospatialDataSourceAdapter]
    if "streaming_vector" in mixins:
        bases.append(StreamingVectorProvider)
    if "tiles" in mixins:
        bases.append(TileProvider)
    if "raster_window" in mixins:
        bases.append(RasterWindowProvider)

    namespace = worker._namespace
    prefix = f"{namespace}_"
    worker_ref = worker
    serial = threading.Lock()
    acquire_timeout = max(worker_ref._call_timeout_s, 1.0)

    def _revalidate(value: Any, model_name: str) -> Any:
        if value is None:
            return value

        from app.schemas import data_fabric_schema

        model = getattr(data_fabric_schema, model_name, None)
        if model is None:
            return value
        try:
            return model.model_validate(value)
        except Exception as exc:  # noqa: BLE001 - 形状漂移 = typed 失败
            from app.services.data_fabric.errors import DataFabricError

            raise DataFabricError(
                f"extension provider returned a malformed {model_name}: {exc}"
            ) from exc

    class WorkerDataProviderAdapter(*bases):  # type: ignore[misc, valid-type]
        """宿主侧 worker provider 代理（七方法 RPC + mixin 动态继承）。

        Round-1 CR-1：每个 RPC 携带 ``_profile``（宿主 ConnectionProfile
        的 JSON 形态）——worker 按 (source_type, profile) 缓存实例，保证
        同 source_type 多连接各自命中正确数据源。
        """

        def __init__(self, connection_profile: Any):
            super().__init__(connection_profile)
            self._serial = serial
            dump = getattr(connection_profile, "model_dump", None)
            self._profile_payload = (
                dump(mode="json") if callable(dump) else None
            )

        def _rpc(self, method: str, **kwargs: Any) -> Any:
            if self._profile_payload is not None:
                kwargs["_profile"] = self._profile_payload
            acquired = serial.acquire(timeout=acquire_timeout)
            if not acquired:
                from app.services.data_fabric.errors import SourceUnreachableError

                raise SourceUnreachableError(
                    f"extension provider endpoint {source_type!r} is saturated "
                    f"(queued > {acquire_timeout}s)"
                )
            try:
                return worker_ref.call(f"provider:{source_type}:{method}", kwargs)
            except ExtensionPlatformError as exc:
                raise _fabric_error_from(exc) from exc
            finally:
                serial.release()

        def probe(self) -> bool:
            return bool(self._rpc("probe"))

        def capabilities(self) -> list[str]:
            return [str(c) for c in (self._rpc("capabilities") or [])]

        def list_datasets(self) -> list[dict[str, Any]]:
            return [dict(d) for d in (self._rpc("list_datasets") or [])]

        def describe(self, dataset_id: str):
            return _revalidate(self._rpc("describe", dataset_id=dataset_id), "DatasetDescriptor")

        def preview(self, dataset_id: str, limit: int = 10) -> dict[str, Any]:
            return dict(self._rpc("preview", dataset_id=dataset_id, limit=limit) or {})

        def query(self, dataset_id: str, query_spec: Any):
            spec = query_spec.model_dump(mode="json") if hasattr(query_spec, "model_dump") else query_spec
            return _revalidate(self._rpc("query", dataset_id=dataset_id, query_spec=spec), "QueryResult")

        def health(self):
            return _revalidate(self._rpc("health"), "DataFabricHealth")

        # ── V3 mixin 方法（经流式/单帧 RPC 代理）────────────────────
        def stream_features(self, query: dict[str, Any], page_size: int = 500):
            if "streaming_vector" not in mixins:
                raise NotImplementedError
            # MAJ-3：流生命周期内持串行锁（与单帧方法互斥，防
            # operation_in_flight 相撞）；错误经映射表转 DataFabricError。
            acquired = serial.acquire(timeout=acquire_timeout)
            if not acquired:
                from app.services.data_fabric.errors import SourceUnreachableError

                raise SourceUnreachableError(
                    f"extension provider endpoint {source_type!r} is saturated "
                    f"(queued > {acquire_timeout}s)"
                )
            kwargs: dict[str, Any] = {"query": dict(query or {}), "page_size": page_size}
            if self._profile_payload is not None:
                kwargs["_profile"] = self._profile_payload
            try:
                events = worker_ref.call_stream(
                    f"provider:{source_type}:stream_features",
                    kwargs,
                )
                for event in events:
                    yield event
            except ExtensionPlatformError as exc:
                raise _fabric_error_from(exc) from exc
            finally:
                serial.release()

        def get_tile(self, z: int, x: int, y: int, **params: Any):
            if "tiles" not in mixins:
                raise NotImplementedError
            from app.extensions_platform.sdk.provider import TilePayload

            payload = self._rpc("get_tile", z=z, x=x, y=y, **params)
            return TilePayload(
                data=bytes.fromhex(payload.get("data_hex", "")),
                content_type=str(payload.get("content_type", "image/png")),
                extent=payload.get("extent"),
                metadata=payload.get("metadata"),
            )

        def get_raster_window(
            self, bbox: tuple[float, float, float, float], crs: str, width: int, height: int
        ) -> dict[str, Any]:
            if "raster_window" not in mixins:
                raise NotImplementedError
            return dict(
                self._rpc(
                    "get_raster_window", bbox=list(bbox), crs=crs, width=width, height=height
                )
                or {}
            )

    WorkerDataProviderAdapter.__name__ = f"WorkerProvider_{source_type[len(prefix):] if source_type.startswith(prefix) else source_type}"
    WorkerDataProviderAdapter.__qualname__ = WorkerDataProviderAdapter.__name__
    return WorkerDataProviderAdapter


def project_worker_providers(
    manifest: Any,
    ledger: Any,
    worker: Any,
    host: Any,
) -> list[str]:
    """握手申报的 worker provider → AdapterRegistry（经台账）。"""
    from app.services.data_fabric.registry import AdapterSpec, get_registry

    registry = get_registry()
    projected: list[str] = []
    for entry in sorted(worker.data_providers, key=lambda e: e["source_type"]):
        source_type = str(entry["source_type"])
        canonical = manifest.namespaced_source_type(source_type)
        if canonical in registry.supported_source_types():
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.REGISTRY_PROJECTION_COLLISION,
                    f"source type {canonical!r} already registered",
                    extension_id=manifest.id,
                )
            )
        adapter_cls = make_worker_provider_class(worker, entry)
        spec = AdapterSpec(
            canonical=canonical,
            adapter_cls=adapter_cls,
            aliases=tuple(f"{manifest.namespace}_{a}" for a in entry.get("aliases") or []),
            is_demo=False,
            notes=f"extension worker provider ({'; '.join(entry.get('mixins') or []) or 'sync methods'}); "
            "endpoint serial: concurrent calls queue",
        )
        try:
            registry.register(spec)
        except Exception as exc:  # noqa: BLE001
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.REGISTRY_PROJECTION_FAILED,
                    f"source type {canonical!r} rejected by AdapterRegistry: {exc}",
                    extension_id=manifest.id,
                )
            ) from exc
        ledger.record(
            "data_provider", canonical, lambda c=canonical: registry.unregister(c)
        )
        projected.append(canonical)
    return projected


def project_worker_cartography_and_recipes(
    manifest: Any,
    ledger: Any,
    worker: Any,
    host: Any,
    grants: Any,
    settings: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """cartography / recipe 投影：payload dict → 既有 ExtensionContext 管线。

    构造一个投影专用的 ExtensionContext（不加载模块；工具投影不走它），
    复用其声明核对 / 命名空间化 / 碰撞检查 / 台账 undo 全套语义（M-2
    checklist 第 3 项的宿主对账扩展由调用方完成）。
    """
    from ..context import ExtensionContext
    from ..sdk.declarations import CartographyItemSpec, WorkflowPackSpec
    from ..trust import TrustLevel
    from app.services.gis_harness.recipes import CartographyRecipe

    shim = ExtensionContext(
        manifest=manifest,
        trust=TrustLevel.LOCAL_UNTRUSTED,
        grants=grants,
        settings=dict(settings),
        tool_registry=host._tool_registry,
        ledger=ledger,
    )
    cartography_ids: list[str] = []
    for item in sorted(worker.cartography_items, key=lambda c: (c["kind"], c["id"])):
        spec = CartographyItemSpec(
            kind=str(item["kind"]),
            id=str(item["id"]),
            description=str(item.get("description", "")),
            runtime_status=str(item.get("runtime_status", "planned")),
            payload=dict(item.get("payload") or {}),
            export_behavior=str(item.get("export_behavior", "degraded")),
            legend_behavior=str(item.get("legend_behavior", "auto")),
            degradation_policy=str(item.get("degradation_policy", "omit_with_disclosure")),
        )
        cartography_ids.append(shim.register_cartography_item(spec))
    recipe_pack_ids: list[str] = []
    for pack_entry in sorted(worker.workflow_packs, key=lambda p: p["pack_id"]):
        recipes = [
            CartographyRecipe.model_validate(r) for r in (pack_entry.get("recipes") or [])
        ]
        spec = WorkflowPackSpec(
            pack_id=str(pack_entry["pack_id"]),
            description=str(pack_entry.get("description", "")),
            recipes=recipes,
        )
        recipe_pack_ids.append(shim.register_workflow_pack(spec))
    return cartography_ids, recipe_pack_ids
