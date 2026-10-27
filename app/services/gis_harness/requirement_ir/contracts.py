"""Requirement IR typed contracts（F02 / ADR-0266）。

版本化、可序列化、有界的需求中间表示：把"用户到底要什么"从
prompt 文本与散落的 state 中收拢为稳定文档，供 lifecycle / patch /
digest / clarification / 下游投影消费。

设计约束（ADR-0266 / docs/dev/f02-gis-intent-requirement-ir-decisions.md）：

- **单一理解真相**：:class:`GISIntentSpec.core` 内嵌既有
  :class:`~app.services.gis_harness.intent.MapRequestIntent`——本模块
  不复制其规则表/词表/置信度模型，sections 由 core 确定性派生（build.py）；
- **字段级 ownership**：section 级 + 稀疏字段级
  :class:`Provenance`，``origin="user"`` 即用户显式输入，patch 层
  （patch.py）据此执行 user-wins 硬约束；
- **版本化**：``requirement_document.v1``，字段只加不改；所有模型
  ``extra="forbid"``，非法字段在边界被拒而非静默进入；
- **有界**：所有列表/字符串有显式上限，防止长会话膨胀；
- **零 IO**：本模块只定义类型与纯函数，持久化在 service.py。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from app.services.gis_harness.intent import (
    ExportFormat,
    MapRequestIntent,
    TaskType,
)

# ── schema 版本（只加不改；破坏性变更升 v2 并提供迁移） ─────────────────
REQUIREMENT_DOCUMENT_SCHEMA = "requirement_document.v1"

# ── 有界性上限 ───────────────────────────────────────────────────────
MAX_TEXT = 200
MAX_MEASURES = 8
MAX_AMBIGUITIES = 16
MAX_PATCHES = 200          # journal 现网保留条数（超出折入 folded_patch_count）
MAX_LOCKS = 32
MAX_DATASETS = 16
MAX_STAGES = 4
MAX_FIELD_PROVENANCE = 32
MAX_REQUIREMENTS = 64
MAX_OPTIONS = 4

# ── 词表（在既有权威词表之上只做收拢，不另造语义） ─────────────────────

Origin = Literal["user", "rule", "llm", "ontology", "memory", "service", "default"]
"""字段来源。``user`` 为 ownership 最高优先（user-wins）；``default``
表示系统安全默认（必须携带 rationale）。``rule/llm/ontology/memory/
service`` 与 intent_semantic 的 slot.source 词表对齐。"""

Actor = Literal["user", "agent", "system"]

SlotState = Literal["proposed", "clarified", "accepted", "superseded", "rejected"]
"""需求条目/槽位 lifecycle（F02 DoD：proposed → clarified/accepted →
superseded；rejected 为用户显式否决终态）。"""

DocumentLifecycle = Literal["draft", "clarifying", "accepted", "superseded"]

TaskKind = Literal["query_only", "analysis", "map", "edit", "export"]
"""五类顶层意图（F02 DoD #1）。``edit`` 表示对既有需求文档的增量修订。"""

StatisticVocab = Literal[
    "count", "sum", "mean", "median", "min", "max",
    "ratio", "rate", "density", "share", "index", "none",
]
DimensionVocab = Literal["none", "administrative", "grid", "category", "custom"]
NormalizationVocab = Literal["none", "per_area", "per_capita", "custom"]
TimeGranularity = Literal["none", "year", "quarter", "month", "day"]
SpatialRelationKind = Literal[
    "none", "within", "intersects", "near", "service_area", "drainage_to", "along",
]
LockScope = Literal[
    "palette", "geometry_kind", "layer_visibility", "layer_lock", "component",
    "measure", "group_by", "purpose", "audience", "export_format", "aoi",
    "scene_mode", "scene_camera", "other",
]
"""G3D04：``scene_mode``（2d/2.5d/3d 维度与地形/挤出/夸张偏好）与
``scene_camera``（pitch/bearing 用户相机选择）是跨轮 user-wins 面。"""

#: 输出媒介词表（scene 决策的媒介门输入；对齐 scene_planning.SceneIntent.medium）。
MediumVocab = Literal[
    "", "interactive", "screen", "print", "export_png", "export_pdf", "export_svg",
]

#: 3D 维度偏好（""=未表达；规范形对齐 MapSpec SCENE_MODES）。
SceneDimensionVocab = Literal["", "2d", "2.5d", "3d"]

#: 3D 分析目标词表（G3D04；分析编排的 typed 目标面）。
SceneAnalysisGoalVocab = Literal[
    "viewshed", "line_of_sight", "skyline", "terrain_profile",
]

#: 3D 数据角色词表（G3D04；对齐 workflow_schema DATA_ROLES 的 3D 子集）。
SceneDatasetRoleVocab = Literal["elevation", "subject", "receptor", "constraint"]

#: 垂直证据种类（G3D04；证据必须来自已核实数据事实，见 scene3d.evidence）。
VerticalEvidenceKind = Literal["height_attribute", "elevation_dem"]


# ── 基础载体 ─────────────────────────────────────────────────────────


class Provenance(BaseModel):
    """字段级来源/证据/所有权（F02：每个字段记录 evidence/source/ownership）。"""

    model_config = ConfigDict(extra="forbid")

    origin: Origin = "default"
    turn: Optional[int] = Field(None, ge=0)
    evidence_refs: Tuple[str, ...] = ()   # intent_evidence / 规则 id / 用户话语 turn 引用
    rationale: str = Field("", max_length=MAX_TEXT)  # default 必填 rationale；其他可选

    def is_user(self) -> bool:
        return self.origin == "user"


EMPTY_PROVENANCE = Provenance(origin="rule")


class UserLock(BaseModel):
    """用户显式锁定（user-wins 载体；与 MapSpec lockedLayerIds 同语义层级）。

    锁定值不被自动优化/超替静默覆盖；唯一解除方式是 user actor 的
    ``remove_lock`` patch。
    """

    model_config = ConfigDict(extra="forbid")

    scope: LockScope
    value: str = Field("", max_length=128)
    provenance: Provenance = Field(default_factory=lambda: Provenance(origin="user"))

    def canonical(self) -> Tuple[str, str]:
        return (self.scope, self.value)


class AmbiguityOption(BaseModel):
    """澄清候选项（形状对齐 clarification.ClarificationOption，独立定义避免反向依赖）。"""

    model_config = ConfigDict(extra="forbid")

    value: str = Field(max_length=128)
    label_zh: str = Field(max_length=MAX_TEXT)
    label_en: str = Field("", max_length=MAX_TEXT)
    is_default: bool = False


class Ambiguity(BaseModel):
    """一条 typed 歧义/澄清需求。

    ``context_key`` 是去重锚：code + 相关字段规范值的摘要。context 未变
    → 同 key → 不重复询问（F02 DoD #2）；context 变 → 新 key 可再问。
    ``blocking=True`` 表示影响正确性或不可逆，才允许向用户提问；
    非 blocking 歧义必须带 ``default_value`` + ``default_rationale``，
    由系统取默认并披露，不打断用户。
    """

    model_config = ConfigDict(extra="forbid")

    code: str = Field(max_length=64)
    path: str = Field("", max_length=128)           # dotted path（patch 寻址同语法）
    context_key: str = Field(max_length=64)
    state: Literal["open", "answered", "waived", "superseded"] = "open"
    blocking: bool = False
    question_zh: str = Field("", max_length=MAX_TEXT)
    question_en: str = Field("", max_length=MAX_TEXT)
    options: List[AmbiguityOption] = Field(default_factory=list, max_length=MAX_OPTIONS)
    default_value: str = Field("", max_length=128)
    default_rationale: str = Field("", max_length=MAX_TEXT)
    answer: str = Field("", max_length=128)
    asked_turn: Optional[int] = None
    answered_turn: Optional[int] = None


# ── GISIntentSpec sections（理解面的 typed 展开） ────────────────────────


class _Section(BaseModel):
    """section 基类：section 级 provenance + lifecycle 状态。"""

    model_config = ConfigDict(extra="forbid")

    provenance: Provenance = Field(default_factory=lambda: EMPTY_PROVENANCE)
    state: SlotState = "proposed"


class TaskSpec(_Section):
    """顶层任务分类（classify.py 的产物）。"""

    kind: TaskKind = "query_only"
    stages: Tuple[TaskKind, ...] = ()   # 显式时序（先分析后制图 → (analysis, map)）
    task_type: TaskType = "distribution_overview"
    reason_codes: Tuple[str, ...] = ()
    confidence: float = Field(0.0, ge=0.0, le=1.0)


class AOISpec(_Section):
    name: str = Field("", max_length=128)
    level: Literal["country", "province", "city", "district", "unknown"] = "unknown"
    geometry_ref: str = Field("", max_length=128)   # boundary identity（如 local:admin:city:成都）
    resolver_state: Literal["unresolved", "resolved", "ambiguous", "degraded"] = "unresolved"


class SubjectSpec(_Section):
    type: Literal[
        "poi", "facility", "boundary", "region", "network", "raster", "unknown",
    ] = "unknown"
    category: str = Field("", max_length=128)


class MeasureSpec(_Section):
    """一个指标槽位。``phrase`` 保留用户原词（审计面，不入 digest）；
    规范形（subject_token/statistic/denominator）入 digest（normalize.py）。
    ``field`` 是 field_resolver 的解析产物（下游权威），IR 只记录不决定。"""

    id: str = Field(max_length=16)                  # m1 / m2 …
    phrase: str = Field("", max_length=128)
    subject_token: str = Field("", max_length=64)   # 规范化主体词（如 小学/landuse）
    statistic: StatisticVocab = "none"
    denominator: str = Field("", max_length=32)     # UnitDimension.value 期望分母
    role: str = Field("", max_length=32)            # SemanticFieldRole.value 提示
    kind: str = Field("", max_length=32)            # MeasurementKind.value 提示
    temporal_required: bool = False
    field: str = Field("", max_length=128)          # 下游解析产物（空 = 未解析）
    field_state: Literal["unresolved", "resolved", "ambiguous", "degraded"] = "unresolved"


class TimeSpec(_Section):
    range_start: str = Field("", max_length=16)
    range_end: str = Field("", max_length=16)
    granularity: TimeGranularity = "none"
    series: bool = False                            # 多期时序


class StatisticsSpec(_Section):
    dimension: DimensionVocab = "none"
    group_by: str = Field("", max_length=48)
    normalization: NormalizationVocab = "none"
    denominator: str = Field("", max_length=32)


class SpatialRelationSpec(_Section):
    kind: SpatialRelationKind = "none"
    target: str = Field("", max_length=128)
    distance_m: Optional[int] = Field(None, ge=0, le=10_000_000)


class OutputSpec(_Section):
    live_map: bool = True
    formats: Tuple[ExportFormat, ...] = ()
    publish: bool = False                           # 对外发布（不可逆面）
    report: bool = False
    output_purpose: str = Field("screen_16_9", max_length=32)  # grammar OUTPUT_PURPOSES 词表
    dpi: Optional[int] = Field(None, ge=72, le=1200)
    # G3D04：输出媒介（interactive 允许透视/动效；静态媒介诚实禁 3D）。
    medium: MediumVocab = ""


class RepresentationSpec(_Section):
    """表达约束（仅记录用户显式约束与锁定；具体表达由 grammar 权威求解）。"""

    palette: str = Field("", max_length=48)         # 规范化名（如 blue / viridis）
    geometry_kind: str = Field("", max_length=48)   # 用户点名的表达（如 heatmap/choropleth）
    hidden_layer_ids: Tuple[str, ...] = ()
    locked_layer_ids: Tuple[str, ...] = ()
    pinned_channels: Dict[str, str] = Field(default_factory=dict)  # 视觉变量 pin


class RequiredComponentsSpec(_Section):
    """必需组件。``None`` = 未表达，由 grammar/completeness 权威决定；
    ``True/False`` = 用户显式要求（digest 只收显式值）。"""

    model_config = ConfigDict(extra="forbid")

    provenance: Provenance = Field(default_factory=lambda: EMPTY_PROVENANCE)
    state: SlotState = "proposed"
    title: Optional[bool] = None
    legend: Optional[bool] = None
    scale_bar: Optional[bool] = None
    north_arrow: Optional[bool] = None
    labels: Optional[bool] = None
    attribution: Optional[bool] = None


class VerticalEvidenceRecord(BaseModel):
    """一条已核实的垂直证据（G3D04）。

    ``verified=True`` 必须来自真实数据事实采样（extrusion stats.valid>0 /
    raster-dem ref 注册回执），由 scene3d.evidence 生产；本记录只承载
    结论与溯源，不承载猜测。
    """

    model_config = ConfigDict(extra="forbid")

    kind: VerticalEvidenceKind
    ref: str = Field("", max_length=128)            # 数据事实溯源（ref/layer id/回执 id）
    verified: bool = False
    detail: str = Field("", max_length=MAX_TEXT)


class SceneSpec(_Section):
    """三维表达需求（G3D04 M2）。

    只记录**用户显式表达**与已核实证据；"分布情况"类话语不推断 3D
    （无 cue → 全默认）。用户显式 3D 而证据/媒介不允许时，偏好保留、
    ``degrade_reason`` 记录降级原因（诚实降级，不静默吞偏好）。
    决策权威在 scene3d.decision（包既有 plan_scene 决策核）。
    """

    model_config = ConfigDict(extra="forbid")

    provenance: Provenance = Field(default_factory=lambda: EMPTY_PROVENANCE)
    state: SlotState = "proposed"
    dimension: SceneDimensionVocab = ""             # 用户维度偏好（""=未表达）
    degrade_reason: str = Field("", max_length=64)  # 显式偏好被降级的 reason code
    terrain_requested: Optional[bool] = None        # 用户显式地形表达（None=未表达）
    extrusion_requested: Optional[bool] = None      # 用户显式立体挤出（None=未表达）
    exaggeration: Optional[float] = Field(None, gt=0.0, le=10.0)
    camera_pitch: Optional[float] = Field(None, ge=0.0, le=85.0)
    camera_bearing: Optional[float] = Field(None, ge=-180.0, le=180.0)
    analysis_goals: Tuple[SceneAnalysisGoalVocab, ...] = Field((), max_length=4)
    dataset_roles: Dict[str, SceneDatasetRoleVocab] = Field(
        default_factory=dict, max_length=8)
    vertical_evidence: List[VerticalEvidenceRecord] = Field(
        default_factory=list, max_length=8)

    def is_default(self) -> bool:
        """全默认判定（digest 条件包含与"无表达"守卫共用）。"""
        return (
            self.dimension == "" and self.degrade_reason == ""
            and self.terrain_requested is None and self.extrusion_requested is None
            and self.exaggeration is None and self.camera_pitch is None
            and self.camera_bearing is None and not self.analysis_goals
            and not self.dataset_roles and not self.vertical_evidence
        )


class GISIntentSpec(BaseModel):
    """理解面：core（单一真相）+ typed sections + 歧义账本 + 字段级 provenance。"""

    model_config = ConfigDict(extra="forbid")

    core: MapRequestIntent
    task: TaskSpec = Field(default_factory=TaskSpec)
    aoi: AOISpec = Field(default_factory=AOISpec)
    subject: SubjectSpec = Field(default_factory=SubjectSpec)
    datasets: Tuple[str, ...] = Field((), max_length=MAX_DATASETS)  # 用户显式点名的数据集
    measures: List[MeasureSpec] = Field(default_factory=list, max_length=MAX_MEASURES)
    time: TimeSpec = Field(default_factory=TimeSpec)
    statistics: StatisticsSpec = Field(default_factory=StatisticsSpec)
    spatial_relation: SpatialRelationSpec = Field(default_factory=SpatialRelationSpec)
    purpose: str = Field("", max_length=32)         # standards MAP_PURPOSES 词表（"" = 未表达）
    audience: str = Field("", max_length=32)        # standards MAP_AUDIENCES 词表
    representation: RepresentationSpec = Field(default_factory=RepresentationSpec)
    scene: SceneSpec = Field(default_factory=SceneSpec)  # G3D04：三维表达需求
    components: RequiredComponentsSpec = Field(default_factory=RequiredComponentsSpec)
    output: OutputSpec = Field(default_factory=OutputSpec)
    ambiguities: List[Ambiguity] = Field(default_factory=list, max_length=MAX_AMBIGUITIES)
    locks: List[UserLock] = Field(default_factory=list, max_length=MAX_LOCKS)
    field_provenance: Dict[str, Provenance] = Field(
        default_factory=dict, max_length=MAX_FIELD_PROVENANCE)


# ── MapRequirementSpec（义务面） ────────────────────────────────────────

RequirementKind = Literal[
    "map", "analysis", "comparison", "statistics", "chart", "export",
]
"""对齐 goal_satisfaction.contracts.RequirementKind 词表（D-03）。"""


class RequirementItem(BaseModel):
    """一条可验证需求义务（proposed→clarified→accepted；superseded/rejected 终态）。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(max_length=16)                  # r1 / r2 …
    kind: RequirementKind
    statement: str = Field("", max_length=MAX_TEXT)
    source_path: str = Field("", max_length=128)    # 派生自 intent 的 dotted path
    state: SlotState = "proposed"
    provenance: Provenance = Field(default_factory=lambda: EMPTY_PROVENANCE)
    pinned: bool = False                            # 用户钉死，graceful degradation 不得吞
    capability: str = Field("", max_length=64)
    export_format: str = Field("", max_length=16)
    scope_name: str = Field("", max_length=64)
    group_by: str = Field("", max_length=48)


class MapRequirementSpec(BaseModel):
    """义务面：由理解面单向派生（derive_requirements），不反向改写理解面。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "map_requirement_spec.v1"
    items: List[RequirementItem] = Field(default_factory=list, max_length=MAX_REQUIREMENTS)
    derived_from: str = Field("", max_length=80)    # 派生时的 requirement_digest


# ── Patch 协议 ───────────────────────────────────────────────────────

PatchOp = Literal[
    "set",              # path→value（白名单 path）
    "add_measure",      # value: {phrase, statistic, ...}
    "remove_measure",   # value: measure id
    "answer_ambiguity",  # value: {context_key, answer}
    "waive_ambiguity",  # value: context_key
    "add_lock",         # value: {scope, value}
    "remove_lock",      # value: {scope, value}
    "accept",           # 用户接受当前需求文档
    "supersede",        # 系统超替（新任务到来）
]


class PatchRecord(BaseModel):
    """一次需求修订。journal 即历史：可重放（fold_patches）、可 diff、可归因。

    ``op_id`` 幂等键；``expected_revision`` CAS；``reason`` 携带默认
    rationale 或用户话语引用（稳定 receipt，不靠自由文本）。
    """

    model_config = ConfigDict(extra="forbid")

    op_id: str = Field(max_length=64)
    turn: int = Field(0, ge=0)
    actor: Actor
    op: PatchOp
    path: str = Field("", max_length=128)
    value: Optional[Any] = None                     # JSON 值（set=标量/列表；其余=dict）
    reason: str = Field("", max_length=MAX_TEXT)
    expected_revision: Optional[int] = None


# ── 文档 envelope ────────────────────────────────────────────────────


class RequirementDocument(BaseModel):
    """需求文档（requirement_document.v1）：F02 的唯一持久单位。

    ``patches`` 为有界 journal（MAX_PATCHES 现网 + folded_patch_count 折叠
    计数）；``fold_patches(base=genesis, journal)`` 必须等于现网文档
    （回放不变式，patch.py 测试覆盖）。
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = REQUIREMENT_DOCUMENT_SCHEMA
    document_id: str = Field(max_length=64)
    revision: int = Field(1, ge=1)
    lifecycle: DocumentLifecycle = "draft"
    intent: GISIntentSpec
    requirements: MapRequirementSpec = Field(default_factory=MapRequirementSpec)
    patches: List[PatchRecord] = Field(default_factory=list, max_length=MAX_PATCHES)
    folded_patch_count: int = Field(0, ge=0)
    superseded_by: str = Field("", max_length=64)
    created_turn: int = Field(0, ge=0)
    updated_turn: int = Field(0, ge=0)
