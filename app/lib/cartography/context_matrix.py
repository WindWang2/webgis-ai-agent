"""Palette Context Matrix — 6 上下文 × 全注册色带的 golden 矩阵（V11 W2.5）。

任务书 W2.5：「CVD / print 全矩阵 golden：6 上下文 × 16 色带 = 96 组可分辨
矩阵落 golden；新增色带需过矩阵才可注册」。

口径单一化（本模块存在的理由）：逐格判定**复用** ``symbology._context_separable``
的同源常量与变换（cvd_* → Machado 模拟 + CIEDE2000 相邻 ΔE；print →
``print_desaturate`` + 灰度 ΔL；screen/projector → ΔE00，projector 加成）——
矩阵与裁决不会各说各话；k 取 ``defaults.DEFAULT_CLASS_COUNT``（W0.4 单点）。

数量对账（§0.5 以代码为准）：任务书按 16 色带估算 96 组；注册表实测
**18** 条色带 → 矩阵 **108** 格。全格落 golden（``tests/cartography/
golden_corpus/context_matrix/matrix.json``），回归纪律：格判定只许变好
（fail→warn/pass 方向），变差即红 —— 「新增色带需过矩阵才可注册」由
:func:`validate_new_palette` 承接（注册路径必须先过全上下文）。

诚实披露：非全 pass。fail/warn 格是**冻结的已知集合**（如 Pastel1 在 CVD
下相邻 ΔE 偏低 —— 浅色低饱和系的固有属性），resolve_symbology 在对应
上下文会自然落选/换带（ADR-0152 既有语义），矩阵负责让这个集合**可见且
不悄悄变大**。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from app.lib.cartography.defaults import DEFAULT_CLASS_COUNT
from app.lib.cartography.palettes import (
    COLOR_PALETTES,
    grayscale_ramp_separation,
    min_adjacent_delta_e,
    print_desaturate,
    sample_ramp_colors,
    simulate_cvd,
)
from app.lib.cartography.symbology import (
    SymbologyConstraints,
    _CONTEXTS,
    _context_min_delta_e,
)

#: 矩阵上下文词表 = symbology 裁决词表（同一来源，不另立）。
MATRIX_CONTEXTS = tuple(_CONTEXTS)

#: 逐上下文的判据种类（golden 报告可读性；判定仍走同源常量）。
_METRIC_KIND = {
    "print": "gray_delta_l",
    "projector": "delta_e00",
    "screen": "delta_e00",
    "cvd_deuteranopia": "delta_e00",
    "cvd_protanopia": "delta_e00",
    "cvd_tritanopia": "delta_e00",
}


def _context_colors(colors: Sequence[str], context: str) -> List[str]:
    if context.startswith("cvd_"):
        sim = [simulate_cvd(c, context) for c in colors]
        return [c if c is not None else "" for c in sim]
    if context == "print":
        return print_desaturate(list(colors))
    return list(colors)


def _context_min_metric(context: str, colors: Sequence[str]) -> Optional[float]:
    """与 ``_context_separable`` 同口径的最小相邻度量。"""
    if context == "print":
        return grayscale_ramp_separation(print_desaturate(list(colors)))
    if context.startswith("cvd_"):
        sim = [simulate_cvd(c, context) for c in colors]
        if any(s is None for s in sim):
            return None
        return min_adjacent_delta_e(sim)
    return min_adjacent_delta_e(list(colors))


def evaluate_cell(
    palette: str,
    context: str,
    *,
    k: int = DEFAULT_CLASS_COUNT,
    constraints: Optional[SymbologyConstraints] = None,
) -> Dict[str, Any]:
    """单格判定（确定性；与 resolve_symbology 同源常量）。"""
    constraints = constraints or SymbologyConstraints()
    if context not in MATRIX_CONTEXTS:
        raise ValueError(f"未知上下文: {context}（合法值：{', '.join(MATRIX_CONTEXTS)}）")
    colors = sample_ramp_colors(palette, k)
    metric = _context_min_metric(context, colors) if len(colors) >= 2 else None
    if context == "print":
        threshold = constraints.min_gray_delta_l
        separable = metric is not None and metric >= threshold
    else:
        threshold = _context_min_delta_e(context, constraints)
        separable = metric is not None and metric >= threshold
    if metric is None:
        verdict = "unavailable"
    elif separable:
        verdict = "pass"
    else:
        verdict = "fail"
    return {
        "context": context,
        "palette": palette,
        "k": k,
        "metric_kind": _METRIC_KIND[context],
        "min_metric": None if metric is None else round(float(metric), 4),
        "threshold": round(float(threshold), 4),
        "separable": bool(separable),
        "verdict": verdict,
    }


def evaluate_matrix(
    *,
    palettes: Optional[Sequence[str]] = None,
    contexts: Optional[Sequence[str]] = None,
    k: int = DEFAULT_CLASS_COUNT,
    constraints: Optional[SymbologyConstraints] = None,
) -> Dict[str, Any]:
    """全矩阵判定（确定性、JSON 可序列化 —— golden 对拍面）。"""
    names = sorted(palettes) if palettes else sorted(COLOR_PALETTES)
    ctxs = list(contexts) if contexts else list(MATRIX_CONTEXTS)
    cells = [
        evaluate_cell(p, c, k=k, constraints=constraints)
        for c in ctxs
        for p in names
    ]
    summary: Dict[str, int] = {"pass": 0, "fail": 0, "unavailable": 0}
    for cell in cells:
        summary[cell["verdict"]] += 1
    return {
        "version": 1,
        "k": k,
        "contexts": ctxs,
        "palettes": names,
        "cells": cells,
        "summary": summary,
    }


def failing_cells(matrix: Dict[str, Any]) -> List[Dict[str, Any]]:
    """fail 格清单（诚实披露面；resolve_symbology 对应上下文会自然落选）。"""
    return [c for c in matrix["cells"] if c["verdict"] == "fail"]


def validate_new_palette(
    colors: Sequence[str],
    *,
    contexts: Optional[Sequence[str]] = None,
    k: int = DEFAULT_CLASS_COUNT,
    constraints: Optional[SymbologyConstraints] = None,
) -> List[str]:
    """注册门（W2.5）：新色带必须在全部上下文可分辨，返回不达标上下文。

    注册路径（含测试锁定的既有色带变更）必须过此门 —— 空列表才可注册。
    ``colors`` 为该色带的 ramp 采样序列（与 sample_ramp_colors 同口径）。
    """
    ctxs = list(contexts) if contexts else list(MATRIX_CONTEXTS)
    failing: List[str] = []
    for context in ctxs:
        constraints_ = constraints or SymbologyConstraints()
        if context == "print":
            sep = grayscale_ramp_separation(print_desaturate(list(colors)))
            ok = sep is not None and sep >= constraints_.min_gray_delta_l
        elif context.startswith("cvd_"):
            sim = [simulate_cvd(c, context) for c in colors]
            ok = (
                not any(s is None for s in sim)
                and (min_adjacent_delta_e(sim) or 0.0)
                >= _context_min_delta_e(context, constraints_)
            )
        else:
            ok = (min_adjacent_delta_e(list(colors)) or 0.0) >= _context_min_delta_e(
                context, constraints_
            )
        if not ok:
            failing.append(context)
    return failing


__all__ = [
    "MATRIX_CONTEXTS",
    "evaluate_cell",
    "evaluate_matrix",
    "failing_cells",
    "validate_new_palette",
]
