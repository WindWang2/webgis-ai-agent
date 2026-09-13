"""SpatialMemory 契约（方向 9 R1，ADR-0183）。

本模块是记忆子系统的**唯一类型真相**：kind/scope/status/证据源的 closed
vocab、写入请求、检索结果、有界序列化。持久化模型在
:mod:`app.models.spatial_memory`；本层不 import DB。

红线（任务书「明确不做」）：不存 raw prompt / CoT / credential / 完整
payload；cache hit 不是语义记忆；value 一律有界且 ref-first。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

#: 记忆类别（closed vocab —— 与模型列 CheckConstraint 对应的 10 类）。
KIND_RESOLVED_PLACE = "resolved_place"
KIND_BOUNDARY_REF = "boundary_ref"
KIND_DATASET_SEMANTICS = "dataset_semantics"
KIND_FIELD_ROLE = "field_role"
KIND_CRS_RESOLUTION = "crs_resolution"
KIND_ANALYSIS_ARTIFACT = "analysis_artifact"
KIND_SUCCESSFUL_STRATEGY = "successful_strategy"
KIND_PROVIDER_FAILURE = "provider_failure"
KIND_PRODUCT_DECISION = "product_decision"
KIND_USER_CARTO_PREF = "user_cartographic_preference"

SPATIAL_MEMORY_KINDS: tuple[str, ...] = (
    KIND_RESOLVED_PLACE,
    KIND_BOUNDARY_REF,
    KIND_DATASET_SEMANTICS,
    KIND_FIELD_ROLE,
    KIND_CRS_RESOLUTION,
    KIND_ANALYSIS_ARTIFACT,
    KIND_SUCCESSFUL_STRATEGY,
    KIND_PROVIDER_FAILURE,
    KIND_PRODUCT_DECISION,
    KIND_USER_CARTO_PREF,
)

SCOPE_SESSION = "session"
SCOPE_PROJECT = "project"
SCOPE_USER = "user"
SPATIAL_MEMORY_SCOPES: tuple[str, ...] = (SCOPE_SESSION, SCOPE_PROJECT, SCOPE_USER)

STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"
STATUS_INVALIDATED = "invalidated"

RULE_TTL = "ttl"
RULE_DATASET_VERSION = "dataset_version"
RULE_MANUAL = "manual"
RULE_SCOPE_GONE = "scope_gone"
INVALIDATION_RULES: tuple[str, ...] = (
    RULE_TTL,
    RULE_DATASET_VERSION,
    RULE_MANUAL,
    RULE_SCOPE_GONE,
)

#: 证据来源（closed vocab —— 写入门只放行这些生产事实源）。
#: 模型自由文本不是事实源；「模型随口推测」在门上被拒（R2）。
SOURCE_INTENT_RESOLUTION = "intent_resolution"          # match_scope/entity 解析成功
SOURCE_REVIEW_PASSED = "review_passed"                  # 评审通过（ Harvest位）
SOURCE_TOOL_RESULT = "tool_result"                      # 工具成功产物（策略/artifact）
SOURCE_TOOL_FAILURE = "tool_failure"                    # dispatch 失败（provider_failure）
SOURCE_DATASET_PIN = "dataset_pin"                      # ads pin/snapshot 事实
SOURCE_PROFILE = "data_profile"                         # 数据剖面（field_role/CRS）
SOURCE_USER_CORRECTION = "explicit_user_correction"     # 用户显式纠正（矛盾裁决必胜）
SOURCE_USER_DECISION = "explicit_user_decision"         # 用户显式产品/偏好决策

EVIDENCE_SOURCES: tuple[str, ...] = (
    SOURCE_INTENT_RESOLUTION,
    SOURCE_REVIEW_PASSED,
    SOURCE_TOOL_RESULT,
    SOURCE_TOOL_FAILURE,
    SOURCE_DATASET_PIN,
    SOURCE_PROFILE,
    SOURCE_USER_CORRECTION,
    SOURCE_USER_DECISION,
)

#: 各 kind 允许的证据源（写入门矩阵；leave-fail-closed：未列出 = 拒绝）。
_KIND_ALLOWED_SOURCES: Dict[str, tuple[str, ...]] = {
    KIND_RESOLVED_PLACE: (
        SOURCE_INTENT_RESOLUTION, SOURCE_USER_CORRECTION,
    ),
    KIND_BOUNDARY_REF: (SOURCE_INTENT_RESOLUTION, SOURCE_USER_CORRECTION),
    KIND_DATASET_SEMANTICS: (SOURCE_DATASET_PIN, SOURCE_PROFILE, SOURCE_REVIEW_PASSED),
    KIND_FIELD_ROLE: (SOURCE_PROFILE, SOURCE_REVIEW_PASSED, SOURCE_DATASET_PIN),
    KIND_CRS_RESOLUTION: (SOURCE_PROFILE, SOURCE_TOOL_RESULT),
    KIND_ANALYSIS_ARTIFACT: (SOURCE_TOOL_RESULT, SOURCE_REVIEW_PASSED),
    KIND_SUCCESSFUL_STRATEGY: (SOURCE_REVIEW_PASSED, SOURCE_TOOL_RESULT),
    KIND_PROVIDER_FAILURE: (SOURCE_TOOL_FAILURE,),
    KIND_PRODUCT_DECISION: (SOURCE_USER_DECISION, SOURCE_USER_CORRECTION),
    KIND_USER_CARTO_PREF: (SOURCE_USER_DECISION, SOURCE_USER_CORRECTION),
}

#: kind 默认 TTL（秒）。None = 不过期（产品/策略等长命先验仍受预算淘汰约束）。
_KIND_DEFAULT_TTL_S: Dict[str, Optional[int]] = {
    KIND_RESOLVED_PLACE: 30 * 24 * 3600,      # 行政区划不常变，但会失效
    KIND_BOUNDARY_REF: 30 * 24 * 3600,
    KIND_DATASET_SEMANTICS: None,             # 由 dataset_version 规则失效
    KIND_FIELD_ROLE: None,
    KIND_CRS_RESOLUTION: 24 * 3600,           # 确定性可重建 —— 记忆只是加速
    KIND_ANALYSIS_ARTIFACT: 14 * 24 * 3600,   # ref 生命周期另受 artifact GC
    KIND_SUCCESSFUL_STRATEGY: None,
    KIND_PROVIDER_FAILURE: 7 * 24 * 3600,     # 失败记忆必须过期（R2）
    KIND_PRODUCT_DECISION: None,
    KIND_USER_CARTO_PREF: None,
}

#: 检索/注入的 kind 基础权重（先验价值：地方/数据语义 > 一次性事实）。
_KIND_BASE_WEIGHT: Dict[str, float] = {
    KIND_RESOLVED_PLACE: 1.2,
    KIND_BOUNDARY_REF: 0.8,
    KIND_DATASET_SEMANTICS: 1.1,
    KIND_FIELD_ROLE: 1.0,
    KIND_CRS_RESOLUTION: 0.6,
    KIND_ANALYSIS_ARTIFACT: 0.7,
    KIND_SUCCESSFUL_STRATEGY: 1.0,
    KIND_PROVIDER_FAILURE: 0.9,
    KIND_PRODUCT_DECISION: 0.9,
    KIND_USER_CARTO_PREF: 1.0,
}

#: value JSON 序列化后的字符预算（超出 → 拒绝，由 sanitizer 先裁剪）。
VALUE_CHAR_BUDGET = 2048
REFS_MAX = 8
EVIDENCE_CHAR_BUDGET = 512


class MemoryPolicyError(ValueError):
    """写入门拒绝（evidence/confidence/scope/kind 不合格）。"""


@dataclass(frozen=True)
class MemoryEvidence:
    """一条记忆的生产证据（closed vocab）。"""

    source: str
    method: str = ""
    turn_id: Optional[str] = None

    def validate(self, kind: str) -> None:
        if self.source not in EVIDENCE_SOURCES:
            raise MemoryPolicyError(
                f"evidence.source {self.source!r} 不在 closed vocab"
            )
        allowed = _KIND_ALLOWED_SOURCES.get(kind, ())
        if self.source not in allowed:
            raise MemoryPolicyError(
                f"kind={kind} 不接受 evidence.source={self.source!r}"
                f"（允许：{allowed}）"
            )

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"source": self.source}
        if self.method:
            out["method"] = str(self.method)[:120]
        if self.turn_id:
            out["turn_id"] = str(self.turn_id)[:64]
        return out


def semantic_fingerprint(kind: str, subject: str, value: Dict[str, Any]) -> str:
    """语义指纹：只由**会构成矛盾的字段**决定（同 key 不同指纹 = supersede）。

    剥离纯呈现/统计字段（confidence、observed_at、counts），与 ADR-0069
    ``classification_fingerprint`` 的「换色带不算换方案」同哲学。
    """
    volatile = {
        "confidence", "observed_at", "recorded_at", "counts", "latency_ms",
        "attempt", "reviewed_at", "sample_size",
    }
    semantic = {
        k: v for k, v in (value or {}).items() if k not in volatile
    }
    blob = json.dumps(
        {"kind": kind, "subject": subject, "value": semantic},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MemoryWriteRequest:
    """一次受控记忆写入（store.record_memory 的唯一入口形状）。"""

    kind: str
    scope: str
    scope_id: str
    subject: str
    value: Dict[str, Any]
    evidence: MemoryEvidence
    confidence: float
    org_id: str
    refs: Sequence[str] = ()
    user_id: Optional[str] = None
    sensitive: bool = False
    invalidation_rule: Optional[str] = None   # None = 按 TTL 解析自动推导
    ttl_s: Optional[int] = None          # None → kind 默认；显式 0/负数 = 拒绝
    fingerprint: Optional[str] = None    # 缺省由 semantic_fingerprint 计算

    def validate(self) -> None:
        if self.kind not in SPATIAL_MEMORY_KINDS:
            raise MemoryPolicyError(f"未知记忆 kind {self.kind!r}")
        if self.scope not in SPATIAL_MEMORY_SCOPES:
            raise MemoryPolicyError(f"未知记忆 scope {self.scope!r}")
        if not self.scope_id or not self.subject:
            raise MemoryPolicyError("scope_id/subject 不得为空")
        if not self.org_id:
            raise MemoryPolicyError("org_id 缺失 —— 记忆必须落租户桶（fail-closed）")
        if self.scope == SCOPE_USER and not self.user_id:
            raise MemoryPolicyError("user 作用域必须显式 user_id（匿名不写用户记忆）")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise MemoryPolicyError("confidence 必须 ∈ [0,1]")
        if self.invalidation_rule is not None and self.invalidation_rule not in INVALIDATION_RULES:
            raise MemoryPolicyError(
                f"未知 invalidation_rule {self.invalidation_rule!r}"
            )
        self.evidence.validate(self.kind)
        if len(str(self.subject)) > 255:
            raise MemoryPolicyError("subject 超长（≤255）")
        import json as _json

        if len(_json.dumps(self.value or {}, ensure_ascii=False, default=str)) > VALUE_CHAR_BUDGET:
            raise MemoryPolicyError(
                f"value 超出字符预算 {VALUE_CHAR_BUDGET}（先过 sanitizer）"
            )


@dataclass(frozen=True)
class SpatialMemoryRecord:
    """检索/投影面的不可变记忆视图（来自 store 行的纯投影）。"""

    id: str
    kind: str
    scope: str
    scope_id: str
    org_id: str
    subject: str
    value: Dict[str, Any]
    refs: List[str] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""
    confidence: float = 0.0
    status: str = STATUS_ACTIVE
    expires_at: Optional[str] = None
    created_at: Optional[str] = None
    last_validated_at: Optional[str] = None
    version: int = 1
    sensitive: bool = False
    invalidation_rule: str = RULE_TTL

    def to_bounded_dict(self, *, include_audit: bool = False) -> Dict[str, Any]:
        """有界序列化（契约导出/日志/API 同形）。

        ``include_audit=False``（模型面/日志面）剥离 evidence.turn_id 等
        关联字段；审计面可显式开启。
        """
        out: Dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "scope": self.scope,
            "subject": self.subject,
            "value": self.value,
            "refs": list(self.refs)[:REFS_MAX],
            "confidence": round(float(self.confidence), 3),
            "status": self.status,
            "version": int(self.version),
            "invalidation_rule": self.invalidation_rule,
        }
        if include_audit:
            out.update({
                "org_id": self.org_id,
                "scope_id": self.scope_id,
                "evidence": dict(self.evidence),
                "fingerprint": self.fingerprint,
                "expires_at": self.expires_at,
                "created_at": self.created_at,
                "last_validated_at": self.last_validated_at,
                "sensitive": self.sensitive,
            })
        return out


@dataclass(frozen=True)
class RetrievedMemory:
    """一条检索命中：记忆 + 得分 + **可读理由**（R4：不透明排序不可接受）。"""

    record: SpatialMemoryRecord
    score: float
    reasons: List[str] = field(default_factory=list)

    def to_bounded_dict(self, *, include_audit: bool = False) -> Dict[str, Any]:
        return {
            "memory": self.record.to_bounded_dict(include_audit=include_audit),
            "score": round(float(self.score), 3),
            "reasons": list(self.reasons)[:6],
        }


__all__ = [
    "SPATIAL_MEMORY_KINDS",
    "SPATIAL_MEMORY_SCOPES",
    "EVIDENCE_SOURCES",
    "INVALIDATION_RULES",
    "KIND_RESOLVED_PLACE",
    "KIND_BOUNDARY_REF",
    "KIND_DATASET_SEMANTICS",
    "KIND_FIELD_ROLE",
    "KIND_CRS_RESOLUTION",
    "KIND_ANALYSIS_ARTIFACT",
    "KIND_SUCCESSFUL_STRATEGY",
    "KIND_PROVIDER_FAILURE",
    "KIND_PRODUCT_DECISION",
    "KIND_USER_CARTO_PREF",
    "SCOPE_SESSION",
    "SCOPE_PROJECT",
    "SCOPE_USER",
    "STATUS_ACTIVE",
    "STATUS_SUPERSEDED",
    "STATUS_INVALIDATED",
    "RULE_TTL",
    "RULE_DATASET_VERSION",
    "RULE_MANUAL",
    "RULE_SCOPE_GONE",
    "VALUE_CHAR_BUDGET",
    "MemoryPolicyError",
    "MemoryEvidence",
    "MemoryWriteRequest",
    "SpatialMemoryRecord",
    "RetrievedMemory",
    "semantic_fingerprint",
    "kind_default_ttl_s",
    "kind_base_weight",
]


def kind_default_ttl_s(kind: str) -> Optional[int]:
    return _KIND_DEFAULT_TTL_S.get(kind)


def kind_base_weight(kind: str) -> float:
    return _KIND_BASE_WEIGHT.get(kind, 0.5)
