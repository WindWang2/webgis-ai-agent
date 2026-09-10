"""OnnxRuntimeProvider —— ONNX Runtime 计算图推理（V3 §B）。

定位：**真实 DL runtime provider 主路径**。模型包 = descriptor.artifact_format
= ``onnx-v1`` 的单文件 .onnx（protobuf 计算图；非 pickle——图是数据驱动
的算子序列，不存在任意代码执行路径），经 package_security +
ModelPackageStore 双验后由 onnxruntime 会话消费。

诚实边界：

- onnxruntime 缺席/损坏（Windows DLL 失败是常态）→ ``load`` typed
  ``ProviderLoadFailed``（probe 细节如实入错），**不阻塞**其余 provider
  主路径（descriptor 词表照常声明，注册面不冒充可用）；
- TensorRT / CUDA 经 onnxruntime **执行提供者**接入（探测驱动，声明了
  不存在的 EP 会让会话构造失败——以 ``get_available_providers`` 为唯一
  真相）；OpenVINO 经其 ORT EP / 独立 runtime 部署形态接入，本平面不
  重复实现；
- 任务支持 = ``dl_output.map_dl_outputs`` 契约（segmentation/detection/
  classification/embedding）；instance/super-resolution/fusion 不在本
  provider 契约内（typed 拒绝，不静默出错结果）。
"""
from __future__ import annotations

import threading
from typing import Any, Dict

import numpy as np

from app.lib.modelops.backends import onnx_execution_providers, probe_backend
from app.lib.modelops.capabilities import (
    DEVICE_CPU,
    DEVICE_CUDA,
    TASK_CLASSIFICATION,
    TASK_EMBEDDING,
    TASK_OBJECT_DETECTION,
    TASK_SEMANTIC_SEGMENTATION,
    ProviderCapabilities,
)
from app.lib.modelops.descriptor import GeoModelDescriptor
from app.lib.modelops.errors import ProviderError, ProviderLoadFailed
from app.lib.modelops.resources import ResourceEstimate
from app.services.modelops.package_store import ModelPackageStore
from app.services.modelops.providers.base import (
    InferenceContext,
    LoadedModel,
    ProviderHealth,
    TileBatch,
    TileOutput,
)
from app.services.modelops.providers.dl_output import map_dl_outputs

_SERVED_TASKS = frozenset(
    {
        TASK_SEMANTIC_SEGMENTATION,
        TASK_OBJECT_DETECTION,
        TASK_CLASSIFICATION,
        TASK_EMBEDDING,
    }
)


class OnnxRuntimeProvider:
    """ONNX Runtime adapter（包消费 + 任务契约映射）。"""

    def __init__(
        self,
        packages: ModelPackageStore,
        *,
        provider_id: str = "onnx-runtime",
        intra_op_threads: int = 2,
        inter_op_threads: int = 1,
    ) -> None:
        self._packages = packages
        self._provider_id = provider_id
        self._intra_op_threads = max(1, intra_op_threads)
        self._inter_op_threads = max(1, inter_op_threads)
        self._lock = threading.Lock()
        self._in_flight = 0

    # ── capabilities / lifecycle ────────────────────────────────────
    def capabilities(self) -> ProviderCapabilities:
        ort_info = probe_backend("onnxruntime")
        devices = {DEVICE_CPU}
        if ort_info.available and any(
            "CUDA" in ep or "Tensorrt" in ep or "TensorRT" in ep
            for ep in ort_info.execution_providers
        ):
            devices.add(DEVICE_CUDA)
        return ProviderCapabilities(
            provider_id=self._provider_id,
            provider_type="onnx_adapter",
            semantic_version="onnx-adapter/1.0.0",
            tasks=frozenset(_SERVED_TASKS),
            prompt_modes=frozenset(),
            devices=frozenset(devices),
            max_batch=8,
            streaming=False,
            cancellation=True,  # batch 边界协作取消（单次 session.run 不可中断）
            text_prompt=False,
            max_output_bytes=64 * 1024 * 1024,
        )

    def load(self, descriptor: GeoModelDescriptor, *, device: str) -> LoadedModel:
        import importlib

        ort_info = probe_backend("onnxruntime")
        if not ort_info.available:
            raise ProviderLoadFailed(
                f"onnxruntime unavailable: {ort_info.detail}",
                correction_hint="install onnxruntime (requirements: onnxruntime>=1.17) "
                "or choose a local_reference provider",
            )
        served = _SERVED_TASKS & set(descriptor.task_types)
        if not served:
            raise ProviderLoadFailed(
                f"onnx adapter serves {sorted(_SERVED_TASKS)}; "
                f"descriptor offers {sorted(descriptor.task_types)}"
            )
        if descriptor.artifact_format != "onnx-v1":
            raise ProviderLoadFailed(
                f"onnx adapter requires artifact_format='onnx-v1' "
                f"(got {descriptor.artifact_format!r})"
            )
        package_path = self._packages.resolve(descriptor)

        ort = importlib.import_module("onnxruntime")
        session_options = ort.SessionOptions()
        session_options.intra_op_num_threads = self._intra_op_threads
        session_options.inter_op_num_threads = self._inter_op_threads
        want_cuda = device == DEVICE_CUDA
        eps = onnx_execution_providers(want_cuda=want_cuda)
        try:
            session = ort.InferenceSession(
                str(package_path), sess_options=session_options, providers=eps
            )
        except Exception as exc:  # noqa: BLE001 — 运行时/包损坏 → typed
            raise ProviderLoadFailed(
                f"onnxruntime session construction failed: {type(exc).__name__}: {exc}"[:400]
            ) from exc

        inputs = session.get_inputs()
        if len(inputs) != 1:
            raise ProviderLoadFailed(
                f"onnx model must expose exactly one input (got {len(inputs)})"
            )
        input_meta = inputs[0]
        declared_bands = descriptor.input_bands
        state: Dict[str, Any] = {
            "session": session,
            "input_name": input_meta.name,
            "output_names": [o.name for o in session.get_outputs()],
            "declared_bands": declared_bands,
        }
        return LoadedModel(
            descriptor=descriptor,
            provider_id=self._provider_id,
            device=device,
            handle_id=f"{descriptor.model_id}@{descriptor.model_version}#onnx",
            state=state,
        )

    def warmup(self, model: LoadedModel) -> Dict[str, Any]:
        """有界 warmup：最小 chip 形状的零张量跑一遍图（触发内存计划）。"""
        descriptor = model.descriptor
        session = model.state["session"]
        ctx_h, ctx_w = descriptor.spatial.context_size
        # 有界：warmup 输入不超过 64x64（真实 chip 尺寸在推理时自然展开）。
        h, w = min(ctx_h, 64), min(ctx_w, 64)
        pixels = np.zeros((1, descriptor.input_bands, h, w), dtype=np.float32)
        session.run(model.state["output_names"], {model.state["input_name"]: pixels})
        return {"warmed": True, "providers": session.get_providers()}

    def estimate_resources(
        self, descriptor: GeoModelDescriptor, *, batch: int, device: str
    ) -> ResourceEstimate:
        h, w = descriptor.spatial.context_size
        # 输入 + K 通道 float32 输出（单 chip 口径；engine 统一乘 batch）。
        k = len(descriptor.class_schema.classes) + 1 if descriptor.class_schema else 4
        per_chip = descriptor.input_bands * h * w * 4 + k * h * w * 4
        weights = descriptor.memory_estimate.weights_bytes
        return ResourceEstimate(
            vram_bytes=per_chip if device == DEVICE_CUDA else 0,
            host_ram_bytes=per_chip * 2 + weights,
            recommended_batch=min(8, max(1, batch)),
        )

    # ── 推理 ────────────────────────────────────────────────────────
    def infer(
        self, model: LoadedModel, batch: TileBatch, ctx: InferenceContext
    ) -> TileOutput:
        with self._lock:
            self._in_flight += 1
        try:
            if ctx.is_cancelled():
                from app.lib.modelops.errors import InferenceCancelled

                raise InferenceCancelled(f"run {ctx.run_id} cancelled before batch")
            session = model.state["session"]
            pixels = np.ascontiguousarray(batch.pixels, dtype=np.float32)
            try:
                raw = session.run(
                    model.state["output_names"], {model.state["input_name"]: pixels}
                )
            except Exception as exc:  # noqa: BLE001 — 运行时错误分类
                raise _classify_ort_error(exc) from exc
            task = model.descriptor.task_types[0]
            return map_dl_outputs(task, raw, model.descriptor, batch)
        finally:
            with self._lock:
                self._in_flight -= 1

    # ── cancel/health/unload ────────────────────────────────────────
    def cancel(self, model: LoadedModel, run_id: str) -> bool:
        # 单次 session.run 不可中断；取消在 batch 边界生效（capabilities 如实声明）。
        return True

    def health(self) -> ProviderHealth:
        ort_info = probe_backend("onnxruntime")
        return ProviderHealth(
            healthy=ort_info.available, detail=ort_info.detail, in_flight=self._in_flight
        )

    def unload(self, model: LoadedModel) -> None:
        model.state["session"] = None


def _classify_ort_error(exc: Exception) -> Exception:
    """onnxruntime 常见失败分类：OOM → ProviderOOM（引擎降批路径复用）。"""
    text = f"{type(exc).__name__}: {exc}"
    lowered = text.lower()
    if "out of memory" in lowered or "memory_limit" in lowered or "allocate" in lowered:
        from app.lib.modelops.errors import ProviderOOM

        return ProviderOOM(f"onnxruntime OOM: {text}"[:400])
    return ProviderError(f"onnxruntime inference failed: {text}"[:400])


__all__ = ["OnnxRuntimeProvider"]
