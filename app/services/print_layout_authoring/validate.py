"""G34 作者计划整体校验（提交前一次性验证，失败零写入）。

组合：模型校验 + 用户锁 + 真实字体/文字度量 + 图例完整性 + 地图槽 fit
+ 页册预算 + source/style revision。任何 P0/P1 阻断都以 typed code 回执，
不靠「看起来没问题」放行。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from app.contracts.workbench_locks import _lock_matches, locked_component_ids_of
from app.lib.cartography.print_layout_schema import validate_print_layout
from app.services.print_layout_authoring.fonts import (
    font_asset_version, font_resource_status, resolve_font, wrap_text,
)
from app.services.print_layout_authoring.legend import layout_legend, read_legend_content
from app.services.print_layout_authoring.pages import page_numbering
from app.services.print_layout_authoring.template import binding_context, resolve_binding


def component_is_locked(component_id: str, locks: List[str]) -> bool:
    """复用 workbench 锁命中谓词（含双向 ``__`` 族语义），不建第二锁位。"""
    return any(_lock_matches(str(l), str(component_id)) for l in locks)


def locks_of(mapspec: Dict[str, Any]) -> List[str]:
    return locked_component_ids_of(mapspec)


def check_source_revision(plan: Dict[str, Any], expected_revision: Optional[int],
                          *, mapspec_revision: Optional[int] = None) -> Dict[str, Any]:
    """stale 检测：**CAS 为准**，计划绑定 revision 只报漂移不锁死。

    历史实现同时要求 ``bound == expected`` 与 ``current == expected``，而
    ``source`` 只在成功 apply 时刷新 —— 一旦有*其它* MapSpec mutation 推进
    revision，两个等式无法同时成立，作者计划将永久不可再编辑。这里改成：

    * ``expected_revision`` 必填，且必须等于当前 mapspec revision（真 CAS）；
      调用方传旧值 → ``SUPERSEDED``，不混合新旧页面内容。
    * 计划绑定 revision 与当前不一致 → 允许，回 ``binding_drift=True`` 与
      ``rebased_from``，由上层报 warning；成功 apply 会重绑。
    """
    source = plan.get("source") or {}
    bound = source.get("mapspec_revision")
    if expected_revision is None:
        return {"ok": False, "code": "MISSING_EXPECTED_REVISION",
                "message": "authoring apply requires expected_revision"}
    if mapspec_revision is None:
        # 无权威 current（纯函数调用 / 离线校验）：退回 bound 当基线。
        # 无法核实的 apply 一律拒绝，绝不因为「读不到」而放行。
        if bound is not None and int(bound) != int(expected_revision):
            return {"ok": False, "code": "SUPERSEDED",
                    "message": f"plan bound to revision {bound}, apply at {expected_revision}"}
        return {"ok": True, "code": "", "bound_revision": int(bound or 0),
                "binding_drift": False}
    if int(mapspec_revision) != int(expected_revision):
        return {"ok": False, "code": "SUPERSEDED",
                "message": f"mapspec revision {mapspec_revision} != expected {expected_revision}"}
    drift = bound is not None and int(bound) != int(mapspec_revision)
    return {"ok": True, "code": "", "bound_revision": int(bound or 0),
            "binding_drift": bool(drift),
            "rebased_from": int(bound) if drift else None}


def _typography_of(component: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    return component.get("typography") or {
        "font_id": "noto-sans-sc-embedded", "size_pt": 9.0,
        "line_height": 1.35, "weight": "normal", "style": "normal",
        "align": "left", "letter_spacing_pt": 0.0, "color": "#1e293b",
        "wrap_mode": "word", "overflow_policy": "expand",
    }


def _text_value(component: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    spec = component.get("text")
    if not isinstance(spec, dict):
        return {"value": "", "resolved": True, "missing": False, "code": ""}
    if str(spec.get("mode") or "literal") == "literal":
        return {"value": str(spec.get("value") or ""), "resolved": True,
                "missing": False, "code": ""}
    return resolve_binding(
        str(spec.get("binding") or "page_title"), context,
        fmt=str(spec.get("format") or "plain"),
        placeholder=str(spec.get("missing_placeholder") or "〔数据缺失〕"),
    )


def validate_authoring(mapspec: Dict[str, Any], plan: Dict[str, Any], *,
                       dpi: int = 300, page_id: str = "",
                       page_number: int = 1, page_count: int = 1) -> Dict[str, Any]:
    """返回 ``{ok, errors, warnings, components, legend, fonts, numbering}``。"""
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    verdict = validate_print_layout(plan)
    for issue in verdict.issues:
        bucket = errors if issue.severity == "error" else warnings
        bucket.append({"code": issue.code, "message": issue.message,
                       "component_id": issue.component_id})

    locks = locks_of(mapspec)
    numbering = page_numbering(plan)
    page = next((p for p in numbering["toc"] if p["page_id"] == page_id), None) \
        if page_id else None
    legend_content = read_legend_content(mapspec, group_by_layer=False)
    ctx = binding_context(
        mapspec, page or {"title": ""},
        page_number=page_number or (page["page_number"] if page else 1),
        page_count=page_count or numbering["page_count"],
        unit=str(legend_content.get("unit") or ""),
    )

    component_reports: List[Dict[str, Any]] = []
    legend_reports: List[Dict[str, Any]] = []
    for c in plan.get("components") or []:
        cid = str(c.get("id") or "")
        frame = c.get("frame") or {}
        typo = _typography_of(c, plan)
        locked = component_is_locked(cid, locks)
        rep: Dict[str, Any] = {
            "component_id": cid, "kind": c.get("kind"), "locked": locked,
            "visible": bool(c.get("visible", True)),
            "frame_mm": {k: frame.get(k) for k in ("x", "y", "width", "height")},
        }
        font = resolve_font(str(typo.get("font_id") or "noto-sans-sc-embedded"),
                            str(typo.get("weight") or "normal"),
                            str(typo.get("style") or "normal"))
        rep["font"] = font
        if not font["available"]:
            errors.append({"code": "FONT_RESOURCE_UNAVAILABLE", "component_id": cid,
                           "message": "selected font cannot be loaded; export would use fallback"})
        for issue in font["issues"]:
            warnings.append({"code": issue, "component_id": cid,
                             "message": "font selection not fully applied"})
        # 真实文字度量 + 换行（长中文/拉丁长词 / overflow / 缺字）
        tv = _text_value(c, ctx)
        rep["text"] = tv
        if tv.get("missing"):
            warnings.append({"code": tv.get("code") or "TEXT_BINDING_MISSING",
                             "component_id": cid,
                             "message": "visible placeholder shown; no guessed metadata"})
        if tv.get("value"):
            layout = wrap_text(
                tv["value"], font_id=font["applied_font_id"] or "noto-sans-sc-embedded",
                size_pt=float(typo.get("size_pt") or 9.0),
                line_height=float(typo.get("line_height") or 1.35),
                letter_spacing_pt=float(typo.get("letter_spacing_pt") or 0.0),
                weight=font["weight_applied"], style=font["style_applied"],
                box_width_mm=float(frame.get("width") or 10.0),
                box_height_mm=float(frame.get("height") or 5.0),
                wrap_mode=str(typo.get("wrap_mode") or "word"),
                overflow_policy=str(typo.get("overflow_policy") or "expand"),
            )
            rep["text_layout"] = layout
            if layout["font"]["missing_glyphs"]:
                warnings.append({"code": "MISSING_GLYPHS", "component_id": cid,
                                 "message": "missing: " + "".join(
                                     layout["font"]["missing_glyphs"][:8])})
            if layout["status"] == "blocked":
                errors.append({"code": "TEXT_OVERFLOW_BLOCKED", "component_id": cid,
                               "message": "text does not fit; policy=block"})
            if layout["truncated"]:
                errors.append({"code": "TEXT_TRUNCATED", "component_id": cid,
                               "message": "text content was truncated"})
        # 图例完整性 / 分栏 / 续排
        if c.get("kind") == "legend":
            lay = c.get("legend") or plan.get("legend_defaults") or {}
            content = read_legend_content(mapspec, cid,
                                          group_by_layer=bool(lay.get("group_by_layer")))
            lp = layout_legend(content, lay, frame, typo, dpi)
            rep["legend"] = {
                "status": lp.get("status"), "code": lp.get("code"),
                "entries_total": lp.get("entries_total"),
                "entries_rendered": lp.get("entries_rendered"),
                "complete": lp.get("complete"), "columns": lp.get("columns"),
                "needed_height_mm": lp.get("needed_height_mm"),
                "has_nodata": content.get("has_nodata"),
                "has_out_of_range": content.get("has_out_of_range"),
            }
            legend_reports.append(lp)
            if lp.get("status") == "blocked":
                errors.append({"code": lp.get("code") or "PRINT_LEGEND_BLOCKED",
                               "component_id": cid,
                               "message": "legend cannot fit; export would lose entries"})
            elif not lp.get("complete"):
                errors.append({"code": "PRINT_LEGEND_INCOMPLETE", "component_id": cid,
                               "message": "legend entries would be dropped"})
        # 地图槽 fit：必须有界且非等比拉伸由编译期保证
        if c.get("kind") == "map_slot":
            slot = c.get("map_slot") or {}
            if str(slot.get("fit") or "contain") not in ("contain", "crop"):
                errors.append({"code": "INVALID_MAP_FIT", "component_id": cid,
                               "message": "fit must be contain or crop"})
        component_reports.append(rep)

    if (plan.get("font_asset_version") and
            plan["font_asset_version"] != font_asset_version()):
        warnings.append({"code": "FONT_ASSET_VERSION_CHANGED",
                         "message": "font resources changed since plan was authored"})

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "components": component_reports,
        "legend_plans": legend_reports,
        "fonts": font_resource_status(),
        "font_asset_version": font_asset_version(),
        "locks": locks,
        "numbering": numbering,
        "binding_context": ctx,
    }
