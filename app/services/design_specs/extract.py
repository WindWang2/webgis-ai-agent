"""Design Spec 提取 —— 从真实 MapSpec 生成设计规范草案（WP2）。

红线（任务书 §4 WP2）：
- **仅可移植表达**：只提取「怎么画」的声明（palette id / 分类方法与类数 /
  组件存在性 / 受控字体 / 纸张 profile），绝不携带数据值（具体断点转换为
  类数 k + 方法声明或如实标注不可恢复）、``ref:`` 数据游标、会话 selection
  或凭证形态字段；
- 草案**不落库** —— 返回 payload + 提取报告，由调用方走
  ``webgis_design_spec_create``（校验 fail-closed）显式保存；
- 匹配都是确定性的：palette 通过色集精确匹配 themes registry；字体按
  G34 允许列表 id 归一；识别不了就如实写进 ``unrecoverable``，不编造。
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

#: 提取器版本（报告可追溯）。
EXTRACTOR_VERSION = "design_spec_extract.v1"

_MAX_LAYERS_SCAN = 64
_MAX_COMPONENTS = 16

# theme id 推断：MapSpec 不携带 theme 声明（主题是前端投影/出版侧概念）——
# 提取面诚实不猜，theme 留空由作者补。
_THEME_INFERABLE = False


def _canonical_colors(colors: List[str]) -> Tuple[str, ...]:
    return tuple(sorted(str(c).strip().lower() for c in colors if str(c).strip()))


def _palette_index() -> Dict[Tuple[str, ...], str]:
    """palette 色集 → palette id 的确定性索引（themes registry 单一目录）。"""
    from app.lib.cartography.themes import get_cartographic_theme_registry

    index: Dict[Tuple[str, ...], str] = {}
    for desc in get_cartographic_theme_registry().palettes():
        colors = getattr(desc, "colors", None) or getattr(desc, "palette", None) or []
        if isinstance(colors, dict):
            colors = colors.get("colors") or []
        key = _canonical_colors(list(colors))
        if key:
            index.setdefault(key, desc.id)
    return index


def _collect_layer_colors(layer: Dict[str, Any]) -> List[str]:
    """layer → 专题色集（paint stops / legend_spec palette），去重保序。"""
    colors: List[str] = []

    def _push(value: Any) -> None:
        if isinstance(value, str) and value.startswith("#") and len(value) in (4, 7):
            colors.append(value)

    legend = layer.get("legend_spec")
    if isinstance(legend, dict):
        palette = legend.get("palette")
        if isinstance(palette, list):
            for c in palette:
                _push(c)
        colors_field = legend.get("colors")
        if isinstance(colors_field, list):
            for c in colors_field:
                _push(c)
        entries = legend.get("entries") or legend.get("classes") or []
        if isinstance(entries, list):
            for e in entries[:32]:
                if isinstance(e, dict):
                    _push(e.get("color"))
    paint = layer.get("paint") or {}
    if isinstance(paint, dict):
        stops = paint.get("fill-color", {}).get("stops") if isinstance(
            paint.get("fill-color"), dict) else None
        if stops is None:
            fc = paint.get("fill-color")
            if isinstance(fc, list) and fc and fc[0] == "step":
                stops = fc[2:]
            else:
                stops = paint.get("stops")
        if isinstance(stops, list):
            for stop in stops[:32]:
                if isinstance(stop, (list, tuple)) and len(stop) == 2:
                    _push(stop[1])
                elif isinstance(stop, str):
                    _push(stop)
        mc = paint.get("circle-color")
        if isinstance(mc, dict) and isinstance(mc.get("stops"), list):
            for stop in mc["stops"][:32]:
                if isinstance(stop, (list, tuple)) and len(stop) == 2:
                    _push(stop[1])
    return colors


def _match_palette(colors: List[str]) -> Tuple[Optional[str], float]:
    """色集精确匹配 → (palette_id | None, confidence)。"""
    if len(colors) < 3:
        return None, 0.0
    index = _palette_index()
    key = _canonical_colors(colors)
    if key in index:
        return index[key], 1.0
    # 子集匹配（图例可能只用了色带前 k 个色）：键包含关系，降置信
    for candidate_key, pid in index.items():
        if key and all(c in candidate_key for c in key) and len(key) >= 3:
            return pid, 0.6
    return None, 0.0


def _extract_classification(layer: Dict[str, Any]) -> Dict[str, Any]:
    """layer → 分类声明（k + 方法；具体断点绝不进草案）。"""
    legend = layer.get("legend_spec") if isinstance(layer.get("legend_spec"), dict) else {}
    out: Dict[str, Any] = {
        "k": None,
        "method": None,
        "recoverable": False,
        "note": "",
    }
    declared_method = legend.get("method") or legend.get("classification")
    if isinstance(declared_method, str) and declared_method:
        out["method"] = declared_method
        out["recoverable"] = True
    k = legend.get("classes") if isinstance(legend.get("classes"), int) else None
    if k is None:
        entries = legend.get("entries") or legend.get("classes") or []
        if isinstance(entries, list) and entries:
            k = len(entries)
    if k is None:
        ramp = legend.get("colors") or legend.get("palette")
        if isinstance(ramp, list) and 2 <= len(ramp) <= 12:
            k = len(ramp)
    if k is None:
        paint = layer.get("paint") or {}
        fc = paint.get("fill-color")
        if isinstance(fc, list) and fc and fc[0] == "step":
            # ["step", input, base, (v1,c1), (v2,c2)...] → 断点数 = 类数
            k = max(1, (len(fc) - 3) // 2 + 1)
    if isinstance(k, int) and 2 <= k <= 12:
        out["k"] = k
    if out["method"] is None:
        out["note"] = (
            "MapSpec 只携带具体断点（数据值，不可移植）；方法声明需作者显式补——"
            "草案仅声明类数 k"
        )
    return out


def _font_resource_from_family(family: Any) -> Tuple[Optional[str], Optional[str]]:
    """字体 family 字符串 → G34 受控资源 id（不可映射返回 None+披露）。"""
    if not isinstance(family, str) or not family.strip():
        return None, None
    from app.services.print_layout_authoring.fonts import FONT_IDS

    lowered = family.lower()
    for fid in FONT_IDS:
        # id 形如 noto-sans-cjk-sc：family 提及 noto/sans/cjk 即归一
        tokens = [t for t in fid.split("-") if t not in ("noto", "sans", "serif", "sc", "dejavu", "embedded")]
        hits = sum(1 for t in tokens if t in lowered)
        if hits >= 1 and (fid.split("-")[0] in lowered or hits >= 2):
            return fid, None
    return None, f"font family '{family[:60]}' 不在受控资源允许列表，未提取"


def _is_thematic(layer: Dict[str, Any]) -> bool:
    """专题层判定：显式声明（legend_spec/data_binding）或 step/interpolate
    数值色阶 paint（无声明的分类层也是专题表达，方法不可恢复但 k 可提取）。"""
    if isinstance(layer.get("legend_spec"), dict) or isinstance(layer.get("data_binding"), dict):
        return True
    paint = layer.get("paint")
    if not isinstance(paint, dict):
        return False
    for key in ("fill-color", "circle-color", "line-color"):
        value = paint.get(key)
        if isinstance(value, list) and value and value[0] in ("step", "interpolate"):
            return True
    return False


def _normalize_method(raw: Any) -> Tuple[Optional[str], bool]:
    """legend_spec 方法词 → classify 词表归一（不可映射 = (None, False)）。"""
    mapping = {
        "quantile": "quantiles",
        "quantiles": "quantiles",
        "equal_interval": "equal_interval",
        "equal-interval": "equal_interval",
        "natural_breaks": "natural_breaks",
        "jenks": "natural_breaks",
        "natural": "natural_breaks",
        "std_dev": "std_dev",
        "head_tail": "head_tail",
        "categorical": "categorical",
        "continuous": "continuous",
    }
    value = str(raw or "").strip().lower()
    if not value:
        return None, False
    if value in mapping:
        return mapping[value], True
    return None, False


def extract_design_spec_draft_sync(document: Dict[str, Any], *, session_id: Optional[str] = None) -> Dict[str, Any]:
    """MapSpec 文档 → 设计规范草案 + 提取报告（纯函数；不落库）。

    ``document`` 必须是会话权威 MapSpec（调用方负责归属校验）。草案通过
    ``validate_design_spec_payload`` + ``create_spec`` 落库。
    """
    from app.lib.cartography.publication_ir import PAGE_PROFILES

    layers = document.get("layers") or []
    if not isinstance(layers, list):
        layers = []
    excluded: List[Dict[str, str]] = []
    unrecoverable: List[str] = []

    palette_votes: Dict[str, float] = {}
    classification: Dict[str, Any] = {"k": None, "method": None, "note": ""}
    thematic_fields: List[str] = []
    category_fields: List[str] = []
    units: List[str] = []

    for layer in layers[:_MAX_LAYERS_SCAN]:
        if not isinstance(layer, dict):
            continue
        legend = layer.get("legend_spec")
        data_binding = layer.get("data_binding")
        thematic = _is_thematic(layer)
        if not thematic:
            continue
        colors = _collect_layer_colors(layer)
        pid, confidence = _match_palette(colors)
        if pid:
            palette_votes[pid] = palette_votes.get(pid, 0.0) + confidence
        elif len(colors) >= 3:
            unrecoverable.append(
                f"layer '{str(layer.get('id'))[:40]}' 的自定义色板无法映射到内置色带（未提取）"
            )
        cls = _extract_classification(layer)
        if cls["recoverable"]:
            normalized, ok = _normalize_method(cls["method"])
            if ok:
                classification["method"] = normalized
                classification["recoverable"] = True
            else:
                classification["recoverable"] = False
                unrecoverable.append(
                    f"layer '{str(layer.get('id'))[:40]}' 的分类方法 "
                    f"'{str(cls['method'])[:24]}' 不在 classify 词表（未提取，需作者显式声明）"
                )
        if cls["k"] and not classification["k"]:
            classification["k"] = cls["k"]
        if cls["note"] and cls["note"] not in classification["note"]:
            classification["note"] = cls["note"]
        field = None
        if isinstance(data_binding, dict):
            field = data_binding.get("field")
            if field:
                thematic_fields.append(str(field))
            class_field = data_binding.get("class_field")
            if class_field:
                category_fields.append(str(class_field))
        if isinstance(legend, dict):
            if legend.get("field") and not field:
                thematic_fields.append(str(legend["field"])[:64])
            if isinstance(legend.get("unit"), str) and legend["unit"].strip():
                units.append(legend["unit"].strip()[:24])
        # ref 游标/凭证形态绝不出现在草案（数据引用是会话事实，不是设计）
        source_key = str(layer.get("source") or "")
        if source_key.startswith("ref:"):
            excluded.append(
                {"item": f"source '{source_key[:40]}'", "reason": "data_ref_not_portable"}
            )

    # palette 投票（确定性：票数最高，平票取字典序）
    matched_palette = None
    palette_confidence = 0.0
    if palette_votes:
        best = sorted(palette_votes.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        matched_palette, palette_confidence = best[0], round(best[1], 3)

    # 组件（存在性声明；手工位置不提取 —— 位置属用户覆盖层）
    layout = document.get("layout") or {}
    components = layout.get("components") if isinstance(layout.get("components"), list) else []
    required_components: List[Dict[str, Any]] = []
    for comp in components[:_MAX_COMPONENTS]:
        if not isinstance(comp, dict):
            continue
        ctype = str(comp.get("type") or "")
        if not ctype:
            continue
        entry: Dict[str, Any] = {"component_type": ctype}
        variant = comp.get("variant") or comp.get("style", {}).get("variant") if isinstance(
            comp.get("style"), dict) else comp.get("variant")
        if isinstance(variant, str) and variant:
            entry["variant"] = variant[:64]
        required_components.append(entry)

    # 纸面版式 → 输出 profile + 字体
    print_layout = layout.get("print_layout") if isinstance(layout.get("print_layout"), dict) else {}
    paper_profile: Optional[str] = None
    font_resource: Optional[str] = None
    font_note: Optional[str] = None
    if print_layout:
        paper = print_layout.get("paper") or {}
        profile = paper.get("profile")
        if isinstance(profile, str) and profile in PAGE_PROFILES:
            paper_profile = profile
        elif profile:
            unrecoverable.append(f"paper profile '{str(profile)[:40]}' 不在 PAGE_PROFILES 词表")
        typography = print_layout.get("typography") or {}
        font_resource, font_note = _font_resource_from_family(typography.get("font_family"))
        if font_note:
            unrecoverable.append(font_note)

    # 数据角色草案：指标（数值）+ 可选类目字段；measurement/unit 不编造
    data_roles: List[Dict[str, Any]] = []
    if thematic_fields:
        indicator: Dict[str, Any] = {
            "name": "indicator",
            "value_type": "number",
            "comparable_across_maps": bool(units) or False,
        }
        if len(set(units)) == 1:
            indicator["unit"] = units[0]
            indicator["unit_required"] = True
        data_roles.append(indicator)
        if category_fields:
            data_roles.append({"name": "category_key", "value_type": "category"})

    payload: Dict[str, Any] = {
        "spec_version": "design_spec.v1",
        "name": "提取草案·未命名",  # 作者落库时必须改名（唯一性校验）
        "color": {},
        "classification": {
            "k_min": classification["k"] or 3,
            "k_max": classification["k"] or 6,
            "shared_required": False,
        },
        "typography": {"font_resource": font_resource} if font_resource else {},
        "components": {"required": required_components},
        "output": {"targets": ["interactive", "png", "pdf", "svg"]},
        "data_roles": data_roles,
        "overrides": {
            "user_overridable": [
                "title", "subtitle", "extent", "viewport", "annotation_text",
                "manual_positions", "layer_visibility",
            ],
            "locked": [],
            "require_override_disclosure": True,
        },
        "lineage": {
            "source": "extracted",
            "source_session_id": str(session_id)[:255] if session_id else None,
        },
    }
    if matched_palette:
        payload["color"] = {"palette_sequential": matched_palette}
    if classification["method"]:
        payload["classification"]["default_method"] = classification["method"]

    fingerprint = _fingerprint_document(document)
    report = {
        "extractor_version": EXTRACTOR_VERSION,
        "source_mapspec_fingerprint": fingerprint[:32],
        "thematic_layers_scanned": min(len(layers), _MAX_LAYERS_SCAN),
        "matched_palette": matched_palette,
        "palette_confidence": palette_confidence,
        "classification": classification,
        "components_extracted": len(required_components),
        "data_roles_extracted": [r["name"] for r in data_roles],
        "unit_declared": units[0] if len(set(units)) == 1 else None,
        "paper_profile": paper_profile,
        "font_resource": font_resource,
        "theme_inferred": _THEME_INFERABLE,
        "unrecoverable": unrecoverable[:16],
        "excluded": excluded[:16],
        "notes": [
            "草案不自动落库；显式调用 create（webgis_design_spec_create / POST /design-specs）",
            "具体分类断点是数据值，永不进入规范 —— 只声明方法与类数",
            "theme 未推断：MapSpec 不携带主题声明，由作者显式选择",
        ],
    }
    return {"draft_payload": payload, "extraction_report": report}


async def extract_design_spec_draft(session_id: str) -> Dict[str, Any]:
    """会话 → 草案（读取权威 MapSpec；大文档 fingerprint 在线程内计算）。"""
    from app.services.mapspec_store import mapspec_store

    store = mapspec_store
    document = await store.get_mapspec(str(session_id))
    if not isinstance(document, dict) or not document:
        from app.services.design_specs.errors import DesignSpecNotFoundError

        raise DesignSpecNotFoundError(
            f"session '{str(session_id)[:64]}' has no committed MapSpec to extract from"
        )
    result = await asyncio.to_thread(
        extract_design_spec_draft_sync, document, session_id=str(session_id)
    )
    return result


def _fingerprint_document(document: Dict[str, Any]) -> str:
    import hashlib
    import json

    payload = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "EXTRACTOR_VERSION",
    "extract_design_spec_draft",
    "extract_design_spec_draft_sync",
]
