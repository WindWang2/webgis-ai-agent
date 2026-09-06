"""app.lib.data —— GIS Data / Artifact / Workspace Foundation V3 契约层。

纯契约与纯算法（vocabulary / fingerprints / artifact_contract / quality /
lifecycle / staleness / large_data）：零 I/O、不依赖 app.services，可独立
测试。有状态服务见 app/services/data_catalog、data_profile、data_ingest、
workspace。
"""
from app.lib.data.artifact_contract import (
    CONTRACT_VERSION,
    ArtifactContract,
    Cacheability,
    ContractDiagnostic,
    LineageInfo,
    ProducedBy,
    RasterShape,
    Reproducibility,
    SourceInfo,
    TemporalExtent,
    from_artifact_record,
    from_db_artifact,
    from_raster_descriptor,
    from_ref_descriptor,
)
from app.lib.data.fingerprints import (
    ChangeClass,
    FingerprintSet,
    StalenessVerdict,
    canonical_fingerprint,
    classify_change,
    compute_reuse_fingerprint,
    safe_canonical_fingerprint,
    staleness_verdict,
)
from app.lib.data.vocabulary import (
    ArtifactCategory,
    LifecycleState,
    LogicalRole,
    MaterializationPolicy,
    PersistenceTier,
    QualityStatus,
    can_transition,
    category_from_any,
    coerce_category,
    lifecycle_from_session_status,
)

__all__ = [
    "CONTRACT_VERSION",
    "ArtifactContract",
    "Cacheability",
    "ContractDiagnostic",
    "LineageInfo",
    "ProducedBy",
    "RasterShape",
    "Reproducibility",
    "SourceInfo",
    "TemporalExtent",
    "from_artifact_record",
    "from_db_artifact",
    "from_raster_descriptor",
    "from_ref_descriptor",
    "ChangeClass",
    "FingerprintSet",
    "StalenessVerdict",
    "canonical_fingerprint",
    "classify_change",
    "compute_reuse_fingerprint",
    "safe_canonical_fingerprint",
    "staleness_verdict",
    "ArtifactCategory",
    "LifecycleState",
    "LogicalRole",
    "MaterializationPolicy",
    "PersistenceTier",
    "QualityStatus",
    "can_transition",
    "category_from_any",
    "coerce_category",
    "lifecycle_from_session_status",
]
