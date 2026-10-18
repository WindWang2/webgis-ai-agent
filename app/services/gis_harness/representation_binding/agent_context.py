"""下一轮 Agent context 的 representation binding 有界摘要（ADR-0219 C6）。

只注入有界语义摘要——用户 pin 的 palette/表达、隐藏/锁定图层、显式禁用
的组件、最近语义编辑 revision、requirement↔mapspec 漂移信号——
**绝不**重复注入完整 MapSpec/Requirement 文档（token 有界 + 单一真相纪律）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.gis_harness.representation_binding.requirement_to_mapspec import (
    _user_owns,
)
from app.services.gis_harness.requirement_ir.normalize import (
    normalize_geometry_kind,
    normalize_palette,
)

_MAX_LAYERS = 8
_MAX_DRIFT = 4


def representation_binding_summary(
    doc: Any,
    mapspec: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """有界摘要（纯函数；doc 为 RequirementDocument，缺席字段诚实留空）。"""
    summary: Dict[str, Any] = {
        "schema": "representation_binding_summary.v1",
        "document_id": getattr(doc, "document_id", ""),
        "lifecycle": getattr(doc.lifecycle, "value", str(getattr(doc, "lifecycle", ""))),
        "requirement_revision": getattr(doc, "revision", 0),
    }
    if doc.lifecycle == "superseded":
        summary["superseded"] = True
        return summary

    spec = doc.intent
    rep = spec.representation
    comps = spec.components

    pinned: Dict[str, str] = {}
    if rep.palette and _user_owns(doc, "representation.palette"):
        normalized = normalize_palette(rep.palette)
        if normalized:
            pinned["palette"] = normalized
    if rep.geometry_kind and _user_owns(doc, "representation.geometry_kind"):
        pinned["representation"] = normalize_geometry_kind(rep.geometry_kind) or rep.geometry_kind
    summary["user_pinned"] = pinned

    hidden = [x for x in rep.hidden_layer_ids
              if _user_owns(doc, "representation.hidden_layer_ids")][:_MAX_LAYERS]
    locked = [x for x in rep.locked_layer_ids
              if _user_owns(doc, "representation.locked_layer_ids")][:_MAX_LAYERS]
    summary["hidden_layer_ids"] = list(hidden)
    summary["locked_layer_ids"] = list(locked)

    disabled: List[str] = []
    for comp_type in ("legend", "title", "scale_bar", "north_arrow", "labels", "attribution"):
        if getattr(comps, comp_type, None) is False and _user_owns(doc, f"components.{comp_type}"):
            disabled.append(comp_type)
    summary["disabled_components"] = disabled[:_MAX_LAYERS]
    summary["latest_semantic_edit"] = {
        "revision": getattr(doc, "revision", 0),
        "turn": getattr(doc, "updated_turn", 0),
    }

    # requirement ↔ mapspec 漂移信号（bounded；供下一轮规划参考，
    # 不做裁决——裁决走 C3 绑定事务）。族归一比对（review P2#13）：
    # requirement 存逻辑基 id（roads），spec 是物理 id（roads-1）。
    drift: List[Dict[str, str]] = []
    if isinstance(mapspec, dict) and hidden:
        def _base(layer_id: str) -> str:
            for sep in ("-", "__"):
                pos = layer_id.find(sep)
                if pos > 0:
                    return layer_id[:pos]
            return layer_id

        visible_families = {
            _base(lyr.get("id"))
            for lyr in mapspec.get("layers") or []
            if isinstance(lyr, dict) and isinstance(lyr.get("id"), str)
            # layout 非 dict（畸形可落盘值）按可见缺省处理（与 layout 缺席
            # 同口径：缺省 visibility=visible）——不得对真值非 dict 裸 .get。
            and (
                not isinstance(lyr.get("layout"), dict)
                or (lyr.get("layout") or {}).get("visibility", "visible") != "none"
            )
        }
        for layer_id in hidden[:_MAX_DRIFT]:
            if layer_id in visible_families:
                drift.append({"layer_id": layer_id, "signal": "user_hidden_but_visible"})
    if drift:
        summary["drift"] = drift
    return summary


def render_summary_block(summary: Dict[str, Any]) -> str:
    """摘要 → 有界文本块（Pi prompt 注入面；空面不渲染）。"""
    if not summary or summary.get("lifecycle") == "superseded":
        return ""
    lines: List[str] = ["[用户制图约束]（requirement↔mapspec 语义绑定；用户显式选择优先）"]
    pinned = summary.get("user_pinned") or {}
    if pinned.get("palette"):
        lines.append(f"- 用户 pin 色带: {pinned['palette']}（重分级/重着色必须保留）")
    if pinned.get("representation"):
        lines.append(f"- 用户 pin 表达: {pinned['representation']}")
    hidden = summary.get("hidden_layer_ids") or []
    if hidden:
        lines.append(f"- 用户隐藏图层: {', '.join(hidden)}（不得自动恢复显示）")
    locked = summary.get("locked_layer_ids") or []
    if locked:
        lines.append(f"- 用户锁定图层: {', '.join(locked)}（变更前必须请求用户）")
    disabled = summary.get("disabled_components") or []
    if disabled:
        lines.append(f"- 用户禁用组件: {', '.join(disabled)}（不得自动补回）")
    if len(lines) == 1:
        return ""    # 无用户约束面 → 不注入（空会话 byte-equivalence 纪律）
    edit = summary.get("latest_semantic_edit") or {}
    if edit:
        lines.append(
            f"- 最近语义编辑: requirement revision {edit.get('revision')}（turn {edit.get('turn')}）")
    return "\n".join(lines[:16])
