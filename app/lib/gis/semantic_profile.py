"""Semantic DatasetProfile — field-level semantic roles (ADR-0092 C1/C2).

升级自结构化 DatasetProfile：在既有派生画像之上叠加**字段语义角色**。
原则（不可妥协）：

- **证据分级**：``rule_derived``（字段名 + dtype + 有界值样本互相印证）>
  ``metadata_derived``（名称/已知 schema 推断）> ``user_declared``；仅凭
  字段名**永远到不了** rule_derived —— 名字会撒谎（"count" 可能是布尔）。
- **不确定即 unknown**：证据不足时不输出角色，绝不虚构；
- 零扫描构造路径保留：值样本由调用方（工具层，有界 ≤200 行）供数，
  本模块绝不读 FeatureCollection / raster；
- 这是 DatasetProfile 的派生投影，不是第二数据真相。
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from app.lib.gis.dataset_profile import DatasetProfile, MAX_PROFILE_FIELDS

#: 有界值样本（每字段最多取多少个非空值做证据）。
MAX_VALUE_SAMPLES = 200

#: 角色样本中判"低基数类别"的去重阈值（distinct/sample ≤ 此值 → category）。
_CATEGORY_CARDINALITY_RATIO = 0.5


class SemanticFieldRole(str, Enum):
    FEATURE_ID = "feature_id"
    CATEGORY = "category"
    ADMIN_DIMENSION = "admin_dimension"
    TEMPORAL_DIMENSION = "temporal_dimension"
    CONTINUOUS_MEASURE = "continuous_measure"
    COUNT_MEASURE = "count_measure"
    RATIO_MEASURE = "ratio_measure"
    POPULATION_MEASURE = "population_measure"
    AREA_MEASURE = "area_measure"
    DISTANCE_MEASURE = "distance_measure"
    NORMALIZATION_DENOMINATOR = "normalization_denominator"
    LABEL = "label"


class EvidenceSource(str, Enum):
    FIELD_NAME = "field_name"
    DTYPE = "dtype"
    VALUE_SAMPLE = "value_sample"
    UNITS = "units"
    KNOWN_SCHEMA = "known_schema"
    USER_DECLARATION = "user_declaration"


class RoleConfidence(str, Enum):
    RULE_DERIVED = "rule_derived"
    METADATA_DERIVED = "metadata_derived"
    USER_DECLARED = "user_declared"
    UNKNOWN = "unknown"


_NAME_RULES: Dict[SemanticFieldRole, "re.Pattern"] = {
    SemanticFieldRole.ADMIN_DIMENSION: re.compile(
        r"(省|市|区|县|旗|镇|乡|街道|村|adcode|admin|district|province|city|county|town)", re.I
    ),
    SemanticFieldRole.TEMPORAL_DIMENSION: re.compile(
        r"(日期|时间|年份|月份|date|time|year|month|day|dt$)", re.I
    ),
    SemanticFieldRole.FEATURE_ID: re.compile(
        r"(^id$|_id$|^fid$|^gid$|objectid|uuid|^code$|编码|编号)", re.I
    ),
    SemanticFieldRole.COUNT_MEASURE: re.compile(
        r"(数量|个数|个数|计数|人数|家数|count|num_?|_num$|n_|total_)", re.I
    ),
    SemanticFieldRole.RATIO_MEASURE: re.compile(
        r"(率|比例|占比|ratio|pct|percent|percentage|share|fraction)", re.I
    ),
    SemanticFieldRole.POPULATION_MEASURE: re.compile(
        r"(人口|population|pop_|_pop$|resident)", re.I
    ),
    SemanticFieldRole.AREA_MEASURE: re.compile(
        r"(面积|area|km2|km²|平方公里|sq_?km|hectare|公顷)", re.I
    ),
    SemanticFieldRole.DISTANCE_MEASURE: re.compile(
        r"(距离|长度|distance|length|dist_)", re.I
    ),
    SemanticFieldRole.CONTINUOUS_MEASURE: re.compile(
        r"(指数|得分|评分|温度|价格|score|index|price|temp|value$|amount)", re.I
    ),
    SemanticFieldRole.LABEL: re.compile(r"(名称|名字|标题|^name$|label|title)", re.I),
    SemanticFieldRole.CATEGORY: re.compile(
        r"(类别|类型|分类|种类|category|type|kind|class$|等级|级别|grade|level)", re.I
    ),
}


class FieldRoleAssignment(BaseModel):
    """一个字段的语义角色判定（可多角色，如 population + denominator）。"""

    field: str
    roles: List[str] = Field(default_factory=list)
    confidence: RoleConfidence = RoleConfidence.UNKNOWN
    evidence: List[str] = Field(default_factory=list)  # bounded source names

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field,
            "roles": list(self.roles[:6]),
            "confidence": self.confidence.value,
            "evidence": list(self.evidence[:6]),
        }


class SemanticDatasetProfile(BaseModel):
    """语义画像投影（DatasetProfile 的衍生层，有界）。"""

    field_roles: List[FieldRoleAssignment] = Field(default_factory=list)
    # 角色 → 字段索引（第一个命名字段；normalized denominator 便捷读面）。
    role_index: Dict[str, str] = Field(default_factory=dict)

    def fields_with_role(self, role: SemanticFieldRole) -> List[str]:
        return [fr.field for fr in self.field_roles if role.value in fr.roles]

    def has_role(self, role: SemanticFieldRole) -> bool:
        return role.value in self.role_index

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field_roles": [fr.to_dict() for fr in self.field_roles[:MAX_PROFILE_FIELDS]],
            "role_index": dict(self.role_index),
        }


def _sample_values(features: Sequence[Any], field: str) -> List[Any]:
    vals: List[Any] = []
    for f in features[:MAX_VALUE_SAMPLES]:
        if not isinstance(f, dict):
            continue
        v = (f.get("properties") or {}).get(field)
        if v is not None and v != "":
            vals.append(v)
        if len(vals) >= MAX_VALUE_SAMPLES:
            break
    return vals


def _numeric_or_none(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _all_integers(vals: List[Any]) -> bool:
    return bool(vals) and all(
        isinstance(v, int) and not isinstance(v, bool) for v in vals
    )


def _all_in_unit_interval(vals: List[Any]) -> bool:
    nums = [_numeric_or_none(v) for v in vals]
    nums = [n for n in nums if n is not None]
    return bool(nums) and all(0.0 <= n <= 1.0 for n in nums)


def _all_date_like(vals: List[Any]) -> bool:
    date_re = re.compile(r"^\d{4}[-/年]?\d{1,2}([-/月]\d{1,2})?日?$")
    strs = [v for v in vals if isinstance(v, str)]
    return bool(strs) and all(date_re.match(s.strip()) for s in strs)


def _name_evidence(field: str, dtype: str) -> List[Tuple[SemanticFieldRole, List[EvidenceSource]]]:
    """名称 + dtype 推断（最多 metadata_derived —— 名称单独永不高置信）。"""
    out: List[Tuple[SemanticFieldRole, List[EvidenceSource]]] = []
    for role, pattern in _NAME_RULES.items():
        if pattern.search(field or ""):
            sources = [EvidenceSource.FIELD_NAME]
            if dtype:
                sources.append(EvidenceSource.DTYPE)
            out.append((role, sources))
    return out


def _dtype_allows(dtype: str, role: SemanticFieldRole) -> bool:
    numeric_roles = {
        SemanticFieldRole.CONTINUOUS_MEASURE,
        SemanticFieldRole.COUNT_MEASURE,
        SemanticFieldRole.RATIO_MEASURE,
        SemanticFieldRole.POPULATION_MEASURE,
        SemanticFieldRole.AREA_MEASURE,
        SemanticFieldRole.DISTANCE_MEASURE,
    }
    if role in numeric_roles:
        return dtype in ("number", "integer", "float", "int", "double")
    if role in (SemanticFieldRole.ADMIN_DIMENSION, SemanticFieldRole.CATEGORY,
                SemanticFieldRole.LABEL, SemanticFieldRole.FEATURE_ID,
                SemanticFieldRole.TEMPORAL_DIMENSION):
        return dtype in ("string", "boolean", "integer", "int", "number", "unknown", "")
    return True


def derive_semantic_profile(
    profile: DatasetProfile,
    *,
    value_samples: Optional[Dict[str, List[Any]]] = None,
    user_roles: Optional[Dict[str, str]] = None,
) -> SemanticDatasetProfile:
    """DatasetProfile + 有界值样本 (+ 用户声明) → 语义画像（纯函数）。

    ``value_samples``: field → 非空值列表（工具层供数，≤MAX_VALUE_SAMPLES）。
    ``user_roles``: field → 角色名字符串（用户声明，最高优先）。
    """
    samples = value_samples or {}
    user = user_roles or {}
    assignments: List[FieldRoleAssignment] = []
    role_index: Dict[str, str] = {}

    fields = list((profile.fields or {}).items())[:MAX_PROFILE_FIELDS]
    for field, dtype in fields:
        vals = samples.get(field) or []
        evidence_notes: List[str] = []
        best_conf = RoleConfidence.UNKNOWN
        roles: List[SemanticFieldRole] = []

        def _promote(conf: RoleConfidence) -> None:
            nonlocal best_conf
            order = [
                RoleConfidence.UNKNOWN,
                RoleConfidence.METADATA_DERIVED,
                RoleConfidence.RULE_DERIVED,
                RoleConfidence.USER_DECLARED,
            ]
            if order.index(conf) > order.index(best_conf):
                best_conf = conf

        # 0) 用户声明最高优先。
        if field in user:
            try:
                declared = SemanticFieldRole(user[field])
            except ValueError:
                declared = None
            if declared is not None:
                roles = [declared]
                evidence_notes.append(EvidenceSource.USER_DECLARATION.value)
                best_conf = RoleConfidence.USER_DECLARED
        # 1) 名称 + dtype 推断（metadata 级）。
        if best_conf is RoleConfidence.UNKNOWN:
            for role, sources in _name_evidence(field, str(dtype or "")):
                if not _dtype_allows(str(dtype or ""), role):
                    continue
                if role not in roles:
                    roles.append(role)
                    evidence_notes.extend(s.value for s in sources)
                    _promote(RoleConfidence.METADATA_DERIVED)
        # 2) 值样本印证（rule 级 —— 需 dtype 协同，不只凭名字）。
        if vals:
            distinct_ratio = len(set(map(str, vals))) / max(1, len(vals))
            # category：字符串 + 低基数（样本印证）。
            if (
                str(dtype) in ("string", "boolean", "")
                and distinct_ratio <= _CATEGORY_CARDINALITY_RATIO
                and SemanticFieldRole.CATEGORY not in roles
            ):
                roles.append(SemanticFieldRole.CATEGORY)
                evidence_notes.append(EvidenceSource.VALUE_SAMPLE.value)
                _promote(RoleConfidence.RULE_DERIVED)
            # temporal：全样本日期形态。
            if _all_date_like(vals) and SemanticFieldRole.TEMPORAL_DIMENSION not in roles:
                roles.append(SemanticFieldRole.TEMPORAL_DIMENSION)
                evidence_notes.append(EvidenceSource.VALUE_SAMPLE.value)
                _promote(RoleConfidence.RULE_DERIVED)
            # ratio：样本落在 [0,1]。
            if _all_in_unit_interval(vals) and SemanticFieldRole.RATIO_MEASURE not in roles:
                roles.append(SemanticFieldRole.RATIO_MEASURE)
                evidence_notes.append(EvidenceSource.VALUE_SAMPLE.value)
                _promote(RoleConfidence.RULE_DERIVED)
            # count：整数 dtype + 非负整数样本。
            if (
                str(dtype) in ("integer", "int")
                and _all_integers(vals)
                and all(_numeric_or_none(v) is not None and _numeric_or_none(v) >= 0 for v in vals)
            ):
                if SemanticFieldRole.COUNT_MEASURE in roles:
                    evidence_notes.append(EvidenceSource.VALUE_SAMPLE.value)
                    _promote(RoleConfidence.RULE_DERIVED)
                elif SemanticFieldRole.CATEGORY not in roles and SemanticFieldRole.FEATURE_ID not in roles:
                    roles.append(SemanticFieldRole.COUNT_MEASURE)
                    evidence_notes.append(EvidenceSource.VALUE_SAMPLE.value)
                    _promote(RoleConfidence.RULE_DERIVED)
        # 3) 派生角色：population/area ⇒ normalization_denominator（C1 便捷
        #    角色，跟随其来源字段的证据等级）。
        if any(r in roles for r in (
            SemanticFieldRole.POPULATION_MEASURE, SemanticFieldRole.AREA_MEASURE,
        )) and SemanticFieldRole.NORMALIZATION_DENOMINATOR not in roles:
            roles.append(SemanticFieldRole.NORMALIZATION_DENOMINATOR)

        if not roles or best_conf is RoleConfidence.UNKNOWN:
            assignments.append(FieldRoleAssignment(
                field=field, roles=[], confidence=RoleConfidence.UNKNOWN,
                evidence=[EvidenceSource.FIELD_NAME.value],
            ))
            continue
        for r in roles:
            role_index.setdefault(r.value, field)
        assignments.append(FieldRoleAssignment(
            field=field,
            roles=[r.value for r in roles],
            confidence=best_conf,
            evidence=list(dict.fromkeys(evidence_notes))[:6],
        ))

    return SemanticDatasetProfile(field_roles=assignments, role_index=role_index)
