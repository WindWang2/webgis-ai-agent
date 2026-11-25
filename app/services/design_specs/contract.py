"""DesignSpecV1 —— 制图设计规范 typed 契约（WP1 权威模型）。

设计规范是一份**引用与约束文档**：它引用既有权威 registry 的条目
（主题 / 色带 / 地图模型 / 组件类型与变体 / 受控字体 / 纸张 profile /
G40 资产 id+revision）并声明数据角色与覆盖策略。三条红线：

1. **不是第二 MapSpec / 渲染器 / 组件库** —— 规范只引用、只约束，执行
   权威不动（MapSpec mutation、print_layout authoring、publication 导出
   各归其主）；
2. **数据值禁止内嵌** —— 分类只允许方法声明（method+k），绝不携带
   breakpoints / stops / 内联 GeoJSON / ``ref:`` 游标 / 凭证形态字段；
   真实数据值属于冻结的数据集合（系列层负责，按数据指纹缓存）；
3. **引用必须可解析** —— 每个引用面创建/落版本时对照权威 registry
   fail-closed 校验，violation 有界快照随版本行落库（同 TemplateVersion
   ``component_refs`` 纪律）。

词表纪律：能复用既有封闭词表的绝不另造 —— purpose/audience/medium 与
data-semantics 复用 ADR-0200 标准层（``standards/rule.py``）；分类方法
词表对齐 ``classify.py``；字体对齐 G34 ``FONT_IDS``；纸张对齐
``publication_ir.PAGE_PROFILES``；输出目标对齐 theme descriptor 的
``output_targets``。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

#: 契约版本（语义变更 = bump + 迁移说明；未知版本 typed 拒绝）。
DESIGN_SPEC_VERSION = "design_spec.v1"

# ── 封闭词表（全部引用既有权威；本模块不发明新语义词） ─────────────────

#: ADR-0200 标准层义务轴 —— 规范适用面沿用同一词表。
PURPOSES: Tuple[str, ...] = ("exploration", "analysis", "publication", "briefing")
AUDIENCES: Tuple[str, ...] = ("public", "technical", "executive", "education")
MEDIUMS: Tuple[str, ...] = ("screen", "mobile", "print")

#: 数据语义（ADR-0200 DATA_SEMANTICS；数据角色的 measurement 轴）。
VALUE_SEMANTICS: Tuple[str, ...] = (
    "count", "rate", "density", "category", "continuous",
    "temporal", "uncertainty",
)

#: 数据角色值类型（属性表值类型；dataset semantics 字段类型对齐）。
ROLE_VALUE_TYPES: Tuple[str, ...] = ("number", "category", "text", "boolean")

#: 输出目标（theme descriptor output_targets 词表）。
OUTPUT_TARGETS: Tuple[str, ...] = ("interactive", "png", "pdf", "svg")

#: 分类策略方法词表（classify.py 5 数值法 + 类目/连续两表达形态）。
CLASSIFICATION_METHODS: Tuple[str, ...] = (
    "quantiles", "equal_interval", "natural_breaks", "std_dev", "head_tail",
    "categorical", "continuous",
)

#: 无数据/空值处理词表。
NO_DATA_HANDLING: Tuple[str, ...] = ("explicit_class", "exclude_disclosed")

#: 几何形态（model_library geometry 词表投影）。
GEOMETRY_KINDS: Tuple[str, ...] = ("polygon", "point", "line", "raster")

#: 用户可覆盖字段（封闭词表；覆盖必须显式披露）。
USER_OVERRIDABLE: Tuple[str, ...] = (
    "title", "subtitle", "extent", "viewport", "annotation_text",
    "manual_positions", "layer_visibility",
)

#: 可锁定字段（锁定后 apply/传播不得改写；用户手工锁定优先于规范）。
LOCKABLE_FIELDS: Tuple[str, ...] = (
    "theme", "palettes", "classification_method", "typography",
    "unit_semantics", "required_components",
)

#: 规范来源词表。
LINEAGE_SOURCES: Tuple[str, ...] = ("created", "extracted", "derived")

# ── 有界预算 ─────────────────────────────────────────────────────────

_MAX_MAP_MODELS = 32
_MAX_DATA_ROLES = 8
_MAX_REQUIRED_COMPONENTS = 16
_MAX_VIOLATIONS = 32
_NAME_PATTERN = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9][\u4e00-\u9fffA-Za-z0-9 _\-()·]{0,119}$")

# ── 防泄漏纪律的封闭违规码 ───────────────────────────────────────────

LEAK_DATA_VALUES = "DATA_VALUES_FORBIDDEN"
LEAK_REF_CURSOR = "REF_CURSOR_FORBIDDEN"
LEAK_CREDENTIAL = "CREDENTIAL_FIELD_FORBIDDEN"
LEAK_INLINE_DATA = "INLINE_DATA_FORBIDDEN"

#: 疑似数据断点的键词（包含式匹配 + ≥3 数值列表才判定，避免误伤 1–2 个
#: 数值的排版参数；extra=forbid schema 之外这是第二道语义防线）。
_DATA_VALUE_KEY_TOKENS = ("break", "stop", "threshold", "quantile")
_FORBIDDEN_INLINE_KEYS = ("inlineData", "geojson", "features")
_CREDENTIAL_KEY_PATTERN = re.compile(
    r"(password|passwd|secret|token|api_?key|credential|private_?key)", re.IGNORECASE
)
_NUMBER_SEQUENCE_MIN = 3  # ≥3 个数值的列表视为疑似断点数据（1–2 个是合法排版参数）


# ── typed payload 模型 ───────────────────────────────────────────────


class _SpecModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Applicability(_SpecModel):
    """适用面：什么地图/什么用途受本规范治理（全部可选 = 通配）。"""

    purposes: List[str] = Field(default_factory=list)
    audiences: List[str] = Field(default_factory=list)
    mediums: List[str] = Field(default_factory=list)
    geometry_kinds: List[str] = Field(default_factory=list)
    map_models: List[str] = Field(default_factory=list)

    @field_validator("purposes")
    @classmethod
    def _v_purposes(cls, v: List[str]) -> List[str]:
        bad = [x for x in v if x not in PURPOSES]
        if bad:
            raise ValueError(f"unknown purposes: {bad} (allowed: {PURPOSES})")
        return v[:8]

    @field_validator("audiences")
    @classmethod
    def _v_audiences(cls, v: List[str]) -> List[str]:
        bad = [x for x in v if x not in AUDIENCES]
        if bad:
            raise ValueError(f"unknown audiences: {bad} (allowed: {AUDIENCES})")
        return v[:8]

    @field_validator("mediums")
    @classmethod
    def _v_mediums(cls, v: List[str]) -> List[str]:
        bad = [x for x in v if x not in MEDIUMS]
        if bad:
            raise ValueError(f"unknown mediums: {bad} (allowed: {MEDIUMS})")
        return v[:8]

    @field_validator("geometry_kinds")
    @classmethod
    def _v_geometry(cls, v: List[str]) -> List[str]:
        bad = [x for x in v if x not in GEOMETRY_KINDS]
        if bad:
            raise ValueError(f"unknown geometry_kinds: {bad} (allowed: {GEOMETRY_KINDS})")
        return v[:8]

    @field_validator("map_models")
    @classmethod
    def _v_models(cls, v: List[str]) -> List[str]:
        if len(v) > _MAX_MAP_MODELS:
            raise ValueError(f"map_models over budget (>{_MAX_MAP_MODELS})")
        return v


class ColorPolicy(_SpecModel):
    """色彩策略：引用既有 theme / palette，不携带色值。"""

    theme_id: Optional[str] = Field(default=None, max_length=80)
    palette_sequential: Optional[str] = Field(default=None, max_length=60)
    palette_diverging: Optional[str] = Field(default=None, max_length=60)
    palette_categorical: Optional[str] = Field(default=None, max_length=60)
    require_cvd_safe: bool = False


class ClassificationPolicy(_SpecModel):
    """分类策略：只允许方法声明；真实断点在冻结数据集合上计算（系列层）。"""

    default_method: Optional[str] = None
    k_min: int = Field(default=3, ge=2, le=12)
    k_max: int = Field(default=6, ge=2, le=12)
    #: 系列可比性契约：同一指标跨图必须共享断点（系列层强制）。
    shared_required: bool = False
    no_data_handling: str = "exclude_disclosed"

    @field_validator("default_method")
    @classmethod
    def _v_method(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in CLASSIFICATION_METHODS:
            raise ValueError(
                f"unknown classification method: {v} (allowed: {CLASSIFICATION_METHODS})"
            )
        return v

    @field_validator("no_data_handling")
    @classmethod
    def _v_no_data(cls, v: str) -> str:
        if v not in NO_DATA_HANDLING:
            raise ValueError(f"unknown no_data_handling: {v} (allowed: {NO_DATA_HANDLING})")
        return v


class TypographyPolicy(_SpecModel):
    """字体策略：引用 G34 受控字体资源（font id），带真实度量校验。"""

    font_resource: Optional[str] = Field(default=None, max_length=64)
    title_size_pt: float = Field(default=18.0, ge=8.0, le=48.0)
    base_size_pt: float = Field(default=10.0, ge=6.0, le=24.0)
    cjk_first: bool = True


class ComponentRequirement(_SpecModel):
    component_type: str = Field(max_length=64)
    variant: Optional[str] = Field(default=None, max_length=64)
    min_count: int = Field(default=1, ge=1, le=8)


class ComponentsPolicy(_SpecModel):
    required: List[ComponentRequirement] = Field(default_factory=list)
    forbidden: List[str] = Field(default_factory=list)

    @field_validator("required")
    @classmethod
    def _v_required(cls, v: List[ComponentRequirement]) -> List[ComponentRequirement]:
        if len(v) > _MAX_REQUIRED_COMPONENTS:
            raise ValueError(f"required components over budget (>{_MAX_REQUIRED_COMPONENTS})")
        seen = set()
        for item in v:
            if item.component_type in seen:
                raise ValueError(f"duplicate required component: {item.component_type}")
            seen.add(item.component_type)
        return v

    @field_validator("forbidden")
    @classmethod
    def _v_forbidden(cls, v: List[str]) -> List[str]:
        if len(v) > _MAX_REQUIRED_COMPONENTS:
            raise ValueError("forbidden components over budget")
        return v


class OutputProfile(_SpecModel):
    targets: List[str] = Field(default_factory=lambda: ["interactive", "png", "pdf", "svg"])
    paper_profile: Optional[str] = Field(default=None, max_length=40)
    dpi: int = Field(default=300, ge=72, le=600)

    @field_validator("targets")
    @classmethod
    def _v_targets(cls, v: List[str]) -> List[str]:
        bad = [x for x in v if x not in OUTPUT_TARGETS]
        if bad:
            raise ValueError(f"unknown output targets: {bad} (allowed: {OUTPUT_TARGETS})")
        return v[:4]


class DataRoleSpec(_SpecModel):
    """数据角色：跨图绑定的字段语义契约（单位/测量语义可比性）。"""

    name: str = Field(max_length=40)
    value_type: str = "number"
    measurement: Optional[str] = None
    unit: Optional[str] = Field(default=None, max_length=24)
    unit_required: bool = False
    #: 跨图可比：绑定该角色的字段在系列内必须同单位同测量语义。
    comparable_across_maps: bool = False
    description: str = Field(default="", max_length=200)

    @field_validator("name")
    @classmethod
    def _v_name(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", v):
            raise ValueError("role name must be lower_snake_case (≤40)")
        return v

    @field_validator("value_type")
    @classmethod
    def _v_value_type(cls, v: str) -> str:
        if v not in ROLE_VALUE_TYPES:
            raise ValueError(f"unknown value_type: {v} (allowed: {ROLE_VALUE_TYPES})")
        return v

    @field_validator("measurement")
    @classmethod
    def _v_measurement(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in VALUE_SEMANTICS:
            raise ValueError(f"unknown measurement: {v} (allowed: {VALUE_SEMANTICS})")
        return v


class OverridesPolicy(_SpecModel):
    """覆盖策略：用户/Agent 各自允许改什么；锁定字段不得被自动传播改写。"""

    user_overridable: List[str] = Field(default_factory=lambda: list(USER_OVERRIDABLE))
    locked: List[str] = Field(default_factory=list)
    #: 显式覆盖必须携带解释（不允许 Agent 背地里覆盖手工改图）。
    require_override_disclosure: bool = True

    @field_validator("user_overridable")
    @classmethod
    def _v_overridable(cls, v: List[str]) -> List[str]:
        bad = [x for x in v if x not in USER_OVERRIDABLE]
        if bad:
            raise ValueError(f"unknown overridable fields: {bad} (allowed: {USER_OVERRIDABLE})")
        return v

    @field_validator("locked")
    @classmethod
    def _v_locked(cls, v: List[str]) -> List[str]:
        bad = [x for x in v if x not in LOCKABLE_FIELDS]
        if bad:
            raise ValueError(f"unknown locked fields: {bad} (allowed: {LOCKABLE_FIELDS})")
        return v


class LineageInfo(_SpecModel):
    """规范来源（审计面；不含会话 selection / 凭证）。"""

    source: str = "created"
    source_session_id: Optional[str] = Field(default=None, max_length=255)
    source_mapspec_fingerprint: Optional[str] = Field(default=None, max_length=80)
    derived_from_spec_id: Optional[str] = Field(default=None, max_length=36)
    note: str = Field(default="", max_length=300)

    @field_validator("source")
    @classmethod
    def _v_source(cls, v: str) -> str:
        if v not in LINEAGE_SOURCES:
            raise ValueError(f"unknown lineage source: {v} (allowed: {LINEAGE_SOURCES})")
        return v


class DesignSpecV1(_SpecModel):
    """design_spec.v1 —— 设计规范 typed 文档。"""

    spec_version: Literal["design_spec.v1"] = DESIGN_SPEC_VERSION
    name: str = Field(max_length=120)
    description: str = Field(default="", max_length=500)
    applicability: Applicability = Field(default_factory=Applicability)
    color: ColorPolicy = Field(default_factory=ColorPolicy)
    classification: ClassificationPolicy = Field(default_factory=ClassificationPolicy)
    typography: TypographyPolicy = Field(default_factory=TypographyPolicy)
    components: ComponentsPolicy = Field(default_factory=ComponentsPolicy)
    output: OutputProfile = Field(default_factory=OutputProfile)
    data_roles: List[DataRoleSpec] = Field(default_factory=list)
    overrides: OverridesPolicy = Field(default_factory=OverridesPolicy)
    lineage: LineageInfo = Field(default_factory=LineageInfo)

    @field_validator("name")
    @classmethod
    def _v_name(cls, v: str) -> str:
        if not _NAME_PATTERN.fullmatch(v or ""):
            raise ValueError(
                "name must be 1-120 chars (CJK/letter/digit first, no control chars)"
            )
        return v.strip()

    @field_validator("data_roles")
    @classmethod
    def _v_roles(cls, v: List[DataRoleSpec]) -> List[DataRoleSpec]:
        if len(v) > _MAX_DATA_ROLES:
            raise ValueError(f"data_roles over budget (>{_MAX_DATA_ROLES})")
        seen = set()
        for item in v:
            if item.name in seen:
                raise ValueError(f"duplicate data role: {item.name}")
            seen.add(item.name)
        return v


# ── 防泄漏结构巡检（raw payload 上执行，先于 pydantic 丢弃 extras） ────


def _walk_leak_findings(node: Any, path: str, findings: List[Dict[str, str]]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            key_text = str(key)
            child = f"{path}.{key_text}" if path else key_text
            if _CREDENTIAL_KEY_PATTERN.search(key_text):
                findings.append({"code": LEAK_CREDENTIAL, "field": child})
                continue
            if key_text in _FORBIDDEN_INLINE_KEYS:
                findings.append({"code": LEAK_INLINE_DATA, "field": child})
                continue
            lowered = key_text.lower()
            if any(tok in lowered for tok in _DATA_VALUE_KEY_TOKENS) and isinstance(value, list):
                nums = [x for x in value if isinstance(x, (int, float)) and not isinstance(x, bool)]
                if len(nums) >= _NUMBER_SEQUENCE_MIN:
                    findings.append({"code": LEAK_DATA_VALUES, "field": child})
                    continue
            if isinstance(value, str) and value.startswith("ref:"):
                findings.append({"code": LEAK_REF_CURSOR, "field": child})
                continue
            _walk_leak_findings(value, child, findings)
    elif isinstance(node, list):
        for i, item in enumerate(node[:64]):
            _walk_leak_findings(item, f"{path}[{i}]", findings)


# ── 引用校验（对照权威 registry；懒 import 防环） ─────────────────────


def _validate_reference(payload: Dict[str, Any]) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    violations: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    def _violation(field: str, ref: str, reason: str) -> None:
        violations.append({"field": field, "ref": str(ref)[:80], "reason": reason})

    def _warning(field: str, ref: str, reason: str) -> None:
        warnings.append({"field": field, "ref": str(ref)[:80], "reason": reason})

    # 1) theme / palette（themes registry 单一目录）
    color = payload.get("color") or {}
    theme_id = color.get("theme_id")
    if theme_id:
        from app.lib.cartography.themes import get_cartographic_theme_registry

        theme = get_cartographic_theme_registry().get_theme(str(theme_id))
        if theme is None:
            _violation("color.theme_id", theme_id, "unknown_theme")
    for slot in ("palette_sequential", "palette_diverging", "palette_categorical"):
        pid = color.get(slot)
        if pid:
            from app.lib.cartography.themes import get_cartographic_theme_registry

            if get_cartographic_theme_registry().get_palette(str(pid)) is None:
                _violation(f"color.{slot}", pid, "unknown_palette")

    # 2) map models（model_library：id 或别名）
    for model_ref in (payload.get("applicability") or {}).get("map_models") or []:
        from app.lib.cartography.model_library import get_map_model_registry

        model = get_map_model_registry().resolve(str(model_ref))
        if model is None:
            _violation("applicability.map_models", model_ref, "unknown_map_model")
        elif getattr(model, "runtime_status", "native") == "planned":
            _violation("applicability.map_models", model_ref, "map_model_planned")

    # 3) 组件类型与变体（component_registry）
    components = payload.get("components") or {}
    from app.lib.cartography.component_registry import get_component_registry

    registry = get_component_registry()
    for item in components.get("required") or []:
        ctype = str(item.get("component_type") or "")
        desc = registry.get(ctype)
        if desc is None:
            _violation("components.required", ctype, "unknown_component_type")
            continue
        variant = item.get("variant")
        if variant:
            variants = getattr(desc, "variants", None) or []
            if variants and str(variant) not in variants:
                _violation("components.required", f"{ctype}:{variant}", "unknown_component_variant")
        if getattr(desc, "deprecated", False):
            _warning("components.required", ctype, "component_deprecated")
    for ctype in components.get("forbidden") or []:
        if registry.get(str(ctype)) is None:
            _violation("components.forbidden", ctype, "unknown_component_type")

    # 4) 字体资源（G34 受控字体；真实可用性）
    typography = payload.get("typography") or {}
    font_id = typography.get("font_resource")
    if font_id:
        from app.services.print_layout_authoring.fonts import FONT_IDS

        if str(font_id) not in FONT_IDS:
            _violation("typography.font_resource", font_id, "unknown_font_resource")

    # 5) 纸张 profile（publication_ir）
    output = payload.get("output") or {}
    paper = output.get("paper_profile")
    if paper:
        from app.lib.cartography.publication_ir import PAGE_PROFILES

        if str(paper) not in PAGE_PROFILES:
            _violation("output.paper_profile", paper, "unknown_paper_profile")

    return violations[:_MAX_VIOLATIONS], warnings[:_MAX_VIOLATIONS]


# ── 校验入口与 digest ────────────────────────────────────────────────


def validate_design_spec_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """raw payload → 校验报告（schema + 引用 + 防泄漏）。

    返回 ``{ok, spec (解析后的 typed dict 或 None), violations, warnings,
    leak_findings}``；violations 非空 = fail-closed（创建/落版本拒绝）。
    """
    violations: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    leak: List[Dict[str, str]] = []
    _walk_leak_findings(payload, "", leak)
    for item in leak:
        violations.append({"field": item["field"], "ref": "", "reason": item["code"]})

    spec_model = None
    if not violations:
        try:
            spec_model = DesignSpecV1.model_validate(payload)
        except ValidationError as exc:
            for err in exc.errors()[:16]:
                violations.append(
                    {
                        "field": ".".join(str(p) for p in err.get("loc", ())),
                        "ref": "",
                        "reason": f"schema:{err.get('type', 'invalid')}",
                    }
                )
    if not violations:
        ref_violations, ref_warnings = _validate_reference(payload)
        violations.extend(ref_violations)
        warnings.extend(ref_warnings)

    # 跨字段一致性：可跨图比较的角色存在但未声明共享分类 → warning
    if spec_model is not None:
        comparable = [r.name for r in spec_model.data_roles if r.comparable_across_maps]
        if comparable and not spec_model.classification.shared_required:
            warnings.append(
                {
                    "field": "classification.shared_required",
                    "ref": ",".join(comparable)[:80],
                    "reason": "comparable_roles_without_shared_classification",
                }
            )
        if spec_model.classification.k_min > spec_model.classification.k_max:
            violations.append(
                {"field": "classification.k_min", "ref": "", "reason": "k_min_gt_k_max"}
            )

    return {
        "ok": not violations,
        "spec": spec_model.model_dump() if spec_model is not None else None,
        "violations": violations[:_MAX_VIOLATIONS],
        "warnings": warnings[:_MAX_VIOLATIONS],
        "leak_findings": leak[:_MAX_VIOLATIONS],
    }


def canonical_json(payload: Any) -> str:
    """canonical JSON（sorted keys、紧凑分隔符、UTF-8）—— digest 单一实现。"""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def structural_digest(payload: Dict[str, Any]) -> str:
    """结构 digest：规范语义内容的 sha256（不含主表元数据/时间戳）。"""
    return "dspec-sha256:" + hashlib.sha256(
        canonical_json(payload).encode("utf-8")
    ).hexdigest()


__all__ = [
    "DESIGN_SPEC_VERSION",
    "PURPOSES",
    "AUDIENCES",
    "MEDIUMS",
    "VALUE_SEMANTICS",
    "OUTPUT_TARGETS",
    "CLASSIFICATION_METHODS",
    "NO_DATA_HANDLING",
    "GEOMETRY_KINDS",
    "USER_OVERRIDABLE",
    "LOCKABLE_FIELDS",
    "LINEAGE_SOURCES",
    "DesignSpecV1",
    "Applicability",
    "ColorPolicy",
    "ClassificationPolicy",
    "TypographyPolicy",
    "ComponentsPolicy",
    "OutputProfile",
    "DataRoleSpec",
    "OverridesPolicy",
    "LineageInfo",
    "validate_design_spec_payload",
    "canonical_json",
    "structural_digest",
]
