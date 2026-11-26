"""MapSeriesV1 —— 系列地图作者计划 typed 契约（WP3）。

系列地图计划是一份**引用与计划文档**：它 pin 一个设计规范（WP1 权威，
``design_spec_id + version``）、登记成员（各自 session 的权威 MapSpec 引用
+ 角色/维度标签/数据绑定）、声明可比性与输出预算。三条红线（与 WP1 同源）：

1. **不建第二地图状态** —— 成员的真实地图状态只存在于各自 session 的权威
   MapSpec（``app/services/mapspec``）；系列只存 session 引用与计划字段；
2. **数据引用逐图绑定** —— ``data_bindings`` 只放字段名（role→field），
   ``ref:`` 游标 / 凭证 / 内联 GeoJSON / 数据断点由防泄漏巡检拒绝
   （复用 ``app/services/design_specs/contract.py`` 的 walk 纪律）；
3. **pin 软废弃兼容** —— 规范退役后既有 pin 继续可解析（解析时点校验
   存在性，不校验 status）。

词表纪律：role/axis 为本域封闭词表；locked_fields/分类方法/输出目标复用
WP1 ``design_specs.contract`` 的权威词表，绝不另造。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# 复用 WP1 权威词表与防泄漏 walk（单一实现，不复制语义）。
from app.services.design_specs.contract import (  # noqa: F401
    CLASSIFICATION_METHODS,
    LOCKABLE_FIELDS,
    OUTPUT_TARGETS,
    _walk_leak_findings,
    canonical_json,
)

#: 契约版本（语义变更 = bump + 迁移说明；未知版本 typed 拒绝）。
MAP_SERIES_VERSION = "map_series.v1"

# ── 封闭词表 ─────────────────────────────────────────────────────────

#: 成员在系列中的表达角色。
MEMBER_ROLES: Tuple[str, ...] = ("primary", "comparison", "context", "detail")

#: 系列维度轴（成员沿哪根轴展开）。
DIMENSION_AXES: Tuple[str, ...] = ("region", "time", "indicator", "theme")

# ── 有界预算 ─────────────────────────────────────────────────────────

_MAX_MEMBERS = 50
_MAX_DIMENSION_TAGS = 8
_MAX_DATA_BINDINGS = 8
_MAX_LOCKED_FIELDS = 16
_MAX_UNIT_CONTRACT = 8
_MAX_LABELS = 50
_MAX_VIOLATIONS = 32
_NAME_PATTERN = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9][\u4e00-\u9fffA-Za-z0-9 _\-()·]{0,119}$")
_MEMBER_ID_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,39}")
_ROLE_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,39}")
_FIELD_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_TAG_PATTERN = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9_\-]{1,40}$")
_HEX_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{3,8}$")

# 防泄漏违规码（复用 WP1 封闭码；本域不新增码）。
LEAK_DATA_VALUES = "DATA_VALUES_FORBIDDEN"
LEAK_REF_CURSOR = "REF_CURSOR_FORBIDDEN"
LEAK_CREDENTIAL = "CREDENTIAL_FIELD_FORBIDDEN"
LEAK_INLINE_DATA = "INLINE_DATA_FORBIDDEN"
#: data_bindings 值必须是纯字段名（role→field；不是列表达式/游标）。
DATA_BINDING_FIELD_INVALID = "DATA_BINDING_FIELD_INVALID"
DATA_BINDING_ROLE_INVALID = "DATA_BINDING_ROLE_INVALID"


# ── typed payload 模型 ───────────────────────────────────────────────


class _SeriesModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SeriesMember(_SeriesModel):
    """系列成员 —— 一个既有 session 的权威 MapSpec 引用 + 计划字段。

    不携带任何地图状态：session_id 是引用，真实状态由 lifecycle engine CAS
    权威。``locked_fields`` 是 user-wins 锁（传播不得改写这些表达字段）。
    """

    member_id: str
    title: str = Field(max_length=160)
    subtitle: Optional[str] = Field(default=None, max_length=160)
    session_id: str = Field(max_length=255)
    role: str = "primary"
    dimension_tags: List[str] = Field(default_factory=list)
    #: [w, s, e, n]（可选；缺省 = 跟随各自会话视口，一致性检查仅披露）。
    extent_override: Optional[List[float]] = None
    #: {role_name: field_name} —— 只放字段名；数据引用逐图绑定各自会话。
    data_bindings: Dict[str, str] = Field(default_factory=dict)
    #: user-wins 锁（⊆ design_specs LOCKABLE_FIELDS；传播 skip 并披露）。
    locked_fields: List[str] = Field(default_factory=list)
    enabled: bool = True

    @field_validator("member_id")
    @classmethod
    def _v_member_id(cls, v: str) -> str:
        if not _MEMBER_ID_PATTERN.fullmatch(v or ""):
            raise ValueError("member_id must be lower_snake_case (≤40)")
        return v

    @field_validator("title", "subtitle")
    @classmethod
    def _v_text(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("title/subtitle must be non-blank when present")
        return v

    @field_validator("session_id")
    @classmethod
    def _v_session(cls, v: str) -> str:
        v = (v or "").strip()
        if not v or len(v) > 255:
            raise ValueError("session_id must be 1-255 chars")
        if any(c in v for c in ("/", "\\", "..")):
            raise ValueError("session_id must be a plain session reference")
        return v

    @field_validator("role")
    @classmethod
    def _v_role(cls, v: str) -> str:
        if v not in MEMBER_ROLES:
            raise ValueError(f"unknown member role: {v} (allowed: {MEMBER_ROLES})")
        return v

    @field_validator("dimension_tags")
    @classmethod
    def _v_tags(cls, v: List[str]) -> List[str]:
        if len(v) > _MAX_DIMENSION_TAGS:
            raise ValueError(f"dimension_tags over budget (>{_MAX_DIMENSION_TAGS})")
        bad = [t for t in v if not _TAG_PATTERN.match(str(t))]
        if bad:
            raise ValueError(f"invalid dimension_tags: {bad[:4]}")
        return v

    @field_validator("extent_override")
    @classmethod
    def _v_extent(cls, v: Optional[List[float]]) -> Optional[List[float]]:
        if v is None:
            return v
        if len(v) != 4 or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
            raise ValueError("extent_override must be [w, s, e, n] numbers")
        w, s, e, n = v
        if not (w < e and s < n):
            raise ValueError("extent_override must satisfy w<e and s<n")
        return [float(x) for x in v]

    @field_validator("data_bindings")
    @classmethod
    def _v_bindings(cls, v: Dict[str, str]) -> Dict[str, str]:
        if len(v) > _MAX_DATA_BINDINGS:
            raise ValueError(f"data_bindings over budget (>{_MAX_DATA_BINDINGS})")
        for role, field_name in v.items():
            if not _ROLE_NAME_PATTERN.fullmatch(str(role)):
                raise ValueError(f"data_bindings role must be lower_snake: {role}")
            if not _FIELD_NAME_PATTERN.fullmatch(str(field_name)):
                raise ValueError(
                    f"data_bindings['{role}'] must be a plain field name, got: {field_name}"
                )
        return v

    @field_validator("locked_fields")
    @classmethod
    def _v_locked(cls, v: List[str]) -> List[str]:
        if len(v) > _MAX_LOCKED_FIELDS:
            raise ValueError(f"locked_fields over budget (>{_MAX_LOCKED_FIELDS})")
        bad = [x for x in v if x not in LOCKABLE_FIELDS]
        if bad:
            raise ValueError(f"unknown locked fields: {bad} (allowed: {LOCKABLE_FIELDS})")
        return v


class ComparabilityContract(_SeriesModel):
    """可比性契约 —— 哪些表达面跨成员共享（一致性检查的生效开关）。"""

    shared_classification: bool = False
    shared_palette: bool = False
    shared_extent_by_role: bool = False
    #: {role_name: unit} ≤8 —— 与规范 data_roles 的单位契约对齐。
    unit_contract: Dict[str, str] = Field(default_factory=dict)

    @field_validator("unit_contract")
    @classmethod
    def _v_units(cls, v: Dict[str, str]) -> Dict[str, str]:
        if len(v) > _MAX_UNIT_CONTRACT:
            raise ValueError(f"unit_contract over budget (>{_MAX_UNIT_CONTRACT})")
        for role, unit in v.items():
            if not _ROLE_NAME_PATTERN.fullmatch(str(role)):
                raise ValueError(f"unit_contract role must be lower_snake: {role}")
            if not str(unit or "").strip() or len(str(unit)) > 24:
                raise ValueError(f"unit_contract['{role}'] must be 1-24 chars")
        return v


class SeriesDimension(_SeriesModel):
    """系列维度 —— 成员沿哪根轴展开（labels 与成员 dimension_tags 呼应）。"""

    axis: str = "region"
    labels: List[str] = Field(default_factory=list)

    @field_validator("axis")
    @classmethod
    def _v_axis(cls, v: str) -> str:
        if v not in DIMENSION_AXES:
            raise ValueError(f"unknown dimension axis: {v} (allowed: {DIMENSION_AXES})")
        return v

    @field_validator("labels")
    @classmethod
    def _v_labels(cls, v: List[str]) -> List[str]:
        if len(v) > _MAX_LABELS:
            raise ValueError(f"dimension labels over budget (>{_MAX_LABELS})")
        return [str(x)[:80] for x in v]


class SeriesOutput(_SeriesModel):
    """输出预算（复用 design_specs OUTPUT_TARGETS 词表；≤4 目标）。"""

    targets: List[str] = Field(default_factory=lambda: ["png", "pdf"])
    paper_profile: Optional[str] = Field(default=None, max_length=40)
    dpi: int = Field(default=300, ge=72, le=600)

    @field_validator("targets")
    @classmethod
    def _v_targets(cls, v: List[str]) -> List[str]:
        bad = [x for x in v if x not in OUTPUT_TARGETS]
        if bad:
            raise ValueError(f"unknown output targets: {bad} (allowed: {OUTPUT_TARGETS})")
        if len(v) > 4:
            raise ValueError("output targets over budget (>4)")
        return v


class MapSeriesV1(_SeriesModel):
    """map_series.v1 —— 系列地图作者计划 typed 文档。"""

    series_version: Literal["map_series.v1"] = MAP_SERIES_VERSION
    name: str = Field(max_length=120)
    description: str = Field(default="", max_length=500)
    #: 设计规范 pin（必填；WP1 权威。跨库引用 —— 无 FK 约束，解析时点校验）。
    design_spec_id: str = Field(max_length=36)
    #: 可选版本 pin；缺省 = 创建/落版本时的 latest（解析时点记录）。
    design_spec_version: Optional[int] = Field(default=None, ge=1)
    members: List[SeriesMember] = Field(default_factory=list)
    #: 数据来源披露（R1-P2）：合成/测试数据必须置 true —— 冻结计划与
    #: series manifest 恒如实传播（披露字段，不是开关）。
    simulated_data: bool = False
    comparability: ComparabilityContract = Field(default_factory=ComparabilityContract)
    dimension: SeriesDimension = Field(default_factory=SeriesDimension)
    output: SeriesOutput = Field(default_factory=SeriesOutput)

    @field_validator("name")
    @classmethod
    def _v_name(cls, v: str) -> str:
        if not _NAME_PATTERN.fullmatch(v or ""):
            raise ValueError(
                "name must be 1-120 chars (CJK/letter/digit first, no control chars)"
            )
        return v.strip()

    @field_validator("design_spec_id")
    @classmethod
    def _v_spec_id(cls, v: str) -> str:
        v = (v or "").strip()
        if not v or len(v) > 36:
            raise ValueError("design_spec_id is required (1-36 chars)")
        return v

    @field_validator("members")
    @classmethod
    def _v_members(cls, v: List[SeriesMember]) -> List[SeriesMember]:
        if len(v) > _MAX_MEMBERS:
            raise ValueError(f"members over budget (>{_MAX_MEMBERS})")
        seen = set()
        for item in v:
            if item.member_id in seen:
                raise ValueError(f"duplicate member_id: {item.member_id}")
            seen.add(item.member_id)
        return v


# ── 校验入口与 digest ────────────────────────────────────────────────


def validate_map_series_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """raw payload → 校验报告（schema + 防泄漏）。

    返回 ``{ok, series (解析后的 typed dict 或 None), violations, warnings,
    leak_findings}``；violations 非空 = fail-closed（创建/落版本拒绝）。
    防泄漏 walk 复用 ``design_specs.contract._walk_leak_findings``
    （成员 payload 禁止 ref: 游标/凭证/内联 GeoJSON/数据断点 ——
    data_bindings 只放字段名）。
    """
    violations: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    leak: List[Dict[str, str]] = []
    _walk_leak_findings(payload, "", leak)
    for item in leak:
        violations.append({"field": item["field"], "ref": "", "reason": item["code"]})

    series_model = None
    if not violations:
        try:
            series_model = MapSeriesV1.model_validate(payload)
        except ValidationError as exc:
            for err in exc.errors()[:16]:
                # reason 携带 validator 原始消息（"Value error, ..." 前缀保留
                # —— 逐条可定位比 design_specs 的裸 type 码更好排查）。
                reason = str(err.get("msg") or err.get("type") or "invalid")[:160]
                violations.append(
                    {
                        "field": ".".join(str(p) for p in err.get("loc", ())),
                        "ref": "",
                        "reason": f"schema:{reason}",
                    }
                )

    # data_bindings 值的语义第二道防线（pydantic 已拒；这里给逐条定位）。
    if series_model is not None:
        for member in series_model.members:
            for role, field_name in member.data_bindings.items():
                if not _FIELD_NAME_PATTERN.fullmatch(str(field_name)):
                    violations.append(
                        {
                            "field": f"members.{member.member_id}.data_bindings.{role}",
                            "ref": str(field_name)[:60],
                            "reason": DATA_BINDING_FIELD_INVALID,
                        }
                    )

    return {
        "ok": not violations,
        "series": series_model.model_dump() if series_model is not None else None,
        "violations": violations[:_MAX_VIOLATIONS],
        "warnings": warnings[:_MAX_VIOLATIONS],
        "leak_findings": leak[:_MAX_VIOLATIONS],
    }


def structural_digest(payload: Dict[str, Any]) -> str:
    """结构 digest：计划语义内容的 sha256（不含主表元数据/时间戳）。"""
    return "mseries-sha256:" + hashlib.sha256(
        canonical_json(payload).encode("utf-8")
    ).hexdigest()


__all__ = [
    "MAP_SERIES_VERSION",
    "MEMBER_ROLES",
    "DIMENSION_AXES",
    "CLASSIFICATION_METHODS",
    "LOCKABLE_FIELDS",
    "OUTPUT_TARGETS",
    "MapSeriesV1",
    "SeriesMember",
    "ComparabilityContract",
    "SeriesDimension",
    "SeriesOutput",
    "validate_map_series_payload",
    "canonical_json",
    "structural_digest",
]
