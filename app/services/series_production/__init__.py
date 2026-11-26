"""Series Production 包 —— 系列出版计划生产（goal-carto-design-series WP5）。

单一导入面。执行权威边界：
- 真实渲染走既有链（print_layout_authoring / publication_export），本包
  只做冻结对账、缓存、QA、manifest —— 不建第二渲染器；
- 系列作者计划与成员状态属 ``app/services/map_series``（WP3 权威）；
  本包消费其 freeze plan typed 结构。
"""
from app.services.series_production.contract import (
    SERIES_FORMATS,
    SERIES_FREEZE_PLAN_VERSION,
    SERIES_MANIFEST_VERSION,
    FrozenMember,
    MemberArtifactRecord,
    MemberQaRecord,
    MemberResult,
    SeriesFreezePlan,
    output_formats,
    validate_freeze_plan_payload,
)
from app.services.series_production.executor import (
    execute_series_production,
    member_cache_key,
    qa_member_artifacts,
)
from app.services.series_production.fingerprint import document_fingerprint
from app.services.series_production.manifest import build_series_manifest

__all__ = [
    "SERIES_FORMATS",
    "SERIES_FREEZE_PLAN_VERSION",
    "SERIES_MANIFEST_VERSION",
    "FrozenMember",
    "MemberArtifactRecord",
    "MemberQaRecord",
    "MemberResult",
    "SeriesFreezePlan",
    "output_formats",
    "validate_freeze_plan_payload",
    "execute_series_production",
    "member_cache_key",
    "qa_member_artifacts",
    "document_fingerprint",
    "build_series_manifest",
]
