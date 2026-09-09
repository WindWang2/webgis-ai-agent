"""ModelOps capability vocabularies（封闭词表 + ProviderCapabilities 契约）。

词表全部封闭：descriptor 校验器与 ProviderRegistry 白名单共用本模块常量，
新增成员必须同步 :data:`DESCRIPTOR_V1_RULES` 的静态断言（无静默扩展）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet

# ── 任务类型（模型做什么）────────────────────────────────────────────
TASK_SEMANTIC_SEGMENTATION = "semantic_segmentation"
TASK_INSTANCE_SEGMENTATION = "instance_segmentation"
TASK_OBJECT_DETECTION = "object_detection"
TASK_CLASSIFICATION = "classification"
TASK_EMBEDDING = "embedding"
TASK_PROMPTABLE_SEGMENTATION = "promptable_segmentation"
TASK_TEMPORAL_FORECAST = "temporal_forecast"

TASK_TYPES: FrozenSet[str] = frozenset(
    {
        TASK_SEMANTIC_SEGMENTATION,
        TASK_INSTANCE_SEGMENTATION,
        TASK_OBJECT_DETECTION,
        TASK_CLASSIFICATION,
        TASK_EMBEDDING,
        TASK_PROMPTABLE_SEGMENTATION,
        TASK_TEMPORAL_FORECAST,
    }
)

# ── 输入模态 ────────────────────────────────────────────────────────
MODALITY_OPTICAL_RGB = "optical_rgb"
MODALITY_OPTICAL_MULTISPECTRAL = "optical_multispectral"
MODALITY_SAR = "sar"
MODALITY_DEM = "dem"
MODALITY_THERMAL = "thermal"
MODALITY_HYPERSPECTRAL = "hyperspectral"

INPUT_MODALITIES: FrozenSet[str] = frozenset(
    {
        MODALITY_OPTICAL_RGB,
        MODALITY_OPTICAL_MULTISPECTRAL,
        MODALITY_SAR,
        MODALITY_DEM,
        MODALITY_THERMAL,
        MODALITY_HYPERSPECTRAL,
    }
)

# ── 输出类型 ────────────────────────────────────────────────────────
OUTPUT_CLASS_RASTER = "class_raster"
OUTPUT_CONFIDENCE_RASTER = "confidence_raster"
OUTPUT_PROBABILITY_STACK = "probability_stack"
OUTPUT_DETECTIONS = "detections"
OUTPUT_INSTANCE_MASKS = "instance_masks"
OUTPUT_EMBEDDINGS = "embeddings"
OUTPUT_LABELS = "labels"
OUTPUT_TEMPORAL_STACK = "temporal_stack"

OUTPUT_TYPES: FrozenSet[str] = frozenset(
    {
        OUTPUT_CLASS_RASTER,
        OUTPUT_CONFIDENCE_RASTER,
        OUTPUT_PROBABILITY_STACK,
        OUTPUT_DETECTIONS,
        OUTPUT_INSTANCE_MASKS,
        OUTPUT_EMBEDDINGS,
        OUTPUT_LABELS,
        OUTPUT_TEMPORAL_STACK,
    }
)

# ── Prompt 模式（promptable 契约）───────────────────────────────────
PROMPT_POINT = "point"
PROMPT_BOX = "box"
PROMPT_MASK = "mask"
PROMPT_TEXT = "text"

PROMPT_MODES: FrozenSet[str] = frozenset({PROMPT_POINT, PROMPT_BOX, PROMPT_MASK, PROMPT_TEXT})

# ── 设备 ────────────────────────────────────────────────────────────
DEVICE_CPU = "cpu"
DEVICE_CUDA = "cuda"

DEVICES: FrozenSet[str] = frozenset({DEVICE_CPU, DEVICE_CUDA})

# ── provider 形态 ───────────────────────────────────────────────────
PROVIDER_LOCAL_REFERENCE = "local_reference"      # 仓库内可信代码（tiny/reference 模型）
PROVIDER_EXTENSION_WORKER = "extension_worker"    # extensions platform 隔离子进程
PROVIDER_REMOTE_ENDPOINT = "remote_endpoint"      # SSRF-gated HTTP 推理端点
PROVIDER_ONNX_ADAPTER = "onnx_adapter"            # 前向声明：本 Epic 不声称可用
PROVIDER_TORCH_ADAPTER = "torch_adapter"          # 前向声明：本 Epic 不声称可用

PROVIDER_TYPES: FrozenSet[str] = frozenset(
    {
        PROVIDER_LOCAL_REFERENCE,
        PROVIDER_EXTENSION_WORKER,
        PROVIDER_REMOTE_ENDPOINT,
        PROVIDER_ONNX_ADAPTER,
        PROVIDER_TORCH_ADAPTER,
    }
)

#: 本 Epic 真实接线的 provider 形态（其余为 descriptor 层前向声明，
#: qualifier/registry 在注册时对未接线形态给 typed 警告而非虚假能力）。
WIRED_PROVIDER_TYPES: FrozenSet[str] = frozenset(
    {PROVIDER_LOCAL_REFERENCE, PROVIDER_EXTENSION_WORKER, PROVIDER_REMOTE_ENDPOINT}
)

# ── 重采样策略（显式重投影/分辨率失配时）────────────────────────────
RESAMPLING_NEAREST = "nearest"
RESAMPLING_BILINEAR = "bilinear"
RESAMPLING_CUBIC = "cubic"
RESAMPLING_AVERAGE = "average"

RESAMPLING_POLICIES: FrozenSet[str] = frozenset(
    {RESAMPLING_NEAREST, RESAMPLING_BILINEAR, RESAMPLING_CUBIC, RESAMPLING_AVERAGE}
)

# ── padding 模式（edge chip）────────────────────────────────────────
PADDING_REFLECT = "reflect"
PADDING_REPLICATE = "replicate"
PADDING_CONSTANT = "constant"

PADDING_MODES: FrozenSet[str] = frozenset({PADDING_REFLECT, PADDING_REPLICATE, PADDING_CONSTANT})

# ── 时序缺失观测策略 ────────────────────────────────────────────────
MISSING_POLICY_MASK = "mask"     # 缺失时相以 quality mask 标注
MISSING_POLICY_FLAG = "flag"     # 缺失时相以 flag 通道传入（模型可学习）
MISSING_POLICY_ERROR = "error"   # 缺失即 typed 拒绝

MISSING_POLICIES: FrozenSet[str] = frozenset({MISSING_POLICY_MASK, MISSING_POLICY_FLAG, MISSING_POLICY_ERROR})


@dataclass(frozen=True)
class ProviderCapabilities:
    """provider 能力声明（ProviderRegistry 白名单 + qualifier 共同消费）。

    ``semantic_version`` 进入 InferenceFingerprint：provider 语义变化
    （同一模型不同输出语义）⇒ reuse key 必然失效。
    """

    provider_id: str
    provider_type: str
    semantic_version: str
    tasks: FrozenSet[str] = field(default_factory=frozenset)
    prompt_modes: FrozenSet[str] = field(default_factory=frozenset)
    devices: FrozenSet[str] = field(default_factory=lambda: frozenset({DEVICE_CPU}))
    max_batch: int = 1
    streaming: bool = False
    cancellation: bool = True
    text_prompt: bool = False
    #: 单 chip 输出上限（bytes），provider 自报；引擎做 output bomb 防护。
    max_output_bytes: int = 64 * 1024 * 1024

    def supports_task(self, task: str) -> bool:
        return task in self.tasks

    def supports_prompt(self, mode: str) -> bool:
        return mode in self.prompt_modes

    def supports_device(self, device: str) -> bool:
        return device in self.devices

    def as_dict(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "provider_type": self.provider_type,
            "semantic_version": self.semantic_version,
            "tasks": sorted(self.tasks),
            "prompt_modes": sorted(self.prompt_modes),
            "devices": sorted(self.devices),
            "max_batch": self.max_batch,
            "streaming": self.streaming,
            "cancellation": self.cancellation,
            "text_prompt": self.text_prompt,
            "max_output_bytes": self.max_output_bytes,
        }


#: 静态自检：本模块词表彼此不冲突（import 时即验证，防手滑合并词表）。
assert not (TASK_TYPES & INPUT_MODALITIES)  # noqa: S101
assert not (TASK_TYPES & OUTPUT_TYPES)  # noqa: S101
assert PROVIDER_TYPES >= WIRED_PROVIDER_TYPES  # noqa: S101
assert TASK_PROMPTABLE_SEGMENTATION in TASK_TYPES  # noqa: S101
