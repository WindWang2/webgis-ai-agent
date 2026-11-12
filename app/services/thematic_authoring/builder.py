"""G33 候选表达组装（WP04/05/06）——plan + 同版本扫描 → 候选 legend_spec +
paint 投影 + 类计数表。预览与提交共用本模块：同一输入必产出同一候选
（确定性），预览零 mutation，提交只是同一候选的持久化。
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field as dc_field
from typing import Any, Dict, List, Optional, Sequence

from app.lib.cartography.thematic_spec import (
    CATEGORICAL,
    DIVERGENT,
    GRADUATED,
    build_categorical_spec,
    build_continuous_spec,
    build_divergent_spec,
    build_graduated_spec,
    build_graduated_spec_from_breaks,
    spec_to_paint,
)
from app.services.thematic_authoring import stats as stats_mod
from app.services.thematic_authoring.contract import (
    SizeChannel,
    ThematicAuthoringPlan,
)
from app.services.thematic_authoring.errors import ThematicAuthoringError
from app.services.thematic_authoring.stats import FieldScan

logger = logging.getLogger(__name__)

_AUTHOR_WHY = "G33 专题表达工作台作者决定"


@dataclass
class CandidateExpression:
    """候选表达（legend_spec + paint 投影 + 类表 + 披露）。"""

    legend_spec: Dict[str, Any]
    paint_color: Optional[Dict[str, Any]]
    size_paint: Optional[Dict[str, Any]]     # {"radius"|"width": StyleMethod}
    class_table: List[Dict[str, Any]] = dc_field(default_factory=list)
    disclosures: List[str] = dc_field(default_factory=list)
    requested_k: Optional[int] = None
    actual_k: Optional[int] = None

    def receipt(self) -> Dict[str, Any]:
        return {
            "legend_spec": self.legend_spec,
            "paint": self.paint_color,
            "size_paint": self.size_paint,
            "class_table": self.class_table,
            "disclosures": self.disclosures,
            "requested_k": self.requested_k,
            "actual_k": self.actual_k,
        }


def _sampled_features(scan: FieldScan) -> List[Dict[str, Any]]:
    """扫描值样本 → 最小 FeatureCollection（供 build_graduated_spec 单一
    分类权威使用；approximate 时天然只分类采样，口径由 basis 披露）。"""
    return [{"properties": {scan.field: v}} for v in scan.values]


def _apply_visual_overrides(
    spec: Dict[str, Any],
    plan: ThematicAuthoringPlan,
    n_classes: int,
) -> None:
    """类级覆盖（labels/class_colors/invert）就地应用到已构建 spec。"""
    if plan.invert_palette and isinstance(spec.get("palette_colors"), list):
        spec["palette_colors"] = list(reversed(spec["palette_colors"]))
    if plan.class_colors:
        colors = spec.get("palette_colors")
        if isinstance(colors, list):
            for i, c in enumerate(plan.class_colors):
                if i < len(colors):
                    colors[i] = c
    if plan.labels:
        labels = spec.get("labels")
        if isinstance(labels, list):
            for i, lab in enumerate(plan.labels):
                if i < len(labels):
                    labels[i] = lab
        else:
            spec["labels"] = list(plan.labels)[:n_classes]
    if plan.nodata is not None:
        spec["nodata"] = {"color": plan.nodata.color, "label": plan.nodata.label}
        spec["nodata_label"] = plan.nodata.label
    if plan.unit is not None:
        spec["unit"] = plan.unit
    if plan.title is not None:
        spec["title"] = plan.title


def _require_field(scan: FieldScan, plan: ThematicAuthoringPlan, current_field: Optional[str]) -> None:
    field = plan.field or current_field
    if not field:
        raise ThematicAuthoringError("MISSING_FIELD", "计划未提供目标字段且层无当前专题字段", field="field")


def _check_type_conversion(current_spec: Optional[Dict[str, Any]], plan: ThematicAuthoringPlan, scan: FieldScan) -> None:
    """跨类型转换适用性检查（WP05）：文本为主的既有类别表达（如 LISA 固定
    科学类别）不得静默转成数值等距分级；数值字段转类别需作者显式给出表。"""
    if not current_spec:
        return
    cur_type = current_spec.get("type")
    if cur_type == CATEGORICAL and plan.mode in ("graduated_auto", "graduated_manual", "continuous", "divergent"):
        valid = scan.scanned
        total_seen = valid + sum(scan.invalid.values())
        if total_seen == 0 or valid / max(total_seen, 1) < 0.5:
            raise ThematicAuthoringError(
                "CANNOT_CONVERT_TYPE",
                f"字段 {scan.field!r} 以文本/缺失值为主（有效数值 {valid}），"
                "不能从类别表达转为数值分级；如需编辑类别请用 categorical 模式",
                field=plan.field or scan.field,
                correction_hint="文本类别字段保持 categorical 模式编辑显示名/颜色/顺序。",
            )


def size_style_method(ch: SizeChannel) -> Dict[str, Any]:
    """比例符号/线宽映射 → paint StyleMethod（WP06）。

    - linear：尺寸线性于数值（半径线性 —— 视觉强调 ∝ v²，需在 UI 披露）。
    - area_sqrt（仅 radius）：stops 携带 r²（面积线性于数值，等值差→等面积
      差），输出 √ —— 与 compileStyleMethod/_resolve_paint_value/图例样本
      同一函数（size_for_value）。
    """
    if ch.scaling == "area_sqrt":
        stops = [
            [float(ch.min_value), float(ch.min_size) ** 2],
            [float(ch.max_value), float(ch.max_size) ** 2],
        ]
        return {
            "method": "interpolate", "field": ch.field, "stops": stops,
            "output_transform": "sqrt",
        }
    return {
        "method": "interpolate", "field": ch.field,
        "stops": [[float(ch.min_value), float(ch.min_size)], [float(ch.max_value), float(ch.max_size)]],
    }


def size_for_value(value: float, ch: SizeChannel) -> Optional[float]:
    """图例/预览样本的同一尺寸函数（与 paint 表达式逐点一致）。

    值域外钳到端点（与 interpolate 端点钳制一致）；非法输入返回 None。
    """
    if not math.isfinite(value):
        return None
    if ch.scaling == "area_sqrt":
        area = ch.min_size ** 2 + (value - ch.min_value) * (ch.max_size ** 2 - ch.min_size ** 2) / (ch.max_value - ch.min_value)
        area = min(max(area, ch.min_size ** 2), ch.max_size ** 2)
        return math.sqrt(area)
    t = (value - ch.min_value) / (ch.max_value - ch.min_value)
    t = min(max(t, 0.0), 1.0)
    return ch.min_size + t * (ch.max_size - ch.min_size)


def size_samples(ch: SizeChannel) -> List[Dict[str, Any]]:
    """图例尺寸样本（min/mid/max 三点，同一函数生成）。"""
    mid = (ch.min_value + ch.max_value) / 2.0
    out = []
    for label, v in (("min", ch.min_value), ("mid", mid), ("max", ch.max_value)):
        s = size_for_value(v, ch)
        if s is not None:
            out.append({"anchor": label, "value": v, "size": round(s, 3)})
    return out


def build_candidate(
    plan: ThematicAuthoringPlan,
    scan: FieldScan,
    *,
    current_spec: Optional[Dict[str, Any]],
    current_field: Optional[str] = None,
) -> CandidateExpression:
    """计划 + 扫描 → 候选表达。所有拒绝都带稳定错误码与定位。"""
    mode = plan.mode
    field = plan.field or current_field or (current_spec or {}).get("field")
    if not field:
        raise ThematicAuthoringError(
            "MISSING_FIELD", "无法确定目标字段（计划与层均缺失）", field="field")
    if scan.field != field:
        raise ThematicAuthoringError(
            "FIELD_MISMATCH",
            f"统计扫描字段 {scan.field!r} ≠ 计划字段 {field!r}（内部一致性问题）",
        )
    _check_type_conversion(current_spec, plan, scan)

    disclosures: List[str] = []
    candidate: Optional[Dict[str, Any]] = None
    requested_k: Optional[int] = None
    actual_k: Optional[int] = None
    class_table: List[Dict[str, Any]] = []

    if mode in ("graduated_auto", "graduated_manual", "revert_auto"):
        if scan.scanned == 0:
            raise ThematicAuthoringError(
                "NO_NUMERIC_DATA",
                f"字段 {field!r} 没有有效数值（无效/缺失 {sum(scan.invalid.values())} 项）",
                field=field,
                correction_hint="检查字段类型或改用 categorical 模式。",
            )
        if mode in ("graduated_auto", "revert_auto"):
            requested_k = plan.k
            features = _sampled_features(scan)
            fc = {"type": "FeatureCollection", "features": features}
            if mode == "revert_auto":
                # 恢复自动：清除作者覆盖，全部交给既有符号化引擎裁决
                candidate = build_graduated_spec(fc, field=field, method=None, k=None, palette=None)
            else:
                candidate = build_graduated_spec(
                    fc, field=field, method=plan.method, k=plan.k, palette=plan.palette,
                )
            if candidate is None:
                if scan.scanned == 1:
                    only = scan.values[0]
                    candidate = build_graduated_spec_from_breaks(
                        field, [only, only], plan.palette or "YlOrRd",
                        labels=["全部相同值"], method="auto_degenerate",
                        why="单值退化（全部特征同值）",
                    )
                else:
                    raise ThematicAuthoringError(
                        "CLASSIFICATION_FAILED",
                        f"字段 {field!r} 数值不足以分级（有效值 {scan.scanned} 个）",
                        field=field,
                    )
        else:  # graduated_manual
            breaks = [float(b) for b in (plan.breaks or [])]
            requested_k = len(breaks) - 1
            candidate = build_graduated_spec_from_breaks(
                field, breaks, plan.palette or "YlOrRd",
                min_val=breaks[0], max_val=breaks[-1],
                labels=plan.labels, class_colors=plan.class_colors,
                invert=plan.invert_palette,
                unit=plan.unit, title=plan.title,
                method="manual", why=_AUTHOR_WHY,
            )
            if candidate is None:
                raise ThematicAuthoringError(
                    "INVALID_BREAKS", "人工断点构建失败（内部校验未通过）", field="breaks")
            if plan.nodata is not None:
                candidate["nodata"] = {"color": plan.nodata.color, "label": plan.nodata.label}
                candidate["nodata_label"] = plan.nodata.label
        if candidate is None:
            raise ThematicAuthoringError("CLASSIFICATION_FAILED", "分级构建失败", field=field)
        if mode in ("graduated_auto",):
            _apply_visual_overrides(candidate, plan, len(candidate.get("breaks", [])) - 1)
        breaks = candidate.get("breaks", [])
        actual_k = len(breaks) - 1
        if requested_k is not None and actual_k != requested_k:
            disclosures.append(
                f"请求 {requested_k} 类，实际 {actual_k} 类（重复值/分布退化导致断点去重——"
                "真实类数诚实保留，不伪造空数值范围）")
        counts = stats_mod.class_counts(scan, breaks)
        colors = candidate.get("palette_colors", [])
        labels = candidate.get("labels", [])
        for i in range(actual_k):
            class_table.append({
                "index": i,
                "range": [breaks[i], breaks[i + 1]] if i + 1 < len(breaks) else [breaks[i], None],
                "color": colors[i] if i < len(colors) else None,
                "label": labels[i] if i < len(labels) else None,
                "count": counts["counts"][i] if i < len(counts["counts"]) else None,
            })
        if counts["under_domain"]:
            disclosures.append(
                f"{counts['under_domain']} 个值低于首断点 {breaks[0]!r}——step 语义下画为第一类颜色（值域下溢已披露）")
        if counts["over_domain"]:
            disclosures.append(
                f"{counts['over_domain']} 个值高于末断点 {breaks[-1]!r}——step 语义下画为最末类颜色（值域上溢已披露）")
        if scan.invalid["null"] or scan.invalid["missing"]:
            disclosures.append(
                f"{scan.invalid['null'] + scan.invalid['missing']} 个 null/缺失值按 nodata 色渲染（零是有效值，不计入）")

    elif mode == "categorical":
        # 歧义键拒绝：同一文本键来自混合原始类型（数值 1 与文本 "1"）——
        # 当前合同无法区分，必须显式消除歧义而非偷偷合并（§三.3/验收 9）。
        authored_keys = {c.key for c in (plan.categories or [])}
        ambiguous = [k for k in scan.ambiguous_keys if k in authored_keys]
        if ambiguous:
            raise ThematicAuthoringError(
                "AMBIGUOUS_CATEGORY_KEY",
                f"类别键 {ambiguous[:3]} 同时来自数值与文本值——类型语义不可区分，拒绝编辑",
                field="categories",
                correction_hint="先在数据侧统一该字段类型，或仅保留单一类型的类别键。",
            )
        cats = []
        for c in plan.categories or []:
            key_type = scan.key_kinds.get(c.key)
            entry: Dict[str, Any] = {"key": c.key, "color": c.color, "label": c.label or c.key}
            if key_type in ("number", "bool"):
                entry["key_type"] = key_type
            cats.append(entry)
        candidate = build_categorical_spec(
            field, cats, palette=plan.palette,
            default=(plan.category_other.model_dump() if plan.category_other else None),
        )
        if candidate is None:
            raise ThematicAuthoringError("EMPTY_CATEGORIES", "类别表构建失败", field="categories")
        if plan.unit is not None:
            candidate["unit"] = plan.unit
        if plan.title is not None:
            candidate["title"] = plan.title
        if plan.nodata is not None:
            candidate["nodata"] = {"color": plan.nodata.color, "label": plan.nodata.label}
            candidate["nodata_label"] = plan.nodata.label
        actual_k = len(cats)
        counts = stats_mod.category_match_counts(scan, cats)
        for i, c in enumerate(cats):
            hit = next((x for x in counts["counts"] if x["key"] == c["key"]), None)
            class_table.append({
                "index": i, "key": c["key"], "key_type": c.get("key_type", "string"),
                "color": c["color"], "label": c["label"],
                "count": hit["count"] if hit else 0,
            })
        if plan.category_other is not None:
            class_table.append({
                "index": actual_k, "key": "__other__", "key_type": "other",
                "color": plan.category_other.color, "label": plan.category_other.label or "Other",
                "count": counts["other_count"],
            })
            actual_k += 1
        else:
            if counts["other_count"]:
                disclosures.append(
                    f"{counts['other_count']} 个值未列出——按现有合同渲染为最后一类颜色 "
                    f"（{cats[-1]['key']!r}）；如需显式 other 桶请设置 category_other")
        if counts["missing_or_null"]:
            disclosures.append(f"{counts['missing_or_null']} 个 null/缺失值渲染为 paint default 色")
        unknown_keys = [c["key"] for c in cats if c["key"] not in scan.category_counts]
        if unknown_keys:
            disclosures.append(
                f"类别键 {unknown_keys[:3]} 未在源中找到（计数 0）——保留键以支持后续数据更新")

    elif mode == "continuous":
        vals = scan.values
        if not vals:
            raise ThematicAuthoringError(
                "NO_NUMERIC_DATA", f"字段 {field!r} 没有有效数值，连续色域不可用", field=field)
        palette = plan.palette or "Viridis"
        candidate = build_continuous_spec(
            min(vals), max(vals), palette, field=field,
        )
        if candidate is None:
            raise ThematicAuthoringError("CONTINUOUS_BUILD_FAILED", "连续色域构建失败", field=field)
        if plan.invert_palette:
            candidate["palette_colors"] = list(reversed(candidate["palette_colors"]))
        if plan.nodata is not None:
            candidate["nodata"] = {"color": plan.nodata.color, "label": plan.nodata.label}
            candidate["nodata_label"] = plan.nodata.label
        if plan.unit is not None:
            candidate["unit"] = plan.unit
        if plan.title is not None:
            candidate["title"] = plan.title
        actual_k = None
        disclosures.append(
            f"连续色域 [{min(vals):g}, {max(vals):g}]——无类表；图例为色带（端点钳制）")

    elif mode == "divergent":
        vals = scan.values
        center = plan.divergent_center
        if not vals or center is None or not math.isfinite(center):
            raise ThematicAuthoringError(
                "MISSING_DIVERGENT_CENTER", "发散中心缺失或非有限", field="divergent_center")
        lo, hi = min(vals), max(vals)
        if not (lo < center < hi):
            raise ThematicAuthoringError(
                "NOT_DIVERGENT_STRUCTURE",
                f"发散中心 {center!r} 不在有效值域 ({lo!r}, {hi!r}) 内——数据无两侧结构，"
                "不能伪造正负差异",
                field="divergent_center",
                correction_hint="把中心设到值域内部（如 0），或改用 graduated/continuous。",
            )
        candidate = build_divergent_spec(
            vals, field=field, center=float(center), palette=plan.palette or "RdBu",
        )
        if candidate is None:
            raise ThematicAuthoringError("DIVERGENT_BUILD_FAILED", "发散表达构建失败", field=field)
        if plan.invert_palette:
            candidate["palette_colors"] = list(reversed(candidate["palette_colors"]))
        if plan.nodata is not None:
            candidate["nodata"] = {"color": plan.nodata.color, "label": plan.nodata.label}
            candidate["nodata_label"] = plan.nodata.label
        if plan.unit is not None:
            candidate["unit"] = plan.unit
        if plan.title is not None:
            candidate["title"] = plan.title
        actual_k = None
        disclosures.append(
            f"发散中心 {center!r}，对称半值域 {candidate.get('max')!r}（两端臂等长覆盖）")
    else:
        raise ThematicAuthoringError("UNKNOWN_MODE", f"未知模式 {mode!r}", field="mode", status_code=400)

    # 单一 paint 投影（与图例同源；警告=候选不完整 → 拒绝提交面）
    paint_color, warnings = spec_to_paint(candidate)
    if paint_color is None:
        raise ThematicAuthoringError(
            "PAINT_PROJECTION_FAILED",
            f"候选表达无法投影为 paint：{warnings}",
            field="legend_spec",
        )

    size_paint: Optional[Dict[str, Any]] = None
    if plan.size_channel is not None:
        ch = plan.size_channel
        size_paint = {ch.channel: size_style_method(ch)}
        disclosures.append(
            f"{ch.channel} 映射：值域 [{ch.min_value:g}, {ch.max_value:g}] → "
            f"[{ch.min_size:g}, {ch.max_size:g}]px，"
            + ("面积线性于数值（等值差→等面积差）" if ch.scaling == "area_sqrt" else "尺寸线性于数值")
            + "；null/缺失按最小尺寸（to-number→0 钳制）")

    if scan.approximated:
        disclosures.append(
            f"统计基于 {scan.sample_size}/{scan.scanned} 个步进采样值（approximate）——"
            "类计数/断点为近似，提交回执保留该口径")

    return CandidateExpression(
        legend_spec=candidate,
        paint_color=paint_color,
        size_paint=size_paint,
        class_table=class_table,
        disclosures=disclosures,
        requested_k=requested_k,
        actual_k=actual_k,
    )
