"""G41 不确定度量资格、来源与可靠性视觉映射（WP06 / WP07）。

科学底线（逐条对应 B18–B26）：

* SE / 区间与估计同单位；variance 是**平方单位**，不自动开方成 SD；负值/
  low>high 是无效证据而不是「稍微不可靠」。
* 区间 level 必须有合法来源且 0<level<1，不自动填 95%；CI 与 PI 分开命名；
  区间**不强制**包含当前估计、不机械居中（由来源模型合同决定）。
* ``author_declared`` 明确标记为「用户声明」，不是独立验证的统计推断；
  ``analysis_evidence`` 必须给可复核 ref。
* 只有 estimate / sample_count / p 值 / 模型得分 / 分类概率 → 不合成误差或
  统计信心，返回 ``NO_UNCERTAINTY_EVIDENCE`` 与真正可行的下一步。
* 误差缺失/无效/越界各有独立状态与视觉输出，不当 0、不当最高可靠性。
* min/max 归一只是视觉映射，不叫 95% 置信度；不重复乘 alpha。
* 两源误差表行序相同但无稳定对应：本任务只接受**同一冻结源同一行**的
  已对齐字段，绝不按位置默 join（B26）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.lib.cartography.palettes import COLOR_PALETTES, resolve_palette_colors
from app.services.bivariate_reliability_authoring.classify import (
    assign_axis,
    axis_intervals,
    equal_interval,
    quantiles,
)
from app.services.bivariate_reliability_authoring.contract import (
    ALGORITHM_VERSION,
    ReliabilitySpec,
)
from app.services.bivariate_reliability_authoring.errors import AuthoringError
from app.services.bivariate_reliability_authoring.stats import (
    ReliabilityScan,
    _measure_magnitude,
    admit,
)

#: 第二通道派生桶字段（独立命名，不覆盖任何用户字段）。
RELIABILITY_BUCKET_FIELD = "__g41_rel_bucket"

_UNKNOWN = "unknown"
_INVALID = "invalid"
_OUT_OF_DOMAIN = "out_of_domain"


@dataclass
class ReliabilityResult:
    """冻结代次上的估计表达 + 第二通道表达 + 派生桶。"""

    estimate_legend: Dict[str, Any]
    bucket_paint: Dict[str, Any]
    bucket_by_feature: List[str]
    buckets: List[Dict[str, Any]]
    measure_meta: Dict[str, Any]
    scan: ReliabilityScan
    estimate_breaks: List[float]
    estimate_intervals: List[Dict[str, Any]]
    disclosures: List[str] = field(default_factory=list)
    bucket_field: str = RELIABILITY_BUCKET_FIELD


def _estimate_breaks(values: List[float], spec: ReliabilitySpec) -> List[float]:
    fn = quantiles if spec.estimate_method == "quantiles" else equal_interval
    internal = fn(values, spec.estimate_k)
    return internal


def _palette_for(spec: ReliabilitySpec, classes: int) -> List[str]:
    if spec.estimate_palette not in COLOR_PALETTES:
        raise AuthoringError(
            "UNKNOWN_PALETTE",
            f"未知色带 {spec.estimate_palette!r}", field="estimate_palette")
    ramp = resolve_palette_colors(spec.estimate_palette)
    if len(ramp) >= classes:
        return list(ramp[:classes])
    # 有界插值补齐（不伪造数据，只补颜色），最多 7 类。
    return [ramp[min(len(ramp) - 1, round(i * (len(ramp) - 1) / max(1, classes - 1)))]
            for i in range(classes)]


def _measure_unit_note(spec: ReliabilitySpec) -> str:
    m = spec.measure
    if m.kind == "variance":
        return f"方差（平方单位：{m.unit}）；不是标准误/标准差，未做开方换算"
    if m.kind == "standard_error":
        return f"标准误（{m.unit or '单位未声明'}）；与估计值同量纲"
    if m.kind == "confidence_interval":
        return f"置信区间 CI，level={m.level:g}（{m.unit or '单位未声明'}）；宽度 = high − low"
    return f"预测区间 PI，level={m.level:g}（{m.unit or '单位未声明'}）；宽度 = high − low"


def _visual_meaning(spec: ReliabilitySpec) -> str:
    v = spec.visual
    channel = "透明度" if v.channel == "opacity" else "描边宽度"
    if v.direction == "higher_less_reliable":
        tail = ("误差越大越透明（alpha 越低）" if v.channel == "opacity"
                else "误差越大描边越细")
    else:
        tail = ("误差越大越不透明（alpha 越高）" if v.channel == "opacity"
                else "误差越大描边越宽")
    return f"{channel}：{tail}；{v.unknown_label}使用独立输出，不代表零误差或最高可靠性"


def compute_reliability(
    features: List[Dict[str, Any]], spec: ReliabilitySpec,
) -> ReliabilityResult:
    """同一冻结源、同一行上的估计 + 有来源误差 → 双通道表达。"""
    m = spec.measure
    v = spec.visual
    disclosures: List[str] = []

    # ── 0. 声明而非推断：必须说明方法/来源，不得靠字段名或常数合成信心 ──
    if m.source == "author_declared":
        if not (m.method_note or m.evidence_note):
            raise AuthoringError(
                "AUTHOR_DECLARATION_UNEXPLAINED",
                "author_declared 必须给出 method_note / evidence_note 说明"
                "该误差字段的来源与含义；不能只凭字段名把它当统计推断",
                field="measure.method_note",
                correction_hint="写明误差由哪个模型/测量流程产生，或改用 analysis_evidence + evidence_ref。")
        disclosures.append(
            "误差度量来源 = 作者声明（author_declared），未经本系统独立验证的统计推断；"
            "样本量、p 值、模型得分、分类概率均不能冒充标准误")
    else:
        disclosures.append(
            f"误差度量来源 = 已有分析 uncertainty evidence（{m.evidence_ref}）；"
            "本服务不重算置信区间、不改写来源模型合同")

    measure_fields = [f for f in (m.value_field, m.low_field, m.high_field) if f]
    seen_measure_field = 0
    for feat in features:
        props = feat.get("properties")
        if not isinstance(props, dict):
            continue
        if any(f in props for f in measure_fields):
            seen_measure_field += 1
    if seen_measure_field == 0:
        raise AuthoringError(
            "NO_UNCERTAINTY_EVIDENCE",
            "冻结源中不存在所声明的误差/区间字段——只有估计值不能合成"
            "误差、置信水平或统计信心（sample_count / p 值 / 模型得分不是误差量）",
            status_code=409,
            correction_hint=(
                "可行下一步：① 提供逐要素非负标准误或方差字段；② 提供 CI/PI "
                "的 low/high 字段 + 合法 level；③ 指向已有分析结果的 "
                "uncertainty evidence ref；④ 改用双变量矩阵模式（不需要误差事实）。"),
            detail={"measure_fields": measure_fields})
    disclosures.append(
        "误差字段必须来自同一已授权冻结源、与估计值同一行；"
        "不接受按行序拼接外部误差表（无法证明逐行对应）")

    # ── 1. 估计值主色（严格数值准入，同一冻结总体）──
    est_values: List[float] = []
    est_by_feature: List[Optional[float]] = [None] * len(features)
    for i, feat in enumerate(features):
        props = feat.get("properties") or {}
        ok, value, _ = admit(props.get(spec.estimate_field))
        if ok:
            est_by_feature[i] = float(value)
            est_values.append(float(value))
    if len(est_values) < 2:
        raise AuthoringError(
            "INSUFFICIENT_ESTIMATE_VALUES",
            f"估计字段只有 {len(est_values)} 个有效有限数值，无法建立主色表达",
            status_code=409, field="estimate_field")
    internal = _estimate_breaks(est_values, spec)
    lo, hi = min(est_values), max(est_values)
    full_breaks = [lo, *internal, hi]
    classes = len(internal) + 1
    palette_colors = _palette_for(spec, classes)
    if classes < spec.estimate_k:
        disclosures.append(
            f"估计主色请求 {spec.estimate_k} 级，实际 {classes} 级"
            "（常数段/重复分位断点去重；如实少分级）")
    estimate_legend: Dict[str, Any] = {
        "type": "graduated",
        "field": spec.estimate_field,
        "method": spec.estimate_method,
        "k": classes,
        "breaks": [round(b, 6) for b in full_breaks],
        "palette": spec.estimate_palette,
        "palette_colors": palette_colors,
        "unit": spec.estimate_unit or None,
        "title": spec.estimate_label or spec.estimate_field,
        "nodata": {"color": "#cccccc", "label": spec.missing_label},
        "labels": [f"{i+1}" for i in range(classes)],
    }
    estimate_intervals = axis_intervals(internal, spec.boundary_rule,
                                        spec.estimate_unit)

    # ── 2. 逐要素误差量（含零、无效、区间非对称）──
    scan = ReliabilityScan(total_features=len(features))
    magnitudes: List[Optional[float]] = []
    states: List[str] = []
    extras: List[Dict[str, Any]] = []
    for i, feat in enumerate(features):
        props = feat.get("properties") or {}
        ok_est, est, _ = admit(props.get(spec.estimate_field))
        if ok_est:
            scan.valid_estimate += 1
        mag, state, kind, extra = _measure_magnitude(props, spec)
        scan.kind_counts[kind] = scan.kind_counts.get(kind, 0) + 1
        if state == "invalid":
            scan.invalid_measure += 1
        elif state == "unknown":
            scan.unknown_measure += 1
        else:
            scan.valid_measure += 1
            if mag is not None and abs(mag) < 1e-12:
                scan.zero_measure += 1
            if extra.get("noncentered"):
                scan.noncentered_measure += 1
        magnitudes.append(mag)
        states.append(state)
        extras.append(extra)
    scan.magnitudes = magnitudes
    scan.states = states
    scan.estimate_values = est_by_feature

    if scan.invalid_measure:
        detail_kinds = {k: c for k, c in scan.kind_counts.items()
                        if k in ("negative_measure", "low_gt_high",
                                 "estimate_outside_interval",
                                 "interval_not_centered")}
        raise AuthoringError(
            "INVALID_MEASURE_VALUES",
            f"{scan.invalid_measure} 个要素的误差证据无效（{detail_kinds}）——"
            "负方差/负标准误/low>high/违反来源模型中心性合同 不是『稍微不可靠』，"
            "绝不自动排序、不当 SD、不静默接受",
            status_code=422, field="measure",
            correction_hint="修正来源数据或改用有合法来源的 CI/PI 字段。")
    if scan.valid_measure == 0:
        raise AuthoringError(
            "NO_UNCERTAINTY_EVIDENCE",
            "误差字段存在但没有任何有效数值——无法建立可靠性表达；"
            "缺失不会自动当作 0 误差或最高可靠性",
            status_code=409, field="measure",
            correction_hint="提供真实非负误差/区间数值，或改用双变量矩阵模式。")

    if scan.noncentered_measure:
        disclosures.append(
            f"{scan.noncentered_measure} 个要素的不确定度区间不含估计值或明显非对称"
            "——按来源模型合同如实保留，不机械强制居中或裁剪")

    # ── 3. 有界视觉桶（归一化只用于视觉映射）──
    span = v.max_value - v.min_value
    bucket_count = v.buckets
    outputs: List[float] = []
    for i in range(bucket_count):
        t = i / max(1, bucket_count - 1)
        if v.direction == "higher_less_reliable":
            outputs.append(round(v.max_output - t * (v.max_output - v.min_output), 6))
        else:
            outputs.append(round(v.min_output + t * (v.max_output - v.min_output), 6))
    if any(abs(v.unknown_output - o) < 1e-9 for o in outputs):
        raise AuthoringError(
            "UNKNOWN_OUTPUT_NOT_DISTINCT",
            "unknown_output 与有效桶输出重合——误差缺失必须独立可辨，"
            "不能悄悄落到某个可靠性等级", field="visual.unknown_output")

    buckets: List[Dict[str, Any]] = []
    for i, out in enumerate(outputs):
        t0 = i / bucket_count
        t1 = (i + 1) / bucket_count
        buckets.append({
            "key": f"b{i}",
            "index": i,
            "lo": round(v.min_value + t0 * span, 6),
            "hi": round(v.min_value + t1 * span, 6),
            "output": out,
            "count": 0,
            "label": f"误差 {v.min_value + t0 * span:g}–{v.min_value + t1 * span:g}"
                     f" {m.unit or '(单位未声明)'}",
        })
    # 越界输出可由作者单独声明；未声明则复用中性 unknown 输出，并在图例
    # 明确披露「共用同一输出、仅计数/图例分列」——不假装图面可分辨（复审 P2）。
    out_of_domain_output = (v.out_of_domain_output
                            if v.out_of_domain_output is not None
                            else v.unknown_output)
    special = {
        _UNKNOWN: {"key": _UNKNOWN, "label": v.unknown_label,
                   "output": v.unknown_output, "count": 0},
        _OUT_OF_DOMAIN: {"key": _OUT_OF_DOMAIN,
                         "label": "误差超出映射值域（已按 clip 政策处理）",
                         "output": out_of_domain_output, "count": 0},
    }

    bucket_by_feature: List[str] = []
    for i, state in enumerate(states):
        mag = magnitudes[i]
        if state != "valid" or mag is None:
            bucket_by_feature.append(_UNKNOWN)
            special[_UNKNOWN]["count"] += 1
            continue
        if mag < v.min_value or mag > v.max_value:
            if v.clip == "reject":
                raise AuthoringError(
                    "VISUAL_DOMAIN_VIOLATION",
                    f"要素误差量 {mag:g} 超出声明值域 "
                    f"[{v.min_value:g},{v.max_value:g}]，clip=reject 明确拒绝；"
                    "不静默截断后称完整",
                    status_code=422, field="visual.min_value",
                    correction_hint="扩大映射值域以覆盖真实误差量，或改用 clamp 并接受披露。")
            scan.clipped += 1
            special[_OUT_OF_DOMAIN]["count"] += 1
            # 越界误差不能悄悄折进某个有效可靠性等级（那会把「未知可靠度」
            # 画成有等级的结论）。使用独立越界桶 + 独立视觉输出并披露。
            bucket_by_feature.append(_OUT_OF_DOMAIN)
            continue
        t = (mag - v.min_value) / span
        idx = min(bucket_count - 1, int(t * bucket_count))
        bucket_by_feature.append(f"b{idx}")
        buckets[idx]["count"] += 1
    if scan.clipped:
        disclosures.append(
            f"{scan.clipped} 个要素误差量超出映射值域，按 clip=clamp 使用独立的"
            "『越界/未知』视觉桶（不折叠进任何有效可靠性等级），并在图例披露；"
            "归一化结果不是置信度")
    if abs(out_of_domain_output - v.unknown_output) < 1e-12:
        disclosures.append(
            f"越界与缺失共用中性输出 {out_of_domain_output:g}（图面不可分辨，"
            "仅在桶计数与图例分列）；需区分请显式设 visual.out_of_domain_output")

    cases: List[List[Any]] = [[b["key"], b["output"]] for b in buckets]
    cases.append([_UNKNOWN, v.unknown_output])
    cases.append([_OUT_OF_DOMAIN, out_of_domain_output])
    bucket_paint: Dict[str, Any] = {
        "method": "match",
        "field": RELIABILITY_BUCKET_FIELD,
        "cases": cases,
        "default": v.unknown_output,
    }

    measure_meta = {
        "kind": m.kind,
        "unit": m.unit or None,
        "unit_declared": bool(m.unit),
        "level": m.level,
        "interval_kind": "CI" if m.kind == "confidence_interval" else (
            "PI" if m.kind == "prediction_interval" else None),
        "value_field": m.value_field,
        "low_field": m.low_field,
        "high_field": m.high_field,
        "source": m.source,
        "evidence_ref": m.evidence_ref,
        "evidence_note": m.evidence_note or None,
        "method_note": m.method_note or None,
        "author_declared": m.source == "author_declared",
        "allow_noncentered": m.allow_noncentered,
        "require_interval_contains_estimate": m.require_interval_contains_estimate,
        "unit_note": _measure_unit_note(spec),
        "visual_meaning": _visual_meaning(spec),
        "algorithm_version": ALGORITHM_VERSION,
    }
    disclosures.append(
        "第二通道是离散有界视觉映射；min/max 归一结果不是 95% 置信度，"
        "也不是统计显著性；不重复乘背景 alpha")

    return ReliabilityResult(
        estimate_legend=estimate_legend,
        bucket_paint=bucket_paint,
        bucket_by_feature=bucket_by_feature,
        buckets=buckets + [special[_UNKNOWN], special[_OUT_OF_DOMAIN]],
        measure_meta=measure_meta,
        scan=scan,
        estimate_breaks=[round(b, 6) for b in full_breaks],
        estimate_intervals=estimate_intervals,
        disclosures=disclosures,
    )
