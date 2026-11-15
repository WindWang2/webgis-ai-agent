"""G41 canonical 科学图例（WP10 的内容权威）。

同一份 JSON 被 live DOM、Canvas(PNG)、前端矢量 SVG、服务端 G34 SVG/PDF
与 Pi 回执共同消费。G34 继续唯一决定位置/尺寸/字体/页框；本模块只负责
**内容**：两轴各自单位与断点、格子意义/颜色/计数/方向、缺失政策、总体口径；
或估计图例 + 误差量名/单位/level/方向/缺失说明。

刻意保留旧 ``type="bivariate"`` 与 ``colors/n/label_a/label_b/breaks_*``
键，使既有 live 与 Canvas 渲染器无需改动即可消费；新增键是加性的。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.bivariate_reliability_authoring.classify import (
    PAIR_CLASS_FIELD,
    BivariateResult,
    row_col_meta,
)
from app.services.bivariate_reliability_authoring.contract import (
    ALGORITHM_VERSION,
    BivariateSpec,
    ReliabilitySpec,
)
from app.services.bivariate_reliability_authoring.reliability import (
    RELIABILITY_BUCKET_FIELD,
    ReliabilityResult,
)
from app.services.bivariate_reliability_authoring.stats import (
    PairedScan,
    paired_visuals,
)

LEGEND_SCHEMA_VERSION = "g41_legend.v1"

_READER_LOAD_NOTE = (
    "双变量矩阵表达两字段的联合分级共现，读者负荷高；颜色共现不是统计相关、"
    "空间相关或因果结论。首版不叠加『两主变量 + 不确定性』第三通道。"
)


def _dedupe(notes: List[str]) -> List[str]:
    """披露按首次出现去重（保持顺序）。

    披露会随 legend_spec 持久化进 MapSpec；同一句话重复出现只增加载荷，
    不增加信息。任何逐要素/逐桶追加的路径都必须先经过这里。
    """
    seen: set = set()
    out: List[str] = []
    for note in notes:
        if note and note not in seen:
            seen.add(note)
            out.append(note)
    return out


def _source_block(anchor: Dict[str, Any], plan_digest: str) -> Dict[str, Any]:
    return {
        "ref_id": anchor.get("ref_id"),
        "content_revision": anchor.get("content_revision"),
        "content_fingerprint": anchor.get("content_fingerprint"),
        "feature_count": anchor.get("feature_count"),
        "algorithm_version": ALGORITHM_VERSION,
        "plan_digest": plan_digest,
        "snapshot_guarantee": (
            "权威单调 generation 前后核对 + 同会话锁 + 全量单次深拷贝；"
            "ABA 由版本号捕获，不靠 fingerprint 相同"
        ),
    }


def build_bivariate_legend(
    spec: BivariateSpec,
    scan: PairedScan,
    result: BivariateResult,
    *,
    anchor: Dict[str, Any],
    plan_digest: str,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    axes = row_col_meta(spec, result)
    col, row = axes["col"], axes["row"]
    missing_count = (scan.only_a_missing + scan.only_b_missing
                     + scan.both_missing)
    visuals = paired_visuals(scan, spec)
    disclosures = list(result.disclosures) + [
        f"边界规则 boundary_rule={spec.boundary_rule}："
        + ("恰等内部断点归上类（G33/MapLibre step）" if spec.boundary_rule == "upper_inclusive"
           else "恰等内部断点归下类（旧 bivariate._bin_index）"),
        "空格保留 requested/actual 类数证据；不伪造足够类数、不随意重排色阵",
        _READER_LOAD_NOTE,
        "散点/直方图是有界可视化样本；联合计数与要素分类来自完整冻结总体（精确）",
    ]
    if spec.swap_axes:
        disclosures.append(
            "已交换两轴：类别索引、色阵意义、联合计数与图例标签/单位/断点同步转置")
    if spec.flip_rows:
        # 只反转**呈现方向**：屏幕行序与箭头；类别索引与 paint 语义不变。
        # 披露文本必须与实现一致，否则读者会据此误判着色分档被重排（复审 P2）。
        disclosures.append(
            "已纵向反转呈现：屏幕行序与箭头方向反转；类别索引、着色与联合计数按规范行序（低→高）不变")
    return {
        "type": "bivariate",
        "schema_version": LEGEND_SCHEMA_VERSION,
        "mode": "bivariate_matrix",
        "algorithm_version": ALGORITHM_VERSION,
        "title": title or f"{col['label']} × {row['label']}",
        # ── 既有渲染器消费键（保持同源）──
        "matrix": spec.matrix_id or "custom",
        "colors": list(result.colors),
        "n": spec.n,
        "label_a": col["label"],
        "label_b": row["label"],
        # swap 后列轴 = 原 B、行轴 = 原 A —— 断点也须整体转置。
        "breaks_a": [round(b, 6) for b in (result.breaks_b if spec.swap_axes
                                           else result.breaks_a)],
        "breaks_b": [round(b, 6) for b in (result.breaks_a if spec.swap_axes
                                           else result.breaks_b)],
        # ── canonical 内容 ──
        "class_field": PAIR_CLASS_FIELD,
        "row_major": True,
        "axes": axes,
        "cells": result.cells,
        "joint": [list(r) for r in result.joint],
        "requested_classes": result.requested_classes,
        "actual_classes_a": result.actual_classes_a,
        "actual_classes_b": result.actual_classes_b,
        "boundary_rule": spec.boundary_rule,
        "counts": {
            "total_features": scan.total_features,
            "valid_pairs": scan.valid_pairs,
            "only_a_missing": scan.only_a_missing,
            "only_b_missing": scan.only_b_missing,
            "both_missing": scan.both_missing,
            "reconciliation": scan.reconcile(),
        },
        "missing": {
            "count": missing_count,
            "label": spec.missing_label,
            "color": spec.missing_color,
            "policy": "独立无数据表达；不画最低格、不按 0 处理",
        },
        "nodata": {"color": spec.missing_color, "label": spec.missing_label},
        "unit_a": col.get("unit"),
        "unit_b": row.get("unit"),
        "unit_declared_a": bool(col.get("unit")),
        "unit_declared_b": bool(row.get("unit")),
        "source": _source_block(anchor, plan_digest),
        "stats_basis": scan.basis(),
        "scatter": visuals["scatter"],
        "histograms": {
            "a": visuals["histogram_a"], "b": visuals["histogram_b"],
        },
        "disclosures": _dedupe(disclosures),
        "approximate": False,
        "notes": disclosures[:4],
    }


def build_reliability_legend(
    spec: ReliabilitySpec,
    result: ReliabilityResult,
    *,
    anchor: Dict[str, Any],
    plan_digest: str,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    m = spec.measure
    v = spec.visual
    scan = result.scan
    disclosures = list(result.disclosures) + [
        f"边界规则 boundary_rule={spec.boundary_rule}",
        "主色表达估计值；第二通道只表达所绑定的具体误差量/区间宽度",
        "置信水平、可靠性、显著性与缺失是不同概念；本图不输出显著性或因果结论",
        _READER_LOAD_NOTE,
    ]
    # 扁平条目：让 G34 既有条目渲染器也能给出可读文字；真实矩阵/桶由
    # canonical 字段驱动专用绘制（不能只靠普通色条）。
    entries: List[Dict[str, str]] = []
    for i, interval in enumerate(result.estimate_intervals):
        color = result.estimate_legend["palette_colors"][
            min(i, len(result.estimate_legend["palette_colors"]) - 1)]
        entries.append({
            "label": f"估计 {interval['label']}",
            "color": color, "kind": "class",
        })
    for b in result.buckets:
        entries.append({
            "label": f"{b['label']} → "
                     f"{'透明度' if v.channel == 'opacity' else '描边宽'} {b['output']:g}",
            "color": "#94a3b8", "kind": "reliability_bucket",
        })
    entries.append({"label": spec.missing_label, "color": "#cccccc",
                    "kind": "nodata"})
    return {
        "type": "reliability_dual_channel",
        "schema_version": LEGEND_SCHEMA_VERSION,
        "mode": "reliability_dual_channel",
        "algorithm_version": ALGORITHM_VERSION,
        "title": title or f"{spec.estimate_label or spec.estimate_field} 与可靠度",
        "estimate": {
            "field": spec.estimate_field,
            "label": spec.estimate_label or spec.estimate_field,
            "unit": spec.estimate_unit or None,
            "unit_declared": bool(spec.estimate_unit),
            "method": spec.estimate_method,
            "requested_k": spec.estimate_k,
            "actual_k": len(result.estimate_intervals),
            "breaks": result.estimate_breaks,
            "intervals": result.estimate_intervals,
            "palette": spec.estimate_palette,
            "palette_colors": list(result.estimate_legend["palette_colors"]),
            "boundary_rule": spec.boundary_rule,
            "legend_spec": dict(result.estimate_legend),
        },
        "measure": dict(result.measure_meta),
        "channel": {
            "kind": v.channel,
            "direction": v.direction,
            "bucket_field": RELIABILITY_BUCKET_FIELD,
            "min_value": v.min_value,
            "max_value": v.max_value,
            "min_output": v.min_output,
            "max_output": v.max_output,
            "unknown_output": v.unknown_output,
            "clip": v.clip,
            "unit": m.unit or None,
            "unit_declared": bool(m.unit),
        },
        "buckets": result.buckets,
        "bucket_paint": dict(result.bucket_paint),
        "missing": {
            "label": spec.missing_label,
            "count": scan.unknown_measure,
            "policy": "误差缺失/无效独立表达；不当 0 误差、不当最高可靠性",
        },
        "counts": {
            **scan.basis(),
            "unknown_measure": scan.unknown_measure,
            "invalid_measure": scan.invalid_measure,
            "zero_measure": scan.zero_measure,
        },
        "source": _source_block(anchor, plan_digest),
        "disclosures": _dedupe(disclosures),
        "approximate": False,
        "notes": disclosures[:4],
        # G34 read_legend_content 兼容的扁平 canonical 条目
        "entries": entries,
        "unit": m.unit or spec.estimate_unit or "",
    }
