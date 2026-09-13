"""Self-heal action registry（ADR-0158 P4/P5）—— 纯函数、有界、风险分级。

设计约束（docs/cartographic-closed-loop.md + 用户 wins 纪律）：

- **两个执行面**：
  - ``runtime``（同代前端 patch）：只做把 live 状态拉回权威 MapSpec 投影的
    修复（恢复可见性/透明度/图例/样式）——跨代语义变更若只改 live 会立刻
    违反 RUNTIME_MAPSPEC_GENERATION 收敛检查，架构上被禁止。
  - ``desired_state``（lifecycle 呈现提交）：改色带/分类方法/级数/值域/标注
    等呈现语义动作，经 ``mapspec_store.layer_upsert`` 走既有生命周期
    （自动获得 deterministic 复审 + 锁 guard + 世代推进），前端经常规
    MapSpec 同步通道收敛 —— 与模板 symbology 追踪同一机制。
- **风险分级**：``auto_safe``（呈现级、可自动执行）<
  ``auto_with_semantic_risk``（默认只计划+披露，需 ``CARTO_SELFHEAL_EXPLICIT=1``
  显式授权）< ``explicit_only``（永不自动执行，仅建议）。
- **排序契约**：``risk`` 升序 → ``expected_effect`` 降序 → action_id 字典序
  （确定性并列裁决）；取首个被授权且适用的动作。
- **03 线联动**：``SymbologyDecision.rejected[]``（PR #1258，未合入时由
  fixture 按 ``cartographic_profile.symbology_decision.rejected`` 形状提供）
  的落选者天然是候选修复动作 —— ``candidates_from_rejected`` 读取该约定。
- 修复-重评-回退（P5）由 ``cartography_runtime._advance_runtime_cartographic_repair``
  编排；本模块只提供动作目录、适用性判定、补丁/提交构造与质量快照比较。
"""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

# ── 风险与执行面常量 ─────────────────────────────────────────────────────

RISK_AUTO_SAFE = "auto_safe"
RISK_SEMANTIC = "auto_with_semantic_risk"
RISK_EXPLICIT_ONLY = "explicit_only"
_RISK_RANK = {RISK_AUTO_SAFE: 0, RISK_SEMANTIC: 1, RISK_EXPLICIT_ONLY: 2}

SURFACE_RUNTIME = "runtime"                # 同代前端 patch
SURFACE_DESIRED_STATE = "desired_state"    # lifecycle 呈现提交


def explicit_authorization_enabled() -> bool:
    """semantic-risk 动作的显式授权开关（默认关 —— 只计划+披露）。"""
    return os.getenv("CARTO_SELFHEAL_EXPLICIT", "").strip().lower() in (
        "1", "true", "yes"
    )


@dataclass(frozen=True)
class SelfHealActionSpec:
    """一个自愈动作的静态契约。"""
    action_id: str
    risk: str
    surface: str
    expected_effect: float          # 0-1，同风险内降序排序键
    description: str
    triggers: frozenset             # 命中的 fail 规则 / 视觉维度


def _spec(
    action_id: str,
    risk: str,
    surface: str,
    expected_effect: float,
    description: str,
    triggers: "tuple[str, ...]",
) -> SelfHealActionSpec:
    return SelfHealActionSpec(
        action_id=action_id,
        risk=risk,
        surface=surface,
        expected_effect=expected_effect,
        description=description,
        triggers=frozenset(triggers),
    )


#: 动作注册表（≥5 种动作类型；trigger 词汇 = 运行态规则 ∪ 确定性规则前缀
#: ∪ 视觉维度 VISUAL_<DIM>）。
SELFHEAL_ACTIONS: Tuple[SelfHealActionSpec, ...] = (
    # ── runtime 同代投影恢复（既有 4 条，行为不变） ──
    _spec("restore_visibility", RISK_AUTO_SAFE, SURFACE_RUNTIME, 0.9,
          "把 live 可见性恢复为权威 MapSpec 投影",
          {"RUNTIME_RESULT_VISIBILITY"}),
    _spec("reapply_opacity", RISK_AUTO_SAFE, SURFACE_RUNTIME, 0.8,
          "把 live 透明度收敛回有限期望值",
          {"RUNTIME_OPACITY_CONVERGENCE"}),
    _spec("refresh_legend", RISK_AUTO_SAFE, SURFACE_RUNTIME, 0.7,
          "把 live 图例刷新为权威 legend_spec",
          {"RUNTIME_LEGEND_CONVERGENCE"}),
    _spec("restore_style_projection", RISK_AUTO_SAFE, SURFACE_RUNTIME, 0.7,
          "把 live 样式投影恢复为权威 paint",
          {"RUNTIME_STYLE_CONVERGENCE"}),
    # ── desired_state lifecycle 呈现提交（新增动作类型） ──
    _spec("rotate_palette", RISK_AUTO_SAFE, SURFACE_DESIRED_STATE, 0.6,
          "换色带：按感知色差从注册表选取替代色带（呈现级）",
          {"VISUAL_COLOR_DISCRIMINABILITY", "carto.color.separability"}),
    _spec("clamp_layout", RISK_AUTO_SAFE, SURFACE_DESIRED_STATE, 0.4,
          "改版面：整饰类呈现钳制（图例位置/浮动组件越界回钳）",
          {"VISUAL_COMPOSITION_BALANCE", "VISUAL_POLISH_COMPLETENESS",
           "carto.load.ratio"}),
    _spec("adjust_classification", RISK_SEMANTIC, SURFACE_DESIRED_STATE, 0.7,
          "换分类方法/级数：优先复用 SymbologyDecision.rejected[] 落选者",
          {"VISUAL_READABILITY", "VISUAL_INFORMATION_DENSITY",
           "carto.color.separability"}),
    _spec("clip_value_domain", RISK_SEMANTIC, SURFACE_DESIRED_STATE, 0.5,
          "重裁值域：对分类断点做分位裁剪（语义级）",
          {"VISUAL_INFORMATION_DENSITY", "carto.load.ratio"}),
    _spec("adjust_labels", RISK_SEMANTIC, SURFACE_DESIRED_STATE, 0.4,
          "改标注策略：注记抽稀/字号降档（牺牲部分可读性，语义级）",
          {"VISUAL_READABILITY", "carto.label.collision_est"}),
    _spec("switch_map_type", RISK_EXPLICIT_ONLY, SURFACE_DESIRED_STATE, 0.6,
          "换图型：仅作为建议上报，永不自动执行",
          {"VISUAL_READABILITY", "VISUAL_INFORMATION_DENSITY"}),
)


def actions_by_id() -> Dict[str, SelfHealActionSpec]:
    return {spec.action_id: spec for spec in SELFHEAL_ACTIONS}


def authorized(spec: SelfHealActionSpec) -> bool:
    if spec.risk == RISK_AUTO_SAFE:
        return True
    if spec.risk == RISK_SEMANTIC:
        return explicit_authorization_enabled()
    return False


# ── 触发词提取（评审证据 → 触发集合） ───────────────────────────────────

def _iter_review_checks(cartography: Dict[str, Any]):
    """评审检查行的两个证据面：runtime checks + desired_review checks。

    期望态失败（如 ``carto.color.separability``）只落在
    ``desired_review.checks`` —— 运行时自愈若看不到它们，呈现提交类动作
    （换色带/改版面）永远不会被触发。
    """
    for check in cartography.get("checks") or []:
        yield check
    desired = cartography.get("desired_review")
    if isinstance(desired, dict):
        for check in desired.get("checks") or []:
            yield check


def triggers_from_review(cartography: Dict[str, Any]) -> Set[str]:
    """从 cartography 证据提取失败规则与视觉维度触发词。"""
    triggers: set = set()
    for check in _iter_review_checks(cartography):
        if not isinstance(check, dict) or check.get("status") not in ("fail", "warning"):
            continue
        rule = str(check.get("rule") or "")
        if rule:
            triggers.add(rule)
    return triggers


# ── 动作选择（risk 升序 + expected_effect 降序 + 确定性并列） ────────────

def select_actions(
    triggers: Set[str],
    *,
    tried_actions: Optional[List[str]] = None,
) -> List[SelfHealActionSpec]:
    """返回按排序契约排列的适用未试动作（含未授权动作，供披露）。"""
    tried = set(tried_actions or [])
    ranked = []
    for spec in SELFHEAL_ACTIONS:
        if spec.action_id in tried:
            continue
        hit = spec.triggers & triggers
        if not hit:
            continue
        ranked.append(((_RISK_RANK[spec.risk], -spec.expected_effect,
                        spec.action_id), spec))
    ranked.sort(key=lambda item: item[0])
    return [spec for _, spec in ranked]


# ── 03 线联动：SymbologyDecision.rejected[] 候选（接口 + fixture 消费） ──

def candidates_from_rejected(mapspec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """读取 03 线落选者约定（未合入时无该键 → 空列表，诚实降级）。

    约定形状（``cartographic_profile.symbology_decision.rejected[]``）::
        {"method": "quantiles", "k": 5, "expected_effect": 0.55,
         "reason": "heavy-tailed distribution"}
    每条都是 ``adjust_classification`` 的具体参数候选。
    """
    profile = mapspec.get("cartographic_profile")
    if not isinstance(profile, dict):
        return []
    decision = profile.get("symbology_decision")
    if not isinstance(decision, dict):
        return []
    rejected = decision.get("rejected")
    if not isinstance(rejected, list):
        return []
    return [item for item in rejected if isinstance(item, dict)]


# ── patch / 提交构造 ─────────────────────────────────────────────────────

def build_runtime_patch(
    spec: SelfHealActionSpec,
    *,
    layer: Dict[str, Any],
    observed: Dict[str, Any],
    runtime_layer_id: str,
    intent_generation: int,
) -> Optional[Dict[str, Any]]:
    """runtime 面：把权威投影构造为同代前端 patch（与既有 planner 同形）。"""
    if spec.surface != SURFACE_RUNTIME:
        return None
    desired: Dict[str, Any] = {}
    before: Dict[str, Any] = {"_intentGeneration": intent_generation}
    action_id = spec.action_id
    if action_id == "restore_visibility":
        layout = layer.get("layout") if isinstance(layer.get("layout"), dict) else {}
        desired["visible"] = (
            layer.get("visible") is not False and layout.get("visibility") != "none"
        )
        before["visible"] = observed.get("visible")
    elif action_id == "reapply_opacity":
        from app.lib.cartography.runtime_repair import _finite_opacity

        opacity = _finite_opacity(layer)
        if opacity is None:
            return None
        desired["opacity"] = opacity
        before["opacity"] = observed.get("opacity")
    elif action_id == "refresh_legend":
        desired["legend_spec"] = layer.get("legend_spec")
        before["legend_spec"] = observed.get("legend_spec")
    elif action_id == "restore_style_projection":
        from app.lib.cartography.runtime_repair import _style_projection

        style = _style_projection(layer)
        if not style:
            return None
        desired["style"] = style
        before["style"] = observed.get("style")
    else:
        return None
    if not desired:
        return None
    return {
        "layer_id": runtime_layer_id,
        "mapspec_layer_id": str(layer.get("id") or ""),
        "before": before,
        "desired": desired,
        "rules": [spec.action_id],
    }


def build_presentation_commit(
    spec: SelfHealActionSpec,
    *,
    layer: Dict[str, Any],
    rejected: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """desired_state 面：构造 layer 的呈现级变更（供 lifecycle layer_upsert）。

    只产出呈现字段（paint 颜色 / legend_spec 呈现部分），不改数据绑定、
    分类断点语义键、filters、source。无法构造适用变更 → None（诚实跳过）。
    """
    if spec.surface != SURFACE_DESIRED_STATE:
        return None
    action_id = spec.action_id
    if action_id == "rotate_palette":
        return _build_palette_rotation(layer)
    if action_id == "clamp_layout":
        return _build_layout_clamp(layer)
    if action_id == "adjust_classification":
        return _build_classification_adjustment(layer, rejected)
    if action_id == "clip_value_domain":
        return _build_value_clip(layer)
    if action_id == "adjust_labels":
        return _build_label_adjustment(layer)
    if action_id == "switch_map_type":
        # explicit_only：构造器永不出手（仅作为建议出现在 plan 披露里）。
        return None
    return None


def _legend_colors(legend_spec: Dict[str, Any]) -> Optional[str]:
    for key in ("palette_colors", "colors"):
        if isinstance(legend_spec.get(key), list) and legend_spec[key]:
            return key
    return None


def _rotate_step_colors(paint_spec: Dict[str, Any], colors: List[str]) -> None:
    """按既有 step/interpolate/match 结构替换输出色（呈现级，结构不动）。"""
    method = paint_spec.get("method")
    if method == "step":
        if paint_spec.get("default") is not None:
            paint_spec["default"] = colors[0]
            rest = colors[1:]
        else:
            rest = colors
        for stop, color in zip(paint_spec.get("stops") or [], rest):
            if isinstance(stop, (list, tuple)) and len(stop) >= 2:
                stop[1] = color
    elif method == "interpolate":
        for stop, color in zip(paint_spec.get("stops") or [], colors):
            if isinstance(stop, (list, tuple)) and len(stop) >= 2:
                stop[1] = color
    elif method == "match":
        for case, color in zip(paint_spec.get("cases") or [], colors):
            if isinstance(case, (list, tuple)) and len(case) >= 2:
                case[1] = color


def _build_palette_rotation(layer: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """换色带：从注册表选当前色带之外的替代（感知色差最优，名字并列裁决）。

    同时产出 paint 输出色补丁 —— legend 与 paint 必须一起换，否则新提交
    会被 LEGEND_STYLE_EQUIVALENCE（paint↔legend 漂移）拒绝。"""
    legend_spec = layer.get("legend_spec")
    if not isinstance(legend_spec, dict):
        return None
    ramp_key = _legend_colors(legend_spec)
    if ramp_key is None:
        return None
    current_colors = [str(c) for c in legend_spec[ramp_key]]
    n = len(current_colors)
    if n < 2:
        return None
    try:
        from app.lib.cartography.palettes import (
            COLOR_PALETTES,
            min_adjacent_delta_e,
            resolve_palette_colors,
        )
    except Exception:  # noqa: BLE001 — 注册表缺席 = 动作不可构造
        return None
    candidates: List[tuple] = []
    for name in sorted(COLOR_PALETTES):
        if name.lower() == str(legend_spec.get("palette") or "").lower():
            continue
        try:
            colors = resolve_palette_colors(name)
        except Exception:  # noqa: BLE001 — 单个候选失败不阻塞
            continue
        if len(colors) < n:
            continue
        trimmed = colors[:n] if len(colors) > n else colors
        try:
            delta_e = min_adjacent_delta_e(trimmed) or 0.0
        except Exception:  # noqa: BLE001
            delta_e = 0.0
        candidates.append((-delta_e, name, trimmed))
    if not candidates:
        return None
    candidates.sort()
    _, best_name, best_colors = candidates[0]
    # 颜色数对齐：多于 n 时等距采样 n 个（呈现级、确定性）。
    if len(best_colors) != n:
        step = (len(best_colors) - 1) / (n - 1)
        best_colors = [
            best_colors[round(index * step)] for index in range(n)
        ]
    new_legend = dict(legend_spec)
    new_legend[ramp_key] = best_colors
    new_legend["palette"] = best_name
    categories = legend_spec.get("categories")
    if isinstance(categories, list) and len(categories) == len(best_colors):
        new_categories = []
        for category, color in zip(categories, best_colors):
            if isinstance(category, dict):
                new_categories.append({**category, "color": color})
            else:
                new_categories.append(category)
        new_legend["categories"] = new_categories
    # paint 输出色同步轮换（与 quality_loop._apply_palette_change 同语义）。
    paint = layer.get("paint")
    paint_patch: Optional[Dict[str, Any]] = None
    if isinstance(paint, dict):
        for prop, spec in paint.items():
            if not isinstance(spec, dict) or not spec.get("method"):
                continue
            cloned = copy.deepcopy(spec)
            _rotate_step_colors(cloned, best_colors)
            paint_patch = paint_patch or {}
            paint_patch[prop] = cloned
    return {
        "operation": "rotate_palette",
        "layer_id": layer.get("id"),
        "legend_spec": new_legend,
        **({"paint": paint_patch} if paint_patch else {}),
        "before_colors": current_colors,
        "after_colors": best_colors,
        "palette": best_name,
    }


def _build_layout_clamp(layer: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """改版面（呈现级）：仅整饰钳制 —— 图例显式可见性翻转不做，这里只把
    legend_spec 的越界放置字段钳回有效范围；无放置信息 → 不适用。"""
    legend_spec = layer.get("legend_spec")
    if not isinstance(legend_spec, dict):
        return None
    placement = legend_spec.get("placement")
    if not isinstance(placement, dict):
        return None
    clamped = dict(placement)
    changed = False
    for key in ("x", "y"):
        value = clamped.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            bounded = min(1.0, max(0.0, float(value)))
            if bounded != value:
                clamped[key] = bounded
                changed = True
    if not changed:
        return None
    return {
        "operation": "clamp_layout",
        "layer_id": layer.get("id"),
        "legend_spec": {**legend_spec, "placement": clamped},
        "before_placement": placement,
        "after_placement": clamped,
    }


def _build_classification_adjustment(
    layer: Dict[str, Any], rejected: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """换分类方法/级数（语义级）：仅当 03 线落选者提供显式候选时构造 ——
    本模块绝不自行猜新断点；断点重算发生在工具/recipe 侧。"""
    if not isinstance(rejected, dict):
        return None
    method = rejected.get("method")
    k = rejected.get("k")
    if method not in ("quantiles", "equal_interval", "natural_breaks", "head_tail"):
        return None
    if not isinstance(k, int) or isinstance(k, bool) or not (2 <= k <= 9):
        return None
    legend_spec = layer.get("legend_spec")
    if not isinstance(legend_spec, dict):
        return None
    return {
        "operation": "adjust_classification",
        "layer_id": layer.get("id"),
        "method": method,
        "k": k,
        "reason": str(rejected.get("reason") or "")[:200],
        "expected_effect": rejected.get("expected_effect"),
    }


def _build_value_clip(layer: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """重裁值域（语义级）：产出裁剪建议参数；断点重算由工具侧完成。"""
    legend_spec = layer.get("legend_spec")
    if not isinstance(legend_spec, dict):
        return None
    breaks = legend_spec.get("breaks")
    if not isinstance(breaks, list) or len(breaks) < 3:
        return None
    finite = [b for b in breaks if isinstance(b, (int, float))
              and not isinstance(b, bool)]
    if len(finite) != len(breaks):
        return None
    return {
        "operation": "clip_value_domain",
        "layer_id": layer.get("id"),
        "strategy": "winsorize_p02_p98",
        "break_count": len(breaks),
    }


def _build_label_adjustment(layer: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """改标注策略（语义级）：注记呈现降档建议（layout text 字段钳制）。"""
    layout = layer.get("layout")
    if not isinstance(layout, dict):
        return None
    text_field = layout.get("text-field")
    if not text_field:
        return None
    current = layout.get("text-size")
    return {
        "operation": "adjust_labels",
        "layer_id": layer.get("id"),
        "before_text_size": current,
        "strategy": "thin_and_shrink",
    }


# ── 质量快照（P5 改善判定；确定性优先，visual 只作降级信号） ─────────────

def quality_snapshot(cartography: Dict[str, Any]) -> Dict[str, int]:
    """有界质量快照：误差计数元组（越小越好）。

    deterministic fail > deterministic warning > visual error 的字典序：
    视觉结论不能盖过确定性证据，只能在自己的层级内变差/变好。两个证据面
    （runtime checks + desired_review checks）都计入。
    """
    deterministic_fail = 0
    deterministic_warning = 0
    visual_error = 0
    visual_warning = 0
    for check in _iter_review_checks(cartography):
        if not isinstance(check, dict):
            continue
        status = check.get("status")
        if check.get("evidence_class") == "visual":
            if status == "fail":
                visual_error += 1
            elif status == "warning":
                visual_warning += 1
            continue
        if status == "fail":
            deterministic_fail += 1
        elif status == "warning":
            deterministic_warning += 1
    return {
        "deterministic_fail": deterministic_fail,
        "deterministic_warning": deterministic_warning,
        "visual_error": visual_error,
        "visual_warning": visual_warning,
    }


def quality_improved(before: Optional[Dict[str, int]], after: Dict[str, int]) -> bool:
    """严格改善：字典序下降。相等或上升都算未改善（诚实，不自欺）。"""
    if not before:
        return False
    return _quality_tuple(after) < _quality_tuple(before)


def quality_worse(before: Optional[Dict[str, int]], after: Dict[str, int]) -> bool:
    """严格变差：字典序上升（确定性维度优先 —— 有害变更必须回退）。"""
    if not before:
        return False
    return _quality_tuple(after) > _quality_tuple(before)


def _quality_tuple(snapshot: Optional[Dict[str, int]]) -> tuple:
    if not snapshot:
        return (0, 0, 0, 0)
    return (
        snapshot.get("deterministic_fail", 0), snapshot.get("deterministic_warning", 0),
        snapshot.get("visual_error", 0), snapshot.get("visual_warning", 0),
    )


__all__ = [
    "RISK_AUTO_SAFE",
    "RISK_SEMANTIC",
    "RISK_EXPLICIT_ONLY",
    "SELFHEAL_ACTIONS",
    "SURFACE_DESIRED_STATE",
    "SURFACE_RUNTIME",
    "SelfHealActionSpec",
    "authorized",
    "build_presentation_commit",
    "build_runtime_patch",
    "candidates_from_rejected",
    "explicit_authorization_enabled",
    "quality_improved",
    "quality_snapshot",
    "quality_worse",
    "select_actions",
    "triggers_from_review",
]
