"""内置 tiny 模型种子（ADR-0119 §3.6 registry seeds）。

种子 = descriptor + **synthetic 包报告**（确定性权重 = checksum 的
sha256 导出函数，见 tiny_reference.derive_anchors）。checksum = descriptor
语义负载的 canonical sha256 —— 即「包内容」的完整内容寻址身份（synthetic
包由 provider 从 checksum 确定性重建，包报告如实标注 synthetic=true）。
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.lib.data.fingerprints import canonical_dumps, sha256_hex
from app.lib.modelops.capabilities import (
    MODALITY_OPTICAL_MULTISPECTRAL,
    OUTPUT_INSTANCE_MASKS,
    MODALITY_OPTICAL_RGB,
    MODALITY_SAR,
    OUTPUT_CLASS_RASTER,
    OUTPUT_CONFIDENCE_RASTER,
    OUTPUT_DETECTIONS,
    OUTPUT_EMBEDDINGS,
    OUTPUT_LABELS,
    OUTPUT_TEMPORAL_STACK,
    TASK_CLASSIFICATION,
    TASK_EMBEDDING,
    TASK_INSTANCE_SEGMENTATION,
    TASK_OBJECT_DETECTION,
    TASK_PROMPTABLE_SEGMENTATION,
    TASK_SEMANTIC_SEGMENTATION,
    TASK_TEMPORAL_FORECAST,
)
from app.lib.modelops.descriptor import (
    ClassSchema,
    DeviceRequirements,
    GeoModelDescriptor,
    MemoryEstimate,
    NormalizationSpec,
    ResolutionRange,
    SpatialRequirements,
    TemporalRequirements,
)
from app.services.modelops.providers.base import ProviderRegistry

#: 内置种子的 scope（global 维度；值仅具语义可读性）。
SEED_OWNER = {"global": "builtin"}


def synthetic_checksum(payload: Dict[str, Any]) -> str:
    """synthetic 包 checksum = descriptor 语义负载的 canonical sha256。"""
    return sha256_hex(canonical_dumps(payload))


def _package_report(descriptor: GeoModelDescriptor) -> Dict[str, Any]:
    """synthetic 包报告（诚实标注：无实体包文件，权重确定性派生）。"""
    return {
        "synthetic": True,
        "checksum": descriptor.checksum,
        "weights": "deterministic-from-checksum (tiny reference)",
        "metadata": {"format": "modelops-synthetic-v1"},
        "entry_count": 0,
        "total_bytes": 0,
    }


def seed_descriptors() -> List[GeoModelDescriptor]:
    """全部内置种子（checksum 在构造时由语义负载派生）。"""

    def make(**kwargs: Any) -> GeoModelDescriptor:
        serializable: Dict[str, Any] = {}
        for key, value in kwargs.items():
            if hasattr(value, "model_dump"):
                serializable[key] = value.model_dump(mode="json")
            elif isinstance(value, tuple):
                serializable[key] = list(value)
            else:
                serializable[key] = value
        checksum = synthetic_checksum(serializable)
        return GeoModelDescriptor.model_validate({**kwargs, "checksum": checksum})

    common_spatial = dict(
        chip_size=(64, 64),
        context_size=(80, 80),
        overlap_px=8,
        padding_mode="reflect",
        resolution_range=ResolutionRange(min_m_per_px=0.1, max_m_per_px=100.0),
    )
    common_device = DeviceRequirements(required="cpu", allow_cpu_fallback=True)

    seeds: List[GeoModelDescriptor] = [
        make(
            model_id="tiny-landcover-seg",
            model_version="1.0.0",
            provider_type="local_reference",
            provider_ref="tiny-reference",
            provider_semantic_version="tiny/1.0.0",
            task_types=(TASK_SEMANTIC_SEGMENTATION,),
            input_modalities=(MODALITY_OPTICAL_RGB,),
            input_bands=3,
            band_order=("red", "green", "blue"),
            normalization=NormalizationSpec(kind="none"),
            output_types=(OUTPUT_CLASS_RASTER, OUTPUT_CONFIDENCE_RASTER),
            class_schema=ClassSchema(classes=("background", "bright", "mid"),
                                     ignore_index=255, nodata_class=None),
            spatial=SpatialRequirements(**common_spatial),
            temporal=TemporalRequirements(max_length=1),
            device_requirements=common_device,
            memory_estimate=MemoryEstimate(weights_bytes=4096, peak_activation_bytes=1 << 20),
            license="CC0-1.0 (synthetic fixture)",
            artifact_format="modelops-synthetic-v1",
            random_seed_policy="deterministic",
        ),
        make(
            model_id="tiny-sar-detector",
            model_version="1.0.0",
            provider_type="local_reference",
            provider_ref="tiny-detection",
            provider_semantic_version="tiny-detect/1.0.0",
            task_types=(TASK_OBJECT_DETECTION,),
            input_modalities=(MODALITY_SAR,),
            input_bands=2,
            band_order=("VV", "VH"),
            normalization=NormalizationSpec(kind="none"),
            output_types=(OUTPUT_DETECTIONS,),
            class_schema=ClassSchema(classes=("background", "target")),
            spatial=SpatialRequirements(**common_spatial),
            device_requirements=common_device,
            memory_estimate=MemoryEstimate(weights_bytes=2048, peak_activation_bytes=1 << 20),
            license="CC0-1.0 (synthetic fixture)",
            random_seed_policy="deterministic",
        ),
        make(
            model_id="tiny-promptable-seg",
            model_version="1.0.0",
            provider_type="local_reference",
            provider_ref="promptable-reference",
            provider_semantic_version="promptable-ref/1.0.0",
            task_types=(TASK_PROMPTABLE_SEGMENTATION,),
            input_modalities=(MODALITY_OPTICAL_RGB,),
            input_bands=3,
            normalization=NormalizationSpec(kind="none"),
            output_types=(OUTPUT_CLASS_RASTER,),
            class_schema=ClassSchema(classes=("background", "object")),
            spatial=SpatialRequirements(**common_spatial),
            device_requirements=common_device,
            memory_estimate=MemoryEstimate(weights_bytes=1024),
            license="CC0-1.0 (synthetic fixture)",
            random_seed_policy="deterministic",
        ),
        make(
            model_id="tiny-temporal-forecast",
            model_version="1.0.0",
            provider_type="local_reference",
            provider_ref="temporal-reference",
            provider_semantic_version="temporal-ref/1.0.0",
            task_types=(TASK_TEMPORAL_FORECAST,),
            input_modalities=(MODALITY_OPTICAL_MULTISPECTRAL,),
            input_bands=2,
            normalization=NormalizationSpec(kind="none"),
            output_types=(OUTPUT_TEMPORAL_STACK,),
            spatial=SpatialRequirements(**common_spatial),
            temporal=TemporalRequirements(max_length=8, missing_policy="flag"),
            device_requirements=common_device,
            memory_estimate=MemoryEstimate(weights_bytes=1024),
            license="CC0-1.0 (synthetic fixture)",
            random_seed_policy="deterministic",
        ),
        make(
            model_id="tiny-instance-seg",
            model_version="1.0.0",
            provider_type="local_reference",
            provider_ref="tiny-instance",
            provider_semantic_version="tiny-instance/1.0.0",
            task_types=(TASK_INSTANCE_SEGMENTATION,),
            input_modalities=(MODALITY_OPTICAL_RGB,),
            input_bands=1,
            normalization=NormalizationSpec(kind="none"),
            output_types=(OUTPUT_INSTANCE_MASKS,),
            class_schema=ClassSchema(classes=("background", "target")),
            spatial=SpatialRequirements(**common_spatial),
            device_requirements=common_device,
            memory_estimate=MemoryEstimate(weights_bytes=1024),
            license="CC0-1.0 (synthetic fixture)",
            random_seed_policy="deterministic",
        ),
        make(
            model_id="tiny-chip-embedder",
            model_version="1.0.0",
            provider_type="local_reference",
            provider_ref="tiny-reference",
            provider_semantic_version="tiny/1.0.0",
            task_types=(TASK_EMBEDDING, TASK_CLASSIFICATION),
            input_modalities=(MODALITY_OPTICAL_RGB,),
            input_bands=3,
            normalization=NormalizationSpec(kind="none"),
            output_types=(OUTPUT_EMBEDDINGS, OUTPUT_LABELS),
            spatial=SpatialRequirements(**common_spatial),
            device_requirements=common_device,
            memory_estimate=MemoryEstimate(weights_bytes=2048),
            license="CC0-1.0 (synthetic fixture)",
            random_seed_policy="deterministic",
        ),
    ]
    return seeds





def seed_providers(registry: ProviderRegistry) -> None:
    """把内置 reference providers 注册进 ProviderRegistry（幂等）。"""
    from app.services.modelops.providers.promptable_reference import (
        PromptableReferenceProvider,
    )
    from app.services.modelops.providers.temporal_reference import (
        TemporalReferenceProvider,
    )
    from app.services.modelops.providers.tiny_detection import TinyDetectionProvider
    from app.services.modelops.providers.tiny_instance import TinyInstanceProvider
    from app.services.modelops.providers.tiny_reference import TinyReferenceProvider

    for provider in (
        TinyReferenceProvider(),
        TinyDetectionProvider(),
        PromptableReferenceProvider(),
        TemporalReferenceProvider(),
        TinyInstanceProvider(),
    ):
        if not registry.has(provider.capabilities().provider_id):
            registry.register(provider)


def seed_registry(store: Any, providers: ProviderRegistry) -> List[str]:
    """注册全部种子模型（幂等；返回 model_id 清单）。"""
    registered: List[str] = []
    for descriptor in seed_descriptors():
        store.register(
            descriptor,
            owner_scope=SEED_OWNER,
            registered_by="modelops.seeds",
            package_report=_package_report(descriptor),
            known_provider_refs=providers.has,
        )
        registered.append(descriptor.model_id)
    return registered
