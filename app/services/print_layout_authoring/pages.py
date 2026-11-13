"""G34 图册页清单与有限页级编排（WP08）。

消费既有 ``publication_ir.plan_publication_pages`` 的
frames/category/feature/time/product 真实分页结果；G34 **不新增**
route corridor / 网格分幅算法，也不重做 atlas executor。

页身份与数据绑定（frame_index / bounds / filters / feature chunk / time
window）以 ``binding_digest`` 固化；排序/启用/改标题绝不改写 digest，
因此「页标题指向另一份数据」是可检测的。页码与目录只从**最终启用页序**
计算，不用旧数组下标。
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, List, Optional

from app.lib.cartography.publication_ir import AtlasPolicy, plan_publication_pages
from app.lib.cartography.print_layout_schema import MAX_ATLAS_PAGES


def _binding_digest(page: Any) -> str:
    """页身份 + 数据绑定字段的确定性摘要（只读，不含用户可编辑字段）。"""
    payload = {
        "page_id": str(getattr(page, "page_id", "") or ""),
        "frame_index": getattr(page, "frame_index", None),
        "bounds": getattr(page, "bounds", None),
        "filter_layer_id": str(getattr(page, "filter_layer_id", "") or ""),
        "filter_property": str(getattr(page, "filter_property", "") or ""),
        "filter_value": str(getattr(page, "filter_value", "") or ""),
        "feature_offset": getattr(page, "feature_offset", None),
        "feature_limit": getattr(page, "feature_limit", None),
        "time_window": getattr(page, "time_window", None),
        "frame_slots": getattr(page, "frame_slots", None),
        "cover": bool(getattr(page, "cover", False)),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def planner_pages(mapspec: Dict[str, Any],
                  atlas: Optional[AtlasPolicy] = None) -> List[Dict[str, Any]]:
    """调用既有 planner 得到真实页清单；失败上抛 typed 异常（不伪造页面）。"""
    ir = plan_publication_pages(mapspec, atlas=atlas)
    out: List[Dict[str, Any]] = []
    for idx, page in enumerate(ir.pages):
        out.append({
            "page_id": str(page.page_id),
            "title": str(page.title or ""),
            "paper": {
                "width_mm": float(page.paper.width_mm),
                "height_mm": float(page.paper.height_mm),
                "profile": str(page.paper.profile or ""),
            },
            "binding_digest": _binding_digest(page),
            "cover": bool(page.cover),
            "planner_order": idx,
        })
    return out


def sync_pages(plan: Dict[str, Any], mapspec: Dict[str, Any],
               atlas: Optional[AtlasPolicy] = None) -> Dict[str, Any]:
    """把 planner 的真实页清单同步进作者计划（保留用户页级决定）。

    用户可编辑字段：``enabled / order / title / paper_profile / template_id /
    overrides / original_title``。``binding_digest`` 每次从 planner 重算；
    若检测到变化，回执里显式 ``binding_changed``，但不静默丢弃用户覆写。
    """
    real = planner_pages(mapspec, atlas)
    if len(real) > MAX_ATLAS_PAGES:
        raise ValueError("PRINT_LAYOUT_PAGE_BUDGET_EXCEEDED")
    out = copy.deepcopy(plan)
    existing = {str(p.get("page_id")): p for p in out.get("atlas", {}).get("pages", [])}
    pages: List[Dict[str, Any]] = []
    changed: List[str] = []
    for idx, rp in enumerate(real):
        pid = rp["page_id"]
        prev = existing.get(pid) or {}
        digest = rp["binding_digest"]
        if prev and str(prev.get("binding_digest") or "") not in ("", digest):
            changed.append(pid)
        pages.append({
            "page_id": pid,
            "enabled": bool(prev.get("enabled", True)),
            "order": int(prev.get("order", idx) if prev else idx),
            "title": str(prev.get("title", rp["title"]) if prev else rp["title"]),
            "original_title": rp["title"],
            "paper_profile": prev.get("paper_profile"),
            "template_id": prev.get("template_id"),
            "overrides": copy.deepcopy(prev.get("overrides") or []),
            "binding_digest": digest,
        })
    out.setdefault("atlas", {})["pages"] = pages
    return {"plan": out, "planner_pages": real, "planner_page_count": len(real),
            "binding_changed": changed}


def final_pages(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    """最终启用页序：按 ``order`` 再按 ``page_id`` 稳定排序。"""
    pages = [p for p in plan.get("atlas", {}).get("pages", [])
             if p.get("enabled") is not False]
    return sorted(pages, key=lambda p: (int(p.get("order") or 0), str(p.get("page_id") or "")))


def page_numbering(plan: Dict[str, Any]) -> Dict[str, Any]:
    """页码 / 总页数 / 目录（只从最终启用清单计算）。"""
    pages = final_pages(plan)
    start = int(plan.get("atlas", {}).get("page_number_start") or 1)
    rows = [{
        "page_id": str(p["page_id"]),
        "title": str(p.get("title") or ""),
        "page_number": start + i,
        "binding_digest": str(p.get("binding_digest") or ""),
    } for i, p in enumerate(pages)]
    return {
        "page_count": len(rows),
        "page_number_start": start,
        "toc": rows,
        "by_page_id": {r["page_id"]: r for r in rows},
        "toc_enabled": bool(plan.get("atlas", {}).get("toc")),
    }


def apply_page_overrides(components: List[Dict[str, Any]],
                         page: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把一页的页级覆写应用到组件副本上（page_plan 的同一优先级）。

    单独拆出来是为了让**锁不变量**能校验「该页生效值」——只比对
    ``plan.components`` 会漏掉 ``atlas.pages[*].overrides`` 这条改锁组件的旁路。
    """
    out = copy.deepcopy(components or [])
    by_id = {str(c.get("id")): c for c in out if isinstance(c, dict)}
    for ov in (page or {}).get("overrides") or []:
        cid = str(ov.get("component_id") or "")
        comp = by_id.get(cid)
        if comp is None:
            continue
        for key in ("frame", "typography", "text", "legend", "visible"):
            if key in ov and ov[key] is not None:
                comp[key] = copy.deepcopy(ov[key])
    return out


def page_plan(plan: Dict[str, Any], page_id: str) -> Dict[str, Any]:
    """解析单页最终作者计划：模板/页级覆写/草稿确定性优先级。

    优先级 = 页级 override > 当前草稿（plan.components） > 模板默认 > 内置兜底。
    ``reset_overrides`` 只清页级 override，不动草稿与源图字段。
    """
    numbering = page_numbering(plan)
    row = numbering["by_page_id"].get(page_id)
    if row is None:
        return {"found": False, "code": "PAGE_DISABLED_OR_UNKNOWN", "page_id": page_id}
    page = next((p for p in plan.get("atlas", {}).get("pages", [])
                 if str(p.get("page_id")) == page_id), {})
    components = copy.deepcopy(plan.get("components") or [])
    by_id = {str(c.get("id")): c for c in components}
    applied: List[str] = []
    for ov in page.get("overrides") or []:
        cid = str(ov.get("component_id") or "")
        c = by_id.get(cid)
        if c is None:
            continue
        for key in ("frame", "typography", "text", "legend", "visible"):
            if key in ov and ov[key] is not None:
                c[key] = copy.deepcopy(ov[key])
                applied.append(f"{cid}.{key}")
    # 页级模板：只影响该页的版式（模板组件由 apply_template(page_id) 写入）
    from app.services.print_layout_authoring.template import template_scope

    template_info: Dict[str, Any] = {}
    tid = page.get("template_id") or plan.get("template_id")
    if tid:
        try:
            template_info = template_scope(plan, str(tid))
        except Exception:
            template_info = {"template_id": tid, "status": "unavailable"}
    # 页级纸张：``paper_profile`` 是该页真实纸面，预览/导出/印刷必须同源。
    page_paper = copy.deepcopy(plan.get("paper") or {})
    profile = str(page.get("paper_profile") or "")
    if profile and profile != "custom":
        from app.lib.cartography.publication_ir import PAGE_PROFILES

        want = PAGE_PROFILES.get(profile)
        if want is not None:
            page_paper = {
                "profile": profile, "width_mm": float(want[0]),
                "height_mm": float(want[1]),
                "orientation": "landscape" if float(want[0]) >= float(want[1]) else "portrait",
            }
    return {
        "found": True,
        "page_id": page_id,
        "page_number": row["page_number"],
        "page_count": numbering["page_count"],
        "title": str(page.get("title") or ""),
        "binding_digest": row["binding_digest"],
        "paper": page_paper,
        "safe_area": copy.deepcopy(plan.get("safe_area") or {}),
        "components": components,
        "legend_defaults": copy.deepcopy(plan.get("legend_defaults") or {}),
        "applied_overrides": applied,
        "template": template_info,
        "toc": numbering["toc"] if numbering["toc_enabled"] else [],
        "priority": "page_override > draft > template_default > builtin",
    }
