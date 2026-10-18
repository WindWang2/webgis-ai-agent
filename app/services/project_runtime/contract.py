"""ProjectRuntimeSnapshotV1 契约（ADR-0224）。

Manifest，不是第二数据库：只记录 authority ids / immutable fingerprints /
version tokens / durable artifact refs / MapSpec 身份 + 有界文档 / dataset
描述符 / context-plan 投影引用 / recovery policy / 披露。绝不包含 raw LLM
hidden state、无限聊天历史、巨型 GeoJSON 载荷、Redis dump、secret/token。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

#: manifest schema 版本（升级时新版本号 + fail-closed 读路径，见 store/validation）。
RUNTIME_SNAPSHOT_SCHEMA_VERSION = 1

# ── 有界性（bounded everything；与 workspace snapshot 同纪律）──────────

#: source bindings 上限（MapSpec sources + layout 组件 refs 合并面）。
SOURCE_BINDING_LIMIT = 64
#: dataset 描述符上限（项目挂载数据集投影）。
DATASET_DESCRIPTOR_LIMIT = 32
#: plan progress 上限（与 anchor 的 32 同界）。
PROGRESS_LIMIT = 32
#: workflow refs 上限。
WORKFLOW_REF_LIMIT = 16
#: capability 上限（capability runtime snapshot 同界）。
CAPABILITY_LIMIT = 16
#: 快照文档体积上限（spec 文档是 presentation + refs，非载荷；防御性界）。
MAPSPEC_DOC_MAX_BYTES = 16 * 1024 * 1024

# ── 晋升分类词表（promotion planner 输出；有限集合）────────────────────

PROMOTED = "promotable"                 # 本轮物化成功
ALREADY_DURABLE = "already_durable"     # 已有 BlobStore 指针（不重物化）
RECOMPUTABLE = "recomputable"           # 载荷已亡但有血缘父，可重算恢复
EXTERNAL_PINNED = "external_version_pinned"  # 外部/版本钉扎源（非会话 ref）
NON_PROMOTABLE = "non_promotable"       # 死 ref 且无指针无血缘（如实披露）
OVERSIZED = "oversized"                 # 超 freeze 预算
QUOTA_BLOCKED = "quota_blocked"         # 超项目 quota 余量
SENSITIVE = "sensitive"                 # 账本标记 sensitive 的 ref（只披露不物化）

PROMOTION_CLASSES = (
    PROMOTED,
    ALREADY_DURABLE,
    RECOMPUTABLE,
    EXTERNAL_PINNED,
    NON_PROMOTABLE,
    OVERSIZED,
    QUOTA_BLOCKED,
    SENSITIVE,
)

# ── 恢复状态词表（ValidationReport.status / finding.state；有限集合）────

RESUME_READY = "ready"
RESUME_READY_WITH_STALE_INPUTS = "ready_with_stale_inputs"
RESUME_REQUIRES_RECOMPUTE = "requires_recompute"
RESUME_MISSING_ARTIFACT = "missing_artifact"
RESUME_CAPABILITY_UNAVAILABLE = "capability_unavailable"
RESUME_PERMISSION_CHANGED = "permission_changed"
RESUME_DATASET_VERSION_CHANGED = "dataset_version_changed"
RESUME_UNSUPPORTED_SCHEMA = "unsupported_schema"
RESUME_BLOCKED = "blocked"

RESUME_STATES = (
    RESUME_READY,
    RESUME_READY_WITH_STALE_INPUTS,
    RESUME_REQUIRES_RECOMPUTE,
    RESUME_MISSING_ARTIFACT,
    RESUME_CAPABILITY_UNAVAILABLE,
    RESUME_PERMISSION_CHANGED,
    RESUME_DATASET_VERSION_CHANGED,
    RESUME_UNSUPPORTED_SCHEMA,
    RESUME_BLOCKED,
)

# ── 恢复计划逐源处置词表 ────────────────────────────────────────────────

DISPOSITION_RESTORE_DURABLE = "restore_from_durable"
DISPOSITION_RESTORE_LIVE = "restore_from_live"
DISPOSITION_NEEDS_RECOMPUTE = "needs_recompute"
DISPOSITION_EXTERNAL_PINNED = "external_pinned"
DISPOSITION_MISSING = "missing"

#: freeze 触发面（显式冻结点；绝不 per-turn 自动快照）。
FREEZE_REASONS = ("explicit_save", "named_checkpoint", "publish")


class DurablePointerRef(BaseModel):
    """对 Wave-1 BlobStore 指针的只读引用（与 workspace snapshot 同语义）。"""

    content_location: str = ""
    content_payload_sha256: str = ""
    content_type: str = "json"       # json | binary | lakehouse_manifest
    byte_size: int = 0


class SourceBinding(BaseModel):
    """一个 MapSpec source（或 layout 组件 ref）的冻结绑定。"""

    source_id: str = ""
    kind: str = "source"             # source | chart | table
    ref: str = ""                    # 冻结时的会话 ref（可空 = inline/外部）
    promotion_class: str = NON_PROMOTABLE
    durable_pointer: Optional[DurablePointerRef] = None
    skip_reason: str = ""            # budget | quota | dead | sensitive | ...

    @field_validator("source_id")
    @classmethod
    def _bounded_source_id(cls, v: str) -> str:
        return str(v)[:96]

    @field_validator("ref")
    @classmethod
    def _bounded_ref(cls, v: str) -> str:
        return str(v)[:160]


class DatasetDescriptor(BaseModel):
    """项目挂载数据集的冻结描述符（版本指纹 = 恢复时漂移判定的比对基准）。"""

    dataset_id: str
    name: str = ""
    version_fingerprint: str = ""
    source_type: str = ""

    @field_validator("dataset_id")
    @classmethod
    def _bounded_dataset_id(cls, v: str) -> str:
        return str(v)[:64]


class PlanProjectionRef(BaseModel):
    """plan/workflow 身份引用（投影引用，不是 plan dump）。

    只携带 identity + 有界 progress + 与 resume anchor 同界的 restorable
    chapter 键（RESTORABLE_CHAPTER_KEYS 语义，``data_requirements`` 永不入内
    —— 恢复章节无数据行在场证据，绑定类行一律不恢复）。
    """

    envelope_id: str = ""
    user_goal: str = ""
    progress: List[Dict[str, Any]] = Field(default_factory=list)
    restorable_chapter: Dict[str, Any] = Field(default_factory=dict)
    workflow_rows_fingerprint: str = ""
    required_capabilities: List[str] = Field(default_factory=list)
    credential_presence_fingerprint: str = ""
    capability_statuses: Dict[str, str] = Field(default_factory=dict)

    @field_validator("user_goal")
    @classmethod
    def _bounded_goal(cls, v: str) -> str:
        return str(v)[:512]

    @field_validator("workflow_rows_fingerprint")
    @classmethod
    def _bounded_rows_fp(cls, v: str) -> str:
        return str(v)[:2048]


class RecoveryPolicy(BaseModel):
    """恢复策略（V1 固定值；显式记录而非隐式行为）。"""

    observation_policy: str = "invalidate"      # 旧 render observation 绝不复用
    restore_in_flight_jobs: bool = False        # in-flight job 绝不伪装恢复成功
    restore_working_context_cache: bool = False  # working context 由权威重建，不复制缓存


class ProjectRuntimeSnapshotV1(BaseModel):
    """项目制图运行时快照（ADR-0224 §1）。"""

    schema_version: int = RUNTIME_SNAPSHOT_SCHEMA_VERSION
    snapshot_id: str
    project_id: str
    session_id: str = ""              # created-from session（历史事实，恢复不复用）
    created_by: str = ""              # 服务侧所有权（路由侧强制鉴权）
    created_at: float = 0.0
    freeze_reason: str = "explicit_save"
    label: str = ""

    # ── authority 引用 ──
    workspace_snapshot_id: str = ""   # artifact 账本权威（freeze 时组合创建）
    map_product_version_no: Optional[int] = None
    workflow_refs: List[str] = Field(default_factory=list)

    # ── MapSpec 冻结 ──
    mapspec_doc: Dict[str, Any] = Field(default_factory=dict)
    mapspec_fingerprint: str = ""     # canonical-JSON SHA256（freeze 时计算）
    mapspec_mutation_revision: int = 0

    # ── 源绑定 / dataset / plan 投影 / 策略 / 披露 ──
    source_bindings: List[SourceBinding] = Field(default_factory=list)
    datasets: List[DatasetDescriptor] = Field(default_factory=list)
    plan: PlanProjectionRef = Field(default_factory=PlanProjectionRef)
    policy: RecoveryPolicy = Field(default_factory=RecoveryPolicy)
    disclosure: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_bindings")
    @classmethod
    def _bounded_sources(cls, v: List[SourceBinding]) -> List[SourceBinding]:
        return list(v)[:SOURCE_BINDING_LIMIT]

    @field_validator("datasets")
    @classmethod
    def _bounded_datasets(cls, v: List[DatasetDescriptor]) -> List[DatasetDescriptor]:
        return list(v)[:DATASET_DESCRIPTOR_LIMIT]

    @field_validator("workflow_refs")
    @classmethod
    def _bounded_workflow_refs(cls, v: List[str]) -> List[str]:
        return [str(w)[:96] for w in v[:WORKFLOW_REF_LIMIT]]

    @field_validator("label")
    @classmethod
    def _bounded_label(cls, v: str) -> str:
        return str(v)[:96]

    @field_validator("freeze_reason")
    @classmethod
    def _bounded_reason(cls, v: str) -> str:
        return str(v)[:32] if str(v) in FREEZE_REASONS else "explicit_save"


# ── 校验 / 恢复计划 / 回执（ResumeRequest → ValidationReport →
#    RecoveryPlan → NewSessionBinding → ResumeReceipt 协议面）────────────


class ValidationFinding(BaseModel):
    """单项校验发现（state ∈ RESUME_STATES；机器可读 + 有界人读 reason）。"""

    state: str
    subject: str = ""                 # ref / dataset_id / capability / project
    reason: str = ""

    @field_validator("subject")
    @classmethod
    def _bounded_subject(cls, v: str) -> str:
        return str(v)[:160]

    @field_validator("reason")
    @classmethod
    def _bounded_reason(cls, v: str) -> str:
        return str(v)[:240]


class SourceDisposition(BaseModel):
    """恢复计划中的逐源处置（执行前的声明；不执行副作用）。"""

    source_id: str = ""
    ref: str = ""
    disposition: str = DISPOSITION_MISSING
    detail: str = ""


class ValidationReport(BaseModel):
    """verify-first 校验报告（resume 前置；只读，不写任何状态）。"""

    snapshot_id: str
    schema_supported: bool = True
    project_ok: bool = False
    permission: str = "ok"            # ok | changed
    mapspec_integrity_ok: bool = False
    findings: List[ValidationFinding] = Field(default_factory=list)
    dispositions: List[SourceDisposition] = Field(default_factory=list)
    dataset_verdicts: Dict[str, str] = Field(default_factory=dict)
    capability_verdicts: Dict[str, str] = Field(default_factory=dict)

    @property
    def status(self) -> str:
        """聚合状态（最严重发现优先；无发现 = ready）。

        blocked/unsupported_schema 为一票否决。「有可恢复源 + 部分源需
        重算/缺失」按 ready_with_stale_inputs 恢复（重算面进恢复计划显式
        披露）；requires_recompute 只在**没有任何可恢复源**时作为终态
        （核心制图面必须重算才能继续）。capability/dataset 漂移即使可部分
        恢复也作为顶层状态浮出（降级可见，绝不假成功）。
        """
        if not self.schema_supported:
            return RESUME_UNSUPPORTED_SCHEMA
        if not self.project_ok:
            return RESUME_BLOCKED
        states = {f.state for f in self.findings}
        if RESUME_BLOCKED in states:
            return RESUME_BLOCKED
        severity_order = (
            RESUME_MISSING_ARTIFACT,
            RESUME_CAPABILITY_UNAVAILABLE,
            RESUME_DATASET_VERSION_CHANGED,
            RESUME_REQUIRES_RECOMPUTE,
        )
        # MapSpec 文档完整性是制图核心面：损坏即终态 missing_artifact
        #（载荷可恢复也救不回 spec 本体）。
        if (
            not self.mapspec_integrity_ok
            and any(f.subject == "mapspec_doc" for f in self.findings)
        ):
            return RESUME_MISSING_ARTIFACT
        if not self._has_restorable_source and self.findings:
            # 无任何可恢复源：按语义严重度定性终态。
            for state in severity_order:
                if state in states:
                    return state
            return RESUME_READY_WITH_STALE_INPUTS
        if RESUME_CAPABILITY_UNAVAILABLE in states:
            return RESUME_CAPABILITY_UNAVAILABLE
        if RESUME_DATASET_VERSION_CHANGED in states:
            return RESUME_DATASET_VERSION_CHANGED
        if self.findings:
            return RESUME_READY_WITH_STALE_INPUTS
        return RESUME_READY

    @property
    def _has_restorable_source(self) -> bool:
        restorable = {
            DISPOSITION_RESTORE_DURABLE,
            DISPOSITION_RESTORE_LIVE,
            DISPOSITION_EXTERNAL_PINNED,
        }
        return any(d.disposition in restorable for d in self.dispositions)

    @field_validator("findings")
    @classmethod
    def _bounded_findings(
        cls, v: List[ValidationFinding]
    ) -> List[ValidationFinding]:
        return list(v)[: SOURCE_BINDING_LIMIT + DATASET_DESCRIPTOR_LIMIT + 8]

    @field_validator("dispositions")
    @classmethod
    def _bounded_dispositions(
        cls, v: List[SourceDisposition]
    ) -> List[SourceDisposition]:
        return list(v)[:SOURCE_BINDING_LIMIT]

    @field_validator("dataset_verdicts")
    @classmethod
    def _bounded_dataset_verdicts(cls, v: Dict[str, str]) -> Dict[str, str]:
        return dict(list(v.items())[:DATASET_DESCRIPTOR_LIMIT])

    @field_validator("capability_verdicts")
    @classmethod
    def _bounded_capability_verdicts(cls, v: Dict[str, str]) -> Dict[str, str]:
        return dict(list(v.items())[:CAPABILITY_LIMIT])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "status": self.status,
            "schema_supported": self.schema_supported,
            "project_ok": self.project_ok,
            "permission": self.permission,
            "mapspec_integrity_ok": self.mapspec_integrity_ok,
            "findings": [f.model_dump() for f in self.findings],
            "dispositions": [d.model_dump() for d in self.dispositions],
            "dataset_verdicts": self.dataset_verdicts,
            "capability_verdicts": self.capability_verdicts,
        }


class RecoveryPlan(BaseModel):
    """显式恢复计划（校验通过后、执行前的机器可读计划）。"""

    snapshot_id: str
    status: str
    steps: List[Dict[str, Any]] = Field(default_factory=list)
    needs_recompute: List[str] = Field(default_factory=list)
    invalidated: List[str] = Field(default_factory=list)

    @field_validator("steps")
    @classmethod
    def _bounded_steps(cls, v: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return list(v)[: SOURCE_BINDING_LIMIT + 8]

    @field_validator("needs_recompute")
    @classmethod
    def _bounded_recompute(cls, v: List[str]) -> List[str]:
        return [str(s)[:160] for s in v[:SOURCE_BINDING_LIMIT]]

    @field_validator("invalidated")
    @classmethod
    def _bounded_invalidated(cls, v: List[str]) -> List[str]:
        return [str(s)[:96] for s in v[:16]]


class ResumeReceipt(BaseModel):
    """恢复回执（诚实披露：恢复了什么 / 没恢复什么 / 需要重算什么）。"""

    snapshot_id: str
    project_id: str
    session_id: str                   # 新 session
    source_session_id: str
    status: str
    restored_refs: List[str] = Field(default_factory=list)
    ref_map: Dict[str, str] = Field(default_factory=dict)
    missing_refs: List[str] = Field(default_factory=list)
    mapspec_rebound: bool = False
    mapspec_frozen_fingerprint: str = ""
    mapspec_new_fingerprint: str = ""
    datasets_changed: List[str] = Field(default_factory=list)
    capabilities_unavailable: List[str] = Field(default_factory=list)
    invalidated: List[str] = Field(default_factory=list)
    artifacts_registered: int = 0
    artifacts_expired: int = 0
    degraded: List[Dict[str, str]] = Field(default_factory=list)
    disclosures: List[str] = Field(default_factory=list)

    @field_validator("missing_refs")
    @classmethod
    def _bounded_missing(cls, v: List[str]) -> List[str]:
        return [str(s)[:160] for s in v[:SOURCE_BINDING_LIMIT]]

    @field_validator("datasets_changed")
    @classmethod
    def _bounded_changed(cls, v: List[str]) -> List[str]:
        return [str(s)[:64] for s in v[:DATASET_DESCRIPTOR_LIMIT]]

    @field_validator("capabilities_unavailable")
    @classmethod
    def _bounded_caps(cls, v: List[str]) -> List[str]:
        return [str(s)[:128] for s in v[:CAPABILITY_LIMIT]]

    @field_validator("disclosures")
    @classmethod
    def _bounded_disclosures(cls, v: List[str]) -> List[str]:
        return [str(s)[:240] for s in v[:32]]
