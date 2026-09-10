"""Typed Provider Protocol + ProviderRegistry（ADR-0119 §3.2）。

接口（Epic §B 全集）::

    capabilities() / load() / warmup() / estimate_resources() /
    infer() / cancel() / health() / unload()

硬约束：

- provider **不得绕过 capability/security broker**：所有实现经
  :class:`ProviderRegistry` 注册（capabilities 白名单校验），引擎只消费
  registry 解析出的实例；``descriptor.provider_ref`` 即 registry 实例 id
  （R1-C1：动态 import/字符串路径加载不存在于本平面）；
- ``infer`` 是同步阻塞调用（引擎在有界工作线程中执行）；协作式取消经
  ``InferenceContext``（provider 在 chip/batch 边界轮询）；
- provider 随机性：descriptor.random_seed_policy 声明；deterministic
  provider 不得使用全局随机源。

数据契约（numpy，无深度学习依赖）：

- 输入 ``TileBatch``：float32 张量栈 (N,C,H,W) + 有效掩膜 (N,1,H,W)；
- 输出 ``TileOutput``：任务类型判别（class probabilities / detections /
  embeddings / instance masks / promptable masks）。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable

import numpy as np

from app.lib.modelops.capabilities import ProviderCapabilities
from app.lib.modelops.descriptor import GeoModelDescriptor
from app.lib.modelops.errors import ProviderError
from app.lib.modelops.resources import DevicePlan, ResourceEstimate

#: 单批张量的最大元素数（防 provider 声明异常导致的无界内存）。
MAX_BATCH_ELEMENTS = 1 << 28  # ≈1 GiB @ float32


@dataclass(frozen=True)
class TileBatch:
    """一个推理批（预处理已完成；provider 只看张量）。"""

    pixels: np.ndarray          # (N,C,H,W) float32
    valid_mask: Optional[np.ndarray] = None  # (N,1,H,W) bool；None = 全有效
    chip_hw: tuple = (0, 0)     # (H, W) core 尺寸（含 pad 时大于 core）
    batch_index: int = 0

    def __post_init__(self) -> None:
        if self.pixels.dtype != np.float32:
            raise ProviderError(f"TileBatch.pixels must be float32 (got {self.pixels.dtype})")
        if self.pixels.size > MAX_BATCH_ELEMENTS:
            raise ProviderError(
                f"TileBatch exceeds {MAX_BATCH_ELEMENTS} elements "
                "(output bomb / memory guard)"
            )


@dataclass
class InferenceContext:
    """一次推理运行的上下文（provider 可见的最小面）。"""

    run_id: str
    device: str = "cpu"
    seed: Optional[int] = None
    #: 协作式取消探针（返回 True = 尽快放弃当前批）。
    cancelled: Any = None           # Callable[[], bool] | None
    #: provider 附加状态（extension/remote 的往返载荷）。
    extras: Dict[str, Any] = field(default_factory=dict)

    def is_cancelled(self) -> bool:
        try:
            return bool(self.cancelled()) if callable(self.cancelled) else False
        except Exception:  # noqa: BLE001 — 取消探针失败不放大为推理失败
            return False


@dataclass(frozen=True)
class TileOutput:
    """单批推理输出（任务判别；其余成员为 None）。"""

    task_type: str
    #: segmentation/promptable： (N,num_classes,H,W) float32 概率（或 logits
    #: 归一后）；provider 保证已归一（engine 抽验和≈1）。
    class_probabilities: Optional[np.ndarray] = None
    #: detection：结构化数组/列表（dict：box=[x,y,w,h] tile 像素坐标、
    #: score、label）。
    detections: Optional[List[Dict[str, Any]]] = None
    #: instance： (N,H,W) int32 instance id（0=背景）+ 每 chip 类别。
    instance_masks: Optional[np.ndarray] = None
    #: embedding： (N,D) float32 向量。
    embeddings: Optional[np.ndarray] = None
    #: classification： (N,num_classes) float32。
    label_probabilities: Optional[np.ndarray] = None

    def validate_for(self, batch: TileBatch) -> None:
        """输出形状/预算校验（output bomb 防护的 provider 侧执行点）。"""
        n = batch.pixels.shape[0]
        if self.task_type in ("semantic_segmentation", "promptable_segmentation"):
            if self.class_probabilities is None:
                raise ProviderError(f"{self.task_type} output requires class_probabilities")
            if self.class_probabilities.shape[0] != n:
                raise ProviderError("class_probabilities batch mismatch")
            if self.class_probabilities.shape[-2:] != batch.pixels.shape[-2:]:
                raise ProviderError(
                    "class_probabilities spatial shape mismatch: "
                    f"{self.class_probabilities.shape[-2:]} != {batch.pixels.shape[-2:]}"
                )
        if self.task_type == "object_detection" and self.detections is None:
            raise ProviderError("object_detection output requires detections")
        if self.task_type == "instance_segmentation" and self.instance_masks is None:
            raise ProviderError("instance_segmentation output requires instance_masks")
        if self.task_type == "embedding" and self.embeddings is None:
            raise ProviderError("embedding output requires embeddings")
        if self.task_type == "classification" and self.label_probabilities is None:
            raise ProviderError("classification output requires label_probabilities")
        if self.class_probabilities is not None and self.task_type != "temporal_forecast":
            # 概率语义抽验（docstring 承诺的实现点，m-5）：每像素和 ≈ 1。
            # temporal_forecast 的 class_probabilities 通道承载预测栈
            # （非概率），不参与此检查。
            sums = self.class_probabilities.sum(axis=1)
            if not np.allclose(sums, 1.0, atol=1e-2):
                raise ProviderError(
                    "class_probabilities do not sum to 1 per pixel "
                    "(provider must return normalized probabilities)"
                )


@dataclass
class LoadedModel:
    """一次成功 load 的句柄（opaque；provider 私有状态）。"""

    descriptor: GeoModelDescriptor
    provider_id: str
    device: str
    handle_id: str
    #: provider 私有状态（权重数组/连接等）。
    state: Any = None


@dataclass(frozen=True)
class ProviderHealth:
    healthy: bool
    detail: str = ""
    in_flight: int = 0


@runtime_checkable
class InferenceProvider(Protocol):
    """provider 协议（结构化；实现不得要求更多引擎内部知识）。"""

    def capabilities(self) -> ProviderCapabilities: ...

    def load(self, descriptor: GeoModelDescriptor, *, device: str) -> LoadedModel: ...

    def warmup(self, model: LoadedModel) -> Dict[str, Any]: ...

    def estimate_resources(
        self, descriptor: GeoModelDescriptor, *, batch: int, device: str
    ) -> ResourceEstimate: ...

    def infer(
        self, model: LoadedModel, batch: TileBatch, ctx: InferenceContext
    ) -> TileOutput: ...

    def cancel(self, model: LoadedModel, run_id: str) -> bool: ...

    def health(self) -> ProviderHealth: ...

    def unload(self, model: LoadedModel) -> None: ...


def ensure_not_cancelled(ctx: InferenceContext) -> None:
    """provider 侧统一取消检查点（batch/chip 边界调用）。"""
    from app.lib.modelops.errors import InferenceCancelled

    if ctx.is_cancelled():
        raise InferenceCancelled(f"run {ctx.run_id} cancelled")


class ProviderRegistry:
    """provider 实例注册表（capability broker 的静态执行点）。

    - ``register`` 校验 capabilities 词表（未知 task/prompt/device typed
      拒绝）并拒绝重复 id；
    - ``ensure_registered``：registry.register（模型注册）与引擎解析共用
      的 C1 门——descriptor.provider_ref 必须是已注册实例 id。
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._providers: Dict[str, InferenceProvider] = {}

    def register(self, provider: InferenceProvider) -> ProviderCapabilities:
        caps = provider.capabilities()
        from app.lib.modelops.capabilities import (
            DEVICES,
            PROMPT_MODES,
            TASK_TYPES,
        )

        unknown_tasks = sorted(caps.tasks - TASK_TYPES)
        unknown_prompts = sorted(caps.prompt_modes - PROMPT_MODES)
        unknown_devices = sorted(caps.devices - DEVICES)
        if unknown_tasks or unknown_prompts or unknown_devices:
            raise ProviderError(
                f"provider {caps.provider_id!r} declares unknown capabilities: "
                f"tasks={unknown_tasks} prompts={unknown_prompts} devices={unknown_devices}"
            )
        if caps.max_batch < 1:
            raise ProviderError(f"provider {caps.provider_id!r} max_batch < 1")
        with self._lock:
            if caps.provider_id in self._providers:
                raise ProviderError(f"provider id {caps.provider_id!r} already registered")
            self._providers[caps.provider_id] = provider
        return caps

    def unregister(self, provider_id: str) -> bool:
        with self._lock:
            return self._providers.pop(provider_id, None) is not None

    def get(self, provider_ref: str) -> InferenceProvider:
        with self._lock:
            provider = self._providers.get(provider_ref)
        if provider is None:
            raise ProviderError(
                f"provider_ref {provider_ref!r} does not resolve to a registered "
                "provider instance (dynamic code loading is forbidden)",
                correction_hint="use list_models/inspect to see wired provider ids",
            )
        return provider

    def has(self, provider_ref: str) -> bool:
        with self._lock:
            return provider_ref in self._providers

    def known_refs(self) -> List[str]:
        with self._lock:
            return sorted(self._providers)

    def probe(self) -> "Callable[[str], bool]":
        """供 registry.register 的 C1 门使用的可调用探测。"""
        return self.has

    def __len__(self) -> int:
        with self._lock:
            return len(self._providers)


def resolve_device_plan(
    descriptor: GeoModelDescriptor,
    caps: ProviderCapabilities,
    *,
    device_override: Optional[str] = None,
) -> DevicePlan:
    """设备解析：descriptor 要求 × provider 能力 → DevicePlan（typed 拒绝）。"""
    from app.lib.modelops.capabilities import DEVICES, DEVICE_CPU, DEVICE_CUDA

    required = device_override or descriptor.device_requirements.required
    if required not in DEVICES:
        raise ProviderError(f"unknown device {required!r}")
    if not caps.supports_device(required):
        if required == DEVICE_CUDA and descriptor.device_requirements.allow_cpu_fallback \
                and caps.supports_device(DEVICE_CPU):
            required = DEVICE_CPU
        else:
            from app.lib.modelops.errors import ResourceUnavailable

            raise ResourceUnavailable(
                f"provider {caps.provider_id!r} cannot serve device {required!r} "
                f"(declares {sorted(caps.devices)})"
            )
    return DevicePlan(device=required)


__all__ = [
    "InferenceProvider",
    "InferenceContext",
    "LoadedModel",
    "ProviderHealth",
    "ProviderRegistry",
    "TileBatch",
    "TileOutput",
    "ensure_not_cancelled",
    "resolve_device_plan",
    "MAX_BATCH_ELEMENTS",
]
