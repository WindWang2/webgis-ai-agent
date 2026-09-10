"""ModelDescriptor V2 —— learned-model 身份/能力/资源契约（ADR-0119）。

不变式：

- **immutable**：frozen pydantic 模型；identity = (model_id, model_version)；
  同 identity 不同 checksum 是碰撞（registry 层 typed 拒绝）。
- **typed**：``schema_version="modelops.descriptor/v1"``；strict extra=forbid
  —— 未知字段注册即拒绝（不静默吞掉未来字段）。
- **secret 分离**：descriptor 只允许 ``credentials_ref``（名字，不是值）；
  任何字段值命中 secret 键名单即 ``SecretLeakGuardError``。
- 「前向声明 ≠ 已接线」：``provider_type`` 允许声明 onnx/torch adapter 词表，
  但 registry 注册时对未接线形态发 typed 警告，provider runtime 绝不冒充。
"""
from __future__ import annotations

from typing import Any, Dict, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .capabilities import (
    DEVICES,
    DEVICE_CPU,
    INPUT_MODALITIES,
    MISSING_POLICIES,
    OUTPUT_TYPES,
    PADDING_MODES,
    PADDING_REFLECT,
    PROVIDER_TYPES,
    RESAMPLING_POLICIES,
    RESAMPLING_NEAREST,
    TASK_TYPES,
)
from .errors import DescriptorError, SecretLeakGuardError

#: 描述符 schema 版本（进入 fingerprint；语义变化 ⇒ 复用失效）。
DESCRIPTOR_SCHEMA_VERSION = "modelops.descriptor/v1"

#: 常规化归一化策略词表（normalization.kind）。
NORMALIZATION_KINDS = frozenset({"none", "mean_std", "min_max"})

#: secret 键名单（对 descriptor/manifest 所有 str 键做防御性扫描）。
_SECRET_KEY_MARKERS = ("password", "secret", "token", "api_key", "apikey", "credential")


def _reject_secret_keys(where: str, mapping: Any, depth: int = 0) -> None:
    """mapping 的键不得命中 secret 词根（值一律以 credentials_ref 引用）。"""
    if depth > 6 or not isinstance(mapping, dict):
        return
    for key in mapping:
        if isinstance(key, str) and any(marker in key.lower() for marker in _SECRET_KEY_MARKERS):
            raise SecretLeakGuardError(
                f"{where}: key {key!r} looks like a secret; pass a credentials_ref "
                "instead — secret values are supplied via the secrets channel only"
            )
        if isinstance(mapping[key], dict):
            _reject_secret_keys(where, mapping[key], depth + 1)


class NormalizationSpec(BaseModel):
    """输入归一化（进 InferenceFingerprint 与 preprocess plan）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["none", "mean_std", "min_max"] = "none"
    mean: Optional[Tuple[float, ...]] = None
    std: Optional[Tuple[float, ...]] = None
    vmin: Optional[Tuple[float, ...]] = None
    vmax: Optional[Tuple[float, ...]] = None

    @model_validator(mode="after")
    def _kind_shape(self) -> "NormalizationSpec":
        if self.kind == "mean_std":
            if not self.mean or not self.std:
                raise DescriptorError("mean_std normalization requires mean[] and std[]")
            if len(self.mean) != len(self.std):
                raise DescriptorError("mean/std length mismatch")
            if any(s == 0 for s in self.std):
                raise DescriptorError("std contains zero")
        if self.kind == "min_max" and (not self.vmin or not self.vmax):
            raise DescriptorError("min_max normalization requires vmin[] and vmax[]")
        return self

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "mean": list(self.mean) if self.mean else None,
            "std": list(self.std) if self.std else None,
            "vmin": list(self.vmin) if self.vmin else None,
            "vmax": list(self.vmax) if self.vmax else None,
        }


class ResolutionRange(BaseModel):
    """地面分辨率容忍区间（米/像素；闭区间）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    min_m_per_px: float = Field(gt=0)
    max_m_per_px: float = Field(gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> "ResolutionRange":
        if self.min_m_per_px > self.max_m_per_px:
            raise DescriptorError("resolution range min > max")
        return self

    def contains(self, m_per_px: float) -> bool:
        return self.min_m_per_px <= m_per_px <= self.max_m_per_px

    def as_dict(self) -> Dict[str, Any]:
        return self.model_dump(mode="json")


class DeviceRequirements(BaseModel):
    """设备需求：required 设备族 + 是否允许 CPU 回退。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    required: Literal["cpu", "cuda"] = DEVICE_CPU
    allow_cpu_fallback: bool = True
    min_vram_mb: int = Field(default=0, ge=0)

    def as_dict(self) -> Dict[str, Any]:
        return self.model_dump(mode="json")


class SpatialRequirements(BaseModel):
    """空间输入要求（qualifier 消费）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chip_size: Tuple[int, int] = Field(default=(256, 256))
    context_size: Tuple[int, int] = Field(default=(256, 256))
    stride: Optional[Tuple[int, int]] = None  # None ⇒ 无重叠（stride=chip）
    overlap_px: int = Field(default=0, ge=0)
    padding_mode: str = PADDING_REFLECT
    crs_requirements: Tuple[str, ...] = ()  # 空 = 接受已声明 CRS；成员形如 "EPSG:32650"
    resolution_range: Optional[ResolutionRange] = None
    resampling_policy: str = RESAMPLING_NEAREST
    #: 显式重投影许可（R1-C2）：CRS/分辨率失配时，只有本开关 + 声明的
    #: resampling_policy 同时存在才执行 ReprojectStage；否则 typed 失败。
    allow_reproject: bool = False
    min_valid_data_ratio: float = Field(default=0.0, ge=0.0, le=1.0)

    def as_dict(self) -> Dict[str, Any]:
        return self.model_dump(mode="json")


class TemporalRequirements(BaseModel):
    """时序输入要求（temporal 契约）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_length: int = Field(default=1, ge=1)
    required_length: Optional[int] = Field(default=None, ge=1)
    missing_policy: str = "error"
    requires_quality_masks: bool = False

    @model_validator(mode="after")
    def _ordered(self) -> "TemporalRequirements":
        if self.required_length is not None and self.required_length > self.max_length:
            raise DescriptorError("required_length > max_length")
        if self.missing_policy not in MISSING_POLICIES:
            raise DescriptorError(f"unknown missing_policy {self.missing_policy!r}")
        return self


class ClassSchema(BaseModel):
    """输出类别 schema（semantic/promptable segmentation）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    classes: Tuple[str, ...] = ()          # index → 名称；index 0 通常是背景
    ignore_index: int = Field(default=255, ge=0, le=255)
    nodata_class: Optional[int] = None

    @model_validator(mode="after")
    def _nodata_in_range(self) -> "ClassSchema":
        if self.classes and self.nodata_class is not None:
            if not (0 <= self.nodata_class < len(self.classes) + 1):
                raise DescriptorError("nodata_class outside class index space")
        return self

    def as_dict(self) -> Dict[str, Any]:
        return self.model_dump(mode="json")


class MemoryEstimate(BaseModel):
    """粗粒度内存估计（bytes；loaded cache 与资源规划消费）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    weights_bytes: int = Field(default=0, ge=0)
    peak_activation_bytes: int = Field(default=0, ge=0)

    @property
    def total_bytes(self) -> int:
        return self.weights_bytes + self.peak_activation_bytes


class OutputTransform(BaseModel):
    """DL 运行时输出变换契约（V3 §B；进 fingerprint）。

    - ``activation``：provider 把原始 runtime 输出映射为契约概率的口径
      （softmax = logits 逐像素/逐样本 softmax；sigmoid = 二类 p/(1-p)
      展开；none = 模型已输出归一概率，validate 抽验兜底）。**必须**进
      指纹：同一模型不同 activation 是不同输出语义；
    - ``output_scale``：super-resolution 输出上采样因子（1 = 常规任务）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    activation: Literal["softmax", "sigmoid", "none"] = "softmax"
    output_scale: int = Field(default=1, ge=1, le=8)

    def as_dict(self) -> Dict[str, Any]:
        return {"activation": self.activation, "output_scale": self.output_scale}


#: 复用资格所允许的种子策略（与 ``app/lib/gis/algorithm_registry.py``
#: RandomSeedPolicy 同词表）。``unseeded``/``caller_seeded`` 的模型**不进
#: reuse**（同 key 不保证同结果）；``caller_seeded`` 的 seed 值进 key。
REUSE_ELIGIBLE_SEED_POLICIES = frozenset({"deterministic", "fixed_seed"})


class GeoModelDescriptor(BaseModel):
    """learned-model 完整描述 —— 「ModelDescriptor V2」（ADR-0119）。

    命名约定（架构挑战 M1）：与 chat 域 ``ModelDescriptorRegistry``
    （ADR-0102，app/services/chat/model_runtime/descriptors.py）**刻意
    不撞名**——本类是 GeoAI 推理模型契约，与 LLM chat transport 无关。

    ``provider_ref`` 硬约束（架构挑战 C1）：只能解析为 **ProviderRegistry
    进程内已注册的 provider 实例 id**；禁止 importlib/字符串路径动态加载
    ——模型包内容永不执行（校验不执行），代码只来自仓库内 trusted
    provider 实现。
    """

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)

    schema_version: str = DESCRIPTOR_SCHEMA_VERSION

    # ── 身份 ────────────────────────────────────────────────────────
    model_id: str = Field(min_length=3, max_length=128)
    model_version: str = Field(min_length=1, max_length=64)
    #: 内容完整性（模型包/权重工件的 sha256 hex）。注册时与 load 时双验。
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_type: str
    provider_ref: str = Field(min_length=1, max_length=512)
    provider_semantic_version: str = Field(min_length=1, max_length=32)

    # ── 能力 ────────────────────────────────────────────────────────
    task_types: Tuple[str, ...]
    input_modalities: Tuple[str, ...]
    input_bands: int = Field(ge=1, le=64)
    band_order: Tuple[str, ...] = ()
    normalization: NormalizationSpec = Field(default_factory=NormalizationSpec)
    output_types: Tuple[str, ...]
    class_schema: Optional[ClassSchema] = None

    # ── 空间/时序要求 ───────────────────────────────────────────────
    spatial: SpatialRequirements = Field(default_factory=SpatialRequirements)
    temporal: TemporalRequirements = Field(default_factory=TemporalRequirements)

    # ── 资源 ────────────────────────────────────────────────────────
    device_requirements: DeviceRequirements = Field(default_factory=DeviceRequirements)
    #: 粗粒度内存估计（host 进程内 loaded 权重 + 峰值激活，bytes）。
    memory_estimate: MemoryEstimate = Field(default_factory=lambda: MemoryEstimate())
    #: DL 运行时输出变换契约（V3 §B：activation/output_scale，进指纹）。
    output_transform: OutputTransform = Field(default_factory=lambda: OutputTransform())

    # ── 治理 ────────────────────────────────────────────────────────
    license: str = Field(default="unknown", max_length=128)
    artifact_format: str = Field(default="modelops-synthetic-v1", max_length=64)
    #: 确定性策略（复用资格门：unseeded/caller_seeded 不进 reuse）。
    random_seed_policy: Literal["deterministic", "fixed_seed", "caller_seeded", "unseeded"] = (
        "deterministic"
    )
    #: 来源/出处描述（id + 注册者；不含 secret）。
    provenance: Dict[str, Any] = Field(default_factory=dict)
    credentials_ref: Optional[str] = Field(default=None, max_length=128)

    @field_validator("task_types")
    @classmethod
    def _tasks_known(cls, v: Tuple[str, ...]) -> Tuple[str, ...]:
        unknown = sorted(set(v) - TASK_TYPES)
        if unknown:
            raise DescriptorError(f"unknown task_types {unknown}")
        if not v:
            raise DescriptorError("task_types must not be empty")
        return v

    @field_validator("input_modalities")
    @classmethod
    def _modalities_known(cls, v: Tuple[str, ...]) -> Tuple[str, ...]:
        unknown = sorted(set(v) - INPUT_MODALITIES)
        if unknown:
            raise DescriptorError(f"unknown input_modalities {unknown}")
        if not v:
            raise DescriptorError("input_modalities must not be empty")
        return v

    @field_validator("output_types")
    @classmethod
    def _outputs_known(cls, v: Tuple[str, ...]) -> Tuple[str, ...]:
        unknown = sorted(set(v) - OUTPUT_TYPES)
        if unknown:
            raise DescriptorError(f"unknown output_types {unknown}")
        if not v:
            raise DescriptorError("output_types must not be empty")
        return v

    @field_validator("provider_type")
    @classmethod
    def _provider_known(cls, v: str) -> str:
        if v not in PROVIDER_TYPES:
            raise DescriptorError(f"unknown provider_type {v!r}")
        return v

    @field_validator("spatial")
    @classmethod
    def _spatial_vocab(cls, v: SpatialRequirements) -> SpatialRequirements:
        if v.padding_mode not in PADDING_MODES:
            raise DescriptorError(f"unknown padding_mode {v.padding_mode!r}")
        if v.resampling_policy not in RESAMPLING_POLICIES:
            raise DescriptorError(f"unknown resampling_policy {v.resampling_policy!r}")
        cw, ch = v.chip_size
        pw, ph = v.context_size
        if cw < 4 or ch < 4:
            raise DescriptorError("chip_size too small (min 4x4)")
        if pw < cw or ph < ch:
            raise DescriptorError("context_size must be >= chip_size")
        return v

    @field_validator("device_requirements")
    @classmethod
    def _device_vocab(cls, v: DeviceRequirements) -> DeviceRequirements:
        if v.required not in DEVICES:
            raise DescriptorError(f"unknown device {v.required!r}")
        return v

    @model_validator(mode="after")
    def _cross_field(self) -> "GeoModelDescriptor":
        if len(self.band_order) not in (0, self.input_bands):
            raise DescriptorError(
                f"band_order length {len(self.band_order)} != input_bands {self.input_bands}"
            )
        seg_tasks = {"semantic_segmentation", "promptable_segmentation", "instance_segmentation"}
        if set(self.task_types) & seg_tasks and self.class_schema is None:
            raise DescriptorError("segmentation tasks require class_schema")
        _reject_secret_keys("descriptor.provenance", self.provenance)
        _reject_secret_keys("descriptor", {"provider_ref": self.provider_ref})
        return self

    # ── 便捷 ────────────────────────────────────────────────────────
    @property
    def identity(self) -> Tuple[str, str]:
        return (self.model_id, self.model_version)

    def as_dict(self) -> Dict[str, Any]:
        return self.model_dump(mode="json")

    def fingerprint_payload(self) -> Dict[str, Any]:
        """进入 InferenceFingerprint 的规范化投影（排除 provenance 元数据）。

        provenance（注册者/时间等）不参与输出语义 ⇒ 不进复用 key；
        checksum/语义/空间/资源字段全部参与。
        """
        return {
            "schema_version": self.schema_version,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "checksum": self.checksum,
            "provider_type": self.provider_type,
            "provider_semantic_version": self.provider_semantic_version,
            "task_types": list(self.task_types),
            "input_modalities": list(self.input_modalities),
            "input_bands": self.input_bands,
            "band_order": list(self.band_order),
            "normalization": self.normalization.as_dict(),
            "output_types": list(self.output_types),
            "class_schema": self.class_schema.model_dump(mode="json") if self.class_schema else None,
            "spatial": self.spatial.model_dump(mode="json"),
            "temporal": self.temporal.model_dump(mode="json"),
            "output_transform": self.output_transform.as_dict(),
            "artifact_format": self.artifact_format,
            "random_seed_policy": self.random_seed_policy,
        }
