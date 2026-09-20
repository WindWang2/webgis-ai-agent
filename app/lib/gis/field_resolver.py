"""Semantic Field Resolver —— 用户短语 → 数据字段（ADR-0204 S4）。

回答"用户说的『每平方公里学校数』到底是哪个字段"：

- ``parse_measure_phrase``：双语（zh/en）纯规则把量词短语解析为
  :class:`FieldQuery`（期望 MeasurementKind / 语义角色 / 主题词 / 分母
  维度 / 时间要求）；未命中不猜（kind/role 留空走兜底）；
- ``resolve_measure_field``：FieldQuery ⊕ DatasetProfile ⊕
  SemanticDatasetProfile（⊕ 可选 DatasetMeasurementProfile / 项目别名）
  → 有界候选打分；**平级多候选或证据不足 → needs_clarification**，
  绝不替用户猜（fail-closed 语义，ADR-0204）；
- 与既有资产的关系：不重造角色推理（复用 SemanticDatasetProfile）、
  不重造量纲判定（复用 DatasetMeasurementProfile）、零扫描（字段与
  证据全部来自画像）。

测量语义区分要求（方向验收）：人口、人口增长率、学校数量、每平方公里
学校数、土地利用类型、变化率 —— 六类短语必须落到不同 kind/分母/时间
要求，并有测试锁定（test_field_resolver_v1）。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, Field

from app.lib.gis.dataset_profile import DatasetProfile, MAX_PROFILE_FIELDS
from app.lib.gis.measurement import (
    CHECK_RATE_MISSING_TEMPORAL,
    MeasurementKind,
    UnitDimension,
    name_kind_hint,
)
from app.lib.gis.semantic_profile import (
    RoleConfidence,
    SemanticDatasetProfile,
    SemanticFieldRole,
)

#: 候选上限与别名上限（有界性）。
MAX_CANDIDATES = 3
MAX_ALIASES = 32
#: 低于此分的候选视为"无证据"（宁可澄清不硬选）。
_MIN_CONFIDENT_SCORE = 2

# ── 查询契约 ───────────────────────────────────────────────────────────────


class FieldQuery(BaseModel):
    """短语解析产物：期望的量纲语义（全部可为空 = 未表达）。"""

    kind: str = ""                 # MeasurementKind.value（"" = 未表达）
    role: str = ""                 # SemanticFieldRole.value（"" = 未表达）
    subject: str = ""              # 主题词（如 "学校"/"土地利用"）
    denominator: str = ""          # 期望分母维度（UnitDimension.value）
    temporal_required: bool = False
    unit_dimension: str = ""       # 期望度量维度（UnitDimension.value）

    def describe(self) -> str:
        parts = [p for p in (self.kind, self.role, self.subject,
                             self.denominator) if p]
        return "/".join(parts) or "unconstrained"


class FieldCandidate(BaseModel):
    """一个候选字段及其证据（有界）。"""

    field: str
    score: int = 0
    role: str = ""                 # 命中的语义角色（第一个交集）
    kind: str = ""                 # 量纲画像中的 kind（如有画像）
    unit: str = ""
    unit_dimension: str = ""
    confidence: str = RoleConfidence.UNKNOWN.value
    evidence: List[str] = Field(default_factory=list)


class FieldResolution(BaseModel):
    """字段解析结论（可序列化、有界）。"""

    query: FieldQuery = Field(default_factory=FieldQuery)
    selected: List[FieldCandidate] = Field(default_factory=list)
    ambiguity: List[str] = Field(default_factory=list)
    needs_clarification: bool = False
    disclosures: List[str] = Field(default_factory=list)
    check_codes: List[str] = Field(default_factory=list)  # 稳定机器码

    @property
    def best(self) -> Optional[FieldCandidate]:
        return self.selected[0] if self.selected else None

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query.describe(),
            "selected": [
                {
                    "field": c.field,
                    "score": c.score,
                    "role": c.role,
                    "kind": c.kind,
                    "unit": c.unit,
                    "confidence": c.confidence,
                    "evidence": list(c.evidence[:4]),
                }
                for c in self.selected[:MAX_CANDIDATES]
            ],
            "ambiguity": list(self.ambiguity[:MAX_CANDIDATES]),
            "needs_clarification": self.needs_clarification,
            "disclosures": list(self.disclosures[:4]),
            "check_codes": list(self.check_codes[:4]),
        }


# ── 短语 → FieldQuery（双语纯规则）─────────────────────────────────────────

# 度量后缀（剥离后余留主题词）。
_TRAILING_CATEGORY_RE = re.compile(r"(类型|类别|种类|categories?|types?|classification)$", re.I)
_TRAILING_COUNT_RE = re.compile(r"(数量|个数|数目|总数|总量|数|counts?|numbers?)$", re.I)
_DENSITY_TAIL_RE = re.compile(r"(密度|densities?|density)$", re.I)
_MEASURE_SUFFIX_RE = re.compile(
    r"(的)?(数量|个数|数目|总数|密度|分布|数|总量|count|number|density)$", re.I
)
_DENSITY_PHRASE_RE = re.compile(
    r"(每平方公里|每平方千米|每万平方米|地均|per\s+square\s+kilometer|"
    r"per\s+km²?|population\s+density|人口密度)", re.I
)
_CAPITA_PHRASE_RE = re.compile(r"(人均|per\s+capita)", re.I)
_RATE_PHRASE_RE = re.compile(
    r"(增长率|变化率|增速|增长\s*率|growth\s+rate|rate\s+of\s+change|change\s+rate)", re.I
)
_SIGNED_PHRASE_RE = re.compile(r"(增减|净变化|增减变化|net\s+change)", re.I)
_POPULATION_PHRASE_RE = re.compile(r"(人口|population|居民|residents?)", re.I)
_AREA_PHRASE_RE = re.compile(r"(面积|area|平方公里|square\s+kilometers?)", re.I)
_CATEGORY_PHRASE_RE = re.compile(
    r"(类型|种类|类别|土地利用|构成|type\s+of|land\s*use|category|classification)", re.I
)
_COUNT_PHRASE_RE = re.compile(r"(数量|个数|数目|多少家|多少个|多少所|count|how\s+many|number\s+of)", re.I)
_TEMPORAL_PHRASE_RE = re.compile(r"(逐年|历年|同比|环比|近年来|over\s+time|yearly|annual|trend)", re.I)


def parse_measure_phrase(phrase: str) -> FieldQuery:
    """量词短语 → FieldQuery（纯函数；未命中留空，绝不猜）。"""
    q = FieldQuery()
    text = str(phrase or "").strip()
    if not text:
        return q

    lowered = text.lower()

    # 1) 率/带符号变化（最高特异性； RATE 必须有时间要求）。
    if _RATE_PHRASE_RE.search(text):
        q.kind = MeasurementKind.RATE.value
        q.temporal_required = True
        if _POPULATION_PHRASE_RE.search(text):
            q.role = SemanticFieldRole.POPULATION_MEASURE.value
            q.subject = "人口"
        elif _AREA_PHRASE_RE.search(text):
            q.role = SemanticFieldRole.AREA_MEASURE.value
        return q
    if _SIGNED_PHRASE_RE.search(text):
        q.kind = MeasurementKind.SIGNED_CHANGE.value
        q.temporal_required = True
        return q

    # 2) 密度（面积/人口分母）。
    if _DENSITY_PHRASE_RE.search(text) or _CAPITA_PHRASE_RE.search(text):
        q.kind = MeasurementKind.DENSITY.value
        if _CAPITA_PHRASE_RE.search(text):
            q.denominator = UnitDimension.POPULATION.value
        else:
            q.denominator = UnitDimension.AREA.value
        q.subject = _extract_subject(text)
        if _POPULATION_PHRASE_RE.search(text) and _DENSITY_PHRASE_RE.search(text):
            # 人口密度本身就是度量（分母是面积）。
            q.subject = "人口"
        return q

    # 3) 类别语义（土地利用类型等）。
    if _CATEGORY_PHRASE_RE.search(text):
        q.kind = MeasurementKind.CATEGORY.value
        q.subject = _extract_subject(text)
        return q

    # 4) 计数（学校数量 / how many schools）。
    if _COUNT_PHRASE_RE.search(text):
        q.kind = MeasurementKind.COUNT.value
        q.role = SemanticFieldRole.COUNT_MEASURE.value
        q.subject = _extract_subject(text)
        return q

    # 5) 主体量词（人口/面积裸词）。
    if _POPULATION_PHRASE_RE.search(text):
        q.kind = MeasurementKind.ABSOLUTE_QUANTITY.value
        q.role = SemanticFieldRole.POPULATION_MEASURE.value
        q.subject = "人口"
        return q
    if _AREA_PHRASE_RE.search(text):
        q.kind = MeasurementKind.ABSOLUTE_QUANTITY.value
        q.role = SemanticFieldRole.AREA_MEASURE.value
        q.subject = "面积"
        q.unit_dimension = UnitDimension.AREA.value
        return q
    if _TEMPORAL_PHRASE_RE.search(text):
        q.temporal_required = True
    return q


def _extract_subject(phrase: str) -> str:
    """剥离度量前/后缀取主题词（'每平方公里学校数' → '学校'）。"""
    text = _DENSITY_PHRASE_RE.sub("", str(phrase or ""))
    text = _CAPITA_PHRASE_RE.sub("", text)
    text = _RATE_PHRASE_RE.sub("", text)
    text = re.sub(r"^(how\s+many|number\s+of|count\s+of|total)\s+", "", text,
                  flags=re.I)
    text = _TRAILING_CATEGORY_RE.sub("", text)
    text = _DENSITY_TAIL_RE.sub("", text)
    text = _TRAILING_COUNT_RE.sub("", text)
    text = re.sub(r"^(的|各|每个|每一|全区|全市|全区县)", "", text)
    text = text.strip(" 的·.,，、")
    return text[:32]


#: 主题词双语同义表（匹配面；bounded，仅高频跨语词对）。
_SUBJECT_SYNONYMS: Dict[str, Tuple[str, ...]] = {
    "人口": ("population", "pop", "resident"),
    "population": ("人口", "居民"),
    "学校": ("school",),
    "school": ("学校",),
    "医院": ("hospital",),
    "hospital": ("医院",),
    "面积": ("area",),
    "area": ("面积",),
    "生产总值": ("gdp",),
    "gdp": ("生产总值", "gdp"),
    "土地利用": ("land_use", "landuse", "land use"),
    "land use": ("土地利用", "land_use"),
}


def _subject_tokens(subject: str) -> Tuple[str, ...]:
    """主题词 → 匹配 token 集（含双语同义；全小写）。"""
    base = subject.strip().lower()
    if not base:
        return ()
    tokens = {base}
    tokens.update(s.lower() for s in _SUBJECT_SYNONYMS.get(base, ()))
    return tuple(t for t in tokens if t)


# ── 打分解析 ───────────────────────────────────────────────────────────────


def resolve_measure_field(
    phrase: str,
    profile: DatasetProfile,
    semantic_profile: Optional[SemanticDatasetProfile] = None,
    *,
    measurement_profile: Optional[Any] = None,
    project_aliases: Optional[Dict[str, str]] = None,
) -> FieldResolution:
    """短语 ⊕ 画像 → 候选字段（确定性打分；歧义 fail-closed）。

    ``project_aliases``: 别名/项目知识 → 字段名（有界 ≤MAX_ALIASES，
    调用方从 project knowledge 提取；本模块不做知识检索）。
    ``measurement_profile``: DatasetMeasurementProfile 或其 dict（可选；
    缺席时 kind 匹配退化为 role 匹配 —— 不虚构）。
    """
    query = parse_measure_phrase(phrase)
    resolution = FieldResolution(query=query)
    lowered = str(phrase or "").lower()
    aliases = {
        str(k)[:64]: str(v)[:128]
        for k, v in list((project_aliases or {}).items())[:MAX_ALIASES]
    }

    # 画像量纲视图（可选；延迟导入避免模块加载环）。
    kind_by_field: Dict[str, str] = {}
    unit_by_field: Dict[str, str] = {}
    dimension_by_field: Dict[str, str] = {}
    mp = measurement_profile
    if mp is not None:
        from app.lib.gis.measurement import DatasetMeasurementProfile

        if isinstance(mp, dict):
            try:
                mp = DatasetMeasurementProfile.from_dict(mp)
            except ValueError:
                mp = None
        if mp is not None:
            for f in (mp.fields or [])[:MAX_PROFILE_FIELDS]:
                if f.measurement_kind:
                    kind_by_field[f.field] = f.measurement_kind
                if f.unit:
                    unit_by_field[f.field] = f.unit
                if f.unit_dimension:
                    dimension_by_field[f.field] = f.unit_dimension

    roles_by_field: Dict[str, List[str]] = {}
    conf_by_field: Dict[str, str] = {}
    if semantic_profile is not None:
        for a in (semantic_profile.field_roles or [])[:MAX_PROFILE_FIELDS]:
            roles_by_field[str(a.field)] = list(a.roles or [])
            conf_by_field[str(a.field)] = str(a.confidence)

    dtypes = {str(k): str(v) for k, v in list((profile.fields or {}).items())[:MAX_PROFILE_FIELDS]}
    numeric_dtypes = {"number", "integer", "int", "float", "double",
                      "float64", "float32", "int64", "int32"}

    has_temporal = any(
        SemanticFieldRole.TEMPORAL_DIMENSION.value in roles
        for roles in roles_by_field.values()
    )

    subject_tokens = _subject_tokens(query.subject)
    scored: List[FieldCandidate] = []
    for field in dtypes:
        roles = roles_by_field.get(field, [])
        fkind = kind_by_field.get(field, "")
        score = 0
        evidence: List[str] = []
        hit_role = ""

        # alias 命中（最高权重：项目知识/用户口径）。
        alias_hit = ""
        for alias, target in aliases.items():
            if target == field and (alias.lower() in lowered or not subject_tokens):
                alias_hit = alias
                break
        if alias_hit:
            score += 4
            evidence.append(f"alias:{alias_hit[:24]}")

        # kind 匹配：画像 kind 强证据 > 名称 kind 弱证据；失配一律惩罚
        # （率字段不是绝对量、密度字段不是计数 —— 量纲错配是生产事故源）。
        if query.kind:
            if fkind == query.kind:
                score += 4
                evidence.append("kind_match")
            else:
                hint = name_kind_hint(field)
                if hint == query.kind:
                    score += 3
                    evidence.append("name_kind_match")
                elif fkind or hint:
                    score -= 2
                    evidence.append("kind_mismatch")
        if query.role and query.role in roles:
            score += 3
            hit_role = query.role
            evidence.append("role_match")

        if subject_tokens:
            fname = field.lower()
            if any(tok in fname for tok in subject_tokens):
                score += 2
                evidence.append("subject_in_field")

        # 类别查询不打数值字段（dtype 守卫）；量查询不打纯文本字段。
        is_numeric = dtypes[field] in numeric_dtypes
        if query.kind == MeasurementKind.CATEGORY.value and is_numeric and not roles:
            score -= 2
        if query.kind in (
            MeasurementKind.COUNT.value,
            MeasurementKind.ABSOLUTE_QUANTITY.value,
            MeasurementKind.RATIO.value,
            MeasurementKind.PERCENTAGE.value,
            MeasurementKind.RATE.value,
            MeasurementKind.DENSITY.value,
        ) and not is_numeric and dtypes[field] not in ("unknown", ""):
            score -= 3
            evidence.append("dtype_mismatch")

        # 密度查询：字段画像 density 维度加分（画像在场时）。
        if query.kind == MeasurementKind.DENSITY.value and fkind == MeasurementKind.DENSITY.value:
            score += 2
            evidence.append("density_profile_match")

        if score <= 0:
            continue
        scored.append(FieldCandidate(
            field=field,
            score=score,
            role=hit_role or (roles[0] if roles else ""),
            kind=fkind,
            unit=unit_by_field.get(field, ""),
            unit_dimension=dimension_by_field.get(field, ""),
            confidence=conf_by_field.get(field, RoleConfidence.UNKNOWN.value),
            evidence=evidence[:6],
        ))

    scored.sort(key=lambda c: (-c.score, c.field))
    resolution.selected = scored[:MAX_CANDIDATES]

    # ── 歧义与澄清（fail-closed）────────────────────────────────────
    if not scored:
        resolution.needs_clarification = True
        resolution.disclosures.append(
            f"未在数据画像中找到与「{phrase}」匹配的字段（查询约束：{query.describe()}）"
        )
    else:
        top = scored[0]
        tied = [c.field for c in scored if c.score == top.score]
        if len(tied) > 1:
            resolution.needs_clarification = True
            resolution.ambiguity = tied[:MAX_CANDIDATES]
            resolution.disclosures.append(
                f"多个字段同分（{'、'.join(tied[:3])}）——需用户澄清而非猜测"
            )
        elif top.score < _MIN_CONFIDENT_SCORE:
            resolution.needs_clarification = True
            resolution.disclosures.append(
                "候选证据不足（仅弱名称重叠）——需用户澄清"
            )

    # ── 诚实披露（时间覆盖/分母缺口）────────────────────────────────
    if query.temporal_required and not has_temporal:
        resolution.check_codes.append(CHECK_RATE_MISSING_TEMPORAL)
        resolution.disclosures.append(
            "查询要求率/变化语义，但数据画像无时间字段证据——"
            "无法证实跨期归属，需补充时序数据或改用单期快照口径。"
        )
    if (
        query.kind == MeasurementKind.DENSITY.value
        and query.denominator == UnitDimension.AREA.value
        and semantic_profile is not None
        and not semantic_profile.has_role(SemanticFieldRole.AREA_MEASURE)
    ):
        resolution.disclosures.append(
            "密度语义需要面积分母，画像未见面积字段——密度只能以聚合单元"
            "计数近似，公平性/强度结论需补充分母数据。"
        )
    return resolution
