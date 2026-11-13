"""G34 精确布局操作（WP04）— 整笔原子作者动作。

纪律：

* 全部在 **deepcopy** 上执行，最终整体复验通过才返回；任何一步失败零写入，
  不留「半应用模板 / 半套对齐」。
* 组件锁来自 ``mapspec.workbench.lockedComponentIds``（workbench_locks.py）。
  选中集合含锁组件时**默认整笔拒绝**（不移动该组件、不形成半套未说明布局）；
  显式 ``skip_locked=true`` 才排除并在回执披露。
* 只接受毫米数值（``set_frame``），绝不持久化浏览器 clientX/clientY。
* 修改多个组件仍是一次 ``set_layout`` intent / 一次 CAS 提交（service 层）。
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from app.contracts.workbench_locks import _lock_matches
from app.lib.cartography.print_layout_schema import (
    MAX_PRINT_COMPONENTS, validate_print_layout,
)
from app.services.print_layout_authoring import template as template_mod

OPS = frozenset({
    "set_frame", "set_typography", "set_text", "set_legend", "set_visibility",
    "align", "distribute", "group", "reorder", "set_map_slot",
    "set_paper", "set_pages", "set_safe_area",
    "apply_template", "save_template", "reset_overrides", "replace_plan",
})

COMPONENT_MUTATING_OPS = frozenset({
    "set_frame", "set_typography", "set_text", "set_legend", "set_visibility",
    "align", "distribute", "group", "reorder", "set_map_slot",
})


class OperationError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def _components(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    return plan.setdefault("components", [])


def _by_id(plan: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {str(c.get("id")): c for c in _components(plan)}


def _selected(plan: Dict[str, Any], op: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """→ (target_ids, unknown_ids)。"""
    ids = [str(x) for x in (op.get("component_ids") or [])][:MAX_PRINT_COMPONENTS]
    known = _by_id(plan)
    return ([i for i in ids if i in known], [i for i in ids if i not in known])


def _finite(v: Any) -> bool:
    import math

    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def _apply_one(plan: Dict[str, Any], op: Dict[str, Any], locks: List[str],
               skipped: List[str], changes: List[Dict[str, Any]]) -> None:
    name = str(op.get("op") or "")
    if name not in OPS:
        raise OperationError("UNKNOWN_PRINT_LAYOUT_OPERATION", name)

    # ── 模板 / 页册 / 纸张：整份替换，失败零写入 ──────────────────────
    if name == "replace_plan":
        # UI 提交整份**本领域**作者计划（不是全文 MapSpec）；仍是一次
        # set_layout intent / 一次 CAS，且必须在应用前复核用户锁。
        candidate = op.get("plan")
        if not isinstance(candidate, dict):
            raise OperationError("INVALID_REPLACE_PLAN", "plan must be an object")
        # 锁复核统一在 apply_operations 收口处做（覆盖删除 / 全字段变更），
        # 此处不再自写一份只比 frame/typography/text 的残版谓词。
        plan.clear()
        plan.update(copy.deepcopy(candidate))
        changes.append({"op": name, "component_count": len(candidate.get("components") or [])})
        return
    if name == "set_safe_area":
        sa = op.get("safe_area")
        if not isinstance(sa, dict) or not all(
                _finite(sa.get(k)) and sa[k] >= 0 for k in
                ("top", "right", "bottom", "left")):
            raise OperationError("INVALID_SAFE_AREA", "safe_area needs 4 finite non-negative mm")
        plan["safe_area"] = {k: float(sa[k]) for k in ("top", "right", "bottom", "left")}
        changes.append({"op": name, "safe_area": plan["safe_area"]})
        return
    if name == "apply_template":
        tid = str(op.get("template_id") or "")
        scope = template_mod.template_scope(plan, tid)
        changes.append({"op": name, "template_id": tid,
                        "version": scope["version"], "scope": scope["component_ids"]})
        replaced = template_mod.apply_template(plan, tid,
                                               page_id=str(op.get("page_id") or ""))
        plan.clear()
        plan.update(replaced)
        return
    if name == "save_template":
        replaced = template_mod.save_template(
            plan, str(op.get("name") or ""), str(op.get("template_id") or ""),
            paper_profile=str(op.get("paper_profile") or "a4_landscape"),
            components=op.get("components"),
            legend_defaults=op.get("legend_defaults"),
            safe_area=op.get("safe_area"),
            placeholders=op.get("placeholders"),
        )
        plan.clear()
        plan.update(replaced)
        changes.append({"op": name, "template_id": op.get("template_id")})
        return
    if name == "reset_overrides":
        replaced = template_mod.reset_overrides(plan, str(op.get("page_id") or ""))
        plan.clear()
        plan.update(replaced)
        changes.append({"op": name, "page_id": op.get("page_id") or ""})
        return
    if name == "set_paper":
        profile = str(op.get("profile") or "a4_landscape")
        from app.lib.cartography.publication_ir import PAGE_PROFILES

        if profile == "custom":
            w, h = op.get("width_mm"), op.get("height_mm")
            if not _finite(w) or not _finite(h) or w <= 10 or h <= 10 or w > 1200 or h > 1200:
                raise OperationError("INVALID_CUSTOM_PAPER", "10mm < size ≤ 1200mm")
        else:
            if profile not in PAGE_PROFILES:
                raise OperationError("UNKNOWN_PAPER_PROFILE", profile)
            w, h = PAGE_PROFILES[profile]
        plan["paper"] = {
            "profile": profile, "width_mm": float(w), "height_mm": float(h),
            "orientation": "portrait" if h > w else "landscape",
        }
        changes.append({"op": name, "profile": profile, "width_mm": w, "height_mm": h})
        return
    if name == "set_pages":
        pages = plan.setdefault("atlas", {}).setdefault("pages", [])
        known = {str(p.get("page_id")): p for p in pages}
        requested = op.get("pages")
        if not isinstance(requested, list) or not requested:
            raise OperationError("INVALID_PAGE_SET", "pages must be a non-empty list")
        for item in requested:
            if not isinstance(item, dict):
                raise OperationError("INVALID_PAGE_SET", "page entry must be an object")
            pid = str(item.get("page_id") or "")
            page = known.get(pid)
            if page is None:
                raise OperationError("PAGE_NOT_FOUND", pid)
            for key in ("enabled", "order", "title", "paper_profile", "template_id"):
                if key in item:
                    page[key] = item[key]
            if "overrides" in item:
                page["overrides"] = copy.deepcopy(item["overrides"])
            # binding_digest / original_title 只读：排序绝不改数据绑定
        enabled = [p for p in pages if p.get("enabled") is not False]
        if len(enabled) > 20:
            raise OperationError("PRINT_LAYOUT_PAGE_BUDGET_EXCEEDED", str(len(enabled)))
        changes.append({"op": name, "pages": len(requested)})
        return

    # ── 组件操作 ───────────────────────────────────────────────────────
    target_ids, unknown = _selected(plan, op)
    if unknown:
        raise OperationError("PRINT_LAYOUT_COMPONENT_NOT_FOUND", ",".join(unknown[:5]))
    if not target_ids:
        raise OperationError("PRINT_LAYOUT_EMPTY_SELECTION", name)
    locked_hits = [i for i in target_ids if _lock_hit(i, locks)]
    if locked_hits and not bool(op.get("skip_locked")):
        raise OperationError("PRINT_LAYOUT_LOCKED_COMPONENT",
                             f"locked: {','.join(locked_hits[:5])}; use skip_locked=true to exclude")
    if locked_hits:
        skipped.extend(locked_hits)
        target_ids = [i for i in target_ids if not _lock_hit(i, locks)]
    if not target_ids:
        changes.append({"op": name, "skipped_locked": locked_hits, "applied": 0})
        return
    comps = _by_id(plan)
    targets = [comps[i] for i in target_ids]

    if name == "set_frame":
        f = op.get("frame")
        if not isinstance(f, dict) or not all(_finite(f.get(k)) for k in ("x", "y", "width", "height")):
            raise OperationError("INVALID_FRAME_MM", "frame needs finite x/y/width/height in mm")
        if f["width"] <= 0 or f["height"] <= 0:
            raise OperationError("INVALID_FRAME_MM", "frame width/height must be positive")
        for c in targets:
            c["frame"] = {k: float(f[k]) for k in ("x", "y", "width", "height")}
        changes.append({"op": name, "component_ids": target_ids, "frame": f})
    elif name == "set_typography":
        patch = op.get("typography")
        if not isinstance(patch, dict):
            raise OperationError("INVALID_TYPOGRAPHY", "typography must be an object")
        for c in targets:
            c["typography"] = {**(c.get("typography") or {}), **copy.deepcopy(patch)}
        changes.append({"op": name, "component_ids": target_ids, "keys": sorted(patch)})
    elif name == "set_text":
        patch = op.get("text")
        if not isinstance(patch, dict):
            raise OperationError("INVALID_TEXT", "text must be an object")
        for c in targets:
            c["text"] = {**(c.get("text") or {}), **copy.deepcopy(patch)}
        changes.append({"op": name, "component_ids": target_ids, "keys": sorted(patch)})
    elif name == "set_legend":
        patch = op.get("legend")
        if not isinstance(patch, dict):
            raise OperationError("INVALID_LEGEND_LAYOUT", "legend must be an object")
        for c in targets:
            c["legend"] = {**(c.get("legend") or {}), **copy.deepcopy(patch)}
        changes.append({"op": name, "component_ids": target_ids, "keys": sorted(patch)})
    elif name == "set_visibility":
        if not isinstance(op.get("visible"), bool):
            raise OperationError("INVALID_VISIBILITY", "visible must be bool")
        for c in targets:
            c["visible"] = op["visible"]
        changes.append({"op": name, "component_ids": target_ids, "visible": op["visible"]})
    elif name == "set_map_slot":
        for c in targets:
            slot = dict(c.get("map_slot") or {})
            for key in ("fit", "frame_id", "frame_index"):
                if key in op:
                    slot[key] = op[key]
            c["map_slot"] = slot
        changes.append({"op": name, "component_ids": target_ids})
    elif name == "align":
        edge = str(op.get("edge") or "left")
        if edge not in ("left", "right", "top", "bottom", "center-x", "center-y"):
            raise OperationError("INVALID_ALIGN_EDGE", edge)
        frames = [c["frame"] for c in targets]
        if edge == "left":
            v = min(float(f["x"]) for f in frames)
            for f in frames:
                f["x"] = v
        elif edge == "right":
            v = max(float(f["x"]) + float(f["width"]) for f in frames)
            for f in frames:
                f["x"] = v - float(f["width"])
        elif edge == "top":
            v = min(float(f["y"]) for f in frames)
            for f in frames:
                f["y"] = v
        elif edge == "bottom":
            v = max(float(f["y"]) + float(f["height"]) for f in frames)
            for f in frames:
                f["y"] = v - float(f["height"])
        elif edge == "center-x":
            v = sum(float(f["x"]) + float(f["width"]) / 2 for f in frames) / len(frames)
            for f in frames:
                f["x"] = v - float(f["width"]) / 2
        else:
            v = sum(float(f["y"]) + float(f["height"]) / 2 for f in frames) / len(frames)
            for f in frames:
                f["y"] = v - float(f["height"]) / 2
        changes.append({"op": name, "component_ids": target_ids, "edge": edge, "value_mm": v})
    elif name == "distribute":
        axis = str(op.get("axis") or "x")
        if axis not in ("x", "y"):
            raise OperationError("INVALID_DISTRIBUTE_AXIS", axis)
        if len(targets) < 3:
            raise OperationError("DISTRIBUTE_NEEDS_THREE", str(len(targets)))
        ordered = sorted(targets, key=lambda c: float(c["frame"][axis]))
        if axis == "x":
            first, last = ordered[0]["frame"], ordered[-1]["frame"]
            span = float(last["x"]) + float(last["width"]) - float(first["x"])
            total = sum(float(c["frame"]["width"]) for c in ordered)
            gap = (span - total) / (len(ordered) - 1)
            cursor = float(first["x"])
            for c in ordered:
                c["frame"]["x"] = cursor
                cursor += float(c["frame"]["width"]) + gap
        else:
            first, last = ordered[0]["frame"], ordered[-1]["frame"]
            span = float(last["y"]) + float(last["height"]) - float(first["y"])
            total = sum(float(c["frame"]["height"]) for c in ordered)
            gap = (span - total) / (len(ordered) - 1)
            cursor = float(first["y"])
            for c in ordered:
                c["frame"]["y"] = cursor
                cursor += float(c["frame"]["height"]) + gap
        changes.append({"op": name, "component_ids": target_ids, "axis": axis,
                        "gap_mm": round(gap, 6)})
    elif name == "group":
        gid = str(op.get("group_id") or "")[:128]
        if not gid:
            raise OperationError("INVALID_GROUP_ID", "group_id required")
        for c in targets:
            c["group_id"] = gid
        changes.append({"op": name, "component_ids": target_ids, "group_id": gid})
    elif name == "reorder":
        order = [str(x) for x in (op.get("order") or [])]
        if set(order) != set(target_ids):
            raise OperationError("INVALID_REORDER_SET", "order must contain exactly the selection")
        for idx, cid in enumerate(order):
            comps[cid]["z"] = idx
        changes.append({"op": name, "order": order})
    else:  # pragma: no cover — OPS 已封闭
        raise OperationError("UNKNOWN_PRINT_LAYOUT_OPERATION", name)


def _lock_hit(component_id: str, locks: List[str]) -> bool:
    """唯一锁谓词：复用 workbench 的 ``_lock_matches``（含双向 ``__`` 族）。

    历史上这里写的是 ``i in locks`` 精确匹配，导致 ``locks=["scale"]`` 时
    ``scale__v2`` 绕过锁；同一条规则在 replace_plan / handler 里又各写一份。
    """
    cid = str(component_id)
    return any(_lock_matches(str(l), cid) for l in locks)


#: 锁组件被冻结的字段（``locked`` 自身是只读投影，不参与比较）。
_LOCKED_FIELDS = ("kind", "frame", "typography", "text", "legend",
                  "map_slot", "visible", "z", "group_id")


def _assert_locks_preserved(before: Dict[str, Any], after: Dict[str, Any],
                            locks: List[str]) -> None:
    """整笔操作的锁不变量：锁组件不得消失、不得被改任何受保护字段。

    放在 ``apply_operations`` 的收口处，因此 ``replace_plan`` / ``apply_template``
    / ``save_template`` 这类整份替换也无处绕过——删除锁组件、改锁组件图例或
    可见性都会整笔拒绝（零写入），而不是静默丢数据。

    同时校验**每一页的生效组件**（基础组件 + ``atlas.pages[*].overrides``）：
    页级覆写是改锁组件的第二条旁路，只比对 ``plan.components`` 会漏掉它。
    """
    if not locks:
        return
    from app.services.print_layout_authoring.pages import apply_page_overrides

    def _index(comps: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        return {str(c.get("id")): c for c in comps or [] if isinstance(c, dict)}

    def _compare(prev: Dict[str, Dict[str, Any]],
                 nxt: Dict[str, Dict[str, Any]], where: str) -> None:
        for cid, old in prev.items():
            if not _lock_hit(cid, locks):
                continue
            new = nxt.get(cid)
            if new is None:
                raise OperationError("PRINT_LAYOUT_LOCKED_COMPONENT",
                                     f"locked component removed{where}: {cid}")
            changed = [f for f in _LOCKED_FIELDS if old.get(f) != new.get(f)]
            if changed:
                raise OperationError(
                    "PRINT_LAYOUT_LOCKED_COMPONENT",
                    f"locked component changed{where}: {cid} ({','.join(changed)})")

    base_before = before.get("components") or []
    base_after = after.get("components") or []
    _compare(_index(base_before), _index(base_after), "")

    prev_pages = {str(p.get("page_id")): p
                  for p in (before.get("atlas") or {}).get("pages") or []
                  if isinstance(p, dict)}
    next_pages = {str(p.get("page_id")): p
                  for p in (after.get("atlas") or {}).get("pages") or []
                  if isinstance(p, dict)}
    for pid in set(prev_pages) | set(next_pages):
        _compare(_index(apply_page_overrides(base_before, prev_pages.get(pid))),
                 _index(apply_page_overrides(base_after, next_pages.get(pid))),
                 f" on page {pid}")


def apply_operations(plan: Dict[str, Any], operations: List[Dict[str, Any]],
                     locks: Optional[List[str]] = None) -> Dict[str, Any]:
    """整笔原子执行 → ``{ok, plan, changes, skipped_locked, issues}``。

    ``plan`` 只在 ``ok=True`` 时有效；失败时返回原 plan 的副本（零应用）。
    """
    original = copy.deepcopy(plan)
    candidate = copy.deepcopy(plan)
    locks = list(locks or [])
    changes: List[Dict[str, Any]] = []
    skipped: List[str] = []
    if not isinstance(operations, list) or not operations:
        return {"ok": False, "plan": original, "changes": [], "skipped_locked": [],
                "issues": [{"code": "EMPTY_OPERATIONS", "message": "no operations"}]}
    if len(operations) > 32:
        return {"ok": False, "plan": original, "changes": [], "skipped_locked": [],
                "issues": [{"code": "OPERATION_BUDGET_EXCEEDED", "message": ">32 ops"}]}
    try:
        for op in operations:
            if not isinstance(op, dict):
                raise OperationError("INVALID_OPERATION", "operation must be an object")
            _apply_one(candidate, op, locks, skipped, changes)
        _assert_locks_preserved(original, candidate, locks)
    except (OperationError, ValueError, TypeError, KeyError) as ex:
        code = getattr(ex, "code", type(ex).__name__)
        return {"ok": False, "plan": original, "changes": [], "skipped_locked": skipped,
                "issues": [{"code": str(code), "message": str(ex)[:512]}]}

    verdict = validate_print_layout(candidate)
    issues = [{"code": i.code, "message": i.message, "severity": i.severity,
               "component_id": i.component_id}
              for i in verdict.issues if i.severity == "error"]
    if not verdict.ok or issues:
        return {"ok": False, "plan": original, "changes": [], "skipped_locked": skipped,
                "issues": issues or [{"code": "PRINT_LAYOUT_INVALID", "message": "validation failed"}]}
    return {"ok": True, "plan": candidate, "changes": changes,
            "skipped_locked": sorted(set(skipped)), "issues": []}
