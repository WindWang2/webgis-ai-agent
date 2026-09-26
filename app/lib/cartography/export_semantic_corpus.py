"""F14 D7：live vs export 结构语义 golden corpus（纯函数、无 I/O、确定性）。

publication 矢量链（``mapspec_to_svg.compile_mapspec_to_svg_detailed``）把
MapSpec 编译为 SVG；live 侧语义由 canonical scene（``render_scene``）描述。
本模块是两者的**结构语义对账面**（设计文档
``docs/dev/publication-export-parity-design.md`` §D7）：

- :func:`expected_semantics`：spec → 应然语义面（chrome 族集合、图例条目数、
  标题/署名文本、隐藏图层集合、panel 内联数据在场摘要）。
- :func:`extract_semantics`：导出 SVG 文本 → 实然语义面（按冻结的 chrome
  marker class 契约解析；解析失败诚实返回 ``{"parse_ok": False}``）。
- :func:`compare_semantics`：逐族裁决 ``match / degraded / missing``（+
  hidden 图层 ``extra``），omitted 回执支持的诚实降级，静默丢失 = fail。

ABI 驱动（ADR-0211）：应然面只收 ``PUBLICATION_COMPONENT_TYPES``
（**运行时读取** ``component_renderers`` 的派生常量，不硬编码）——主 agent
为新族落地渲染器并把矩阵置 ``publication=True`` 后，语料闭环自动纳入新族，
无需改本模块。chrome marker class 契约已冻结：

- ``mapspec-chrome`` 组内 ``chrome-title``（1–2 个 text：主/副标题）、
  ``chrome-north-arrow``、``chrome-scale-bar``、``chrome-legend``（条目 swatch
  = 组内 ``rect[fill]``，另含恰一块卡片背景 rect）、``chrome-inset``、
  ``chrome-attribution``、``chrome-graticule``、``chrome-map-border``；
- 即将新增：``chrome-colorbar``（连续色带）、``chrome-annotation``（注记卡）、
  ``chrome-panel[data-kind=statistics|chart|table|methodology|uncertainty|decision]``。

全部输出 bounded（防病态输入）、可 JSON 序列化、确定性（排序稳定），
stable reason codes 记于各 verdict 的 ``detail``。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

from app.lib.cartography.component_renderers import PUBLICATION_COMPONENT_TYPES
from app.lib.cartography.render_scene import derive_legend_items
from app.lib.cartography.render_scene import resolve_components

# ── boundedness 常量（防病态输入；输出恒可序列化）────────────────────────
MAX_FAMILIES = 32          # chrome 族 / families 报告条目上限
MAX_ENTRIES = 64           # 图例条目 / 注记行 / 颜色 / layer 组计数上限
MAX_TEXT_CHARS = 200       # 单段提取文本截断
MAX_PANELS = 16            # panel 摘要条目上限
MAX_RECEIPTS = 64          # omitted 回执消费上限
MAX_WALK_NODES = 20000     # SVG 遍历节点上限
MAX_WALK_DEPTH = 64        # SVG 遍历深度上限

#: 图例单盒渲染条目上限（与 svg_marginalia.render_legend_box(max_items=12)
#: 同口径 —— expected 面与渲染面同语义截断）。
MAX_LEGEND_ITEMS_PER_BOX = 12

#: 图例族归并键：legend / categorical_legend 两型都画 ``chrome-legend``。
LEGEND_FAMILY = "legend"
_LEGEND_COMPONENT_TYPES = ("legend", "categorical_legend")

#: chrome marker class → 语义族（组件 type）。chrome-title 特判（title/subtitle
#: 共组，按 text 子元素数区分）；chrome-panel 按 data-kind 二次分派。
_CHROME_CLASS_TO_FAMILY: Dict[str, str] = {
    "chrome-north-arrow": "north_arrow",
    "chrome-scale-bar": "scale_bar",
    "chrome-legend": LEGEND_FAMILY,
    "chrome-inset": "inset_map",
    "chrome-attribution": "attribution",
    "chrome-graticule": "graticule",
    "chrome-map-border": "map_border",
    "chrome-colorbar": "continuous_colorbar",
    "chrome-annotation": "annotation",
}

#: chrome-panel data-kind → 组件 type（冻结契约词表）。
_PANEL_KIND_TO_TYPE: Dict[str, str] = {
    "statistics": "statistics_panel",
    "chart": "chart_panel",
    "table": "table_panel",
    "methodology": "methodology_note",
    "uncertainty": "uncertainty_panel",
    "decision": "decision_panel",
}

#: 组件 type → chrome-panel data-kind（反向表，expected 面消费）。
_PANEL_TYPE_TO_KIND: Dict[str, str] = {v: k for k, v in _PANEL_KIND_TO_TYPE.items()}

#: panel 组件的内联数据契约（corpus 约定，与 live 面板数据协议同键面）：
#: type → (options 容器键, 容器内行键 or None)。行键为 None 时容器本身即数据。
_PANEL_INLINE_DATA: Dict[str, Tuple[str, Optional[str]]] = {
    "statistics_panel": ("stats", "items"),
    "chart_panel": ("chart", "points"),
    "table_panel": ("table", "rows"),
    "methodology_note": ("warnings", None),
    "uncertainty_panel": ("uncertainty", None),
    "decision_panel": ("rows", None),
}

_COLOR_PREFIX_RE = re.compile(r"^(#|rgb|hsl)", re.IGNORECASE)
_LAYER_CLASS_RE = re.compile(r"^layer-(.+)$")


# ── 内部工具（全部确定性）───────────────────────────────────────────────


def _bounded_list(values: List[Any], cap: int) -> List[Any]:
    return values[:cap]


def _clip_text(value: Any) -> str:
    text = value if isinstance(value, str) else ""
    return text[:MAX_TEXT_CHARS]


def _is_color_like(value: Any) -> bool:
    return isinstance(value, str) and bool(_COLOR_PREFIX_RE.match(value))


def _local_tag(elem: Any) -> str:
    tag = getattr(elem, "tag", "")
    return tag.rpartition("}")[2] if isinstance(tag, str) else ""


def _layer_hidden(layer: Dict[str, Any]) -> bool:
    """与编译器/``render_scene._layer_visible`` 同口径的隐藏判定
    （``layout.visibility == "none"`` 或顶层 ``visible is False``）。"""
    layout = layer.get("layout") if isinstance(layer.get("layout"), dict) else {}
    return layout.get("visibility") == "none" or layer.get("visible") is False


def _layer_paint_colors(layer: Dict[str, Any]) -> List[str]:
    """layer paint 面内联颜色串（#hex / rgb() / hsl()），保序、去重、有界。"""
    paint = layer.get("paint") if isinstance(layer.get("paint"), dict) else {}
    out: List[str] = []
    for value in paint.values():
        if _is_color_like(value) and value not in out:
            out.append(value)
    return out


def _legend_boxes(mapspec: Dict[str, Any], enabled: List[Any]) -> Tuple[int, bool, List[str]]:
    """镜像 ``mapspec_to_svg._render_chrome_groups`` 的图例绘制循环。

    返回 ``(条目数和, 是否画出至少一盒, 画盒的组件 id 列表)``。绑定
    ``options.layerId`` 优先；未绑定取首个含 ``legend_spec`` 的 layer（只取
    一次，与编译器 ``drawn_unbound`` 同语义）；条目数按渲染口径截断到
    ``MAX_LEGEND_ITEMS_PER_BOX``。
    """
    legend_specs_by_layer: Dict[Any, Dict[str, Any]] = {}
    for layer in mapspec.get("layers", []) or []:
        if isinstance(layer, dict) and isinstance(layer.get("legend_spec"), dict):
            legend_specs_by_layer[layer.get("id")] = layer["legend_spec"]

    total = 0
    any_box = False
    drawn_ids: List[str] = []
    drawn_unbound = False
    for comp in enabled:
        if comp.type not in _LEGEND_COMPONENT_TYPES:
            continue
        spec_d: Optional[Dict[str, Any]] = None
        if comp.layer_id and comp.layer_id in legend_specs_by_layer:
            spec_d = legend_specs_by_layer[comp.layer_id]
        elif not comp.layer_id and not drawn_unbound and legend_specs_by_layer:
            spec_d = next(iter(legend_specs_by_layer.values()))
            drawn_unbound = True
        if not isinstance(spec_d, dict):
            continue
        model = derive_legend_items(spec_d)
        if model is None:
            continue
        shown = min(len(model.get("entries") or []), MAX_LEGEND_ITEMS_PER_BOX)
        if shown <= 0:
            # render_legend_box 空盒不画（编译器同语义）。
            continue
        any_box = True
        total += shown
        if comp.id:
            drawn_ids.append(comp.id)
    return total, any_box, drawn_ids


# ── 1) spec → 应然语义面 ────────────────────────────────────────────────


def expected_semantics(mapspec: dict) -> dict:
    """MapSpec dict → 应然语义面（与 ``_render_chrome_groups`` 同口径）。

    - ``chrome_families``：enabled 且 type ∈ ``PUBLICATION_COMPONENT_TYPES``
      （运行时读取矩阵派生常量）的可绘制组件族（有序去重；图例族归并键
      ``legend``，其余按组件 type 原样）。可绘制性镜像编译器：title/subtitle/
      attribution 需非空 text；legend 族需有可呈现条目（空盒不画）。
    - ``legend_entries``：画盒图例条目数和（渲染截断口径）。
    - ``title_text`` / ``subtitle_text`` / ``attribution_text``：组件
      ``options.text``。
    - ``annotation_lines``：注记总行数（text 按 ``\\n`` 切 + items 数；
      仅当 annotation 族在 publication 矩阵内 —— ABI 驱动）。
    - ``hidden_layers``：``layout.visibility=="none"`` 或 ``visible:False``
      的 layer id（排序、有界）。
    - ``hidden_layer_colors``：隐藏层 paint 独占颜色（扣除可见层共色）——
      编译器当前不产出 layer 分组，颜色指纹是隐藏层泄漏的诚实探针。
    - ``panel_specs``：panel/披露族组件的内联数据在场摘要（kind、行数期望、
      has_data 布尔）。
    - ``family_components``：族 → 组件 id 列表（omitted 回执 component_id
      对账用）。
    """
    spec = mapspec if isinstance(mapspec, dict) else {}
    resolved = resolve_components(spec)
    enabled = [c for c in resolved if c.enabled]

    families: List[str] = []
    family_components: Dict[str, List[str]] = {}

    def _record(family: str, comp_id: str) -> None:
        if family not in families:
            families.append(family)
        ids = family_components.setdefault(family, [])
        if comp_id and comp_id not in ids:
            ids.append(comp_id)

    title_text = ""
    subtitle_text = ""
    attribution_text = ""

    for comp in enabled:
        if comp.type not in PUBLICATION_COMPONENT_TYPES:
            continue
        comp_id = comp.id if isinstance(comp.id, str) else ""
        if comp.type in _LEGEND_COMPONENT_TYPES:
            continue  # 图例族按绘制循环统一裁决（下方面）
        if comp.type == "title":
            if comp.text:
                title_text = comp.text
                _record("title", comp_id)
        elif comp.type == "subtitle":
            if comp.text:
                subtitle_text = comp.text
                _record("subtitle", comp_id)
        elif comp.type == "attribution":
            if comp.text:
                attribution_text = comp.text
                _record("attribution", comp_id)
        else:
            # north_arrow / scale_bar / graticule / inset_map / map_border /
            # 及未来置位的 colorbar / annotation / panel 族：enabled 即画。
            _record(comp.type, comp_id)

    legend_entries, legend_drawn, legend_ids = _legend_boxes(spec, enabled)
    if legend_drawn:
        if LEGEND_FAMILY not in families:
            families.append(LEGEND_FAMILY)
        ids = family_components.setdefault(LEGEND_FAMILY, [])
        for cid in legend_ids:
            if cid not in ids:
                ids.append(cid)

    annotation_lines = 0
    if "annotation" in PUBLICATION_COMPONENT_TYPES:
        for comp in enabled:
            if comp.type != "annotation":
                continue
            annotation_lines += len(comp.text.split("\n")) if comp.text else 0
            items = comp.options.get("items")
            if isinstance(items, list):
                annotation_lines += len(items)
    annotation_lines = min(annotation_lines, MAX_ENTRIES)

    hidden_layers: List[str] = []
    hidden_colors: List[str] = []
    visible_colors: List[str] = []
    layers = spec.get("layers") if isinstance(spec.get("layers"), list) else []
    for layer in layers:
        if not isinstance(layer, dict) or not isinstance(layer.get("id"), str):
            continue
        if _layer_hidden(layer):
            if layer["id"] not in hidden_layers:
                hidden_layers.append(layer["id"])
            for color in _layer_paint_colors(layer):
                if color not in hidden_colors:
                    hidden_colors.append(color)
        else:
            for color in _layer_paint_colors(layer):
                if color not in visible_colors:
                    visible_colors.append(color)
    hidden_layers = sorted(_bounded_list(hidden_layers, MAX_FAMILIES))
    hidden_layer_colors = sorted(
        _bounded_list([c for c in hidden_colors if c not in visible_colors], MAX_ENTRIES))

    panel_specs: List[Dict[str, Any]] = []
    for comp in enabled:
        kind = _PANEL_TYPE_TO_KIND.get(comp.type)
        if kind is None:
            continue
        if len(panel_specs) >= MAX_PANELS:
            break
        container_key, rows_key = _PANEL_INLINE_DATA[comp.type]
        payload = comp.options.get(container_key)
        if rows_key is None:
            rows = payload if isinstance(payload, list) else (
                list(payload.keys()) if isinstance(payload, dict) and payload else [])
        else:
            rows = payload.get(rows_key) if isinstance(payload, dict) else None
            rows = rows if isinstance(rows, list) else []
        panel_specs.append({
            "component_id": comp.id if isinstance(comp.id, str) else "",
            "type": comp.type,
            "kind": kind,
            "expected_rows": min(len(rows), MAX_ENTRIES),
            "has_data": bool(rows) if rows_key is not None else bool(payload),
        })

    return {
        "chrome_families": _bounded_list(families, MAX_FAMILIES),
        "legend_entries": min(legend_entries, MAX_ENTRIES),
        "title_text": _clip_text(title_text),
        "subtitle_text": _clip_text(subtitle_text),
        "attribution_text": _clip_text(attribution_text),
        "annotation_lines": annotation_lines,
        "hidden_layers": hidden_layers,
        "hidden_layer_colors": hidden_layer_colors,
        "panel_specs": panel_specs,
        "family_components": {
            fam: _bounded_list(ids, MAX_FAMILIES)
            for fam, ids in sorted(family_components.items())
        },
    }


# ── 2) SVG → 实然语义面 ─────────────────────────────────────────────────


def _walk_elements(root: Any) -> List[Tuple[Any, int]]:
    """document 序遍历（节点/深度双 bounded —— 防病态深宽输入）。"""
    out: List[Tuple[Any, int]] = []
    stack: List[Tuple[Any, int]] = [(root, 0)]
    while stack:
        elem, depth = stack.pop()
        out.append((elem, depth))
        if len(out) >= MAX_WALK_NODES or depth >= MAX_WALK_DEPTH:
            continue
        children = list(elem)
        for child in reversed(children):
            stack.append((child, depth + 1))
        if len(out) >= MAX_WALK_NODES:
            break
    return out


def extract_semantics(svg: str) -> dict:
    """导出 SVG 文本 → 实然语义面（按冻结 chrome marker 契约解析）。

    解析失败/非字符串 → ``{"parse_ok": False}``（不抛异常）。全部计数
    bounded（截断到常量上限）；列表排序保证确定性。
    """
    if not isinstance(svg, str):
        return {"parse_ok": False}
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return {"parse_ok": False}

    families: set = set()
    legend_entry_count = 0
    legend_group_count = 0
    title_text = ""
    subtitle_text = ""
    attribution_text = ""
    annotation_line_count = 0
    panel_kinds: List[str] = []
    layer_groups: set = set()
    vector_layer_colors: set = set()
    truncated = False

    for elem, _depth in _walk_elements(root):
        tag = _local_tag(elem)
        if tag != "g":
            continue
        classes = (elem.get("class") or "").split()
        if not classes:
            continue

        # layer 分组（若导出链未来产出 class="layer-<id>" 组）
        for token in classes:
            m = _LAYER_CLASS_RE.match(token)
            if m and len(layer_groups) < MAX_ENTRIES:
                layer_groups.add(m.group(1))
            elif m:
                truncated = True

        if "mapspec-vector-layers" in classes:
            for node, _d in _walk_elements(elem):
                ntag = _local_tag(node)
                if ntag not in ("rect", "circle", "path", "line", "image", "text"):
                    continue
                for attr in ("fill", "stroke"):
                    value = node.get(attr)
                    if _is_color_like(value):
                        if len(vector_layer_colors) >= MAX_ENTRIES:
                            truncated = True
                        else:
                            vector_layer_colors.add(value)

        if "chrome-title" in classes and not title_text and not subtitle_text:
            texts = [
                (child.text or "")
                for child in list(elem)
                if _local_tag(child) == "text"
            ][:4]
            if texts:
                title_text = _clip_text(texts[0])
            if len(texts) >= 2:
                subtitle_text = _clip_text(texts[1])
            # title/subtitle 共组：首个 text 非空 = title 在场；
            # 第二个 text 非空 = subtitle 在场（仅副标题时首 text 为空串）。
            if title_text:
                families.add("title")
            if subtitle_text:
                families.add("subtitle")

        if "chrome-legend" in classes:
            legend_group_count += 1
            # 条目 swatch = 组内 rect[fill]；每组恰一块卡片背景 rect
            # （render_legend_box 契约），扣除后即条目数。
            fills = sum(
                1 for child in list(elem)
                if _local_tag(child) == "rect" and child.get("fill")
            )
            entries = max(0, fills - 1)
            legend_entry_count = min(legend_entry_count + entries, MAX_ENTRIES)
            if fills - 1 > MAX_ENTRIES:
                truncated = True
            families.add(LEGEND_FAMILY)

        if "chrome-annotation" in classes:
            families.add("annotation")
            count = sum(
                1 for node, _d in _walk_elements(elem) if _local_tag(node) == "text"
            )
            annotation_line_count = min(annotation_line_count + count, MAX_ENTRIES)
            if count > MAX_ENTRIES:
                truncated = True

        if "chrome-panel" in classes:
            families.add("panel")  # 占位族键；具体 type 经 data-kind 分派
            kind = elem.get("data-kind") or ""
            if kind:
                comp_type = _PANEL_KIND_TO_TYPE.get(kind)
                if comp_type is not None:
                    families.add(comp_type)
                if kind not in panel_kinds:
                    if len(panel_kinds) < MAX_PANELS:
                        panel_kinds.append(kind)
                    else:
                        truncated = True

        for token in classes:
            family = _CHROME_CLASS_TO_FAMILY.get(token)
            if family is not None:
                families.add(family)

        if "chrome-attribution" in classes and not attribution_text:
            for child in list(elem):
                if _local_tag(child) == "text":
                    attribution_text = _clip_text(child.text or "")
                    break

    return {
        "parse_ok": True,
        "chrome_families": sorted(families)[:MAX_FAMILIES],
        "legend_entry_count": legend_entry_count,
        "legend_group_count": min(legend_group_count, MAX_ENTRIES),
        "title_text": title_text,
        "subtitle_text": subtitle_text,
        "attribution_text": attribution_text,
        "annotation_line_count": annotation_line_count,
        "panel_kinds": panel_kinds,
        "layer_groups": sorted(layer_groups)[:MAX_ENTRIES],
        "vector_layer_colors": sorted(vector_layer_colors)[:MAX_ENTRIES],
        "truncated": truncated,
    }


# ── 3) 逐族裁决 ─────────────────────────────────────────────────────────


def _normalize_receipts(omitted_codes: Optional[List[Dict[str, Any]]]) -> List[Dict[str, str]]:
    """omitted 回执归一（component_id / type / code；bounded、稳定）。"""
    receipts: List[Dict[str, str]] = []
    if not isinstance(omitted_codes, list):
        return receipts
    for item in omitted_codes[:MAX_RECEIPTS]:
        if not isinstance(item, dict):
            continue
        rtype = item.get("type") or item.get("component_type") or ""
        cid = item.get("component_id") or item.get("id") or ""
        code = item.get("code") or ""
        if not isinstance(rtype, str) or not rtype:
            continue
        receipts.append({
            "type": rtype,
            "component_id": cid if isinstance(cid, str) else "",
            "code": code if isinstance(code, str) else "",
        })
    return receipts


def _receipt_matches(receipt: Dict[str, str], family: str,
                     family_components: Dict[str, List[str]]) -> bool:
    rtype = receipt["type"]
    type_match = rtype == family or (
        family == LEGEND_FAMILY and rtype in _LEGEND_COMPONENT_TYPES)
    if not type_match:
        return False
    ids = family_components.get(family)
    if receipt["component_id"] and ids:
        return receipt["component_id"] in ids
    return True


def compare_semantics(expected: dict, actual: dict, *,
                      omitted_codes: Optional[List[Dict[str, Any]]] = None) -> dict:
    """逐族裁决（families ≤32；verdict/detail 稳定、可序列化）。

    - ``match``：族在 expected 且在 actual（计数可比族——legend——条目数
      一致；条目数漂移按静默丢失计 ``missing``）。
    - ``degraded``：expected 有、actual 缺，但 ``omitted_codes`` 有该组件的
      结构化省略回执（type/component_id 匹配）→ 诚实降级。
    - ``missing``：expected 有、actual 缺且无回执（静默丢失 = fail）。
    - ``extra``：隐藏 layer 的 id 分组或 paint 独占颜色出现在导出件
      （user-wins：隐藏不得入导出件）。
    ``ok = missing == 0 and extra == 0``。解析失败的 actual 等价于空抽取面
    （全部 expected 族 missing，除非有回执）。
    """
    expected = expected if isinstance(expected, dict) else {}
    actual = actual if isinstance(actual, dict) else {}
    parse_ok = actual.get("parse_ok") is True

    expected_families = [
        f for f in (expected.get("chrome_families") or [])
        if isinstance(f, str)
    ][:MAX_FAMILIES]
    actual_families = set(
        f for f in (actual.get("chrome_families") or []) if isinstance(f, str)
    ) if parse_ok else set()
    family_components = (
        expected.get("family_components")
        if isinstance(expected.get("family_components"), dict) else {}
    )
    receipts = _normalize_receipts(omitted_codes)

    report_families: List[Dict[str, str]] = []
    summary = {"match": 0, "degraded": 0, "missing": 0, "extra": 0}

    def _verdict(family: str, verdict: str, detail: str) -> None:
        if len(report_families) >= MAX_FAMILIES:
            return
        summary[verdict] = summary.get(verdict, 0) + 1
        report_families.append({"family": family, "verdict": verdict, "detail": detail})

    for family in expected_families:
        if family in actual_families:
            if family == LEGEND_FAMILY:
                exp_n = expected.get("legend_entries")
                act_n = actual.get("legend_entry_count") if parse_ok else 0
                if isinstance(exp_n, int) and isinstance(act_n, int) and exp_n != act_n:
                    _verdict(family, "missing",
                             f"legend_entry_count_mismatch expected={exp_n} actual={act_n}")
                    continue
                _verdict(family, "match", f"legend_entries={act_n}")
            else:
                _verdict(family, "match", "marker_present")
            continue
        # actual 缺席：回执 → 诚实降级；否则静默丢失
        hit = next((r for r in receipts if _receipt_matches(r, family, family_components)), None)
        if hit is not None:
            _verdict(family, "degraded", f"omitted_receipt code={hit['code'] or 'n/a'}")
        else:
            _verdict(family, "missing", "marker_absent_without_receipt")

    # 隐藏图层：id 分组 / paint 独占颜色任一出现 = extra（user-wins）
    hidden_layers = [
        h for h in (expected.get("hidden_layers") or []) if isinstance(h, str)
    ][:MAX_FAMILIES]
    if hidden_layers:
        layer_groups = set(
            g for g in (actual.get("layer_groups") or []) if isinstance(g, str)
        ) if parse_ok else set()
        leaked_ids = sorted(set(hidden_layers) & layer_groups)
        hidden_colors = set(
            c for c in (expected.get("hidden_layer_colors") or []) if isinstance(c, str)
        )
        actual_colors = set(
            c for c in (actual.get("vector_layer_colors") or []) if isinstance(c, str)
        ) if parse_ok else set()
        leaked_colors = sorted(hidden_colors & actual_colors)
        if leaked_ids or leaked_colors:
            detail_parts = []
            if leaked_ids:
                detail_parts.append("layer_groups=" + ",".join(leaked_ids[:8]))
            if leaked_colors:
                detail_parts.append("paint_colors=" + ",".join(leaked_colors[:8]))
            _verdict("hidden_layers", "extra", "hidden_layer_leaked " + " ".join(detail_parts))
        else:
            _verdict("hidden_layers", "match", "hidden_layers_absent_from_export")

    ok = summary["missing"] == 0 and summary["extra"] == 0
    return {
        "families": report_families,
        "summary": summary,
        "ok": ok,
        "parse_ok": parse_ok,
    }
