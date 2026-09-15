"""视觉驱动的 MapSpec 自愈编译器（ADR-0186 visual self-healing v1）。

定位：把 Direction 01 的结构化 ``VisualJudgeReport`` 缺陷清单编译成**事务性
MapSpec 微变异**（micro-mutations）。本模块是纯函数层——不含任何 IO 与
session 状态；事务挂载（锁 / CAS / checkpoint / revision 单调 / 失败回滚）
全部复用 ``lifecycle_engine.apply_mutation``，见其 ``ApplyVisualHealPatchIntent``
分支与 ``apply_visual_heal_patch`` 入口。

设计规范（缺陷→变异映射矩阵、阶梯、披露码）：
``docs/dev/visual-self-healing-rules.md``。三条硬边界：

1. 只触呈现面（symbol layout / paint 呈现键 / legend 色带 / layers 次序），
   绝不触碰 sources、数据绑定与分类断点（min/max/breaks/categories[].key）；
2. 诚实优先：定位不到 / 映射不到 / 已最优 / 不可靠目标一律 skip 并披露，
   空计划在引擎侧被拒（HEAL_PLAN_EMPTY），绝不为了"提交了"而制造补丁；
3. 收敛防护在引擎侧：同一缺陷指纹至多 2 次自愈（MAX_VISUAL_HEAL_ITERATIONS），
   第 3 次请求触发 ``SelfHealConvergenceExhausted``，禁止震荡死循环。
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

from app.lib.cartography.palettes import (
    COLOR_PALETTES,
    contrast_ratio,
    min_adjacent_delta_e,
)

if TYPE_CHECKING:  # 懒耦合：仅类型标注引用，运行时鸭子类型（防循环/重依赖）
    from app.lib.harness.visual_evaluator import VisualJudgeReport

logger = logging.getLogger(__name__)

# ── 原子操作集（微变异词汇表，ADR-0186 D2）─────────────────────────────────
MUTATION_HEAL_LABEL_COLLISION = "MUTATION_HEAL_LABEL_COLLISION"
MUTATION_HEAL_CONTRAST = "MUTATION_HEAL_CONTRAST"
MUTATION_HEAL_LAYER_ORDER = "MUTATION_HEAL_LAYER_ORDER"
MUTATION_HEAL_OPACITY = "MUTATION_HEAL_OPACITY"

HEAL_OP_CODES = frozenset({
    MUTATION_HEAL_LABEL_COLLISION,
    MUTATION_HEAL_CONTRAST,
    MUTATION_HEAL_LAYER_ORDER,
    MUTATION_HEAL_OPACITY,
})

#: 可自愈缺陷类别白名单（映射矩阵之外的类别一律不编译）。
HEALABLE_CATEGORIES: Tuple[str, ...] = (
    "label_collision", "contrast", "layer_order", "opacity",
)

#: 同一缺陷指纹允许的最大自愈次数（ADR-0186 D4：≤2 次迭代，第 3 次请求必拦）。
MAX_VISUAL_HEAL_ITERATIONS = 2

_SEVERITY_RANK: Dict[str, int] = {"error": 3, "warning": 2, "info": 1}
#: 影响域权重（排序用）：遮挡破坏整图可读性，故最优先。
_CATEGORY_IMPACT: Dict[str, int] = {
    "layer_order": 4, "contrast": 3, "label_collision": 2, "opacity": 1,
}

# ── 阶梯常量 ────────────────────────────────────────────────────────────────
#: 缺省对比度门限：WCAG AA 大字号/图形件（配色库多色候选实测可达标；
#: 4.5 正文阈值按需经 min_contrast_ratio 显式提供）。
DEFAULT_MIN_CONTRAST_RATIO = 3.0
_DARK_CANVAS_LUMINANCE = 0.18
_LABEL_SIZE_FLOOR = 8.0
_LABEL_SIZE_STEP = 0.85
_LABEL_PADDING_CAP = 8
_LABEL_PADDING_DEFAULT = 2
_OPACITY_RAISE_STEP = 0.3
_OCCLUDER_OPACITY_SCALE = 0.6
_OCCLUDER_OPACITY_FLOOR = 0.25

#: 视为"垫底/遮挡型"的图层类型（LayerZOrderAdjuster 的向上推断面）。
OCCLUDING_LAYER_TYPES = frozenset(
    {"heatmap", "raster", "hillshade", "fill-extrusion", "fill"}
)

#: 图层类型 → opacity paint 键（与 lifecycle_engine._OPACITY_PAINT_KEYS 同源；
#: 本地复制以防 services↔lib 反向依赖，漂移由引擎单测看护）。
_OPACITY_PAINT_KEYS: Dict[str, str] = {
    "circle": "circle-opacity",
    "fill": "fill-opacity",
    "line": "line-opacity",
    "raster": "raster-opacity",
    "heatmap": "heatmap-opacity",
    "fill-extrusion": "fill-extrusion-opacity",
    "symbol": "icon-opacity",
}


class SelfHealConvergenceExhausted(RuntimeError):
    """同一缺陷指纹的自愈预算耗尽（≤2 次）且仍无改善证据。

    reason ∈ max_iterations（同一指纹已提交 2 次自愈仍被再次请求）/
    no_improvement（quality_score 连续 2 次不提升）/ repeated_patch
    （同一补丁签名重放，必无新信息）。保护机制绝不以死循环换收敛。
    """

    def __init__(self, defect_fingerprint: str, attempts: int, reason: str) -> None:
        self.defect_fingerprint = defect_fingerprint
        self.attempts = attempts
        self.reason = reason
        super().__init__(
            f"visual self-heal convergence exhausted: fingerprint={defect_fingerprint} "
            f"attempts={attempts} reason={reason}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 值对象
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class VisualCritiqueItem:
    """归一化后的可定位视觉缺陷（维度级 critique → 靶图层×属性）。

    layer_ids 为空 = 未定位（planner 诚实跳过，绝不臆测整图）；
    occluder_layer_id 仅 layer_order/opacity 缺陷使用（遮挡者/垫底者）。
    """

    category: str
    severity: str = "warning"
    layer_ids: Tuple[str, ...] = ()
    occluder_layer_id: Optional[str] = None
    dimension: str = ""
    evidence: str = ""
    min_contrast_ratio: Optional[float] = None
    suggested_operation: Optional[str] = None


@dataclass(frozen=True)
class HealOp:
    """一条自愈微变异。载荷字段按 op 二选一：

    - LABEL_COLLISION → layout_patch（merge 进 layer.layout）
    - CONTRAST → colors（长度匹配逐位替换，镜像 _apply_palette_change 语义）
    - LAYER_ORDER → move_above=(target, occluder)（最小重排）
    - OPACITY → paint_opacity（layer_id → 新 opacity 值）
    """

    op: str
    layer_ids: Tuple[str, ...]
    rationale: str
    severity: str = "warning"
    defect_key: str = ""
    layout_patch: Optional[Dict[str, Any]] = None
    colors: Optional[Tuple[str, ...]] = None
    paint_opacity: Optional[Dict[str, float]] = None
    move_above: Optional[Tuple[str, str]] = None


@dataclass(frozen=True)
class VisualHealPlan:
    """一次自愈编译的完整产出（含诚实披露），可序列化、可重放。"""

    defects: Tuple[VisualCritiqueItem, ...]
    ops: Tuple[HealOp, ...]
    skipped: Tuple[Dict[str, str], ...] = ()
    defect_fingerprint: str = ""
    ops_signature: str = ""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )


def defect_fingerprint(defects: Sequence[VisualCritiqueItem]) -> str:
    """缺陷集指纹（与输入顺序无关）。收敛防护账本的键。"""
    entries = sorted(_canonical_json(dataclass_dict(d)) for d in defects)
    payload = _canonical_json(entries).encode("utf-8")
    return f"vheal-sha256:{hashlib.sha256(payload).hexdigest()}"


def dataclass_dict(item: VisualCritiqueItem) -> Dict[str, Any]:
    return {
        "category": item.category,
        "severity": item.severity,
        "layer_ids": list(item.layer_ids),
        "occluder_layer_id": item.occluder_layer_id,
        "dimension": item.dimension,
        "evidence": item.evidence,
        "min_contrast_ratio": item.min_contrast_ratio,
        "suggested_operation": item.suggested_operation,
    }


def _ops_signature(ops: Sequence[HealOp]) -> str:
    payloads = []
    for op in ops:
        payloads.append({
            "op": op.op,
            "layer_ids": list(op.layer_ids),
            "layout_patch": op.layout_patch,
            "colors": list(op.colors) if op.colors else None,
            "paint_opacity": op.paint_opacity,
            "move_above": list(op.move_above) if op.move_above else None,
        })
    payload = _canonical_json(payloads).encode("utf-8")
    return f"vhealop-sha256:{hashlib.sha256(payload).hexdigest()}"


# ─────────────────────────────────────────────────────────────────────────────
# 归一化桥：VisualJudgeReport → VisualCritiqueItem（映射矩阵 §1）
# ─────────────────────────────────────────────────────────────────────────────

#: 维度 → [(category, keywords)]，序即优先级（遮挡类措辞含"覆盖/叠"易与
#: overlap 混淆，故 layer_order 关键词最先判）。
_DIMENSION_KEYWORD_RULES: Dict[str, Tuple[Tuple[str, Tuple[str, ...]], ...]] = {
    "readability": (
        ("layer_order", ("occlud", "cover", "遮挡", "覆盖", "遮盖", "hidden", "below")),
        ("opacity", ("opacity", "transparen", "透明", "淡")),
        ("contrast", ("contrast", "对比", "看不清", "辨")),
        ("label_collision", ("overlap", "collision", "重叠", "注记", "label")),
    ),
    "composition_balance": (
        ("layer_order", ("occlud", "cover", "遮挡", "覆盖", "遮盖", "hidden", "below")),
    ),
    "information_density": (
        ("label_collision", ("overlap", "collision", "重叠", "注记", "label")),
    ),
    "polish_completeness": (
        ("opacity", ("opacity", "transparen", "透明")),
    ),
}
#: 维度级缺省映射（无关键词命中时的兜底；未列维度不可映射 → 丢弃）。
_DIMENSION_FALLBACK: Dict[str, str] = {"color_discriminability": "contrast"}


def _classify_critique(dimension: str, text: str) -> Optional[str]:
    lowered = (dimension + " " + text).lower()
    for category, keywords in _DIMENSION_KEYWORD_RULES.get(dimension, ()):
        if any(keyword in lowered for keyword in keywords):
            return category
    return _DIMENSION_FALLBACK.get(dimension)


def _locate_layers(text: str, known_layer_ids: Sequence[str]) -> List[str]:
    """证据文本 → 命中的已知图层 id（按文本出现序；含子层前缀不在此扩）。"""
    lowered = text.lower()
    hits = [
        (lowered.find(lid.lower()), lid)
        for lid in known_layer_ids
        if lid and lowered.find(lid.lower()) >= 0
    ]
    hits.sort()
    return [lid for _pos, lid in hits]


def _parse_min_contrast(text: str) -> Optional[float]:
    lowered = text.lower()
    if "aaa" in lowered:
        return 7.0
    import re as _re

    match = _re.search(r"(\d+(?:\.\d+)?)\s*[:：]\s*1", lowered)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    match = _re.search(r"(?:ratio|对比度|contrast)\D{0,6}(\d+(?:\.\d+)?)", lowered)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def normalize_visual_report(
    report: "VisualJudgeReport",
    *,
    known_layer_ids: Sequence[str] = (),
) -> List[VisualCritiqueItem]:
    """维度级视觉裁判报告 → 可定位缺陷列表。

    只做确定性映射与文本定位；映射不出（unmappable）的条目被丢弃，
    未命中任何已知图层 id 的条目保留空 layer_ids（planner 按
    unlocalized_defect 跳过）——两处都会在 plan.skipped 诚实披露。
    """
    items: List[VisualCritiqueItem] = []
    for critique in getattr(report, "critiques", None) or []:
        suggestion = str(getattr(critique, "suggestion", "") or "")
        evidence = str(getattr(critique, "evidence", "") or "")
        dimension = str(getattr(critique, "dimension", "") or "")
        severity = str(getattr(critique, "severity", "warning") or "warning")
        category = _classify_critique(dimension, f"{suggestion} {evidence}")
        if category not in HEALABLE_CATEGORIES:
            continue
        # 结构化直通（Direction 01 预留字段），缺席时回退文本定位
        raw_ids = getattr(critique, "layer_ids", None)
        if isinstance(raw_ids, (list, tuple)) and raw_ids:
            layer_ids = tuple(str(x) for x in raw_ids if isinstance(x, str) and x)
        else:
            layer_ids = tuple(_locate_layers(f"{suggestion} {evidence}", known_layer_ids))
        occluder: Optional[str] = None
        if category in ("layer_order", "opacity") and len(layer_ids) >= 2:
            # 文本先出现者为靶，其后首个提及者视为遮挡者
            layer_ids, occluder = (layer_ids[0],), layer_ids[1]
        items.append(VisualCritiqueItem(
            category=category,
            severity=severity if severity in _SEVERITY_RANK else "warning",
            layer_ids=layer_ids,
            occluder_layer_id=occluder,
            dimension=dimension,
            evidence=suggestion or evidence,
            min_contrast_ratio=_parse_min_contrast(f"{suggestion} {evidence}"),
            suggested_operation=getattr(critique, "suggested_operation", None),
        ))
    return items


# ─────────────────────────────────────────────────────────────────────────────
# 色彩诊断与选色（ContrastRemapper 的判据，ADR-0186 D6）
# ─────────────────────────────────────────────────────────────────────────────


def canvas_color(mapspec: Optional[Dict[str, Any]]) -> str:
    """画布色 = 第一个 background 层的 background-color（缺席 → 白底）。"""
    layers = (mapspec or {}).get("layers")
    if isinstance(layers, list):
        for layer in layers:
            if isinstance(layer, dict) and layer.get("type") == "background":
                paint = layer.get("paint")
                color = paint.get("background-color") if isinstance(paint, dict) else None
                if isinstance(color, str) and color:
                    return color
    return "#ffffff"


def _paint_color_methods(layer: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """paint 中携带分类色的 method 包装（step/interpolate/match），按属性名稳定序。"""
    paint = layer.get("paint")
    if not isinstance(paint, dict):
        return []
    out: List[Tuple[str, Dict[str, Any]]] = []
    for prop in sorted(paint):
        spec = paint[prop]
        if (
            isinstance(spec, dict)
            and prop.endswith("-color")
            and spec.get("method") in ("step", "interpolate", "match")
        ):
            out.append((prop, spec))
    return out


def _method_output_colors(spec: Dict[str, Any]) -> List[str]:
    """单个 method 包装的输出色序（与 _apply_palette_change 的取色面一致）：

    step = [default] + stops（有 default 时）；interpolate = stops；match = cases。
    """
    method = spec.get("method")
    colors: List[str] = []
    if method == "step":
        if spec.get("default") is not None:
            colors.append(spec["default"])
        sources: Sequence[Any] = spec.get("stops") or []
    elif method == "interpolate":
        sources = spec.get("stops") or []
    elif method == "match":
        sources = spec.get("cases") or []
    else:
        return []
    for stop in sources:
        if isinstance(stop, (list, tuple)) and len(stop) >= 2 and isinstance(stop[1], str):
            colors.append(stop[1])
    return colors


def extract_layer_colors(layer: Dict[str, Any]) -> List[str]:
    """图层当前分类色（paint 方法色优先，legend 色带兜底）。"""
    for _prop, spec in _paint_color_methods(layer):
        colors = [c for c in _method_output_colors(spec) if c]
        if len(colors) >= 2:
            return colors
    legend_spec = layer.get("legend_spec")
    if isinstance(legend_spec, dict):
        for key in ("palette_colors", "colors"):
            values = legend_spec.get(key)
            if isinstance(values, list) and len(values) >= 2:
                return [c for c in values if isinstance(c, str)]
    return []


def select_contrast_palette(
    n_colors: int,
    canvas: str = "#ffffff",
    min_ratio: float = DEFAULT_MIN_CONTRAST_RATIO,
) -> Optional[Tuple[str, ...]]:
    """从配色库为 n 色选一个"画布对比达标 + 感知区分度最大"的确定性候选。

    排序键：(画布最差对比 ≥ min_ratio 优先, min_adjacent_delta_e 降序,
    调色板名字典序)。无候选达标 → None（调用方诚实跳过，不降门限凑数）。
    """
    if n_colors < 2:
        return None
    best: Optional[Tuple[bool, float, float, str, Tuple[str, ...]]] = None
    for name in sorted(COLOR_PALETTES):
        palette = COLOR_PALETTES[name]
        if len(palette) < n_colors:
            continue
        colors = tuple(
            palette[i * (len(palette) - 1) // (n_colors - 1)]
            for i in range(n_colors)
        )
        min_canvas = min(contrast_ratio(c, canvas) for c in colors)
        min_de = min_adjacent_delta_e(list(colors))
        if min_de is None:
            continue
        candidate = (min_canvas >= min_ratio, min_de, name, colors)
        # 排序键：(门限达标优先, min-ΔE 降序, 名字典序)
        if best is None or (-candidate[0], -candidate[1], candidate[2]) < (
            -best[0], -best[1], best[2]
        ):
            best = candidate
    if best is None or not best[0]:
        return None
    return best[3]


def apply_palette_to_layer(layer: Dict[str, Any], colors: Sequence[str]) -> bool:
    """按位替换呈现色（长度一致才动）。镜像 quality_loop._apply_palette_change：
    legend 色带 + paint step/interpolate/match；分类断点/类别键不动。"""
    color_list = list(colors)
    if not color_list:
        return False
    changed = False
    legend_spec = layer.get("legend_spec")
    if isinstance(legend_spec, dict):
        ramp_key = (
            "palette_colors"
            if isinstance(legend_spec.get("palette_colors"), list)
            else "colors" if isinstance(legend_spec.get("colors"), list)
            else None
        )
        if ramp_key and len(legend_spec[ramp_key]) == len(color_list):
            legend_spec[ramp_key] = list(color_list)
            changed = True
        categories = legend_spec.get("categories")
        if isinstance(categories, list) and len(categories) == len(color_list):
            for category, color in zip(categories, color_list):
                if isinstance(category, dict):
                    category["color"] = color
                    changed = True
    for _prop, spec in _paint_color_methods(layer):
        outputs = _method_output_colors(spec)
        if len(outputs) != len(color_list):
            continue
        method = spec.get("method")
        if method == "step":
            if spec.get("default") is not None:
                spec["default"] = color_list[0]
                rest = color_list[1:]
            else:
                rest = color_list
            stops = spec.get("stops") or []
            for stop, color in zip(stops, rest):
                if isinstance(stop, (list, tuple)) and len(stop) >= 2:
                    stop[1] = color
        elif method == "interpolate":
            for stop, color in zip(spec.get("stops") or [], color_list):
                if isinstance(stop, (list, tuple)) and len(stop) >= 2:
                    stop[1] = color
        elif method == "match":
            for case, color in zip(spec.get("cases") or [], color_list):
                if isinstance(case, (list, tuple)) and len(case) >= 2:
                    case[1] = color
        else:
            continue
        changed = True
    return changed


# ─────────────────────────────────────────────────────────────────────────────
# Resolvers（映射矩阵 §3）
# ─────────────────────────────────────────────────────────────────────────────


def _find_layer(
    mapspec: Optional[Dict[str, Any]], layer_id: str
) -> Optional[Dict[str, Any]]:
    layers = (mapspec or {}).get("layers")
    if not isinstance(layers, list):
        return None
    for layer in layers:
        if isinstance(layer, dict) and str(layer.get("id") or "") == layer_id:
            return layer
    return None


def _skip(
    defect: VisualCritiqueItem, layer_ids: Tuple[str, ...], reason: str
) -> Dict[str, str]:
    return {
        "category": defect.category,
        "layer_ids": ",".join(layer_ids),
        "reason": reason,
    }


class LabelCollisionResolver:
    """注记重合 → symbol layout 微变异（阶梯：先避让/间距，后步进缩小字号）。"""

    def resolve(
        self,
        mapspec: Optional[Dict[str, Any]],
        defect: VisualCritiqueItem,
        *,
        attempt: int = 0,
    ) -> Tuple[List[HealOp], List[Dict[str, str]]]:
        ops: List[HealOp] = []
        skipped: List[Dict[str, str]] = []
        key = defect_fingerprint([defect])
        for layer_id in defect.layer_ids:
            layer = _find_layer(mapspec, layer_id)
            if layer is None:
                skipped.append(_skip(defect, (layer_id,), "unknown_layer"))
                continue
            if layer.get("type") != "symbol":
                skipped.append(_skip(defect, (layer_id,), "unsupported_layer_type"))
                continue
            layout = layer.get("layout") if isinstance(layer.get("layout"), dict) else {}
            has_text = bool(layout.get("text-field")) or isinstance(layer.get("label"), dict)
            if not has_text:
                skipped.append(_skip(defect, (layer_id,), "unsupported_layer_type"))
                continue
            text_size = layout.get("text-size")
            if attempt >= 1 and isinstance(text_size, dict):
                # 表达式字号不可靠改写（诚实边界：绝不盲写表达式）
                skipped.append(_skip(defect, (layer_id,), "unsafe_target"))
                continue
            padding = layout.get("text-padding")
            base_padding = (
                padding
                if isinstance(padding, (int, float)) and padding > 0
                else _LABEL_PADDING_DEFAULT
            )
            patch: Dict[str, Any] = {
                "text-allow-overlap": False,
                "text-ignore-placement": True,
                "text-padding": min(base_padding * 2, _LABEL_PADDING_CAP),
            }
            rationale = f"enforce non-overlap + padding {base_padding}->{patch['text-padding']}"
            if attempt >= 1 and isinstance(text_size, (int, float)) and text_size > _LABEL_SIZE_FLOOR:
                patch["text-size"] = max(
                    round(text_size * _LABEL_SIZE_STEP, 2), _LABEL_SIZE_FLOOR
                )
                rationale += f"; step text-size {text_size}->{patch['text-size']}"
            ops.append(HealOp(
                op=MUTATION_HEAL_LABEL_COLLISION,
                layer_ids=(layer_id,),
                rationale=rationale,
                severity=defect.severity,
                defect_key=key,
                layout_patch=patch,
            ))
        return ops, skipped


class ContrastRemapper:
    """色彩反差缺陷 → 配色库确定性重映射（画布 AA 门限 + ΔE 严格更优才动）。"""

    def resolve(
        self,
        mapspec: Optional[Dict[str, Any]],
        defect: VisualCritiqueItem,
        *,
        attempt: int = 0,
    ) -> Tuple[List[HealOp], List[Dict[str, str]]]:
        ops: List[HealOp] = []
        skipped: List[Dict[str, str]] = []
        key = defect_fingerprint([defect])
        min_ratio = (
            defect.min_contrast_ratio
            if isinstance(defect.min_contrast_ratio, (int, float)) and defect.min_contrast_ratio > 0
            else DEFAULT_MIN_CONTRAST_RATIO
        )
        canvas = canvas_color(mapspec)
        for layer_id in defect.layer_ids:
            layer = _find_layer(mapspec, layer_id)
            if layer is None:
                skipped.append(_skip(defect, (layer_id,), "unknown_layer"))
                continue
            current = extract_layer_colors(layer)
            if len(current) < 2 or not _paint_color_methods(layer):
                skipped.append(_skip(defect, (layer_id,), "unsafe_target"))
                continue
            selected = select_contrast_palette(len(current), canvas, min_ratio)
            if selected is None:
                skipped.append(_skip(defect, (layer_id,), "no_palette_meets_target"))
                continue
            current_de = min_adjacent_delta_e(current) or 0.0
            selected_de = min_adjacent_delta_e(list(selected)) or 0.0
            if selected_de <= current_de:
                # 区分度是缺陷主诉：非严格更优一律不动（诚实无操作）
                skipped.append(_skip(defect, (layer_id,), "already_optimal"))
                continue
            ops.append(HealOp(
                op=MUTATION_HEAL_CONTRAST,
                layer_ids=(layer_id,),
                rationale=(
                    f"palette remap on canvas {canvas}: min-dE "
                    f"{current_de:.1f}->{selected_de:.1f}, gate>={min_ratio}"
                ),
                severity=defect.severity,
                defect_key=key,
                colors=tuple(selected),
            ))
        return ops, skipped


class LayerZOrderAdjuster:
    """遮挡/层叠倒置 → 最小重排（靶层抬到遮挡者正上方；background 恒居首）。"""

    def resolve(
        self,
        mapspec: Optional[Dict[str, Any]],
        defect: VisualCritiqueItem,
        *,
        attempt: int = 0,
    ) -> Tuple[List[HealOp], List[Dict[str, str]]]:
        ops: List[HealOp] = []
        skipped: List[Dict[str, str]] = []
        key = defect_fingerprint([defect])
        layers = (mapspec or {}).get("layers") if isinstance(mapspec, dict) else None
        if not isinstance(layers, list):
            return ops, [_skip(defect, tuple(defect.layer_ids), "unknown_layer")]
        ids = [str(layer.get("id") or "") for layer in layers if isinstance(layer, dict)]
        type_by_id = {
            str(layer.get("id") or ""): str(layer.get("type") or "")
            for layer in layers if isinstance(layer, dict)
        }
        target = defect.layer_ids[0] if defect.layer_ids else ""
        if not target:
            return ops, [_skip(defect, (), "unlocalized_defect")]
        if target not in ids:
            return ops, [_skip(defect, (target,), "unknown_layer")]
        if type_by_id.get(target) == "background":
            # 不变量：background 恒为绘层次序首元素，抬升无意义
            return ops, [_skip(defect, (target,), "unsafe_target")]
        occluder = defect.occluder_layer_id
        if occluder:
            if occluder not in ids:
                return ops, [_skip(defect, (target,), "no_occluder_found")]
            if ids.index(occluder) < ids.index(target):
                # 遮挡者已在靶层之下：现状已满足诉求
                return ops, [_skip(defect, (target,), "already_optimal")]
        else:
            above = [
                lid for lid in ids[ids.index(target) + 1:]
                if type_by_id.get(lid) in OCCLUDING_LAYER_TYPES
            ]
            if not above:
                return ops, [_skip(defect, (target,), "already_optimal")]
            # 目标态 = 靶层高于全部遮挡型层 → 取位置最高者，抬到其正上方
            occluder = above[-1]
        ops.append(HealOp(
            op=MUTATION_HEAL_LAYER_ORDER,
            layer_ids=(target, occluder),
            rationale=f"raise '{target}' directly above occluder '{occluder}'",
            severity=defect.severity,
            defect_key=key,
            move_above=(target, occluder),
        ))
        return ops, skipped


class OpacityAdjuster:
    """低透明缺陷 → paint *-opacity 阶梯补偿（抬靶层；靶层已实心则压垫底者）。"""

    def resolve(
        self,
        mapspec: Optional[Dict[str, Any]],
        defect: VisualCritiqueItem,
        *,
        attempt: int = 0,
    ) -> Tuple[List[HealOp], List[Dict[str, str]]]:
        ops: List[HealOp] = []
        skipped: List[Dict[str, str]] = []
        key = defect_fingerprint([defect])
        target = defect.layer_ids[0] if defect.layer_ids else ""
        if not target:
            return ops, [_skip(defect, (), "unlocalized_defect")]
        layer = _find_layer(mapspec, target)
        if layer is None:
            return ops, [_skip(defect, (target,), "unknown_layer")]
        opacity_key = _OPACITY_PAINT_KEYS.get(str(layer.get("type") or ""))
        if opacity_key is None:
            return ops, [_skip(defect, (target,), "unsupported_layer_type")]
        paint = layer.get("paint") if isinstance(layer.get("paint"), dict) else {}
        current = paint.get(opacity_key, 1.0)
        if isinstance(current, (dict, list)):
            return ops, [_skip(defect, (target,), "unsafe_target")]
        current_value = float(current) if isinstance(current, (int, float)) else 1.0
        entries: Dict[str, float] = {}
        if current_value < 1.0:
            entries[target] = min(1.0, current_value + _OPACITY_RAISE_STEP)
        elif defect.occluder_layer_id:
            occluder = defect.occluder_layer_id
            occ_layer = _find_layer(mapspec, occluder)
            occ_key = (
                _OPACITY_PAINT_KEYS.get(str(occ_layer.get("type") or ""))
                if isinstance(occ_layer, dict) else None
            )
            if occ_layer is None or occ_key is None:
                return ops, [_skip(defect, (target,), "no_occluder_found")]
            occ_paint = (
                occ_layer.get("paint") if isinstance(occ_layer.get("paint"), dict) else {}
            )
            occ_current = occ_paint.get(occ_key, 1.0)
            if isinstance(occ_current, (dict, list)):
                return ops, [_skip(defect, (target,), "unsafe_target")]
            occ_value = float(occ_current) if isinstance(occ_current, (int, float)) else 1.0
            entries[occluder] = max(
                _OCCLUDER_OPACITY_FLOOR, occ_value * _OCCLUDER_OPACITY_SCALE
            )
        else:
            return ops, [_skip(defect, (target,), "already_optimal")]
        ops.append(HealOp(
            op=MUTATION_HEAL_OPACITY,
            layer_ids=tuple(entries.keys()),
            rationale=f"opacity compensation: {entries}",
            severity=defect.severity,
            defect_key=key,
            paint_opacity=entries,
        ))
        return ops, skipped


# ─────────────────────────────────────────────────────────────────────────────
# 策略规划器（映射矩阵 §2）
# ─────────────────────────────────────────────────────────────────────────────


class VisualHealStrategyPlanner:
    """缺陷清单 → 确定性 VisualHealPlan（排序 → 冲突消解 → 派发 Resolver）。"""

    def __init__(self) -> None:
        self._resolvers: Dict[str, Any] = {
            "label_collision": LabelCollisionResolver(),
            "contrast": ContrastRemapper(),
            "layer_order": LayerZOrderAdjuster(),
            "opacity": OpacityAdjuster(),
        }

    def plan(
        self,
        mapspec: Optional[Dict[str, Any]],
        defects: Sequence[VisualCritiqueItem],
        *,
        attempt: int = 0,
    ) -> VisualHealPlan:
        attempt = max(0, int(attempt))
        valid = [d for d in defects if d.category in HEALABLE_CATEGORIES]
        ordered = sorted(
            valid,
            key=lambda d: (
                -_SEVERITY_RANK.get(d.severity, 1),
                -_CATEGORY_IMPACT.get(d.category, 0),
                defect_fingerprint([d]),
            ),
        )
        ops: List[HealOp] = []
        skipped: List[Dict[str, str]] = []
        claimed: Dict[str, str] = {}  # layer_id → 已占坑缺陷指纹（同层一单）
        for defect in ordered:
            key = defect_fingerprint([defect])
            if not defect.layer_ids:
                skipped.append(_skip(defect, (), "unlocalized_defect"))
                continue
            if any(lid in claimed for lid in defect.layer_ids):
                skipped.append(
                    _skip(defect, tuple(defect.layer_ids), "superseded_by_higher_priority")
                )
                continue
            resolver = self._resolvers[defect.category]
            defect_ops, defect_skipped = resolver.resolve(
                mapspec, defect, attempt=attempt
            )
            if defect_ops:
                for op in defect_ops:
                    for lid in op.layer_ids:
                        claimed.setdefault(lid, key)
            ops.extend(defect_ops)
            skipped.extend(defect_skipped)
        return VisualHealPlan(
            defects=tuple(defects),
            ops=tuple(ops),
            skipped=tuple(skipped),
            defect_fingerprint=defect_fingerprint(defects),
            ops_signature=_ops_signature(ops),
        )


# ─────────────────────────────────────────────────────────────────────────────
# 计划应用（COW：只深拷贝被触碰的图层分支；重排不复制图层本体）
# ─────────────────────────────────────────────────────────────────────────────


def apply_heal_plan(
    mapspec: Optional[Dict[str, Any]], plan: VisualHealPlan
) -> Tuple[Dict[str, Any], int]:
    """将 plan 应用到 spec 副本。返回 (new_mapspec, applied_op_count)。

    未命中任何图层的 op 不计数——引擎以 applied==0 视为空操作诚实拒绝。
    """
    out: Dict[str, Any] = dict(mapspec) if isinstance(mapspec, dict) else {}
    layers: List[Any] = list(out.get("layers") or [])
    out["layers"] = layers
    applied = 0
    copied: Dict[str, Dict[str, Any]] = {}

    def layer_copy(layer_id: str) -> Optional[Dict[str, Any]]:
        if layer_id in copied:
            return copied[layer_id]
        for index, layer in enumerate(layers):
            if isinstance(layer, dict) and str(layer.get("id") or "") == layer_id:
                clone = copy.deepcopy(layer)
                layers[index] = clone
                copied[layer_id] = clone
                return clone
        return None

    for op in plan.ops:
        if op.op == MUTATION_HEAL_LABEL_COLLISION:
            hit = False
            for layer_id in op.layer_ids:
                layer = layer_copy(layer_id)
                if layer is None:
                    continue
                layout = dict(layer.get("layout") or {})
                layout.update(op.layout_patch or {})
                layer["layout"] = layout
                hit = True
            if hit:
                applied += 1
        elif op.op == MUTATION_HEAL_CONTRAST:
            hit = False
            for layer_id in op.layer_ids:
                layer = layer_copy(layer_id)
                if layer is not None and apply_palette_to_layer(layer, list(op.colors or ())):
                    hit = True
            if hit:
                applied += 1
        elif op.op == MUTATION_HEAL_LAYER_ORDER:
            if not op.move_above:
                continue
            target_id, occluder_id = op.move_above
            target_layer = next(
                (layer for layer in layers if isinstance(layer, dict) and str(layer.get("id") or "") == target_id),
                None,
            )
            if target_layer is None:
                continue
            current_ids = [
                str(layer.get("id") or "") for layer in layers if isinstance(layer, dict)
            ]
            if target_id not in current_ids or occluder_id not in current_ids:
                continue
            layers.remove(target_layer)
            occluder_index = next(
                (i for i, layer in enumerate(layers)
                 if isinstance(layer, dict) and str(layer.get("id") or "") == occluder_id),
                None,
            )
            if occluder_index is None:  # 防御：理论上不可达（前置校验过）
                layers.insert(
                    next(i for i, layer in enumerate(layers)
                         if isinstance(layer, dict) and str(layer.get("id") or "") == target_id),
                    target_layer,
                )
                continue
            layers.insert(occluder_index + 1, target_layer)
            applied += 1
        elif op.op == MUTATION_HEAL_OPACITY:
            hit = False
            for layer_id, value in (op.paint_opacity or {}).items():
                layer = layer_copy(layer_id)
                if layer is None:
                    continue
                opacity_key = _OPACITY_PAINT_KEYS.get(str(layer.get("type") or ""))
                if opacity_key is None:
                    continue
                paint = dict(layer.get("paint") or {})
                paint[opacity_key] = value
                layer["paint"] = paint
                hit = True
            if hit:
                applied += 1
        else:
            logger.warning("visual heal: unknown op dropped: %s", op.op)
    return out, applied
