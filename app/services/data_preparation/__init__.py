"""数据准备域 —— inspect → propose → validate → prepare → bind（公共出口）。

与既有面的边界（BASELINE.md）：
- 画像/质量/解析：消费 app/lib/data/profile.py + quality.py 的已就位证据，
  绝不重扫数据；
- 语义身份：descriptor / 角色绑定 / preflight / provenance 以
  app/services/dataset_semantics 为权威，本域只做提案与准备计划；
- 提案角色词表：vocabulary.py 单一真相（细粒度提案层），
  SemanticFieldRole 只经 to_semantic_roles 单向投影。

禁止绕包导入（与 dataset_semantics 同纪律）。
"""
from app.services.data_preparation.field_proposal import (
    AMBIGUITY_GAP,
    AUTO_ADOPT_CONFIDENCE,
    MIN_CANDIDATE_CONFIDENCE,
    FieldProposal,
    RoleCandidate,
    propose_field_roles,
    proposals_summary,
)
from app.services.data_preparation.spatial_receipt import (
    InspectionFinding,
    SpatialInspectionReceipt,
    build_spatial_receipt,
)
from app.services.data_preparation.vocabulary import (
    ADMIN_NAME_TERMS,
    FINDING_AXIS_ORDER_SUSPECT,
    FINDING_DENOMINATOR_ABSENT,
    FINDING_PK_DUPLICATED,
    FINDING_PK_MISSING,
    PROPOSAL_ROLES,
    ROLE_SYNONYMS,
    FindingSeverity,
    ReasonCode,
    SuggestionCode,
    is_proposal_role,
    to_semantic_roles,
)

__all__ = [
    "AMBIGUITY_GAP",
    "AUTO_ADOPT_CONFIDENCE",
    "MIN_CANDIDATE_CONFIDENCE",
    "ADMIN_NAME_TERMS",
    "FINDING_AXIS_ORDER_SUSPECT",
    "FINDING_DENOMINATOR_ABSENT",
    "FINDING_PK_DUPLICATED",
    "FINDING_PK_MISSING",
    "PROPOSAL_ROLES",
    "ROLE_SYNONYMS",
    "FieldProposal",
    "RoleCandidate",
    "FindingSeverity",
    "InspectionFinding",
    "ReasonCode",
    "SuggestionCode",
    "SpatialInspectionReceipt",
    "build_spatial_receipt",
    "is_proposal_role",
    "propose_field_roles",
    "proposals_summary",
    "to_semantic_roles",
]
