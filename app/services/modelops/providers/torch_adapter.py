"""TorchScriptProvider —— PyTorch TorchScript 推理（V3 §B）。

定位：真实 PyTorch provider 路径。模型包 = ``artifact_format="torchscript-v1"``
的单文件 TorchScript archive（``torch.jit.load`` 语义——**不是** pickle
的 ``.pth``；后者在 package_security 成员黑名单）。

诚实边界（V3 验收「GPU 不可用时降级」的 provider 侧执行点）：

- torch 缺席/损坏（Windows DLL 加载失败是**常态**而非异常）→ probe
  ``available=False`` → ``load`` typed ``ProviderLoadFailed``，主路径
  （local_reference / onnx / remote）完全不受影响；
- ``required="cuda"`` 而 CUDA 不可用 → descriptor 的
  ``allow_cpu_fallback``（引擎 ``resolve_device_plan`` 已先行解析）+
  provider 侧二次防御；
- ``torch.jit.load`` 执行序列化计算图：信任域 = operator 经 registry
  注册的包（owner scope 注册门 + checksum 双验），docstring 如实声明。

任务契约与 ONNX adapter 共用 ``dl_output.map_dl_outputs``（一处实现）。
"""
from __future__ import annotations

import threading
from typing import Any, Dict

import numpy as np

from app.lib.modelops.backends import probe_backend
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


class _TorchNoGrad:
    """torch.no_grad 的惰性上下文（torch 缺席时不炸 import）。"""

    def __init__(self, torch_mod: Any) -> None:
        self._cm = torch_mod.no_grad()

    def __enter__(self):
        return self._cm.__enter__()

    def __exit__(self, *exc):
        return self._cm.__exit__(*exc)


class TorchScriptProvider:
    """TorchScript adapter（探测门 + 包消费 + 任务契约映射）。"""

    def __init__(
        self,
        packages: ModelPackageStore,
        *,
        provider_id: str = "torch-script",
    ) -> None:
        self._packages = packages
        self._provider_id = provider_id
        self._lock = threading.Lock()
        self._in_flight = 0

    # ── capabilities / lifecycle ────────────────────────────────────
    def capabilities(self) -> ProviderCapabilities:
        info = probe_backend("torch")
        devices = {DEVICE_CPU}
        if info.available and info.cuda_available:
            devices.add(DEVICE_CUDA)
        return ProviderCapabilities(
            provider_id=self._provider_id,
            provider_type="torch_adapter",
            semantic_version="torch-adapter/1.0.0",
            tasks=frozenset(_SERVED_TASKS),
            prompt_modes=frozenset(),
            devices=frozenset(devices),
            max_batch=8,
            streaming=False,
            cancellation=True,  # batch 边界协作取消
            text_prompt=False,
            max_output_bytes=64 * 1024 * 1024,
        )

    def load(self, descriptor: GeoModelDescriptor, *, device: str) -> LoadedModel:
        import importlib

        info = probe_backend("torch")
        if not info.available:
            raise ProviderLoadFailed(
                f"torch runtime unavailable: {info.detail}",
                correction_hint="install a working torch build, or use a "
                "local_reference / onnx_adapter provider instead",
            )
        if not (_SERVED_TASKS & set(descriptor.task_types)):
            raise ProviderLoadFailed(
                f"torch adapter serves {sorted(_SERVED_TASKS)}; "
                f"descriptor offers {sorted(descriptor.task_types)}"
            )
        if descriptor.artifact_format != "torchscript-v1":
            raise ProviderLoadFailed(
                f"torch adapter requires artifact_format='torchscript-v1' "
                f"(got {descriptor.artifact_format!r})"
            )
        if device == DEVICE_CUDA and not info.cuda_available:
            raise ProviderLoadFailed(
                "torch runtime reports no CUDA device; descriptor must allow cpu fallback"
            )
        package_path = self._packages.resolve(descriptor)

        torch = importlib.import_module("torch")
        try:
            if device == DEVICE_CUDA:
                module = torch.jit.load(str(package_path), map_location="cuda")
            else:
                module = torch.jit.load(str(package_path), map_location="cpu")
            module.eval()
        except Exception as exc:  # noqa: BLE001 — 包损坏/语义不符 → typed
            raise ProviderLoadFailed(
                f"torch.jit.load failed: {type(exc).__name__}: {exc}"[:400]
            ) from exc
        state: Dict[str, Any] = {"module": module, "torch": torch}
        return LoadedModel(
            descriptor=descriptor,
            provider_id=self._provider_id,
            device=device,
            handle_id=f"{descriptor.model_id}@{descriptor.model_version}#torch",
            state=state,
        )

    def warmup(self, model: LoadedModel) -> Dict[str, Any]:
        descriptor = model.descriptor
        module = model.state["module"]
        ctx_h, ctx_w = descriptor.spatial.context_size
        h, w = min(ctx_h, 64), min(ctx_w, 64)
        pixels = np.zeros((1, descriptor.input_bands, h, w), dtype=np.float32)
        with _TorchNoGrad(model.state["torch"]):
            module(model.state["torch"].from_numpy(pixels))
        return {"warmed": True}

    def estimate_resources(
        self, descriptor: GeoModelDescriptor, *, batch: int, device: str
    ) -> ResourceEstimate:
        h, w = descriptor.spatial.context_size
        k = len(descriptor.class_schema.classes) + 1 if descriptor.class_schema else 4
        per_chip = descriptor.input_bands * h * w * 4 + k * h * w * 4
        weights = descriptor.memory_estimate.weights_bytes
        return ResourceEstimate(
            vram_bytes=per_chip + weights if device == DEVICE_CUDA else 0,
            host_ram_bytes=per_chip * 2 + (0 if device == DEVICE_CUDA else weights),
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
            torch = model.state["torch"]
            module = model.state["module"]
            pixels = np.ascontiguousarray(batch.pixels, dtype=np.float32)
            device_index = int(ctx.extras.get("device_index", 0) or 0)
            try:
                with _TorchNoGrad(torch):
                    if model.device == DEVICE_CUDA:
                        # 多 GPU 亲和：forward 在亲和设备上下文中执行。
                        with torch.cuda.device(device_index):
                            outputs = module(torch.from_numpy(pixels))
                    else:
                        outputs = module(torch.from_numpy(pixels))
            except Exception as exc:  # noqa: BLE001 — 运行时错误分类
                raise _classify_torch_error(exc) from exc
            raw = self._to_numpy(outputs, torch)
            task = model.descriptor.task_types[0]
            return map_dl_outputs(task, raw, model.descriptor, batch)
        finally:
            with self._lock:
                self._in_flight -= 1

    # ── cancel/health/unload ────────────────────────────────────────
    def cancel(self, model: LoadedModel, run_id: str) -> bool:
        return True  # batch 边界协作取消

    def health(self) -> ProviderHealth:
        info = probe_backend("torch")
        return ProviderHealth(
            healthy=info.available, detail=info.detail, in_flight=self._in_flight
        )

    def unload(self, model: LoadedModel) -> None:
        model.state["module"] = None

    # ── internals ───────────────────────────────────────────────────
    @staticmethod
    def _to_numpy(outputs: Any, torch: Any) -> list:
        if isinstance(outputs, (list, tuple)):
            return [o.detach().cpu().numpy() for o in outputs]
        return [outputs.detach().cpu().numpy()]


def _classify_torch_error(exc: Exception) -> Exception:
    text = f"{type(exc).__name__}: {exc}"
    lowered = text.lower()
    if "out of memory" in lowered or "cuda oom" in lowered:
        from app.lib.modelops.errors import ProviderOOM

        return ProviderOOM(f"torch OOM: {text}"[:400])
    return ProviderError(f"torch inference failed: {text}"[:400])


__all__ = ["TorchScriptProvider"]
