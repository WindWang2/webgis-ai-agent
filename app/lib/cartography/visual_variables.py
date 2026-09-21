"""Visual Variable Grammar（C1，ADR-0205）—— 测量语义 × 视觉变量适配矩阵.

定位（ADR-0205 D1/D2）：**事前规划层**。仓库已有完整的裁决引擎族
（``resolve_symbology`` 唯一裁决 method×k×palette、``label_plan`` 标注、
``layout_solver`` 布局）与事后校验族（``semantic_checks`` 的
``carto.visualvar.overload``），但「哪个字段以哪个测量语义占用哪个视觉
通道」此前无人裁决——通道过载只能成图后报警，``data_kind`` 全仓无人
推导（signed change 被系统性画成 sequential 单向色带）。

本模块只做三件事，全部纯函数：

1. **冻结词表**：``MEASUREMENT_KINDS``（测量语义）与 ``VISUAL_VARIABLES``
   （视觉变量，Bertin/ Mackinlay 体系）；runtime 不支持的通道
   （texture/orientation）诚实标注，grammar 永不分配。
2. **适配矩阵**：``CHANNEL_FIT[measurement][variable] → ChannelFit``，
   level ∈ preferred/allowed/rejected，每格带稳定 reason code
   （``GRAMMAR.CHAN.*``）。到 standards ``DATA_SEMANTICS``（ADR-0200
   冻结词表）是**单向投影**——本模块 import 它，standards 不反向依赖。
3. **推断与推导**：``infer_measurement_kind`` 以确定性证据（负值占比、
   词素、基数）推断字段测量语义（reasons/rejected 全程对账）；
   ``derive_data_kind`` 是 data_kind 的推导单点（nominal→qualitative、
   signed_change→diverging、…），供调用方喂 ``resolve_symbology``。

**不做**：不重裁决 palette/k/分类法（ADR-0152 红线）；不渲染；无 IO、
无随机、无时钟——同输入两次求值 ``model_dump()`` 完全相等。

诚实边界：signed_change 需要双符号值证据——全负值/同号的变化量字段
（如纯亏损列）不会被值证据触发，需调用方以 ``explicit`` pin；词素推断
为启发式（边界见 ADR-0205），均以 reasons/rejected 全程对账。
"""
from __future__ import annotations

import math
import re
from typing import Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from app.lib.cartography.symbology import DataKind
from app.lib.cartography.standards.rule import DATA_SEMANTICS

# ── 冻结词表 ─────────────────────────────────────────────────────────────

#: 测量语义（Stevens 测量层级 + 制图扩展态）。``explicit`` 由调用方 pin，
#: 不在此列（pin 走 user-wins 记录，不改变词表）。
MEASUREMENT_KINDS: Tuple[str, ...] = (
    "nominal",        # 类别（无序）：行政区名、土地利用类型
    "ordinal",        # 有序类别：等级、排名、风险档位
    "quantitative",   # 数值（原点任意）：温度、指数
    "ratio",          # 比率（零有意义）：面积、长度、人口、计数
    "rate",           # 归一化率：占比、密度、人均
    "signed_change",  # 带符号变化：净迁移、增减量、距平
    "uncertainty",    # 不确定度：标准差、置信区间宽、误差
    "temporal",       # 时间量：年份、时相序号
)

#: 视觉变量词表（Bertin/Mackinlay；texture/orientation 的 runtime 现状
#: 诚实标注 —— 任务书「若 runtime 支持」口径，grammar 永不分配
#: unsupported 通道）。
VISUAL_VARIABLES: Tuple[str, ...] = (
    "position", "size", "shape", "hue", "lightness",
    "saturation", "opacity", "texture", "orientation",
)

#: runtime 支持状态（MapLibre/deck 表达域现状；与 model_library 的
#: runtime_status 同风格——描述性标注，不虚构能力）。
CHANNEL_RUNTIME_STATUS: Dict[str, str] = {
    "position": "supported",
    "size": "supported",
    "shape": "partial",      # MapLibre symbol 仅图标枚举，非自由形状
    "hue": "supported",
    "lightness": "supported",
    "saturation": "supported",
    "opacity": "supported",
    "texture": "unsupported",  # fill-pattern 有，但 CVD/print 语义未成契约
    "orientation": "partial",  # icon-rotate 存在，语义面未成契约
}

#: grammar 可分配的通道（primary/secondary 都必须可分配）。
def allocatable(variable: str) -> bool:
    """通道当前是否可被 grammar 分配（runtime 支持门槛）。"""
    return CHANNEL_RUNTIME_STATUS.get(variable) == "supported"


# ── 适配矩阵（冻结；level 三集完备划分 9 变量）────────────────────────────

#: 每个测量语义的通道适配三分集（preferred + allowed + rejected 必须恰好
#: 划分 ``VISUAL_VARIABLES``，``_build_channel_fit`` 构建期断言）。
_CHANNEL_FIT_TABLE: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "nominal": {
        "preferred": ("hue", "shape"),
        "allowed": ("position", "texture"),
        "rejected": ("size", "lightness", "saturation", "opacity", "orientation"),
    },
    "ordinal": {
        "preferred": ("lightness", "saturation"),
        "allowed": ("hue", "size", "position", "opacity", "texture"),
        "rejected": ("shape", "orientation"),
    },
    "quantitative": {
        "preferred": ("position", "size", "lightness"),
        "allowed": ("hue", "saturation", "opacity"),
        "rejected": ("shape", "texture", "orientation"),
    },
    "ratio": {
        "preferred": ("size", "position"),
        "allowed": ("lightness", "hue", "saturation", "opacity"),
        "rejected": ("shape", "texture", "orientation"),
    },
    "rate": {
        "preferred": ("lightness", "position"),
        "allowed": ("size", "hue", "saturation", "opacity"),
        "rejected": ("shape", "texture", "orientation"),
    },
    "signed_change": {
        "preferred": ("hue", "lightness"),
        "allowed": ("saturation", "opacity", "position"),
        "rejected": ("size", "shape", "texture", "orientation"),
    },
    "uncertainty": {
        "preferred": ("opacity", "texture"),
        "allowed": ("lightness", "saturation"),
        "rejected": ("position", "size", "shape", "hue", "orientation"),
    },
    "temporal": {
        "preferred": ("position", "lightness"),
        "allowed": ("hue", "orientation", "size", "saturation", "opacity"),
        "rejected": ("shape", "texture"),
    },
}

#: rejected 格的制图学理由（关键格逐条给语义；未列出的用通用文案）。
_REJECT_REASONS: Dict[Tuple[str, str], str] = {
    ("nominal", "size"): "大小暗示数量差异，类别间无数量语义",
    ("nominal", "lightness"): "明度差暗示有序层级，类别无序",
    ("nominal", "saturation"): "饱和度差暗示有序层级，类别无序",
    ("nominal", "opacity"): "透明度差暗示有序/数量语义，类别无序",
    ("ordinal", "shape"): "形状无感知序，无法表达等级",
    ("quantitative", "shape"): "形状无感知量级",
    ("signed_change", "size"): "大小只能编码幅值，正负号丢失（应 diverging 色相/明度）",
    ("signed_change", "shape"): "形状无符号语义",
    ("uncertainty", "hue"): "色相是主通道级编码，不确定度只应作次通道（透明度/晕线）",
    ("uncertainty", "size"): "大小是主通道级编码，不确定度只应作次通道",
    ("uncertainty", "shape"): "形状无程度语义",
}


class ChannelFit(BaseModel):
    """单个 (测量语义, 视觉变量) 格的适配结论。"""

    variable: str
    level: str                      # preferred | allowed | rejected
    reason_code: str                # GRAMMAR.CHAN.*（稳定）
    reason: str = ""
    runtime_status: str = "supported"


def _build_channel_fit() -> Dict[str, Dict[str, ChannelFit]]:
    """构建期展开矩阵并断言三分集完备划分（防手抄漏格）。"""
    table: Dict[str, Dict[str, ChannelFit]] = {}
    for kind, rows in _CHANNEL_FIT_TABLE.items():
        covered: List[str] = []
        for level in ("preferred", "allowed", "rejected"):
            covered.extend(rows[level])
        if sorted(covered) != sorted(VISUAL_VARIABLES):
            raise AssertionError(f"CHANNEL_FIT 词表划分不完整: {kind} -> {sorted(covered)}")
        cells: Dict[str, ChannelFit] = {}
        for level in ("preferred", "allowed", "rejected"):
            for variable in rows[level]:
                cells[variable] = ChannelFit(
                    variable=variable,
                    level=level,
                    reason_code=(
                        f"GRAMMAR.CHAN.{variable.upper()}_{kind.upper()}_{level.upper()}"
                    ),
                    reason=_REJECT_REASONS.get((kind, variable), ""),
                    runtime_status=CHANNEL_RUNTIME_STATUS[variable],
                )
        table[kind] = cells
    return table


#: 冻结矩阵（import 期构建一次；划分断言失败即 import 失败，fail-closed）。
CHANNEL_FIT: Dict[str, Dict[str, ChannelFit]] = _build_channel_fit()


def channel_fit_order(kind: str) -> Dict[str, Tuple[str, ...]]:
    """矩阵行的**声明序**三分集（求解次序的单一来源）。

    返回 ``{"preferred": (...), "allowed": (...), "rejected": (...)}``，
    元组内保持 ``_CHANNEL_FIT_TABLE`` 的声明次序（= 制图学优先序）——
    调用方按此序绑定主/次通道，不得重排。未知 kind fail-closed。
    """
    if kind not in _CHANNEL_FIT_TABLE:
        raise ValueError(
            f"未知测量语义 {kind!r}（合法：{', '.join(MEASUREMENT_KINDS)}）")
    return {
        level: tuple(_CHANNEL_FIT_TABLE[kind][level])
        for level in ("preferred", "allowed", "rejected")
    }


def channel_fit(measurement: str, variable: str) -> ChannelFit:
    """查矩阵（未知词 fail-closed ValueError）。"""
    if measurement not in CHANNEL_FIT:
        raise ValueError(
            f"未知测量语义 {measurement!r}（合法：{', '.join(MEASUREMENT_KINDS)}）")
    row = CHANNEL_FIT[measurement]
    if variable not in row:
        raise ValueError(
            f"未知视觉变量 {variable!r}（合法：{', '.join(VISUAL_VARIABLES)}）")
    return row[variable]


# ── 到 standards DATA_SEMANTICS 的单向投影 ───────────────────────────────

#: grammar → standards（ADR-0200 冻结词表）投影。单向：grammar import
#: standards 词表，standards 不感知 grammar。signed_change 在 standards
#: 词表中无对应态，投影到 continuous 并在上层披露（诚实边界）。
MEASUREMENT_TO_DATA_SEMANTICS: Dict[str, Tuple[str, ...]] = {
    "nominal": ("category",),
    "ordinal": ("continuous",),
    "quantitative": ("continuous",),
    "ratio": ("count", "continuous"),
    "rate": ("rate",),
    "signed_change": ("continuous",),
    "uncertainty": ("uncertainty",),
    "temporal": ("temporal",),
}


def project_to_data_semantics(measurement: str) -> Tuple[str, ...]:
    """投影到 standards DATA_SEMANTICS（词表外 fail-closed）。"""
    if measurement not in MEASUREMENT_TO_DATA_SEMANTICS:
        raise ValueError(f"未知测量语义 {measurement!r}")
    projected = MEASUREMENT_TO_DATA_SEMANTICS[measurement]
    for item in projected:
        if item not in DATA_SEMANTICS:
            raise AssertionError(f"投影值 {item} 不在 standards DATA_SEMANTICS")
    return projected


# ── 测量语义推断（确定性证据推断）────────────────────────────────────────

#: rate/ratio 词素（多语言）。ASCII 词素按边界安全匹配（``_token_`` 有界，
#: 防 ``migration`` 误命中 ``ratio`` 这类子串假阳性）；CJK 词素按子串。
_RATE_NAME_TOKENS: Tuple[str, ...] = (
    "rate", "ratio", "pct", "percent", "percentage", "share", "proportion",
    "per_capita", "per_km2", "per_area", "density",
    "人均", "占比", "比率", "比例", "密度", "单位面积",
)
#: 有序档位词素。
_ORDINAL_NAME_TOKENS: Tuple[str, ...] = (
    "rank", "ranking", "grade", "level", "tier", "risk_level",
    "severity", "等级", "排名", "级别", "档次", "星级",
)
#: 计数/总量词素（ratio 判定）。
_RATIO_NAME_TOKENS: Tuple[str, ...] = (
    "count", "num", "number", "total", "sum", "pop", "population",
    "area", "length", "数量", "总数", "人口", "面积", "长度",
)
#: 不确定度词素。
_UNCERTAINTY_NAME_TOKENS: Tuple[str, ...] = (
    "uncertainty", "confidence", "std", "variance", "ci", "moe", "error",
    "不确定", "置信", "误差", "方差", "标准差",
)
#: 时间词素（含周期时间词——month/hour 等作为主题字段时即周期量；
#: 词素启发边界已在 ADR-0205 诚实披露）。
_TEMPORAL_NAME_TOKENS: Tuple[str, ...] = (
    "year", "date", "time", "period", "epoch",
    "hour", "month", "weekday", "day_of_week", "season",
    "年", "日期", "时间", "时相", "月份", "小时", "星期", "季度",
)
#: 周期时间词素（temporal → cyclic data_kind 的唯一触发面）。
_CYCLIC_NAME_TOKENS: Tuple[str, ...] = (
    "hour", "month", "weekday", "day_of_week", "season", "季度", "月份", "小时", "星期",
)

#: 强名称词素（语义近确定，先于值证据）。
_STRONG_NAME_TOKENS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("uncertainty", _UNCERTAINTY_NAME_TOKENS),
    ("temporal", _TEMPORAL_NAME_TOKENS),
)
#: 一般名称词素（值证据 signed 之后才参与）。
_WEAK_NAME_TOKENS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("rate", _RATE_NAME_TOKENS),
    ("ordinal", _ORDINAL_NAME_TOKENS),
    ("ratio", _RATIO_NAME_TOKENS),
)

_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")

#: signed_change 判定的负值占比下限（双符号在场且负侧 ≥5% 才认变化量；
#: 否则疑为哨兵值，记录 rejected 不采纳）。
_SIGNED_NEG_SHARE = 0.05
#: 低基数整数 → ordinal 的唯一值上限（离散档位证据）。
_ORDINAL_MAX_UNIQUE = 6
#: 值样本推断上限（与 label_plan.FieldStats 样本口径一致，有界）。
_INFERENCE_SAMPLE_CAP = 4096


class MeasurementDecision(BaseModel):
    """测量语义推断工件：结论 + 证据理由 + 落选者（对账面）。"""

    field: str
    kind: str                        # ∈ MEASUREMENT_KINDS
    source: str                      # explicit | evidence | fallback
    data_kind: DataKind              # derive_data_kind 的同局推导
    reasons: List[str] = Field(default_factory=list)
    rejected: List[Dict[str, str]] = Field(default_factory=list)
    reason_codes: List[str] = Field(default_factory=list)

    @property
    def data_semantics(self) -> Tuple[str, ...]:
        """到 standards 词表的单向投影。"""
        return project_to_data_semantics(self.kind)


def derive_data_kind(kind: str, *, field_name: str = "") -> DataKind:
    """data_kind 推导单点（ADR-0205 D3）。

    nominal→qualitative；signed_change→diverging；temporal 且字段名含
    周期词素→cyclic（仓内无 cyclic 色带时 resolve_symbology 已有诚实
    降级披露）；其余→sequential。未知 kind fail-closed。
    """
    if kind not in MEASUREMENT_KINDS:
        raise ValueError(
            f"未知测量语义 {kind!r}（合法：{', '.join(MEASUREMENT_KINDS)}）")
    if kind == "nominal":
        return "qualitative"
    if kind == "signed_change":
        return "diverging"
    if kind == "temporal":
        name = (field_name or "").lower()
        if any(token in name for token in _CYCLIC_NAME_TOKENS):
            return "cyclic"
    return "sequential"


def _norm_name(field_name: str) -> str:
    """字段名归一化：小写、分隔符统一为 ``_``（边界匹配用）。"""
    lowered = (field_name or "").strip().lower()
    out: List[str] = []
    for ch in lowered:
        out.append(ch if (ch.isalnum() or ord(ch) > 127) else "_")
    return "".join(out)


def _token_in_name(token: str, normalized: str) -> bool:
    """词素命中判定：ASCII 词素边界安全（``_token_`` 有界，防
    ``migration`` 误命中 ``ratio``）；CJK 词素无词边界，按子串。"""
    if any(ord(ch) > 127 for ch in token):
        return token in normalized
    return f"_{token}_" in f"_{normalized}_"


def _token_hits(field_name: str, table) -> List[Tuple[str, str]]:
    """词表扫描（按表序 = 判定优先级；每族取首个命中词素）。"""
    normalized = _norm_name(field_name)
    if not normalized.strip("_"):
        return []
    hits: List[Tuple[str, str]] = []
    for kind, tokens in table:
        for token in tokens:
            if _token_in_name(token, normalized):
                hits.append((kind, token))
                break
    return hits


def infer_measurement_kind(
    field_name: str,
    *,
    dtype: str = "number",
    values: Optional[Sequence[float]] = None,
    unique_count: Optional[int] = None,
    explicit: Optional[str] = None,
) -> MeasurementDecision:
    """字段测量语义推断（确定性，纯函数）。

    判定次序（先命中先得，落选者全程留痕）：
    1. ``explicit``（调用方/用户 pin）→ 尊重（source=explicit）；
    2. dtype 为 string/bool → nominal（类别面）；
    3. 强名称词素（uncertainty > temporal——语义近确定）；
    4. 强值证据：双符号且负侧占比 ≥5% → signed_change（少数负值疑哨兵，
       rejected 留痕不采纳）；
    5. 一般名称词素（rate > ordinal > ratio）；
    6. 值证据：整数低基数（≤6 唯一值）→ ordinal；
    7. 兜底 quantitative（与全仓现状一致的 fallback，行为零漂移）。

    values 只接受有限数值样本（有界 ≤4096）；非数值输入按空证据处理。
    """
    field = str(field_name or "")
    rejected: List[Dict[str, str]] = []
    reasons: List[str] = []
    codes: List[str] = []

    def _codes(*add: str) -> None:
        codes.extend(c for c in add if c not in codes)

    if explicit is not None:
        if explicit not in MEASUREMENT_KINDS:
            raise ValueError(
                f"explicit 测量语义 {explicit!r} 非法（合法：{', '.join(MEASUREMENT_KINDS)}）")
        return MeasurementDecision(
            field=field, kind=explicit, source="explicit",
            data_kind=derive_data_kind(explicit, field_name=field),
            reasons=[f"调用方显式指定测量语义 {explicit}（user-wins）"],
            reason_codes=["GRAMMAR.MEAS.EXPLICIT"],
        )

    finite: List[float] = []
    if values:
        for v in list(values)[:_INFERENCE_SAMPLE_CAP]:
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
                finite.append(float(v))

    dtype_norm = (dtype or "number").lower()

    # 2. 类别面 dtype
    if dtype_norm in ("string", "bool", "geometry", "other"):
        return MeasurementDecision(
            field=field, kind="nominal", source="evidence",
            data_kind=derive_data_kind("nominal", field_name=field),
            reasons=[f"dtype={dtype_norm} → 类别面"],
            reason_codes=["GRAMMAR.MEAS.DTYPE_NOMINAL"],
        )

    def _finish(kind: str, source: str) -> MeasurementDecision:
        return MeasurementDecision(
            field=field, kind=kind, source=source,
            data_kind=derive_data_kind(kind, field_name=field),
            reasons=reasons, rejected=rejected, reason_codes=codes,
        )

    # 3. 强名称词素
    strong = _token_hits(field, _STRONG_NAME_TOKENS)
    if strong:
        kind, token = strong[0]
        reasons.append(f"字段名强词素 {token!r} → {kind}")
        _codes(f"GRAMMAR.MEAS.NAME_{kind.upper()}")
        return _finish(kind, "evidence")

    # 4. 强值证据：signed_change
    if finite:
        neg = [v for v in finite if v < 0]
        pos = [v for v in finite if v > 0]
        if neg and pos:
            neg_share = len(neg) / len(finite)
            if neg_share >= _SIGNED_NEG_SHARE:
                reasons.append(
                    f"值证据：双符号（neg {len(neg)}/{len(finite)}="
                    f"{neg_share:.0%} ≥ {_SIGNED_NEG_SHARE:.0%}）→ signed_change")
                _codes("GRAMMAR.MEAS.VALUE_SIGNED")
                return _finish("signed_change", "evidence")
            rejected.append({
                "kind": "signed_change",
                "reason": f"负值占比 {neg_share:.0%} < {_SIGNED_NEG_SHARE:.0%}，疑哨兵值不采纳",
            })
            _codes("GRAMMAR.CHAN.SIGNED_SENTINEL_SUSPECT")

    # 5. 一般名称词素
    weak = _token_hits(field, _WEAK_NAME_TOKENS)
    if weak:
        kind, token = weak[0]
        reasons.append(f"字段名词素 {token!r} → {kind}")
        _codes(f"GRAMMAR.MEAS.NAME_{kind.upper()}")
        if len(weak) > 1:
            reasons.append(f"多词素命中 {[(k, t) for k, t in weak[1:]]}，按判定次序取 {kind}")
        if kind in ("rate", "ratio", "ordinal") and finite:
            reasons.append(f"值证据 n={len(finite)} 佐证数值面")
        return _finish(kind, "evidence")

    # 6. 值证据：低基数整数 → ordinal
    if finite:
        unique = unique_count if unique_count is not None else len(set(finite))
        if (
            dtype_norm in ("int", "integer")
            and unique <= _ORDINAL_MAX_UNIQUE
            and all(float(v).is_integer() for v in finite)
        ):
            reasons.append(
                f"值证据：整数低基数（unique={unique} ≤ {_ORDINAL_MAX_UNIQUE}）→ ordinal")
            _codes("GRAMMAR.MEAS.VALUE_LOW_CARDINAL_INTEGER")
            return _finish("ordinal", "evidence")

    # 7. 兜底（与全仓现状一致：无证据 = quantitative/sequential，零漂移）
    if not finite:
        _codes("GRAMMAR.MEAS.INSUFFICIENT_EVIDENCE")
        reasons.append("证据不足（无词素命中且无有限值样本）→ 兜底 quantitative（行为与现状一致）")
    else:
        reasons.append("无更强证据 → quantitative（原点任意数值，sequential 族）")
    return _finish("quantitative", "fallback" if not finite else "evidence")


__all__ = [
    "MEASUREMENT_KINDS",
    "VISUAL_VARIABLES",
    "CHANNEL_RUNTIME_STATUS",
    "CHANNEL_FIT",
    "MEASUREMENT_TO_DATA_SEMANTICS",
    "ChannelFit",
    "MeasurementDecision",
    "allocatable",
    "channel_fit",
    "channel_fit_order",
    "project_to_data_semantics",
    "derive_data_kind",
    "infer_measurement_kind",
]
