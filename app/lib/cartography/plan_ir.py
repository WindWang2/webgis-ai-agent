"""MapPlanIR —— 版本化制图计划中间表示（F12 / ADR-0214 D1）。

planner/LLM 负责意图与选择（哪个数据集、哪种植被表达、用户改了什么），
本 IR 是它们与 MapSpec mutation 之间的**确定性中间层**：所有输入一律引用
权威决策（grammar decision / recipe / template / capability manifest /
measurement profile），本模块**不复制大 payload、不重新推断任何语义**。

纪律（与 decision_record 同口径）：

- **refs-only**：每条引用 = (ref_id, fingerprint, schema_version)；有界字段
  逐项钳制（条目数、字符串长度），超限构造期 fail-closed。
- **确定性**：``plan_ir_fingerprint`` = canonical JSON sha256（sort_keys +
  有限浮点精度，复用 decision_record.canonical_decision_json）；同输入同 id。
- **可序列化**：纯 Pydantic v2 模型，model_dump 后可直接进 trace/replay。
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

#: IR schema 版本（语义变更 = bump + 迁移说明；消费方按版本分派）。
PLAN_IR_VERSION = "1.0.0"

#: 内容寻址 id 前缀。
IR_ID_PREFIX = "mpir-"

# ── 有界词表（封闭；新增 = additive）────────────────────────────────────

LayerAction = Literal[
    "present_primary",     # 新建/确保主专题层（可见）
    "present_secondary",   # 新建/确保次级层（可见）
    "present_reference",   # 新建/确保参考底衬层（可见）
    "set_visibility",      # 仅改既有层可见性（多轮：隐藏/恢复）
    "restyle",             # 仅改既有层表达面（paint/legend/k/palette）
    "rebind",              # 仅改既有层数据绑定 ref
    "remove",              # 显式删除既有层（amendment 明确要求）
]
ROLE_ACTIONS: Dict[str, str] = {
    "primary": "present_primary",
    "secondary": "present_secondary",
    "reference": "present_reference",
}

ComponentAction = Literal["ensure", "patch", "hide", "remove"]

AuthorityKind = Literal[
    "plan",                 # MapProductPlan（planner 产物）
    "grammar_decision",     # GrammarDecision（方向4）
    "recipe",               # CartographyRecipe
    "template",             # 制图/产品模板
    "capability_manifest",  # runtime manifest v4（ABI v2）
    "measurement_profile",  # DatasetMeasurementProfile（方向2）
    "field_resolution",     # FieldResolution（双语字段解析）
]

EvidenceKind = Literal[
    "requirement", "grammar_audit", "symbology_rationale",
    "lock_snapshot", "amendment", "analysis_output", "disclosure",
]

#: 多轮 amendment 封闭词表（ADR-0214 D2）。
AmendmentKind = Literal[
    "add_chart", "set_layer_visibility", "restyle_layer", "set_title",
    "add_export", "pin_component_zone", "remove_layer", "remove_component",
]

# ── 有界上限（构造期强制；防 payload 走私）───────────────────────────────

MAX_REQUIREMENTS = 16
MAX_DATASETS = 16
MAX_FIELDS_PER_DATASET = 16
MAX_AUTHORITIES = 16
MAX_ANALYSIS_OUTPUTS = 16
MAX_LAYER_INTENTS = 32
MAX_COMPONENT_INTENTS = 48
MAX_EXPORTS = 8
MAX_EVIDENCE = 32
MAX_AMENDMENTS = 32
MAX_REASON_CODES = 12
MAX_DISCLOSURES = 12
_STR_MAX = 256
_ID_MAX = 128
#: blueprint paint/legend patch 键数与字节预算（表达面 token，不是数据）。
_BLUEPRINT_KEYS_MAX = 24
_BLUEPRINT_BYTES_MAX = 8192


def _canon(value: Any) -> str:
    """canonical JSON（与 decision_record 同口径：排序键 + 浮点 6 位）。"""
    try:
        from app.lib.runtime.decision_record import canonical_decision_json
        return canonical_decision_json(value)
    except Exception:  # noqa: BLE001 — 循环 import 防线（本地等价实现）
        import json

        def _round(v: Any) -> Any:
            if isinstance(v, float):
                return round(v, 6)
            if isinstance(v, dict):
                return {k: _round(x) for k, x in v.items()}
            if isinstance(v, (list, tuple)):
                return [_round(x) for x in v]
            return v

        return json.dumps(_round(value), sort_keys=True,
                          ensure_ascii=False, default=str)


def digest_of(payload: Any) -> str:
    return hashlib.sha256(_canon(payload).encode("utf-8")).hexdigest()


# ── 引用面（refs-only；大 payload 的唯一合法载体是指纹）──────────────────


class _Bounded(BaseModel):
    """共用模型约束（frozen = 构造后不可变，保证 IR 可作 dict 键/可 replay）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")


class RequirementRef(_Bounded):
    """一条用户需求记录的引用（原文不入 IR —— 只有 id + 内容指纹）。"""

    requirement_id: str = Field(min_length=1, max_length=_ID_MAX)
    text_digest: str = Field(min_length=8, max_length=64)  # 原文 sha256 前缀
    turn_id: str = Field(default="", max_length=_ID_MAX)


class FieldRef(_Bounded):
    """字段引用（语义来自 measurement_profile / field_resolution，不重算）。"""

    name: str = Field(min_length=1, max_length=_STR_MAX)
    role: str = Field(default="primary", max_length=32)  # primary|secondary|context
    measurement: str = Field(default="", max_length=48)  # MeasurementKind 或空


class DatasetRef(_Bounded):
    """数据集引用（profile 指纹判 stale；绝不携带要素/统计数据）。"""

    dataset_id: str = Field(min_length=1, max_length=_ID_MAX)
    bound_ref: str = Field(default="", max_length=_ID_MAX)  # source ref / alias
    profile_fingerprint: str = Field(default="", max_length=80)
    geometry: str = Field(default="", max_length=32)
    crs: str = Field(default="", max_length=64)
    feature_count: Optional[int] = Field(default=None, ge=0)
    fields: List[FieldRef] = Field(default_factory=list, max_length=MAX_FIELDS_PER_DATASET)


class AuthorityRef(_Bounded):
    """权威决策引用（grammar/recipe/template/manifest/plan/profile）。"""

    kind: AuthorityKind
    ref_id: str = Field(min_length=1, max_length=_ID_MAX)
    fingerprint: str = Field(default="", max_length=80)
    schema_version: str = Field(default="", max_length=32)


class AnalysisOutputRef(_Bounded):
    """分析产物引用（统计/分级结果 artifact 的指纹，不是结果本体）。"""

    output_id: str = Field(min_length=1, max_length=_ID_MAX)
    capability: str = Field(default="", max_length=96)
    fingerprint: str = Field(default="", max_length=80)


class EvidenceRef(_Bounded):
    """证据引用（结构化、有界；free text 一律禁 —— 用 reason codes）。"""

    kind: EvidenceKind
    ref: str = Field(min_length=1, max_length=192)
    digest: str = Field(default="", max_length=80)


# ── 意图面 ───────────────────────────────────────────────────────────────


class LayerBlueprint(_Bounded):
    """有界图层蓝图：compiler 构建 upsert layer dict 的**唯一**表达面输入。

    只携带表达面 token（type/paint 子集/legend 形态摘要/k/方法/palette），
    不携带任何要素数据；键数与字节预算双闸（超限构造期拒绝 —— 拒绝即暴露
    投影层把大 payload 塞进了 IR）。
    """

    layer_type: str = Field(min_length=1, max_length=48)   # fill/line/circle/heatmap/raster…
    cartography: str = Field(default="", max_length=96)     # 制图家族（plan 词表）
    paint: Dict[str, Any] = Field(default_factory=dict)
    legend_spec: Dict[str, Any] = Field(default_factory=dict)
    classification: Dict[str, Any] = Field(default_factory=dict)  # {k, method, palette}
    label_spec: Dict[str, Any] = Field(default_factory=dict)

    def blueprint_bytes(self) -> int:
        return len(_canon(self.model_dump()).encode("utf-8"))

    def model_post_init(self, __context: Any) -> None:
        sections = (self.paint, self.legend_spec, self.classification, self.label_spec)
        for name, section in (("paint", self.paint), ("legend_spec", self.legend_spec),
                              ("classification", self.classification),
                              ("label_spec", self.label_spec)):
            if len(section) > _BLUEPRINT_KEYS_MAX:
                raise ValueError(
                    f"LayerBlueprint.{name} 键数 {len(section)} 超上限 "
                    f"{_BLUEPRINT_KEYS_MAX}（IR refs-only：数据不入 IR）")
        if self.blueprint_bytes() > _BLUEPRINT_BYTES_MAX:
            raise ValueError(
                f"LayerBlueprint 字节 {self.blueprint_bytes()} 超预算 "
                f"{_BLUEPRINT_BYTES_MAX}（IR refs-only：数据不入 IR）")


class LayerIntent(_Bounded):
    """单图层意图：期望终态（可见性/角色/表达面），不是操作脚本。

    ``layer_id`` 空 = 新建（由 compiler 按 deterministic 命名规则铸 id）；
    非空 = 目标既有层。``locked=True`` 是 workbench 锁快照的转录 —— compiler
    在 obligations 层对锁目标 fail-closed。
    """

    intent_id: str = Field(min_length=1, max_length=_ID_MAX)
    action: LayerAction
    layer_id: str = Field(default="", max_length=_ID_MAX)
    title: str = Field(default="", max_length=_STR_MAX)
    source_ref: str = Field(default="", max_length=_ID_MAX)   # dataset/output ref
    blueprint: Optional[LayerBlueprint] = None
    expected_visible: bool = True
    expected_opacity: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    locked: bool = False          # user 锁快照（只读转录，engine 仍强制）
    origin: Literal["planner", "user", "repair"] = "planner"
    evidence_refs: List[EvidenceRef] = Field(default_factory=list, max_length=8)
    reason_codes: List[str] = Field(default_factory=list, max_length=MAX_REASON_CODES)


class ComponentIntent(_Bounded):
    """单组件意图（title/legend/scale_bar/chart/…；词表归 component registry）。"""

    intent_id: str = Field(min_length=1, max_length=_ID_MAX)
    component_type: str = Field(min_length=1, max_length=64)
    component_id: str = Field(default="", max_length=_ID_MAX)  # 空 = deterministic 铸
    action: ComponentAction = "ensure"
    required: bool = False        # 义务组件（finalization 对账 + obligations 闸）
    title: str = Field(default="", max_length=_STR_MAX)
    options: Dict[str, Any] = Field(default_factory=dict, max_length=_BLUEPRINT_KEYS_MAX)
    bound_layer_id: str = Field(default="", max_length=_ID_MAX)
    pinned_zone: str = Field(default="", max_length=48)  # user pin（user-wins）
    locked: bool = False
    evidence_refs: List[EvidenceRef] = Field(default_factory=list, max_length=8)
    reason_codes: List[str] = Field(default_factory=list, max_length=MAX_REASON_CODES)


# ── 义务面 ───────────────────────────────────────────────────────────────


class LayoutObligation(_Bounded):
    """布局义务（output_purpose 与 grammar 的 OUTPUT_PURPOSES 同词表）。"""

    output_purpose: str = Field(default="screen_16_9", max_length=48)
    viewport_px: List[int] = Field(default_factory=lambda: [1280, 720], min_length=2, max_length=2)
    pinned_zones: Dict[str, str] = Field(default_factory=dict, max_length=_BLUEPRINT_KEYS_MAX)
    purpose: str = Field(default="", max_length=48)
    audience: str = Field(default="", max_length=48)
    medium: str = Field(default="", max_length=48)


class ExportObligation(_Bounded):
    """导出义务（格式词表归 export 面；obligations 层做支持性闸）。"""

    fmt: str = Field(min_length=1, max_length=24)
    required: bool = True
    reason_codes: List[str] = Field(default_factory=list, max_length=MAX_REASON_CODES)


class FinalDisplayObligation(_Bounded):
    """最终显示义务：完成前必须被 deterministic 对账的期望终态。

    这是 DoD「最终显示图层确认」的**期望面**真相：expected_visible_layers
    = layer intent_id → 期望可见性；expected_components = 必须在场且 enabled
    的组件 intent_id 列表。ack_mode 透传 display_confirmation 语义。
    """

    expected_visible_layers: Dict[str, bool] = Field(default_factory=dict, max_length=MAX_LAYER_INTENTS)
    expected_components: List[str] = Field(default_factory=list, max_length=MAX_COMPONENT_INTENTS)
    ack_mode: Literal["auto", "required"] = "auto"
    reason_codes: List[str] = Field(default_factory=list, max_length=MAX_REASON_CODES)


class UserLockSnapshot(_Bounded):
    """编译时刻的 workbench 锁快照（只读转录；权威仍在 workbench state）。"""

    layer_ids: List[str] = Field(default_factory=list, max_length=64)
    component_ids: List[str] = Field(default_factory=list, max_length=64)
    workbench_revision: Optional[int] = Field(default=None, ge=0)
    fingerprint: str = Field(default="", max_length=80)


# ── 根文档 ───────────────────────────────────────────────────────────────


class MapPlanIR(_Bounded):
    """MapPlanIR 根文档（versioned / content-addressed / refs-only）。"""

    ir_version: str = PLAN_IR_VERSION
    ir_id: str = Field(min_length=1, max_length=_ID_MAX)
    revision: int = Field(default=1, ge=1)             # 多轮演进代数
    supersedes: str = Field(default="", max_length=_ID_MAX)  # 上一代 ir_id

    requirements: List[RequirementRef] = Field(default_factory=list, max_length=MAX_REQUIREMENTS)
    datasets: List[DatasetRef] = Field(default_factory=list, max_length=MAX_DATASETS)
    authorities: List[AuthorityRef] = Field(default_factory=list, max_length=MAX_AUTHORITIES)
    analysis_outputs: List[AnalysisOutputRef] = Field(default_factory=list, max_length=MAX_ANALYSIS_OUTPUTS)

    layer_intents: List[LayerIntent] = Field(default_factory=list, max_length=MAX_LAYER_INTENTS)
    component_intents: List[ComponentIntent] = Field(default_factory=list, max_length=MAX_COMPONENT_INTENTS)

    layout: LayoutObligation = Field(default_factory=LayoutObligation)
    exports: List[ExportObligation] = Field(default_factory=list, max_length=MAX_EXPORTS)
    final_display: FinalDisplayObligation = Field(default_factory=FinalDisplayObligation)
    user_locks: UserLockSnapshot = Field(default_factory=UserLockSnapshot)

    evidence: List[EvidenceRef] = Field(default_factory=list, max_length=MAX_EVIDENCE)
    disclosures: List[str] = Field(default_factory=list, max_length=MAX_DISCLOSURES)
    reason_codes: List[str] = Field(default_factory=list, max_length=MAX_REASON_CODES)
    #: 上游 plan 指纹（MapProductPlan.compute_plan_fingerprint 口径转录）。
    plan_fingerprint: str = Field(default="", max_length=80)

    def ir_fingerprint(self) -> str:
        return "mpir-sha256:" + digest_of(self.model_dump())[:40]


def compute_ir_id(payload: Any) -> str:
    """内容寻址 ir_id（不含 ir_id 本身的 canonical sha256 前缀）。"""
    body = {k: v for k, v in dict(payload).items() if k != "ir_id"} \
        if isinstance(payload, dict) else payload
    return IR_ID_PREFIX + digest_of(body)[:12]


def spec_doc_of(current: Any) -> Dict[str, Any]:
    """会话状态 → MapSpec 文档（引擎态 map_state 把文档嵌在 ``mapspec`` 键；
    测试/工具面常直接给扁平 doc）。无文档返回空 dict。"""
    if not isinstance(current, dict):
        return {}
    nested = current.get("mapspec")
    if isinstance(nested, dict):
        return nested
    return current


__all__ = [
    "PLAN_IR_VERSION",
    "IR_ID_PREFIX",
    "ROLE_ACTIONS",
    "RequirementRef", "FieldRef", "DatasetRef", "AuthorityRef",
    "AnalysisOutputRef", "EvidenceRef",
    "LayerBlueprint", "LayerIntent", "ComponentIntent",
    "LayoutObligation", "ExportObligation", "FinalDisplayObligation",
    "UserLockSnapshot",
    "MapPlanIR",
    "compute_ir_id", "digest_of", "spec_doc_of",
]
