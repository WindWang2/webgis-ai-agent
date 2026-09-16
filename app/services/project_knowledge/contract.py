"""ProjectKnowledge 契约（方向：Project Knowledge Projection）。

本模块是项目级知识投影的**唯一类型真相**：closed vocab、写入门请求、
检索/复用判定结果、有界序列化。持久化模型在
:mod:`app.models.project_knowledge`；本层不 import DB。

红线（与 gis_memory / evidence_claim 同源）：
- 投影**不是第二个权威 store**：每行语义身份全部借自既有权威 id
  (``authority_store`` + ``authority_id`` + ``version_token``)，可随时从
  权威源重建（rebuild 幂等且充分）；
- 不存 payload / CoT / secret；summary ≤ SUMMARY_CHAR_BUDGET；refs ≤ 8
  且只存 ref-tag（{authority,id,relation,token?}）；
- 复用判定只看指纹与作用域（AOI/temporal/method/上游指纹），**名字相似
  永不升级判定**；正向证明缺失时判定一律降级（fail-closed）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ── closed vocab：实体类别（brief 实体清单 → 权威源映射） ──────────────
EK_DATASET_VERSION = "dataset_version"   # ProjectDataset（ds_*）
EK_ARTIFACT = "artifact"                 # Artifact / ArtifactRevision head
EK_MAP_PRODUCT = "map_product"           # MapProductVersion (project, version_no)
EK_WORKFLOW = "workflow"                 # Workflow（current_revision 指纹）
EK_METHOD = "method"                     # ArtifactLineage capability:algorithm 对
EK_MISSION = "mission"                   # GISMissionRow (msn-*)
EK_FAILURE_PATTERN = "failure_pattern"   # failed mission / provider failure
EK_PLACE = "place"                       # gis_memory resolved_place（project scope）
EK_PREFERENCE = "preference"             # carto_project_facts preference

ENTITY_KINDS: Tuple[str, ...] = (
    EK_DATASET_VERSION,
    EK_ARTIFACT,
    EK_MAP_PRODUCT,
    EK_WORKFLOW,
    EK_METHOD,
    EK_MISSION,
    EK_FAILURE_PATTERN,
    EK_PLACE,
    EK_PREFERENCE,
)

# ── closed vocab：权威 store（borrowed identity 的第一半） ─────────────
AS_PROJECT_DATASET = "project_dataset"
AS_ARTIFACT = "artifact"
AS_MAP_PRODUCT = "map_product"
AS_WORKFLOW = "workflow"
AS_MISSION = "mission"
AS_GIS_MEMORY = "gis_memory"
AS_CARTO_FACT = "carto_fact"
AS_SESSION_REF = "session_ref"

AUTHORITY_STORES: Tuple[str, ...] = (
    AS_PROJECT_DATASET,
    AS_ARTIFACT,
    AS_MAP_PRODUCT,
    AS_WORKFLOW,
    AS_MISSION,
    AS_GIS_MEMORY,
    AS_CARTO_FACT,
    AS_SESSION_REF,
)

# ── closed vocab：状态 / 失效依据 / 复用判定 / 关系 tag ────────────────
ST_ACTIVE = "active"
ST_STALE = "stale"
ST_SUPERSEDED = "superseded"
ST_INVALIDATED = "invalidated"
ENTRY_STATUSES: Tuple[str, ...] = (ST_ACTIVE, ST_STALE, ST_SUPERSEDED, ST_INVALIDATED)

RULE_NONE = ""
RULE_VERSION_BUMP = "version_bump"    # 权威 digest/token 前进（数据/内容换版）
RULE_HEAD_CHANGED = "head_changed"    # artifact head revision 变化
RULE_CLAIM_LOST = "claim_lost"        # verified_by 的 claim 不再 SUPPORTED
RULE_MANUAL = "manual"                # 显式撤销
RULE_SCOPE_GONE = "scope_gone"        # 权威行消失 / dataset detached / 项目终结
INVALIDATION_RULES: Tuple[str, ...] = (
    RULE_NONE, RULE_VERSION_BUMP, RULE_HEAD_CHANGED, RULE_CLAIM_LOST,
    RULE_MANUAL, RULE_SCOPE_GONE,
)

VERDICT_EXACT = "exact"
VERDICT_RECOMPUTE_PARTIAL = "recompute_partial"
VERDICT_NOT_REUSABLE = "not_reusable"
REUSE_VERDICTS: Tuple[str, ...] = (VERDICT_EXACT, VERDICT_RECOMPUTE_PARTIAL, VERDICT_NOT_REUSABLE)

REL_DERIVED_FROM = "derived_from"     # ArtifactLineage / source_dataset
REL_PRODUCED = "produced"             # mission → artifact/map_product
REL_USED = "used"                     # mission → dataset/artifact input
REL_VERIFIED_BY = "verified_by"       # → claim id（仅 SUPPORTED 时作正向证据）
REL_APPLIES_TO = "applies_to"         # method → dataset kind
REL_FAILED_WITH = "failed_with"       # failure_pattern → error_code/ref
REL_REUSABLE_FOR = "reusable_for"     # 检索期计算，不落库（此处仅预留词表）
RELATION_TAGS: Tuple[str, ...] = (
    REL_DERIVED_FROM, REL_PRODUCED, REL_USED, REL_VERIFIED_BY,
    REL_APPLIES_TO, REL_FAILED_WITH, REL_REUSABLE_FOR,
)

# ── 有界预算 ──────────────────────────────────────────────────────────
SUMMARY_CHAR_BUDGET = 300             # 单行摘要（写入口截断）
REFS_MAX = 8                          # ref-tag 数上限
REF_TOKEN_MAX = 128                   # ref-tag 内 token 长度上限
SUBJECT_CHAR_BUDGET = 255             # 与 gis_memory subject 同界
METHOD_KEY_MAX = 200                  # "capability:algorithm"
TEMPORAL_LABEL_MAX = 64
CARD_CHAR_BUDGET = 1600               # ProjectContextCard 渲染字符预算（D5）
CARD_MAX_ITEMS = 12                   # card 条目上限（D5）
CARD_FAILURE_WARNING_MAX = 3          # 失败警告行数上限（bounded warnings）
CARD_SUMMARY_MAX = 80                 # card 内摘要截断（对齐 proactive_retriever）
PROJECT_ROW_BUDGET = 400              # 每 (org, project) 行数预算（= SCOPE_BUDGET[project]）
REBUILD_MISSION_MAX = 32              # rebuild 每项目采样的 mission 上限
REBUILD_MAP_PRODUCT_MAX = 8           # rebuild 采样的 map product 版本上限


class KnowledgePolicyError(ValueError):
    """投影写入门拒绝（kind/authority/status/预算不合格）。"""


def validate_bbox(value: Any) -> Optional[Tuple[float, float, float, float]]:
    """校验 [minx, miny, maxx, maxy]；非法返回 None（宁缺勿错）。"""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        xs = tuple(float(v) for v in value)
    except (TypeError, ValueError):
        return None
    minx, miny, maxx, maxy = xs
    if minx > maxx or miny > maxy:
        return None
    return (minx, miny, maxx, maxy)


def bbox_contains(outer: Sequence[float], inner: Sequence[float]) -> bool:
    """inner 是否落入 outer（边界相等视为包含；保守相等语义，不做模糊容差）。"""
    if len(outer) != 4 or len(inner) != 4:
        return False
    return (
        outer[0] <= inner[0] and outer[1] <= inner[1]
        and inner[2] <= outer[2] and inner[3] <= outer[3]
    )


@dataclass(frozen=True)
class RefTag:
    """关系 ref-tag：指回权威 id 的有界关系边投影（不建第二张图）。"""

    relation: str
    authority: str
    id: str
    token: str = ""   # 索引时观察到的权威指纹（上游 liveness 复核用）

    def validate(self) -> None:
        if self.relation not in RELATION_TAGS:
            raise KnowledgePolicyError(f"未知 relation {self.relation!r}")
        if self.authority not in AUTHORITY_STORES:
            raise KnowledgePolicyError(f"ref-tag authority {self.authority!r} 不在 closed vocab")
        if not self.id:
            raise KnowledgePolicyError("ref-tag id 不得为空")

    def to_dict(self) -> Dict[str, str]:
        return {
            "relation": self.relation,
            "authority": self.authority,
            "id": str(self.id)[:REF_TOKEN_MAX],
            "token": str(self.token or "")[:REF_TOKEN_MAX],
        }


def ref_tags_from(value: Any) -> List[RefTag]:
    """宽松解析（索引器内部用；脏 tag 丢弃不炸）。"""
    out: List[RefTag] = []
    for item in (value or [])[:REFS_MAX]:
        if not isinstance(item, dict):
            continue
        try:
            tag = RefTag(
                relation=str(item.get("relation") or ""),
                authority=str(item.get("authority") or ""),
                id=str(item.get("id") or ""),
                token=str(item.get("token") or ""),
            )
            tag.validate()
        except KnowledgePolicyError:
            continue
        out.append(tag)
    return out


@dataclass(frozen=True)
class KnowledgeUpsert:
    """一次投影 upsert（store.upsert_entry 的唯一入口形状）。

    ``version_token`` 是索引时观察到的权威版本指纹（fingerprint/sha/revision
    字符串）。同 natural key 同 token = 再验证刷新；不同 token = 旧行
    superseded + 新行 active。
    """

    org_id: str
    project_id: str
    entity_kind: str
    authority_store: str
    authority_id: str
    subject: str
    version_token: str
    summary: str = ""
    bbox: Optional[Sequence[float]] = None
    temporal_label: Optional[str] = None
    method_key: Optional[str] = None
    refs: Sequence[RefTag] = ()
    invalidation_rule: str = RULE_NONE

    def validate(self) -> None:
        if not self.org_id:
            raise KnowledgePolicyError("org_id 缺失 —— 知识必须落租户桶（fail-closed）")
        if not self.project_id:
            raise KnowledgePolicyError("project_id 缺失 —— 项目知识必须落项目桶")
        if self.entity_kind not in ENTITY_KINDS:
            raise KnowledgePolicyError(f"未知 entity_kind {self.entity_kind!r}")
        if self.authority_store not in AUTHORITY_STORES:
            raise KnowledgePolicyError(f"未知 authority_store {self.authority_store!r}")
        if not self.authority_id:
            raise KnowledgePolicyError("authority_id 不得为空 —— 知识必须回指权威源")
        if not self.subject:
            raise KnowledgePolicyError("subject 不得为空")
        if self.invalidation_rule not in INVALIDATION_RULES:
            raise KnowledgePolicyError(f"未知 invalidation_rule {self.invalidation_rule!r}")
        if len(self.subject) > SUBJECT_CHAR_BUDGET:
            raise KnowledgePolicyError(f"subject 超长（≤{SUBJECT_CHAR_BUDGET}）")
        for tag in self.refs[:REFS_MAX]:
            tag.validate()


@dataclass(frozen=True)
class KnowledgeEntry:
    """检索/投影面的不可变知识条目视图（store 行的纯投影）。"""

    id: str
    org_id: str
    project_id: str
    entity_kind: str
    authority_store: str
    authority_id: str
    subject: str
    version_token: str
    summary: str = ""
    bbox: Optional[Tuple[float, float, float, float]] = None
    temporal_label: Optional[str] = None
    method_key: Optional[str] = None
    refs: List[RefTag] = field(default_factory=list)
    status: str = ST_ACTIVE
    invalidation_rule: str = RULE_NONE
    version: int = 1
    weight: float = 0.0
    created_at: Optional[str] = None
    last_validated_at: Optional[str] = None

    def to_bounded_dict(self) -> Dict[str, Any]:
        """有界序列化（API/日志同形）：ids + 摘要 + ref-tag，绝无 payload。"""
        return {
            "id": self.id,
            "entity_kind": self.entity_kind,
            "authority": {"store": self.authority_store, "id": self.authority_id},
            "version_token": self.version_token[:REF_TOKEN_MAX],
            "subject": self.subject,
            "summary": self.summary[:CARD_SUMMARY_MAX],
            "bbox": list(self.bbox) if self.bbox else None,
            "temporal_label": self.temporal_label,
            "method_key": self.method_key,
            "refs": [t.to_dict() for t in self.refs[:REFS_MAX]],
            "status": self.status,
            "version": int(self.version),
        }


@dataclass(frozen=True)
class ReuseQuery:
    """一次跨 Mission 复用检索的作用域请求。

    全部字段可为 None（未知即不能确认 → 判定降级，绝不 wildcard 放行）。
    """

    bbox: Optional[Sequence[float]] = None
    temporal_label: Optional[str] = None
    method_key: Optional[str] = None
    dataset_fingerprints: Optional[Dict[str, str]] = None   # ds_id → fingerprint
    kinds: Optional[Sequence[str]] = None                    # 限定实体类别
    limit: int = 8


@dataclass(frozen=True)
class ReuseCandidate:
    """一条复用候选：条目 + 判定 + **可读理由**（不透明判定不可接受）。"""

    entry: KnowledgeEntry
    verdict: str
    reasons: List[str] = field(default_factory=list)
    stale_causes: List[str] = field(default_factory=list)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "entry": self.entry.to_bounded_dict(),
            "verdict": self.verdict,
            "reasons": list(self.reasons)[:6],
            "stale_causes": list(self.stale_causes)[:6],
        }


__all__ = [
    "ENTITY_KINDS", "AUTHORITY_STORES", "ENTRY_STATUSES", "INVALIDATION_RULES",
    "REUSE_VERDICTS", "RELATION_TAGS",
    "EK_DATASET_VERSION", "EK_ARTIFACT", "EK_MAP_PRODUCT", "EK_WORKFLOW",
    "EK_METHOD", "EK_MISSION", "EK_FAILURE_PATTERN", "EK_PLACE", "EK_PREFERENCE",
    "AS_PROJECT_DATASET", "AS_ARTIFACT", "AS_MAP_PRODUCT", "AS_WORKFLOW",
    "AS_MISSION", "AS_GIS_MEMORY", "AS_CARTO_FACT", "AS_SESSION_REF",
    "ST_ACTIVE", "ST_STALE", "ST_SUPERSEDED", "ST_INVALIDATED",
    "RULE_NONE", "RULE_VERSION_BUMP", "RULE_HEAD_CHANGED", "RULE_CLAIM_LOST",
    "RULE_MANUAL", "RULE_SCOPE_GONE",
    "VERDICT_EXACT", "VERDICT_RECOMPUTE_PARTIAL", "VERDICT_NOT_REUSABLE",
    "REL_DERIVED_FROM", "REL_PRODUCED", "REL_USED", "REL_VERIFIED_BY",
    "REL_APPLIES_TO", "REL_FAILED_WITH", "REL_REUSABLE_FOR",
    "SUMMARY_CHAR_BUDGET", "REFS_MAX", "REF_TOKEN_MAX", "SUBJECT_CHAR_BUDGET",
    "METHOD_KEY_MAX", "TEMPORAL_LABEL_MAX",
    "CARD_CHAR_BUDGET", "CARD_MAX_ITEMS", "CARD_FAILURE_WARNING_MAX",
    "CARD_SUMMARY_MAX", "PROJECT_ROW_BUDGET",
    "REBUILD_MISSION_MAX", "REBUILD_MAP_PRODUCT_MAX",
    "KnowledgePolicyError", "RefTag", "ref_tags_from", "KnowledgeUpsert",
    "KnowledgeEntry", "ReuseQuery", "ReuseCandidate",
    "validate_bbox", "bbox_contains",
]
