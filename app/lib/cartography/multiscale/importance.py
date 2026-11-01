"""语义重要性规则引擎（M2）—— 有字段证据才取舍，绝不伪造主干。

输入证据三源（DECISIONS D4）：
1. ``field_schema``：ref descriptor 的字段→类型（string/number/boolean）；
2. ``value_counts``：候选字段的**真实值分布**（服务端聚合通道产物，
   如 ``feature_aggregate.aggregate_features`` —— 不接受采样臆造基数）；
3. 有序词表：道路等级（中文行政等级 + OSM highway 双语别名）/
   行政级别（数值 0..5，升序 = 级别从高到低）。

裁决规则（确定性纯函数）：
- 候选字段：名匹配重要性同义词表 ∧ 类型 string/number ∧ 2..=32 个非空
  取值 ∧ 值分布在场；
- 值域**全匹配**单一词表（词表外值 ⇒ 拒 —— 宁不取舍，不猜半序）；
- 裸数值等级字段（无 admin_level 语义）**恒拒**（1 高还是 5 高无证据，
  伪造排序 = 伪造主干道路）；
- 用户显式 override 赢（user-wins），但字段必须在数据中真实存在且
  序值与值分布有交集（否则结构化拒绝）；
- 多候选：同义词特异性（长者优先）→ 取值覆盖面 → 字段字典序，
  全程可解释（每个被拒候选带理由码）。

带级取舍（``kept_values_for_band``）：词表序前 K 个（DEFAULT_KEEP_RANKS
按 tier），mandatory 值恒并入 —— 排序依据永远可追溯到上方的模型证据。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field

#: 候选取值数上下界（<2 无排序意义；>32 视为自由文本不是等级词表）。
MIN_DISTINCT_VALUES = 2
MAX_DISTINCT_VALUES = 32

#: 候选字段名同义词（特异性降序 —— 更长/更具体者恒优先）。
#: 元素：(规范化模式, 特异性分)。匹配按小写 + 去空白/连字符。
IMPORTANCE_FIELD_SYNONYMS: Tuple[Tuple[str, int], ...] = (
    ("道路等级", 100),
    ("roadclass", 100),
    ("road_class", 100),
    ("highway", 95),
    ("行政级别", 95),
    ("adminlevel", 95),
    ("admin_level", 95),
    ("道路级别", 90),
    ("行政等级", 90),
    ("level", 60),
    ("grade", 60),
    ("等级", 55),
    ("级别", 55),
    ("class", 40),
    ("类别", 35),
    ("类型", 35),
    ("category", 35),
)

#: 道路等级有序词表（rank 0 最重要）。中文公路行政等级 + 城市道路分类
#: + OSM highway 别名映射到同一 canonical 序。序为制图取舍惯例：
#: 跨城干线（高速/国道/省道）先行，城市道路次之，末梢道路最后。
_ROAD_CLASS_ORDER: Tuple[Tuple[str, ...], ...] = (
    ("高速公路", "高速", "motorway", "expressway"),
    ("国道", "trunk", "national"),
    ("省道", "primary", "provincial"),
    ("快速路", "城市快速路", "urban_expressway"),
    ("主干路", "主干道", "arterial"),
    ("县道", "secondary", "county"),
    ("次干路", "次干道", "collector"),
    ("乡道", "tertiary", "township"),
    ("支路", "unclassified", "residential", "branch", "street"),
    ("村道", "living_street", "village"),
    ("其他道路", "service", "path", "footway", "track", "other"),
)

#: 行政级别词表：数值 0..5 升序 = 级别从高到低（OSM admin_level 惯例的
#: 稠密化投影；字符串别名并列供人读）。
_ADMIN_LEVEL_MAX = 5

_ADMIN_ALIASES: Dict[int, Tuple[str, ...]] = {
    0: ("国家", "country", "national"),
    1: ("省级", "省", "province", "state"),
    2: ("地市级", "市", "地级", "city", "prefecture"),
    3: ("县区级", "县", "区", "县级", "county", "district"),
    4: ("乡镇级", "乡镇", "街道", "town", "township"),
    5: ("村级", "村", "社区", "village"),
}


def _normalize_token(v: Any) -> str:
    s = str(v).strip().lower()
    return "".join(ch for ch in s if not ch.isspace())


def _normalize_field(name: str) -> str:
    return "".join(ch for ch in str(name).strip().lower() if not ch.isspace())


def _match_field_synonym(field: str) -> Optional[Tuple[str, int]]:
    """字段名 → (同义词, 特异性分)；不匹配 → None。"""
    nf = _normalize_field(field)
    best: Optional[Tuple[str, int]] = None
    for pattern, specificity in IMPORTANCE_FIELD_SYNONYMS:
        np = _normalize_field(pattern)
        if nf == np or nf.endswith(np) or nf.startswith(np) or np in nf:
            if best is None or specificity > best[1]:
                best = (pattern, specificity)
    return best


def _road_rank(value: Any) -> Optional[int]:
    token = _normalize_token(value)
    for rank, aliases in enumerate(_ROAD_CLASS_ORDER):
        if token in {_normalize_token(a) for a in aliases}:
            return rank
    return None


def _admin_rank(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        iv = int(value)
        return iv if 0 <= iv <= _ADMIN_LEVEL_MAX else None
    token = _normalize_token(value)
    for rank, aliases in _ADMIN_ALIASES.items():
        if token in {_normalize_token(a) for a in aliases}:
            return rank
    return None


class ImportanceModel(BaseModel):
    """一次可信重要性裁决（可序列化、确定性）。"""

    model_config = ConfigDict(extra="allow")

    field: str
    value_order: List[Any] = Field(default_factory=list)
    matched_vocabulary: str = ""
    matched_field_synonym: str = ""
    evidence: str = "field_schema+aggregate+vocabulary"
    distinct_values: int = 0
    non_null_coverage: float = 0.0
    reason_codes: List[str] = Field(default_factory=list)


class CandidateAssessment(BaseModel):
    """单候选字段的裁决痕迹（可解释性：为什么选/拒）。"""

    model_config = ConfigDict(extra="allow")

    field: str
    accepted: bool = False
    matched_vocabulary: str = ""
    reason_codes: List[str] = Field(default_factory=list)


class ImportanceDerivation(BaseModel):
    """derive_importance_model 的完整输出（模型 + 全候选痕迹 + 理由）。"""

    model_config = ConfigDict(extra="allow")

    selected: Optional[ImportanceModel] = None
    candidates: List[CandidateAssessment] = Field(default_factory=list)
    reason_codes: List[str] = Field(default_factory=list)


def derive_importance_model(
    field_schema: Dict[str, Any],
    value_counts: Dict[str, Dict[Any, int]],
    feature_count: int,
    *,
    user_field: Optional[str] = None,
    user_value_order: Optional[Sequence[Any]] = None,
) -> ImportanceDerivation:
    """字段证据 + 值分布 + 词表 → 可信重要性模型（或 None + 理由）。"""
    derivation = ImportanceDerivation()
    total = max(0, int(feature_count))

    # ── 用户 override（user-wins）：先于一切计算序，但字段必须真实 ──
    if user_field is not None:
        dist = value_counts.get(user_field)
        if not dist:
            derivation.reason_codes.append("user_field_absent:no_distribution")
            return derivation
        order = list(user_value_order or [])
        present = [v for v in order if v in dist]
        if not present:
            derivation.reason_codes.append("user_field_absent:values_not_in_data")
            return derivation
        non_null = sum(dist.values())
        derivation.selected = ImportanceModel(
            field=user_field,
            value_order=present,
            matched_vocabulary="user_override",
            matched_field_synonym="user",
            evidence="user_override",
            distinct_values=len(dist),
            non_null_coverage=(non_null / total) if total else 0.0,
            reason_codes=["user_override_honored"],
        )
        return derivation

    # ── 计算序：候选字段逐个裁决 ──
    scored: List[Tuple[int, int, str, ImportanceModel]] = []
    for field, fspec in (field_schema or {}).items():
        ftype = fspec.get("type") if isinstance(fspec, dict) else None
        assessment = CandidateAssessment(field=field)
        derivation.candidates.append(assessment)
        if ftype not in ("string", "number"):
            assessment.reason_codes.append("type_not_ordinal_carrier")
            continue
        dist = (value_counts or {}).get(field)
        if not dist:
            assessment.reason_codes.append("no_distribution_evidence")
            continue
        values = [v for v in dist.keys() if v is not None]
        if len(values) < MIN_DISTINCT_VALUES:
            assessment.reason_codes.append("insufficient_distinct_values")
            continue
        if len(values) > MAX_DISTINCT_VALUES:
            assessment.reason_codes.append("too_many_distinct_values:free_text")
            continue
        synonym = _match_field_synonym(field)
        if synonym is None:
            assessment.reason_codes.append("field_name_not_importance_synonym")
            continue

        matched_vocab = ""
        ordered: Optional[List[Any]] = None
        if _normalize_field(field).endswith(("adminlevel", "admin_level")) or (
            _normalize_field(synonym[0]) in ("admin_level", "adminlevel", "行政级别", "行政等级")
        ):
            # 行政级别：数值升序（0 国家 … 5 村级）或全别名匹配
            if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
                if all(_admin_rank(v) is not None for v in values):
                    matched_vocab = "admin_level"
                    ordered = sorted(values, key=lambda v: int(v))
            if matched_vocab == "":
                ranks = {v: _admin_rank(v) for v in values}
                if all(r is not None for r in ranks.values()):
                    matched_vocab = "admin_level"
                    ordered = sorted(values, key=lambda v: ranks[v])
        if matched_vocab == "" and ftype in ("string", "number"):
            ranks = {v: _road_rank(v) for v in values}
            if all(r is not None for r in ranks.values()):
                matched_vocab = "road_class"
                ordered = sorted(values, key=lambda v: (ranks[v], str(v)))
        if matched_vocab == "":
            if _looks_like_road_field(field):
                assessment.reason_codes.append("values_outside_vocabulary")
            else:
                assessment.reason_codes.append("no_ordered_vocabulary")
            continue

        non_null = sum(dist.values())
        coverage = non_null / total if total else 0.0
        model = ImportanceModel(
            field=field,
            value_order=ordered or [],
            matched_vocabulary=matched_vocab,
            matched_field_synonym=synonym[0],
            distinct_values=len(values),
            non_null_coverage=round(coverage, 6),
            reason_codes=[f"vocabulary:{matched_vocab}", f"synonym:{synonym[0]}"],
        )
        assessment.accepted = True
        assessment.matched_vocabulary = matched_vocab
        scored.append((synonym[1], len(values), field, model))

    if not scored:
        derivation.reason_codes.append("no_trusted_importance_field")
        # 拒因聚合到顶层（机器可读的单一出口；候选级痕迹仍在 candidates）。
        seen: List[str] = []
        for c in derivation.candidates:
            for r in c.reason_codes:
                key = r.split(":")[0]
                if key not in seen:
                    seen.append(key)
                    derivation.reason_codes.append(r)
        return derivation

    # 确定性裁决：特异性 → 取值覆盖面 → 字段字典序。
    scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
    derivation.selected = scored[0][3]
    derivation.reason_codes.append(
        f"selected:{derivation.selected.field}:specificity={scored[0][0]}")
    return derivation


def _looks_like_road_field(field: str) -> bool:
    nf = _normalize_field(field)
    return any(
        np in nf or nf.endswith(np) or nf.startswith(np)
        for np, _ in IMPORTANCE_FIELD_SYNONYMS
        if np in ("道路等级", "roadclass", "road_class", "highway", "道路级别")
    )


#: 缺省带级保留秩（词表序前 K；street = 全保留哨兵 999）。
#: 惯例：粗尺度保跨城干线（world 3 = 高速/国道/省道），city 带纳入县乡
#: 干线，street 带全量。可被用户逐带覆盖（apply 工具入参）。
DEFAULT_KEEP_RANKS: Dict[str, int] = {
    "world": 3,
    "province": 4,
    "city": 6,
    "street": 999,
}

_STREET_SENTINEL = 999


def kept_values_for_band(
    model: ImportanceModel,
    tier: str,
    keep_ranks: Optional[Dict[str, int]] = None,
    *,
    mandatory_values: Optional[Sequence[Any]] = None,
) -> List[Any]:
    """重要性模型 + tier → 该带保留取值（词表序前 K + mandatory 并集）。

    tier 必须在 SCALE_TIERS 带名词表内（fail-closed —— 未知带是调用方
    契约错误，不静默当 street）。
    """
    from app.lib.cartography.scale_rules import SCALE_TIERS

    tiers = tuple(t.name for t in SCALE_TIERS)
    if tier not in tiers:
        raise ValueError(f"unknown scale tier: {tier!r}")
    ranks = keep_ranks or DEFAULT_KEEP_RANKS
    k = int(ranks.get(tier, DEFAULT_KEEP_RANKS["street"]))
    kept = list(model.value_order[:k])
    if mandatory_values:
        for v in mandatory_values:
            if v not in kept and v in model.value_order:
                kept.append(v)
            elif v not in kept:
                # mandatory 值不在词表序内（如用户点名具体等级别名）：
                # 保留但记录在尾部 —— user-wins 优先于词表纯净度。
                kept.append(v)
    return kept


__all__ = [
    "MIN_DISTINCT_VALUES",
    "MAX_DISTINCT_VALUES",
    "IMPORTANCE_FIELD_SYNONYMS",
    "DEFAULT_KEEP_RANKS",
    "ImportanceModel",
    "CandidateAssessment",
    "ImportanceDerivation",
    "derive_importance_model",
    "kept_values_for_band",
]
