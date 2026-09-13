"""标注计划契约（ac-05，ADR-0154）—— 标注字段自动挑选 + 标注策略自动编排.

职责边界（与既有制图模块的关系）：

- **不替代** 03 线的符号化决策（``symbology``/``visualization_plan``），
  也不替代 06 线的符号律（字号绝对基准归 06 线，本模块只产出
  ``size_ratio`` 比例系数）；
- **不渲染**：``choose_label_field`` / ``plan_label_strategy`` 是纯函数，
  无 IO、无随机、无时钟 —— 同输入两次求解 ``model_dump()`` 完全相等，
  golden 测试可直接锁定输出；
- **消费方**：``label_layer`` 组件（gis_harness 组件目录，ADR-0154 §P6）
  把本模块的决策嵌入 MapSpec ``layer.label``；前端
  ``frontend/lib/mapspec-runtime/label-layout.ts`` 按 ``mode / top_n /
  priority_field / zoom_bands / size_ratio`` 做碰撞避让与抽稀；
  ``semantic_checks._check_label_collision`` 在 spec 声明了策略时按策略
  修正注记盒估计（``carto.label.collision_est`` 告警率随之下降）。

字段挑选特征（P1）：name-like 词表（exact > 子串，多语言）→ 语义类型
排除（主键/编码/时间戳/几何/长文本）→ 字段基数比（唯一值/总数）→
长度分布 → 空值率。同分 tie-break 按 §0.4 固定次序：
``exact 词表命中 > title/label 族 > 语义类型 > 长度分布``，再按字段名
字典序（保证全序、可复现）。

无合格字段时 **field=None + advisory**（禁止用 ID 当标注）——
``sensors`` 数据集（tests/fixtures/labeling_datasets.py）锁定该契约。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

# ── 词表（多语言 name-like；exact 命中权重高于子串）──────────────────────
#: 精确命中词表（字段名归一化后整名匹配；大小写不敏感）。
NAME_EXACT_VOCAB: Tuple[str, ...] = (
    "name", "title", "label", "名称", "名字", "标题", "标注",
    "nom", "nombre",
)
#: 子串命中词表（字段名含该子词即得分，按子词长度加权 —— "站点名称"
#: 命中 "名称"，"线路名" 命中 "名"；类型/类别为弱语义标注词，靠基数
#: 惩罚压制）。
NAME_SUBSTR_VOCAB: Tuple[str, ...] = (
    "name", "title", "label", "名称", "名字", "标题", "标注",
    "名", "类型", "类别",
)
#: 重要度字段词表（P2 priority_field 代理排序用，按次序优先）。
PRIORITY_FIELD_VOCAB: Tuple[str, ...] = (
    "population", "pop_est", "pop", "人口", "gdp", "产值", "value", "值",
    "面积", "area", "stars", "magnitude", "scalerank", "seat_count", "ridership",
    "daily_ridership", "fleet_count", "aqi", "price",
)
#: 语义类型排除 —— 主键/编码字段名词表（正则，大小写不敏感）。
_ID_NAME_RE = re.compile(
    r"(^|_)(id|fid|oid|objectid|uuid|guid|code|编码|编号|代码|no|number|num)$",
    re.IGNORECASE,
)
#: 时间戳字段名词表。
_TIME_NAME_RE = re.compile(
    r"(时间|日期|_at$|_date$|_time$|^date|^time|timestamp|updated|created|last_seen)",
    re.IGNORECASE,
)
#: 几何字段名词表。
_GEOM_NAME_RE = re.compile(r"(geometry|geom|wkt|coordinates|the_geom|shape)", re.IGNORECASE)
#: 纯数值样本（整型/浮点度量皆可 —— dtype 判定与数值 ID 识别共用）。
_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")
#: 编码样本：业务编码（CD-2024-0001 / AQ-B-001）、hex 串、UUID。
_CODE_SAMPLE_RE = re.compile(
    r"^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|[0-9a-fA-F]{6,}"
    r"|[A-Za-z]{1,6}[-_]\d{2,4}[-_]\d{1,6}"
    r"|[A-Za-z]{1,6}[-_]\d{1,6})$"
)
#: ISO 时间戳样本。
_TS_SAMPLE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?)?Z?$"
)

# ── 阈值（全部模块常量，golden 可锁）────────────────────────────────────
#: 选中门槛：最高分低于该值 → 不标注 + advisory。
LABEL_FIELD_MIN_SCORE = 0.50
#: 空值率惩罚上限（超此比例按线性惩罚到底）。
NULL_RATE_HARD = 0.30
#: 平均长度上限（超过按长文本惩罚；≥40 视为长文本字段，强惩罚）。
LONG_TEXT_MEAN = 24.0
LONG_TEXT_HARD = 40.0
#: 基数比窗口：唯一值/总数落在 [0.02, 1] 内不惩罚；低于 0.02 线性惩罚。
CARDINALITY_FLOOR = 0.02
#: 密度阈值（P2）：要素数超过即从 ``all`` 降为 ``top_n``。
DENSE_FEATURE_COUNT = 2000
#: 更密（超过即 ``hover_only``）。
EXTREME_FEATURE_COUNT = 20000
#: top_n 默认档位（确定性三档，随密度递减）。
TOP_N_TIERS: Tuple[Tuple[int, int], ...] = ((8000, 400), (10**12, 250))


# ── 输入归一化 ──────────────────────────────────────────────────────────
class FieldStats(BaseModel):
    """单字段的挑选输入（从 profile 或 FeatureCollection 归一化而来）。"""

    name: str
    dtype: str = "string"            # string | number | bool | geometry | other
    samples: List[str] = Field(default_factory=list)
    total_count: int = 0             # 要素总数（非空 + 空）
    null_count: int = 0
    unique_count: Optional[int] = None   # 已知精确基数时给出；否则由样本估计


def _sample_str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def field_stats_from_profile(profile: Dict[str, Any]) -> List[FieldStats]:
    """把制图 profile（``fields`` 字典 + ``featureCount``）归一化为 FieldStats。

    接受的每字段形状（与 ``semantic_checks`` 消费的 profile 同源）：
    ``{type, sampleValues, null_count, unique_count?}``；缺省键宽松补齐。
    """
    fields = profile.get("fields")
    total = int(profile.get("featureCount") or 0)
    out: List[FieldStats] = []
    if isinstance(fields, dict):
        for fname, spec in fields.items():
            if not isinstance(spec, dict):
                continue
            samples = [_sample_str(v) for v in (spec.get("sampleValues") or [])]
            out.append(FieldStats(
                name=str(fname),
                dtype=str(spec.get("type") or "string"),
                samples=samples,
                total_count=total or len(samples),
                null_count=int(spec.get("null_count") or 0),
                unique_count=spec.get("unique_count"),
            ))
    return out


def field_stats_from_features(features: List[Dict[str, Any]]) -> List[FieldStats]:
    """直接从 GeoJSON FeatureCollection 归一化（组件构建/测试便利入口）。

    纯内存遍历，无 IO。``unique_count`` 在此精确统计（全量数据在手）。
    """
    by_name: Dict[str, Dict[str, Any]] = {}
    total = 0
    for feat in features or []:
        props = feat.get("properties")
        if not isinstance(props, dict):
            continue
        total += 1
        for k, v in props.items():
            slot = by_name.setdefault(k, {"samples": [], "seen": set(), "nulls": 0})
            if v is None:
                slot["nulls"] += 1
                continue
            s = _sample_str(v)
            slot["samples"].append(s)
            if len(slot["samples"]) <= 64:
                slot["seen"].add(s)
    out: List[FieldStats] = []
    for k, slot in by_name.items():
        vals = slot["samples"]
        dtype = "number"
        for s in vals[:16]:
            if not _NUMERIC_RE.match(s):
                dtype = "string"
                break
        # 样本超过 64 后基数按样本集估计（下界）；测试数据集规模远小于该界
        out.append(FieldStats(
            name=k,
            dtype=dtype,
            samples=vals[:64],
            total_count=total,
            null_count=int(slot["nulls"]),
            unique_count=len(slot["seen"]) if len(vals) <= 64 else None,
        ))
    return out


# ── 工件模型 ────────────────────────────────────────────────────────────
class RejectedField(BaseModel):
    """被否决的候选字段（一等工件：决策可解释性的对账面）。"""

    field: str
    score: float
    reason: str            # 稳定中文短码风格理由（golden 锁文本）


class LabelFieldChoice(BaseModel):
    """P1 工件：字段挑选决策（无候选时 field=None + advisory）。"""

    field: Optional[str] = None
    confidence: float = 0.0
    reasons: List[str] = Field(default_factory=list)
    rejected: List[RejectedField] = Field(default_factory=list)
    advisory: Optional[str] = None
    runner_up: Optional[str] = None


class ZoomBand(BaseModel):
    """缩放分级档（P4）：[min_zoom, max_zoom) 内显示 top ``top_ratio`` 比例。

    ``size_ratio`` 为该档字号相对基准（06 线符号律给出）的比例系数；
    ``None`` = 沿用层默认。低 zoom 只标大类/大要素，深入后逐级展开。
    """

    min_zoom: float
    max_zoom: float
    top_ratio: float = 1.0
    size_ratio: Optional[float] = None


class LabelStrategy(BaseModel):
    """P2 工件：标注策略编排（mode/top_n/priority/zoom 分级/字号系数）。"""

    mode: str = "all"                     # all | top_n | hover_only
    top_n: Optional[int] = None
    priority_field: Optional[str] = None
    priority_source: str = "none"         # value_field | area_proxy | none
    zoom_bands: List[ZoomBand] = Field(default_factory=list)
    size_ratio: float = 1.0               # 绝对基准归 06 线符号律
    reasons: List[str] = Field(default_factory=list)


# ── 评分（全部确定性；子函数均纯函数）──────────────────────────────────
def _norm(s: str) -> str:
    return s.strip().lower()


def _vocab_score(field_name: str) -> Tuple[float, str]:
    """name-like 词表得分：exact 1.0；子串按子词长/字段名长加权。"""
    n = _norm(field_name)
    if n in NAME_EXACT_VOCAB:
        return 1.0, f"exact_vocab_hit({field_name})"
    best = 0.0
    hit = ""
    for token in NAME_SUBSTR_VOCAB:
        if token and token in n:
            w = 0.55 + 0.25 * (len(token) / max(len(n), 1)) + (
                0.10 if n.startswith(token) or n.endswith(token) else 0.0
            )
            if w > best:
                best, hit = w, token
    if best > 0.0:
        return round(best, 3), f"substring_vocab_hit({hit}@{field_name})"
    return 0.0, ""


def _samples_semantic_penalty(stats: FieldStats) -> Tuple[float, str]:
    """样本语义排除：编码/时间戳/纯数值主键 → 强惩罚（非硬排除 —— 证据不足时保留少量分）。"""
    vals = [s for s in stats.samples if s]
    if not vals:
        return 0.0, ""
    n = len(vals)
    code_hits = sum(1 for s in vals if _CODE_SAMPLE_RE.match(s))
    ts_hits = sum(1 for s in vals if _TS_SAMPLE_RE.match(s))
    num_hits = sum(1 for s in vals if _NUMERIC_RE.match(s))
    if ts_hits / n >= 0.8:
        return 1.0, "timestamp_like_values"
    if code_hits / n >= 0.8:
        return 1.0, "code_like_values"
    if num_hits / n >= 0.8:
        return 0.9, "numeric_id_like_values"
    return 0.0, ""


def _length_penalty(stats: FieldStats) -> Tuple[float, str]:
    vals = [s for s in stats.samples if s]
    if not vals:
        return 0.0, ""
    mean_len = sum(len(s) for s in vals) / len(vals)
    if mean_len > LONG_TEXT_HARD:
        return 1.0, f"long_text(mean={mean_len:.1f}>{LONG_TEXT_HARD:.0f})"
    if mean_len > LONG_TEXT_MEAN:
        return 0.5, f"long_text(mean={mean_len:.1f}>{LONG_TEXT_MEAN:.0f})"
    if mean_len < 1.0:
        return 0.5, "too_short"
    return 0.0, ""


def score_label_field(stats: FieldStats) -> Tuple[float, List[str]]:
    """单字段评分（0..1，越高越适合做标注字段）+ 理由列表。

    合成口径：词表基础分 ×(1 - 语义惩罚) 再减基数/长度/空值惩罚，
    下限截到 0。数值型字段一律重伤（ID/度量皆不宜做文字标注）。
    无词表命中 → 0 分，但**语义排除理由照记**（id_like/time_like/
    code 样本……）—— rejected 是对账面，0 分字段也要能解释为什么。
    """
    reasons: List[str] = []
    base, hit = _vocab_score(stats.name)
    if base > 0.0:
        reasons.append(hit)
    score = base

    pen, why = _samples_semantic_penalty(stats)
    if pen > 0.0:
        score -= pen
        reasons.append(why)
    if _ID_NAME_RE.search(stats.name):
        score -= 1.0
        reasons.append("id_like_field_name")
    if _TIME_NAME_RE.search(stats.name):
        score -= 1.0
        reasons.append("time_like_field_name")
    if _GEOM_NAME_RE.search(stats.name):
        score -= 1.0
        reasons.append("geometry_field_name")
    if stats.dtype in ("number", "int", "float", "geometry"):
        score -= 0.9
        reasons.append(f"numeric_dtype({stats.dtype})")
    if base <= 0.0:
        # 无词表命中：语义理由已记则保留，否则如实记 no_vocab_hit。
        reasons.append("no_vocab_hit")
        return max(score, 0.0), reasons

    # 基数比：样本估计或精确值（唯一值/总数 → 接近 1 才是好标注字段）
    total = stats.total_count or len(stats.samples) or 1
    nonnull = max(total - stats.null_count, 1)
    if stats.unique_count is not None:
        card = stats.unique_count / nonnull
    elif stats.samples:
        card = len(set(stats.samples)) / len(stats.samples)
    else:
        card = 0.0
    if card < CARDINALITY_FLOOR:
        score -= 1.0
        reasons.append(f"low_cardinality(ratio={card:.2f}<{CARDINALITY_FLOOR:.2f})")
    elif card < 0.10:
        score -= 0.6
        reasons.append(f"low_cardinality(ratio={card:.2f}<0.10)")
    elif card < 0.50:
        score -= 0.3
        reasons.append(f"low_cardinality(ratio={card:.2f}<0.50)")
    elif card >= 0.98 and stats.dtype == "string":
        reasons.append(f"cardinality(ratio={card:.2f})")

    # 长度分布
    pen, why = _length_penalty(stats)
    if pen > 0.0:
        score -= pen
        reasons.append(why)

    # 空值率
    null_rate = (stats.null_count / total) if total else 0.0
    if null_rate > NULL_RATE_HARD:
        score -= 0.5
        reasons.append(f"null_rate({null_rate:.2f}>{NULL_RATE_HARD:.2f})")
    elif null_rate > 0:
        score -= 0.15
        reasons.append(f"null_rate({null_rate:.2f})")

    return max(score, 0.0), reasons


#: tie-break 词表次序（§0.4：name > title > label > 语义类型 > 长度分布 ——
#: 前两级由评分吸收，这里锁定同分时的字段名族顺序，再按名字典序全序化）。
_TIEBREAK_ORDER: Tuple[str, ...] = ("name", "名称", "名字", "title", "标题", "label", "标注")


def _tiebreak_key(item: Tuple[str, float, List[str]]) -> Tuple[int, str]:
    name = item[0]
    for i, token in enumerate(_TIEBREAK_ORDER):
        if token in name.lower() or token in name:
            return (i, name)
    return (len(_TIEBREAK_ORDER), name)


def choose_label_field(
    profile: Dict[str, Any],
    intent: Optional[Dict[str, Any]] = None,
    *,
    field_stats: Optional[List[FieldStats]] = None,
) -> LabelFieldChoice:
    """P1 主入口：挑选最佳标注字段（纯函数，确定性）。

    参数
    ----
    profile:
        制图 profile（``fields``/``featureCount``，与 semantic_checks 同源）。
    intent:
        可选的用户意图覆盖（如显式 ``{"label_field": "xx"}`` —— 用户显式
        指定时不做挑选，仅校验存在性）。
    field_stats:
        可直接注入归一化字段统计（测试便利；优先于 profile）。
    """
    _ = intent  # 显式覆盖由调用方（组件构建层）处理；本函数只做数据驱动挑选
    stats_list = field_stats if field_stats is not None else field_stats_from_profile(profile)

    # 全字段评分入对账面（0 分字段也带语义理由 —— rejected 是一等工件）
    scored: List[Tuple[str, float, List[str]]] = []
    for stats in stats_list:
        s, reasons = score_label_field(stats)
        scored.append((stats.name, s, reasons))
    # 全序化：分数降序 → tie-break 词表族次序 → 字段名字典序
    scored.sort(key=lambda t: (-t[1],) + _tiebreak_key(t))
    positive = [t for t in scored if t[1] > 0.0]

    if not positive or positive[0][1] < LABEL_FIELD_MIN_SCORE:
        rejected = [
            RejectedField(field=nm, score=round(s, 3), reason=";".join(rs) or "below_min_score")
            for nm, s, rs in scored
        ]
        return LabelFieldChoice(
            field=None,
            confidence=round(positive[0][1], 3) if positive else 0.0,
            reasons=["no_field_meets_threshold"],
            rejected=rejected,
            advisory=(
                "未发现可用的标注字段（无 name-like 候选或候选得分过低），"
                "本层不自动标注；可用 label_layer 组件显式指定字段"
            ),
        )

    best_name, best_score, best_reasons = positive[0]
    runner_up = positive[1][0] if len(positive) > 1 else None
    rejected = [
        RejectedField(field=nm, score=round(s, 3), reason=";".join(rs) or "lower_score")
        for nm, s, rs in scored
        if nm != best_name
    ]
    return LabelFieldChoice(
        field=best_name,
        confidence=round(best_score, 3),
        reasons=list(best_reasons),
        rejected=rejected,
        runner_up=runner_up,
    )


# ── P2：策略编排 ────────────────────────────────────────────────────────
#: 默认 zoom 分级（点/线/面共用；4 档，门禁 §5 按此断言）。
DEFAULT_ZOOM_BANDS: Tuple[ZoomBand, ...] = (
    ZoomBand(min_zoom=0.0, max_zoom=8.0, top_ratio=0.10, size_ratio=0.9),
    ZoomBand(min_zoom=8.0, max_zoom=11.0, top_ratio=0.25, size_ratio=0.95),
    ZoomBand(min_zoom=11.0, max_zoom=14.0, top_ratio=0.60, size_ratio=1.0),
    ZoomBand(min_zoom=14.0, max_zoom=24.0, top_ratio=1.0, size_ratio=1.0),
)


def _find_priority_field(stats_list: List[FieldStats]) -> Tuple[Optional[str], str]:
    """重要度代理字段：词表次序扫描；未命中数值字段时按面积代理兜底。"""
    by_lower = {_norm(s.name): s for s in stats_list}
    for token in PRIORITY_FIELD_VOCAB:
        for norm_name, stats in by_lower.items():
            if token == norm_name or (len(token) >= 3 and token in norm_name):
                if stats.dtype in ("number", "int", "float"):
                    return stats.name, "value_field"
    # 词表未命中：数值型字段兜底取名字典序首个数值字段（确定性）
    numeric = sorted(
        (s for s in stats_list if s.dtype in ("number", "int", "float")),
        key=lambda s: s.name,
    )
    if numeric:
        return numeric[0].name, "value_field"
    return None, "area_proxy"


def _top_n_for(feature_count: int) -> int:
    for upper, n in TOP_N_TIERS:
        if feature_count <= upper:
            return n
    return TOP_N_TIERS[-1][1]


def plan_label_strategy(
    profile: Dict[str, Any],
    view: Optional[Dict[str, Any]] = None,
    *,
    field_choice: Optional[LabelFieldChoice] = None,
    field_stats: Optional[List[FieldStats]] = None,
) -> LabelStrategy:
    """P2 主入口：按要素密度与 zoom 分档编排标注策略（纯函数，确定性）。

    - ``n <= 2000`` → ``all``（zoom 分级仍生效，比例全 1.0 收敛为不过滤）；
    - ``2000 < n <= 20000`` → ``top_n``（确定性档位取整）；
    - ``n > 20000`` → ``hover_only``（交互悬停查看，不常驻）。
    """
    _ = view  # 分档由数据密度决定；view.zoom 由前端运行时消费 zoom_bands
    stats_list = field_stats if field_stats is not None else field_stats_from_profile(profile)
    n = int(profile.get("featureCount") or 0)
    if n <= 0 and stats_list:
        n = max(s.total_count for s in stats_list)

    reasons: List[str] = []
    if field_choice is not None and field_choice.field is None:
        reasons.append("no_label_field")
        return LabelStrategy(mode="hover_only", top_n=None, priority_field=None,
                             priority_source="none", zoom_bands=[],
                             reasons=reasons)

    if n > EXTREME_FEATURE_COUNT:
        mode = "hover_only"
        top_n = None
        reasons.append(f"extreme_density(n={n}>{EXTREME_FEATURE_COUNT})")
    elif n > DENSE_FEATURE_COUNT:
        mode = "top_n"
        top_n = _top_n_for(n)
        reasons.append(f"dense(n={n}>{DENSE_FEATURE_COUNT})→top_n({top_n})")
    else:
        mode = "all"
        top_n = None
        reasons.append(f"sparse(n={n}<={DENSE_FEATURE_COUNT})→all")

    pfield, psource = _find_priority_field(stats_list)
    if pfield:
        reasons.append(f"priority_field({pfield}<{psource})")

    bands = list(DEFAULT_ZOOM_BANDS)
    if mode == "all":
        # 稀疏层：zoom 分级收敛为全量显示（保留档位结构供门禁与前端一致）
        bands = [
            ZoomBand(min_zoom=b.min_zoom, max_zoom=b.max_zoom,
                     top_ratio=1.0, size_ratio=b.size_ratio)
            for b in bands
        ]
        reasons.append("zoom_bands_saturated(all)")
    else:
        reasons.append("zoom_bands_default(top_ratio_graded)")

    return LabelStrategy(
        mode=mode,
        top_n=top_n,
        priority_field=pfield,
        priority_source=psource,
        zoom_bands=bands,
        size_ratio=1.0,
        reasons=reasons,
    )


# ── spec 契约组装（后端唯一写入面）──────────────────────────────────────
def build_label_spec(
    profile: Dict[str, Any],
    view: Optional[Dict[str, Any]] = None,
    *,
    field_stats: Optional[List[FieldStats]] = None,
    explicit_field: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """把 P1+P2 决策组装成 MapSpec ``layer.label`` dict（无候选 → None）。

    产物形状（camelCase，与 ``mapspec_schema.MapSpecLayerLabel`` 一致）：
    ``{field, mode, topN, priorityField, zoomBands[{minZoom,maxZoom,
    topRatio,sizeRatio}], sizeRatio, haloMode:"auto"}`` —— 前端
    ``label-layout.ts::normalizeLabelStrategy`` 是该形状的唯一交互侧
    消费者；``haloMode="auto"`` 打开底图亮度自适应配色（P5）。

    ``explicit_field``：用户显式指定字段（label_layer 组件 options 或
    intent 覆盖）时跳过挑选、仅做策略编排；指定字段不在 profile 字段
    列表里 → 仍按显式值产出（诚实保留用户输入，由前端 get 表达式
    自然落空），但附 reasons 说明。
    """
    stats_list = field_stats if field_stats is not None else field_stats_from_profile(profile)
    choice = choose_label_field(profile, field_stats=stats_list)
    strategy = plan_label_strategy(profile, view, field_choice=choice, field_stats=stats_list)

    field = explicit_field or choice.field
    if field is None:
        return None

    reasons = list(strategy.reasons)
    if explicit_field:
        reasons.append(f"explicit_field({explicit_field})")
    elif choice.confidence < 1.0:
        reasons.append(f"auto_field({choice.field}@conf={choice.confidence})")

    return {
        "field": field,
        "mode": strategy.mode,
        "topN": strategy.top_n,
        "priorityField": strategy.priority_field,
        "zoomBands": [
            {
                "minZoom": b.min_zoom,
                "maxZoom": b.max_zoom,
                "topRatio": b.top_ratio,
                "sizeRatio": b.size_ratio,
            }
            for b in strategy.zoom_bands
        ],
        "sizeRatio": strategy.size_ratio,
        "haloMode": "auto",
        "_reasons": reasons,   # 工具层备注（extra=allow 契约，消费方可忽略）
    }


__all__ = [
    "FieldStats",
    "RejectedField",
    "LabelFieldChoice",
    "ZoomBand",
    "LabelStrategy",
    "field_stats_from_profile",
    "field_stats_from_features",
    "score_label_field",
    "choose_label_field",
    "plan_label_strategy",
    "build_label_spec",
    "NAME_EXACT_VOCAB",
    "NAME_SUBSTR_VOCAB",
    "PRIORITY_FIELD_VOCAB",
    "DEFAULT_ZOOM_BANDS",
    "LABEL_FIELD_MIN_SCORE",
    "DENSE_FEATURE_COUNT",
    "EXTREME_FEATURE_COUNT",
]
