"""Extension provider adapter —— host.invoke_model_provider → Provider 协议。

信任域边界（R1-M6 冻结）：

- adapter **只经既有 ExtensionHost 公共面**（``invoke_model_provider``）
  往返；不 import worker/client 内部件、不触达 host 私有记录（架构静态
  测试 test_extension_adapter_isolation 强制）；
- extension 代码执行域（core in-process / worker 子进程）完全由
  extension platform 激活策略决定，本 adapter 不放宽；
- **线程 offload**：host 调用是同步阻塞（worker.call 带超时），本
  adapter 用独立有界线程池 + in-flight 信号量承载（R1-M5），事件循环
  永不阻塞；
- 协议：JSON 往返（request = {task, pixels 嵌套列表, chip 尺寸, prompt}；
  response = {class_probabilities} 或 {detections}）；输出形状/预算在
  TileOutput.validate_for 复验（output bomb 防护延伸到扩展通道）。
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any, Dict

import numpy as np

from app.lib.modelops.capabilities import ProviderCapabilities
from app.lib.modelops.descriptor import GeoModelDescriptor
from app.lib.modelops.errors import ProviderError, ProviderLoadFailed
from app.lib.modelops.resources import ResourceEstimate
from app.services.modelops.providers.base import (
    InferenceContext,
    LoadedModel,
    ProviderHealth,
    TileBatch,
    TileOutput,
    ensure_not_cancelled,
)

logger = logging.getLogger(__name__)

#: adapter 专用池（与引擎池隔离——worker 取消延迟=host call_timeout，
#: 阻塞的 call 不得耗尽引擎工作线程，R1-M5）。
_ADAPTER_POOL = ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="modelops-ext-adapter"
)
_ADAPTER_SEMAPHORE = threading.BoundedSemaphore(4)


class ExtensionProviderAdapter:
    """把「已投影的扩展 model provider 工具」适配为 InferenceProvider。

    ``invoke`` 为可调用注入点（生产 = ExtensionHost.invoke_model_provider
    的部分应用；测试 = 假 invoke）。adapter 自身不解析扩展包。
    """

    def __init__(
        self,
        provider_id: str,
        invoke: Any,
        *,
        semantic_version: str = "extension/1.0.0",
        tasks: tuple = ("semantic_segmentation",),
        call_timeout_s: float = 60.0,
    ) -> None:
        self._provider_id = provider_id
        self._invoke = invoke
        self._semantic_version = semantic_version
        self._tasks = tuple(tasks)
        self._call_timeout_s = call_timeout_s
        self._lock = threading.Lock()
        self._in_flight = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self._provider_id,
            provider_type="extension_worker",
            semantic_version=self._semantic_version,
            tasks=frozenset(self._tasks),
            prompt_modes=frozenset(),
            devices=frozenset({"cpu"}),
            max_batch=4,
            streaming=False,
            cancellation=False,  # worker 单帧 RPC：取消=放弃 future（超时上界）
            text_prompt=False,
            max_output_bytes=32 * 1024 * 1024,
        )

    def load(self, descriptor: GeoModelDescriptor, *, device: str) -> LoadedModel:
        if device != "cpu":
            raise ProviderLoadFailed(f"extension adapter serves cpu only (got {device!r})")
        return LoadedModel(
            descriptor=descriptor,
            provider_id=self._provider_id,
            device=device,
            handle_id=f"{descriptor.model_id}@{descriptor.model_version}#ext",
            state=None,
        )

    def warmup(self, model: LoadedModel) -> Dict[str, Any]:
        return {"warmed": True, "transport": "extension_host"}

    def estimate_resources(
        self, descriptor: GeoModelDescriptor, *, batch: int, device: str
    ) -> ResourceEstimate:
        h, w = descriptor.spatial.chip_size
        per_chip = descriptor.input_bands * h * w * 4
        return ResourceEstimate(
            vram_bytes=0,
            host_ram_bytes=per_chip * max(1, batch),
            recommended_batch=min(4, max(1, batch)),
            externally_enforced=True,  # 子进程 RLIMIT 强制——host 侧不可观测
        )

    def infer(
        self, model: LoadedModel, batch: TileBatch, ctx: InferenceContext
    ) -> TileOutput:
        ensure_not_cancelled(ctx)
        n, c, h, w = batch.pixels.shape
        request = {
            "task": model.descriptor.task_types[0],
            "model_id": model.descriptor.model_id,
            "model_version": model.descriptor.model_version,
            "batch": n,
            "bands": c,
            "width": w,
            "height": h,
            "pixels": batch.pixels.round(6).tolist(),
        }
        if not _ADAPTER_SEMAPHORE.acquire(timeout=30.0):
            raise ProviderError("extension adapter in-flight cap exhausted")
        with self._lock:
            self._in_flight += 1
        try:
            # 独立池 offload（同步阻塞 host call 不占引擎线程）。
            future = _ADAPTER_POOL.submit(self._invoke, request)
            result = future.result(timeout=self._call_timeout_s)
        except FutureTimeoutError as exc:
            raise ProviderError(
                f"extension call exceeded {self._call_timeout_s}s (cancel latency "
                "upper bound = call_timeout, R1-M5)"
            ) from exc
        finally:
            with self._lock:
                self._in_flight -= 1
            _ADAPTER_SEMAPHORE.release()
        return self._parse(model, result, batch)

    def _parse(self, model: LoadedModel, result: Any, batch: TileBatch) -> TileOutput:
        if not isinstance(result, dict):
            raise ProviderError(
                f"extension provider returned {type(result).__name__}; dict expected"
            )
        error = result.get("error")
        if error:
            raise ProviderError(f"extension provider error: {str(error)[:300]}")
        task = model.descriptor.task_types[0]
        if "class_probabilities" in result:
            probs = np.asarray(result["class_probabilities"], dtype=np.float32)
            if probs.ndim == 3:
                probs = probs[None]
            output = TileOutput(task_type=task, class_probabilities=probs)
        elif "detections" in result:
            output = TileOutput(
                task_type=task, detections=list(result["detections"] or [])
            )
        else:
            raise ProviderError(
                "extension provider response lacks class_probabilities/detections"
            )
        output.validate_for(batch)
        return output

    def cancel(self, model: LoadedModel, run_id: str) -> bool:
        # worker 单帧 RPC 无协作取消（capabilities 如实声明 False）；
        # 引擎侧放弃 future，延迟上界 = call_timeout（R1-M5 语义）。
        return False

    def health(self) -> ProviderHealth:
        with self._lock:
            return ProviderHealth(healthy=True, in_flight=self._in_flight)

    def unload(self, model: LoadedModel) -> None:
        return None
