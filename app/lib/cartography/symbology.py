"""自适应符号化引擎（AC-03 / ADR-0152）：method × k × palette × clip_policy
的**唯一裁决入口** ``resolve_symbology``。

背景（docs/dev/ac-03-symbology-recon.md）：``choose_classification``（ADR-0073）
已实现高质量的分布驱动分类裁决，但全仓只有 create_thematic_map 一处接线；
h3_binning / apply_template / build_thematic_style 等入口仍硬编码
quantiles/5/YlOrRd —— 同一份数据走不同入口出图质量参差。本模块把
「分类法裁决（复用 choose_classification，禁止重写算法）→ k 裁决 →
色带裁决 → 离群值策略」串联为一个纯函数引擎，五个入口全部改从这里取
符号化决策，``SymbologyDecision`` 是可序列化的一等工件（``rejected[]``
同时是 09 线自愈动作的可替换动作清单）。

裁决优先级（§0.4 全自动默认决策）：
1. 调用方**显式**指定 method/palette → 尊重（source=explicit），只校正
   k 边界与无障碍硬约束（每项校正写 rejected[]，禁止无声改数）；
2. **重尾证据**（mean ≥ 1.5×median，ADR-0073 同阈值）→ head_tail，
   可推翻模板偏好与 explicit 之外的一切；
3. 模板/模型的**推荐**偏好 → 在证据不反对时获尊重（source=recommended）；
4. 无证据（n<8 / 全等）→ equal_interval + k=3 + low_confidence；
5. 其余 → choose_classification 分布裁决（近均匀 → equal_interval/
   quantiles；默认 natural_breaks）。

色带裁决维度：数据类型（sequential/diverging/qualitative/cyclic）×
底图亮度（暗底优先感知均匀族）× 上下文约束（screen/projector/print/
cvd_deuteranopia/cvd_protanopia/cvd_tritanopia）。CVD 用 Machado(2009)
线性 RGB 矩阵模拟后做 CIEDE2000 可分辨校验；print 做降饱和 + 灰度
ΔL 可分级校验。CVD/print 是无障碍硬约束：显式色带不达标同样换带并
在 rejected[] 披露。

纯函数、无 IO（与 visualization_plan.py 同风格）；颜色计算全部落在
palettes.py 的确定性实现上（同一输入恒同一输出，golden 测试锁数值）。
"""
from __future__ import annotations

import math
from typing import Dict, List, Literal, Optional

from numpy import quantile as _np_quantile
from pydantic import BaseModel, Field

from app.lib.cartography.model_library import CLASSIFICATION_METHODS, PALETTE_KINDS
from app.lib.cartography.palettes import (
    COLOR_PALETTES,
    NATIVE_HEATMAP_COLORS,
    grayscale_ramp_separation,
    min_adjacent_delta_e,
    print_desaturate,
    sample_ramp_colors,
    simulate_cvd,
)
from app.lib.cartography.visualization_plan import (
    DistributionStats,
    _HEAVY_TAIL_THRESHOLD,
    _skew_ratio,
    choose_classification,
    distribution_stats_from_values,
)

# ── 词表 ────────────────────────────────────────────────────────────────────

DataKind = Literal["sequential", "diverging", "qualitative", "cyclic"]
PaletteContext = Literal[
    "screen", "projector", "print",
    "cvd_deuteranopia", "cvd_protanopia", "cvd_tritanopia",
]
ClipPolicy = Literal["none", "clip_p99", "head_tail", "log"]

_CONTEXTS: tuple = (
    "screen", "projector", "print",
    "cvd_deuteranopia", "cvd_protanopia", "cvd_tritanopia",
)
_CLIP_POLICIES: tuple = ("none", "clip_p99", "head_tail", "log")
_DATA_KINDS: tuple = ("sequential", "diverging", "qualitative", "cyclic")

# 结构化模式（不是分布分级方法，但模板/调用方会作为 method 传入）。
# categorical 走 qualitative 色带族；lisa 的五色是制图学固定语义色，
# 色带裁决对它不适用（palette 置空并披露）。
_MODE_METHODS: tuple = ("categorical", "lisa")

# projector 环境光冲淡色彩，相邻类可分辨阈值比屏幕更严。
_PROJECTOR_DELTA_E_BONUS = 2.0
# 暗底判据（themes.cartographic.dark_interactive 知识：低亮度端不可辨）。
_DARK_BASEMAP_LUMINANCE = 0.3


# ── 输入 / 输出模型 ─────────────────────────────────────────────────────────


class SymbologyProfile(BaseModel):
    """数据与显示证据（纯数据，无 IO）。values 为已滤除非有限值的样本。"""

    values: List[float] = Field(default_factory=list)
    feature_density: Optional[float] = None   # 要素数 / 视口面积(千px²)
    basemap_luminance: float = 1.0            # 底图相对亮度 [0,1]
    data_kind: DataKind = "sequential"


class SymbologyIntent(BaseModel):
    """调用方意图：显式指定（requested，最高优先）与声明偏好（recommended，
    来自模板 payload / 模型库知识，可被强证据推翻）。"""

    context: PaletteContext = "screen"
    requested_method: Optional[str] = None
    requested_k: Optional[int] = None
    requested_palette: Optional[str] = None
    requested_clip: Optional[ClipPolicy] = None
    recommended_method: Optional[str] = None
    recommended_k: Optional[int] = None
    recommended_palette: Optional[str] = None
    recommended_classifiers: List[str] = Field(default_factory=list)
    origin: str = ""   # 偏好来源说明（如模板 id），进 reasons


class SymbologyConstraints(BaseModel):
    """裁决阈值（默认即门禁阈值；测试可通过实例化收紧/放宽）。"""

    min_k: int = 3
    max_k: int = 7
    # ΔE00=10：屏幕上全部 18 条 COLOR_PALETTES 色带在 k=5 中点采样下通过
    # （零默认行为变化），而 CVD 模拟下 ColorBrewer sequential 族（8-11）
    # 落选、感知均匀族入选——「CVD 优先」由阈值自然涌现而非特判。
    min_class_delta_e: float = 10.0
    min_gray_delta_l: float = 0.06      # print 灰度相邻 ΔL 下限（themes 同源）
    density_soft_cap: float = 4.0       # 要素密度 > 软上限 → k-1
    density_hard_cap: float = 15.0      # > 硬上限 → k-2
    small_n: int = 8                    # n < small_n → 证据不足
    extreme_span_ratio: float = 1e4     # max/min ≥ 此值（全正）→ log


class SymbologyDecision(BaseModel):
    """一次符号化裁决的一等工件（可序列化；rejected[] 是 09 线自愈的
    可替换动作清单：kind ∈ method/k/palette/clip）。"""

    method: str
    k: int
    palette: str = ""
    clip_policy: ClipPolicy = "none"
    context: PaletteContext = "screen"
    reasons: List[str] = Field(default_factory=list)
    rejected: List[Dict[str, str]] = Field(default_factory=list)
    confidence: float = 0.6
    source: str = "distribution"   # explicit | recommended | distribution | fallback
    low_confidence: bool = False
    clip_low: Optional[float] = None
    clip_high: Optional[float] = None
    n_clipped: int = 0

    def why(self) -> str:
        """裁决理由摘要（legend_spec v2 的 ``why`` 字段口径）。"""
        return "; ".join(self.reasons)

    def to_dict(self) -> Dict[str, object]:
        return self.model_dump()


def _reject(decision_rejected: List[Dict[str, str]], kind: str,
            value: object, reason: str) -> None:
    decision_rejected.append({"kind": kind, "value": str(value), "reason": reason})


def _normalize_choice_rejected(raw: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """把 choose_classification 的 {"method","reason"} 落选记录归一为
    SymbologyDecision 的 {"kind","value","reason"} 形状（09 线消费口径）。"""
    return [
        {"kind": "method", "value": str(r.get("method", "")),
         "reason": str(r.get("reason", ""))}
        for r in raw or []
    ]


# ── 分类法裁决（P1；复用 choose_classification，不重写算法）────────────────


def _known_classifier(m: Optional[str]) -> Optional[str]:
    if m and m in CLASSIFICATION_METHODS:
        return m
    return None


def _adjudicate_method(
    profile: SymbologyProfile,
    intent: SymbologyIntent,
    stats: Optional[DistributionStats],
    constraints: SymbologyConstraints,
    rejected: List[Dict[str, str]],
    reasons: List[str],
) -> tuple:
    """返回 (method, choice|None, source, confidence, low_confidence)。

    choice 是 choose_classification 的产物（authority/reasons 继续下传）；
    mode 方法（categorical/lisa）与低置信默认不经过它。
    """
    explicit = intent.requested_method
    if explicit in _MODE_METHODS:
        reasons.append(
            f"显式结构化模式 {explicit}（非分布分级；categorical 走定性色带族、"
            "lisa 用制图学固定语义色）"
        )
        return explicit, None, "explicit", 1.0, False
    if _known_classifier(explicit):
        choice = choose_classification(
            stats,  # type: ignore[arg-type]
            recommended=intent.recommended_classifiers or None,
            requested_method=explicit,
            requested_k=intent.requested_k,
        )
        reasons.extend(choice.reasons)
        return choice.method, choice, "explicit", 1.0, False

    # 低置信：证据不足（n<8 / 全等 / 无有限值）→ §0.4 固定默认。
    n_unique = len(set(profile.values))
    if stats is None or stats.n < constraints.small_n or n_unique <= 2:
        why = (
            f"数据分布证据不足（n={stats.n if stats else 0}, "
            f"唯一值={n_unique}）"
        )
        reasons.append(why + "——按 §0.4 默认 equal_interval + k=3（low_confidence）")
        return "equal_interval", None, "fallback", 0.35, True

    skew = _skew_ratio(stats)
    heavy = skew is not None and skew >= _HEAVY_TAIL_THRESHOLD
    tmpl = _known_classifier(intent.recommended_method)

    if heavy:
        # 重尾证据最强：把 head_tail 保证进候选池（池非空且无 head_tail 时
        # choose_classification 的重尾分支不会触发——见其实现）。
        pool = list(dict.fromkeys(
            ["head_tail", *(tmpl and [tmpl] or []), *intent.recommended_classifiers]
        ))
        choice = choose_classification(stats, recommended=pool, requested_k=intent.requested_k)
        reasons.extend(choice.reasons)
        if tmpl and tmpl != "head_tail":
            _reject(
                rejected, "method", tmpl,
                f"重尾分布证据（mean={stats.mean:.4g} ≥ 1.5×median）"
                f"推翻模板偏好 {tmpl}（CVD/重尾是无障碍与正确性硬约束）",
            )
        return choice.method, choice, "distribution", 0.85, False

    if tmpl:
        # 证据不反对 → 模板偏好获尊重（池仅含偏好，choose_classification 会
        # 选中它；近均匀时若偏好属 equal_interval/quantiles 同样按证据入选）。
        choice = choose_classification(
            stats, recommended=[tmpl], requested_k=intent.requested_k)
        reasons.append(
            f"分布证据不反对（skew={skew:.0%}），模板/模型偏好 {tmpl} 获尊重"
            + (f"（来源：{intent.origin}）" if intent.origin else "")
        )
        reasons.extend(choice.reasons)
        return choice.method, choice, "recommended", 0.7, False

    choice = choose_classification(
        stats,
        recommended=intent.recommended_classifiers or None,
        requested_k=intent.requested_k,
    )
    reasons.extend(choice.reasons)
    return choice.method, choice, "distribution", 0.6, False


# ── k 裁决（P2）──────────────────────────────────────────────────────────────


def _context_min_delta_e(context: PaletteContext, constraints: SymbologyConstraints) -> float:
    return constraints.min_class_delta_e + (
        _PROJECTOR_DELTA_E_BONUS if context == "projector" else 0.0
    )


def _context_separable(
    palette: str,
    k: int,
    context: PaletteContext,
    constraints: SymbologyConstraints,
) -> bool:
    """上下文可分辨校验：cvd_* 用模拟后 ΔE00；print 用灰度 ΔL；其余 ΔE00。"""
    colors = sample_ramp_colors(palette, k)
    if len(colors) < 2:
        return False
    if context == "print":
        ramp = print_desaturate(colors)
        sep = grayscale_ramp_separation(ramp)
        return sep is not None and sep >= constraints.min_gray_delta_l
    if context.startswith("cvd_"):
        sim = [simulate_cvd(c, context) for c in colors]
        if any(s is None for s in sim):
            return False
        return (min_adjacent_delta_e(sim) or 0.0) >= _context_min_delta_e(context, constraints)
    return (min_adjacent_delta_e(colors) or 0.0) >= _context_min_delta_e(context, constraints)


def _palette_max_k(
    palette: str,
    context: PaletteContext,
    constraints: SymbologyConstraints,
    k_hi: int,
) -> int:
    """色带在上下文门限内可分辨的最大类数（P2「由 min_adjacent_delta_e
    反推上限」）；从 k_hi 向下探测，[min_k, k_hi] 内无一通过返回 0。"""
    lo = max(3, constraints.min_k)
    for k in range(max(lo, min(k_hi, constraints.max_k)), lo - 1, -1):
        if _context_separable(palette, k, context, constraints):
            return k
    return 0


def _base_k(
    method: str,
    intent: SymbologyIntent,
) -> int:
    """k 裁决与色带校验共用的基准 k（显式 > 偏好 > 方法元数据缺省）。"""
    meta = CLASSIFICATION_METHODS.get(method)
    base = (
        intent.requested_k
        or intent.recommended_k
        or (meta.default_k if meta else 5)
        or 5
    )
    try:
        return int(base)
    except (TypeError, ValueError):
        return 5


def _adjudicate_k(
    method: str,
    profile: SymbologyProfile,
    intent: SymbologyIntent,
    palette: str,
    context: PaletteContext,
    constraints: SymbologyConstraints,
    low_confidence: bool,
    k_cap: Optional[int],
    rejected: List[Dict[str, str]],
    reasons: List[str],
) -> int:
    lo, hi = constraints.min_k, constraints.max_k
    base = _base_k(method, intent)
    if intent.requested_k is not None and intent.recommended_k is None:
        reasons.append(f"调用方显式 k={base}（边界与可分辨性校正见 rejected）")
    elif intent.recommended_k is not None and intent.requested_k is None:
        reasons.append(f"模板/模型偏好 k={base}" + (f"（{intent.origin}）" if intent.origin else ""))
    k = max(lo, min(hi, base))
    if k != base:
        _reject(rejected, "k", base, f"k 超出裁决边界 [{lo},{hi}]，校正为 {k}（沿用 ADR-0073 边界）")

    if method in _MODE_METHODS:
        # lisa 固定五类、categorical 的 k 是类别数上限——不做统计/可分辨下修。
        return k

    n_unique = len(set(profile.values))
    if low_confidence:
        if k != lo:
            _reject(rejected, "k", k, f"证据不足（n<8/全等）——k 固定为 {lo}")
        return lo
    if method != "head_tail" and n_unique - 1 < k:
        new_k = max(lo, n_unique - 1)
        if new_k < k:
            _reject(rejected, "k", k,
                    f"唯一值数 n_unique={n_unique} 不足以支撑 {k} 级，下修为 {new_k}")
            k = new_k
    density = profile.feature_density
    if density is not None and method != "head_tail":
        if density > constraints.density_hard_cap:
            _reject(rejected, "k", k,
                    f"屏幕要素密度 {density:.1f} > 硬上限 {constraints.density_hard_cap:.0f}——k-2 下修")
            k -= 2
        elif density > constraints.density_soft_cap:
            _reject(rejected, "k", k,
                    f"屏幕要素密度 {density:.1f} > 软上限 {constraints.density_soft_cap:.0f}——k-1 下修")
            k -= 1
        k = max(lo, k)
    # 色带可分辨上限（P2：由 min_adjacent_delta_e 反推）——head_tail 类数由
    # 数据决定、k 仅是请求上限，同样受此硬约束。
    if k_cap is not None and k > k_cap:
        _reject(rejected, "k", k,
                f"色带 {palette} 在 {context} 上下文最多可分辨 {k_cap} 级——k 下修")
        k = k_cap
    return max(lo, min(hi, k))


# ── 色带裁决（P3）───────────────────────────────────────────────────────────

# 各数据类型族的候选顺序：CVD 安全者在前（§0.4：CVD 优先于美观），
# 家族内先 ColorBrewer 惯例后长尾。RdYlGn 无条件垫底（colorblind_safe=False）。
_FAMILY_ORDER: Dict[str, List[str]] = {
    "sequential": ["YlOrRd", "Blues", "Greens", "Oranges", "Purples", "Reds",
                   "Viridis", "Magma", "Inferno", "Plasma"],
    "diverging": ["RdBu", "PuOr", "RdYlGn"],
    "qualitative": ["Set2", "Dark2", "Set1", "Pastel1"],
    # 仓内无专用 cyclic 色带：诚实降级到感知均匀族并披露（不虚构 cyclic 能力）。
    "cyclic": ["Viridis", "Magma", "Inferno", "Plasma"],
}


def _palette_exists(p: Optional[str]) -> Optional[str]:
    if p and (p in COLOR_PALETTES or p in NATIVE_HEATMAP_COLORS):
        return p
    return None


def _palette_kind_matches(palette: str, data_kind: DataKind) -> bool:
    meta = PALETTE_KINDS.get(palette)
    if meta is None:
        return True  # 热力族等注册表外语义：不参与族匹配筛选
    kind = meta.kind
    if data_kind in ("sequential", "cyclic"):
        return kind in ("sequential", "perceptual_uniform")
    if data_kind == "diverging":
        return kind == "diverging"
    return kind == "qualitative"


def _candidate_order(
    data_kind: DataKind,
    intent: SymbologyIntent,
    basemap_luminance: float,
    context: PaletteContext,
    reasons: List[str],
    rejected: List[Dict[str, str]],
) -> List[str]:
    family = list(_FAMILY_ORDER.get(data_kind, _FAMILY_ORDER["sequential"]))
    if data_kind == "cyclic":
        reasons.append(
            "仓内无专用 cyclic（周期）色带——诚实降级到感知均匀族并披露，"
            "不虚构周期映射能力"
        )
    explicit = _palette_exists(intent.requested_palette)
    recommended = _palette_exists(intent.recommended_palette)

    ordered: List[str] = []
    if explicit:
        ordered.append(explicit)
        reasons.append(f"显式色带 {explicit}（无障碍硬约束下仍接受上下文校验）")
    if recommended:
        # 模板审美是持久决策：推荐色带恒居次席（仅显式更高），族不匹配只
        # 披露不降序——除非 CVD/-print 硬约束，引擎不重排用户的配色选择。
        if recommended not in ordered:
            ordered.append(recommended)
        if _palette_kind_matches(recommended, data_kind):
            reasons.append(
                f"模板/模型推荐色带 {recommended}" + (f"（{intent.origin}）" if intent.origin else "")
            )
        else:
            reasons.append(
                f"推荐色带 {recommended} 与数据类型 {data_kind} 族不同——"
                "作为审美偏好保留（上下文校验不通过时才回落匹配族）"
            )
    for p in family:
        if p not in ordered:
            ordered.append(p)
    if basemap_luminance < _DARK_BASEMAP_LUMINANCE and not explicit:
        perceptual = [p for p in ordered if PALETTE_KINDS.get(p)
                      and PALETTE_KINDS[p].kind == "perceptual_uniform"]
        rest = [p for p in ordered if p not in perceptual]
        ordered = perceptual + rest
        reasons.append(
            "深色底图（亮度<0.3）：感知均匀族优先（低亮度端 ColorBrewer 色带不可辨，"
            "themes.dark_interactive 知识）"
        )
    if context.startswith("cvd_"):
        unsafe_tail = [p for p in ordered
                       if PALETTE_KINDS.get(p) and not PALETTE_KINDS[p].colorblind_safe]
        if unsafe_tail:
            for p in unsafe_tail:
                if p in (explicit, recommended):
                    _reject(rejected, "palette", p,
                            f"显式/推荐色带 {p} 非色盲安全（PALETTE_KINDS），CVD 上下文后置——"
                            "无障碍是硬约束（§0.4），仅当排序内无更优候选时才会启用")
            ordered = [p for p in ordered if p not in unsafe_tail] + unsafe_tail
            reasons.append("CVD 上下文：非色盲安全色带（PALETTE_KINDS 标记）后置")
    return ordered


def _adjudicate_palette(
    data_kind: DataKind,
    intent: SymbologyIntent,
    basemap_luminance: float,
    context: PaletteContext,
    k_probe: int,
    constraints: SymbologyConstraints,
    rejected: List[Dict[str, str]],
    reasons: List[str],
) -> tuple:
    """按偏好序选首个在上下文门限内可分辨的色带。

    返回 (palette, k_cap)：k_cap 是该色带可分辨的最大类数（None = 不设限，
    仅用于定性 print 兜底路径）。校验失败的候选逐条进 rejected。
    """
    if intent.requested_method == "lisa":
        reasons.append("lisa 模式使用制图学固定语义色（HH/LL/HL/LH/NS），色带裁决不适用")
        return "", None
    ordered = _candidate_order(data_kind, intent, basemap_luminance, context, reasons, rejected)
    for p in ordered:
        if p not in COLOR_PALETTES:
            # 热力族（透明首停靠点）在通用分级裁决里不作为 ramp 候选——
            # 其 CVD/print 校验由 heatmap_data 的 family 映射路径承担。
            _reject(rejected, "palette", p, "热力族色带不进入通用分级候选（透明首停靠点渲染范式）")
            continue
        k_cap = _palette_max_k(p, context, constraints, k_probe)
        if k_cap >= constraints.min_k:
            if k_cap < k_probe:
                reasons.append(
                    f"色带 {p} 在 {context} 上下文最多可分辨 {k_cap} 级"
                    f"（ΔE00/灰度ΔL 门限内），k 以此为上限"
                )
            return p, k_cap
        _reject(rejected, "palette", p,
                f"{context} 上下文下 {constraints.min_k}–{k_probe} 级均不可分辨"
                "（CVD 模拟 ΔE00 / 灰度 ΔL 低于门限）")
    # 兜底：非定性数据回感知均匀锚；定性（categorical）数据的灰度打印本就
    # 需形状/图案辅助（themes.print_paper 知识）——保留族内首选并披露，
    # 不虚构「Viridis 能印出可分辨类别」。
    if data_kind == "qualitative" and ordered:
        first = next((p for p in ordered if p in COLOR_PALETTES), None)
        if first:
            _reject(rejected, "palette", "qualitative_print",
                    "定性色带在灰度打印下不可分级（themes.print_paper）——"
                    f"保留族内首选 {first}，类别面打印应辅以形状/图案区分")
            return first, None
    _reject(rejected, "palette", "all_candidates",
            "全部候选在当前上下文不可分辨——回退感知均匀锚 Viridis 并降低置信")
    return "Viridis", _palette_max_k("Viridis", context, constraints, k_probe) or None


# ── 离群值与值域策略（P4）───────────────────────────────────────────────────


def _adjudicate_clip(
    profile: SymbologyProfile,
    stats: Optional[DistributionStats],
    method: str,
    intent: SymbologyIntent,
    constraints: SymbologyConstraints,
    rejected: List[Dict[str, str]],
    reasons: List[str],
) -> ClipPolicy:
    requested = intent.requested_clip
    if requested in _CLIP_POLICIES:
        reasons.append(f"显式离群值策略 {requested}（尊重显式）")
        return requested
    if intent.requested_method in _MODE_METHODS:
        return "none"
    if stats is None or not profile.values:
        return "none"
    if method == "head_tail":
        reasons.append("head_tail 分类天然以均值断裂吸收长尾——clip_policy=head_tail（legend 披露）")
        return "head_tail"
    vals = sorted(profile.values)
    vmax, vmin = vals[-1], vals[0]
    if vmin > 0 and vmax / vmin >= constraints.extreme_span_ratio:
        reasons.append(
            f"值域跨度 max/min={vmax / vmin:.0e} ≥ {constraints.extreme_span_ratio:.0e}"
            "且全正——clip_policy=log（log10 空间分级，breaks 回原域）"
        )
        return "log"
    p99 = float(_np_quantile(vals, 0.99))
    if vmax > 1.5 * p99 and (vmax - vmin) > 0:
        reasons.append(
            f"离群比 max={vmax:.4g} > 1.5×p99={p99:.4g}——clip_policy=clip_p99"
            "（默认 §0.4；legend 将明示截断条目）"
        )
        return "clip_p99"
    return "none"


def apply_clip(
    values: List[float], policy: ClipPolicy,
) -> tuple:
    """把 clip_policy 应用到分类输入（builder 的唯一裁剪执行点）。

    返回 (clipped_values, clip_low, clip_high, n_clipped)。仅 clip_p99 修改
    数值（上限截断）；head_tail/log/none 原样返回（log 的变换在分类空间
    由 builder 执行，不在此处）。
    """
    if policy != "clip_p99" or not values:
        return list(values), None, None, 0
    high = float(_np_quantile(values, 0.99))
    clipped = [min(v, high) for v in values]
    n_clipped = sum(1 for v in values if v > high)
    return clipped, None, (high if n_clipped else None), n_clipped


# ── 唯一裁决入口（P1）───────────────────────────────────────────────────────


def resolve_symbology(
    profile: SymbologyProfile,
    intent: Optional[SymbologyIntent] = None,
    constraints: Optional[SymbologyConstraints] = None,
) -> SymbologyDecision:
    """自适应符号化唯一裁决入口（纯函数）。

    五个入口（build_thematic_style / create_thematic_map / h3_binning /
    heatmap_data / apply_template）一律经此取 (method, k, palette,
    clip_policy)；入口不得自决，多入口结果不一致即缺陷。
    """
    intent = intent or SymbologyIntent()
    constraints = constraints or SymbologyConstraints()
    rejected: List[Dict[str, str]] = []
    reasons: List[str] = []
    if intent.context not in _CONTEXTS:
        # fail-closed：非法上下文按 screen 处置但必须留痕（禁止无声改数）。
        rejected.append({
            "kind": "context", "value": str(intent.context),
            "reason": f"未知上下文——按 screen 处置（合法值：{', '.join(_CONTEXTS)}）",
        })
        intent = intent.model_copy(update={"context": "screen"})
    data_kind = profile.data_kind if profile.data_kind in _DATA_KINDS else "sequential"

    stats = distribution_stats_from_values(profile.values)

    method, choice, source, confidence, low_confidence = _adjudicate_method(
        profile, intent, stats, constraints, rejected, reasons)
    if choice is not None and choice.rejected:
        rejected.extend(_normalize_choice_rejected(choice.rejected))

    if method == "categorical" and profile.data_kind == "sequential":
        # categorical 模式语义上就是定性数据——色带族随之切换。
        data_kind = "qualitative"

    probe_hi = max(constraints.min_k, min(_base_k(method, intent), constraints.max_k))
    palette, k_cap = _adjudicate_palette(
        data_kind, intent, profile.basemap_luminance, intent.context,
        probe_hi, constraints, rejected, reasons)

    k = _adjudicate_k(
        method, profile, intent, palette, intent.context,
        constraints, low_confidence, k_cap, rejected, reasons)

    clip_policy = _adjudicate_clip(
        profile, stats, method, intent, constraints, rejected, reasons)
    clip_low = clip_high = None
    n_clipped = 0
    if clip_policy == "clip_p99" and profile.values:
        _, clip_low, clip_high, n_clipped = apply_clip(profile.values, clip_policy)

    if low_confidence:
        reasons.append("low_confidence：证据不足的保守默认，拿到 ≥8 个有效样本后应重裁")

    return SymbologyDecision(
        method=method,
        k=k,
        palette=palette,
        clip_policy=clip_policy,
        context=intent.context,
        reasons=reasons,
        rejected=rejected,
        confidence=confidence,
        source=source,
        low_confidence=low_confidence,
        clip_low=clip_low,
        clip_high=clip_high,
        n_clipped=n_clipped,
    )


def symbology_decision_from_values(
    values: List[float],
    *,
    context: PaletteContext = "screen",
    data_kind: DataKind = "sequential",
    feature_density: Optional[float] = None,
    basemap_luminance: float = 1.0,
    requested_method: Optional[str] = None,
    requested_k: Optional[int] = None,
    requested_palette: Optional[str] = None,
    recommended_method: Optional[str] = None,
    recommended_k: Optional[int] = None,
    recommended_palette: Optional[str] = None,
    recommended_classifiers: Optional[List[str]] = None,
    origin: str = "",
) -> SymbologyDecision:
    """入口便捷封装：从原始值一步取裁决（五个接线点的共同形态）。"""
    return resolve_symbology(
        SymbologyProfile(
            values=[v for v in values if isinstance(v, (int, float))
                    and not isinstance(v, bool) and math.isfinite(v)],
            feature_density=feature_density,
            basemap_luminance=basemap_luminance,
            data_kind=data_kind,
        ),
        SymbologyIntent(
            context=context,
            requested_method=requested_method,
            requested_k=requested_k,
            requested_palette=requested_palette,
            recommended_method=recommended_method,
            recommended_k=recommended_k,
            recommended_palette=recommended_palette,
            recommended_classifiers=recommended_classifiers or [],
            origin=origin,
        ),
    )


__all__ = [
    "DataKind", "PaletteContext", "ClipPolicy",
    "SymbologyProfile", "SymbologyIntent", "SymbologyConstraints",
    "SymbologyDecision", "resolve_symbology", "symbology_decision_from_values",
    "apply_clip",
]
