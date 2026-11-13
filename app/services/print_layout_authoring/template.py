"""G34 版式模板与受控动态文本（WP07）。

模板 = **有限版式配置**（纸张/安全区/组件框/排版/图例排版 + 关闭占位符），
不是可运行插件；不携带其它用户数据、临时路径或任意脚本。
应用前列出覆盖范围；只改 ``layout.print_layout`` 内的本领域字段，
源图标题 / 人工标签 / 锁 / 图层可见性 / frame 参数一律不动。

动态文本绑定只读可信元数据；格式化是关闭函数集（plain/upper/lower/
padded2），无 eval、无 HTML 注入、无 SQL。缺值保持**可见占位**，
绝不猜地名、年份或许可。
"""
from __future__ import annotations

import copy
import hashlib
from typing import Any, Dict, List, Optional

from app.lib.cartography.print_layout_schema import (
    MAX_TEMPLATES, MAX_TEMPLATE_BYTES, PrintTemplate, validate_print_layout,
)
from app.lib.cartography.publication_ir import PAGE_PROFILES
from app.services.print_layout_authoring.fonts import font_asset_version

#: 允许被模板替换的占位符（关闭词表；无脚本、无 URL、无路径）。
ALLOWED_PLACEHOLDERS = (
    "page_title", "page_number", "page_count", "data_time",
    "unit", "source_attribution", "map_title", "map_subtitle",
)
FORMATTERS = ("plain", "upper", "lower", "padded2")


class PrintTemplateError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def _template_bytes(tpl: Dict[str, Any]) -> int:
    import json

    return len(json.dumps(tpl, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def save_template(plan: Dict[str, Any], name: str,
                  template_id: str, *,
                  paper_profile: str = "a4_landscape",
                  components: Optional[List[Dict[str, Any]]] = None,
                  legend_defaults: Optional[Dict[str, Any]] = None,
                  safe_area: Optional[Dict[str, Any]] = None,
                  placeholders: Optional[List[str]] = None) -> Dict[str, Any]:
    """保存/更新一个有限版式模板（同 id 时 version+1；字节与数量有界）。"""
    tid = str(template_id or "").strip()
    if not (tid.startswith("tpl:") and 4 <= len(tid) <= 128):
        raise PrintTemplateError("INVALID_TEMPLATE_ID", "template id must start with 'tpl:'")
    if tid.count("/") or ".." in tid or "\\" in tid:
        raise PrintTemplateError("INVALID_TEMPLATE_ID", "template id must not contain paths")
    if paper_profile not in PAGE_PROFILES and paper_profile != "custom":
        raise PrintTemplateError("UNKNOWN_PAPER_PROFILE", paper_profile)
    placeholders = [p for p in (placeholders or []) if p in ALLOWED_PLACEHOLDERS][:16]
    existing = next((t for t in plan.get("templates", []) if t.get("id") == tid), None)
    version = int(existing["version"]) + 1 if existing else 1
    tpl: Dict[str, Any] = {
        "id": tid, "version": version, "name": str(name or "")[:128],
        "paper_profile": paper_profile,
        "safe_area": copy.deepcopy(safe_area or plan.get("safe_area") or {}),
        "components": copy.deepcopy(components if components is not None
                                    else plan.get("components") or []),
        "legend_defaults": copy.deepcopy(legend_defaults or plan.get("legend_defaults") or {}),
        "placeholders": placeholders,
    }
    if _template_bytes(tpl) > MAX_TEMPLATE_BYTES:
        raise PrintTemplateError("TEMPLATE_TOO_LARGE", f">{MAX_TEMPLATE_BYTES} bytes")
    PrintTemplate.model_validate(tpl)  # 关闭词表复验
    out = copy.deepcopy(plan)
    templates = [t for t in out.get("templates", []) if t.get("id") != tid]
    templates.append(tpl)
    if len(templates) > MAX_TEMPLATES:
        raise PrintTemplateError("TEMPLATE_BUDGET_EXCEEDED", str(MAX_TEMPLATES))
    out["templates"] = templates
    return out


def template_scope(plan: Dict[str, Any], template_id: str) -> Dict[str, Any]:
    """应用前列出覆盖范围（用户可核对，避免半应用）。"""
    tpl = next((t for t in plan.get("templates", []) if t.get("id") == template_id), None)
    if tpl is None:
        raise PrintTemplateError("TEMPLATE_NOT_FOUND", template_id)
    width, height = PAGE_PROFILES.get(tpl["paper_profile"], PAGE_PROFILES["a4_landscape"])
    return {
        "template_id": tpl["id"], "version": tpl["version"],
        "paper": {"profile": tpl["paper_profile"], "width_mm": width, "height_mm": height},
        "safe_area": copy.deepcopy(tpl.get("safe_area") or {}),
        "component_ids": [str(c.get("id")) for c in tpl.get("components") or []],
        "component_count": len(tpl.get("components") or []),
        "legend_defaults": copy.deepcopy(tpl.get("legend_defaults") or {}),
        "placeholders": list(tpl.get("placeholders") or []),
        "preserved": [
            "MapSpec.layout.components（源图标题/人工标签）",
            "mapspec.workbench.lockedComponentIds（用户锁）",
            "layer visibility / frame 参数 / source style",
            "atlas page bindings（页身份与数据绑定）",
            "组件已有的 literal 文本（人工决定）",
        ],
    }


def apply_template(plan: Dict[str, Any], template_id: str, *,
                   page_id: str = "") -> Dict[str, Any]:
    """应用模板（原子：整份 copy 后一次替换，失败零写入）。

    只替换版式参数；同 id 组件已有的 ``text``（人工文字）保留，
    用户锁在 service 层从 workbench 重新投影，不被模板覆盖。
    """
    tpl = next((t for t in plan.get("templates", []) if t.get("id") == template_id), None)
    if tpl is None:
        raise PrintTemplateError("TEMPLATE_NOT_FOUND", template_id)
    width, height = PAGE_PROFILES.get(tpl["paper_profile"], PAGE_PROFILES["a4_landscape"])
    out = copy.deepcopy(plan)
    prev_by_id = {str(c.get("id")): c for c in out.get("components") or []}
    new_components: List[Dict[str, Any]] = []
    for src in tpl.get("components") or []:
        c = copy.deepcopy(src)
        prev = prev_by_id.get(str(c.get("id")))
        if prev is not None:
            # 人工文字是人工决定：模板只覆盖版式，不覆盖已写的文字。
            if prev.get("text") is not None:
                c["text"] = copy.deepcopy(prev["text"])
            # locked 是只读投影，service 会重新计算；此处不采信模板值。
            c["locked"] = bool(prev.get("locked"))
        new_components.append(c)
    out["components"] = new_components
    out["paper"] = {
        "profile": tpl["paper_profile"], "width_mm": width, "height_mm": height,
        "orientation": "portrait" if height > width else "landscape",
    }
    out["safe_area"] = copy.deepcopy(tpl.get("safe_area") or out.get("safe_area") or {})
    out["legend_defaults"] = copy.deepcopy(tpl.get("legend_defaults") or out.get("legend_defaults") or {})
    out["template_id"] = tpl["id"]
    out["template_version"] = int(tpl["version"])
    out["font_asset_version"] = font_asset_version()
    verdict = validate_print_layout(out)
    if not verdict.ok:
        codes = ",".join(i.code for i in verdict.issues if i.severity == "error")
        raise PrintTemplateError("TEMPLATE_APPLY_INVALID", codes)
    if page_id:
        for page in out.get("atlas", {}).get("pages", []):
            if page.get("page_id") == page_id:
                page["template_id"] = tpl["id"]
                break
    return out


def reset_overrides(plan: Dict[str, Any], page_id: str = "") -> Dict[str, Any]:
    """复位**只清本领域页级覆写**，不删除源图标题/标签/锁/图层可见性。"""
    out = copy.deepcopy(plan)
    pages = out.get("atlas", {}).get("pages", [])
    targets = pages if not page_id else [p for p in pages if p.get("page_id") == page_id]
    if page_id and not targets:
        raise PrintTemplateError("PAGE_NOT_FOUND", page_id)
    for page in targets:
        page["overrides"] = []
        page["template_id"] = None
        page["paper_profile"] = None
        page["title"] = str(page.get("original_title") or "")
    if not page_id:
        out["template_id"] = None
        out["template_version"] = 0
    return out


def resolve_binding(binding: str, context: Dict[str, Any], *,
                    fmt: str = "plain",
                    placeholder: str = "〔数据缺失〕") -> Dict[str, Any]:
    """只读可信元数据 → 有界文本；缺值可见占位，不猜地名/年份/许可。"""
    if binding not in ALLOWED_PLACEHOLDERS:
        return {"value": placeholder, "resolved": False, "missing": True,
                "code": "UNKNOWN_TEXT_BINDING"}
    keys = {
        "page_title": "page_title",
        "page_number": "page_number",
        "page_count": "page_count",
        "data_time": "data_time",
        "unit": "unit",
        "source_attribution": "source_attribution",
        "map_title": "map_title",
        "map_subtitle": "map_subtitle",
    }
    raw = context.get(keys[binding])
    missing = raw is None or (isinstance(raw, str) and not raw.strip())
    value = placeholder if missing else str(raw)
    if not missing:
        if fmt == "upper":
            value = value.upper()
        elif fmt == "lower":
            value = value.lower()
        elif fmt == "padded2":
            value = value.zfill(2)
        elif fmt != "plain":
            return {"value": placeholder, "resolved": False, "missing": True,
                    "code": "UNKNOWN_TEXT_FORMAT"}
    return {
        "value": value, "resolved": not missing, "missing": missing,
        "binding": binding, "code": "" if not missing else "TEXT_BINDING_MISSING",
    }


def binding_context(mapspec: Dict[str, Any], page: Dict[str, Any], *,
                    page_number: int = 1, page_count: int = 1,
                    unit: str = "") -> Dict[str, Any]:
    """从**只读** MapSpec/页元数据装配绑定上下文（不写回任何字段）。"""
    layout = mapspec.get("layout") if isinstance(mapspec, dict) else None
    comps = (layout or {}).get("components") or [] if isinstance(layout, dict) else []
    def _text(kind: str) -> str:
        for c in comps:
            if isinstance(c, dict) and c.get("type") == kind and c.get("enabled") is not False:
                opts = c.get("options") if isinstance(c.get("options"), dict) else {}
                return str(opts.get("text") or "")
        return ""
    return {
        "page_title": str(page.get("title") or ""),
        "page_number": page_number,
        "page_count": page_count,
        "data_time": str((mapspec.get("time") or {}).get("current") or "")
                     if isinstance(mapspec.get("time"), dict) else "",
        "unit": str(unit or ""),
        "source_attribution": str(page.get("source_attribution") or ""),
        "map_title": _text("title"),
        "map_subtitle": _text("subtitle"),
    }


def template_fingerprint(plan: Dict[str, Any]) -> str:
    """页缓存/导出 fingerprint 用：模板 id+version+组件几何摘要。"""
    payload = "|".join([
        str(plan.get("template_id") or ""), str(plan.get("template_version") or 0),
        str(plan.get("font_asset_version") or ""),
        ",".join(
            f"{c.get('id')}:{c.get('frame', {}).get('x')}:{c.get('frame', {}).get('y')}"
            f":{c.get('frame', {}).get('width')}:{c.get('frame', {}).get('height')}"
            for c in plan.get("components") or []
        ),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
