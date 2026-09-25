"""Dataset Semantics —— GISDatasetDescriptor 全链契约包（ADR-0215）。

公共出口（单一导入面；消费方禁止绕过本包直取内部模块）：

- ``derive_descriptor`` / ``build_descriptor``：铸造（builder）；
- ``DatasetSemanticStore`` / ``get_dataset_semantic_store``：有界持久化（store）；
- ``evaluate_reuse``：指纹对账 → 复用裁决（reuse）；
- ``descriptor_resolver_profile`` / ``descriptor_to_dataset_profile`` /
  ``descriptor_to_measurement_profile`` / ``descriptor_semantic_view`` /
  ``descriptor_to_d1_kwargs``：单向投影（projections）。
"""
from app.services.dataset_semantics.builder import (
    MAX_DESCRIPTOR_BYTES,
    SAMPLING_FEATURE_CAP,
    bounded_value_samples,
    build_descriptor,
    build_descriptor_from_fabric_descriptor,
    build_descriptor_from_ref_descriptor,
    derive_descriptor,
    derive_descriptor_from_spatial_profile,
    derive_descriptor_from_v3,
    descriptor_payload_bytes,
)
from app.services.dataset_semantics.projections import (
    descriptor_resolver_profile,
    descriptor_semantic_view,
    descriptor_to_d1_kwargs,
    descriptor_to_dataset_profile,
    descriptor_to_measurement_profile,
)
from app.services.dataset_semantics.reuse import (
    ReuseDecision,
    evaluate_reuse,
    evaluate_reuse_with_history,
)
from app.services.dataset_semantics.store import (
    DatasetSemanticStore,
    DescriptorRecord,
    PutResult,
    get_dataset_semantic_store,
    reset_dataset_semantic_store,
)

__all__ = [
    "MAX_DESCRIPTOR_BYTES",
    "SAMPLING_FEATURE_CAP",
    "build_descriptor",
    "build_descriptor_from_fabric_descriptor",
    "build_descriptor_from_ref_descriptor",
    "derive_descriptor",
    "derive_descriptor_from_spatial_profile",
    "derive_descriptor_from_v3",
    "descriptor_payload_bytes",
    "descriptor_resolver_profile",
    "descriptor_semantic_view",
    "descriptor_to_d1_kwargs",
    "descriptor_to_dataset_profile",
    "descriptor_to_measurement_profile",
    "ReuseDecision",
    "evaluate_reuse",
    "evaluate_reuse_with_history",
    "DatasetSemanticStore",
    "DescriptorRecord",
    "PutResult",
    "get_dataset_semantic_store",
    "reset_dataset_semantic_store",
]
