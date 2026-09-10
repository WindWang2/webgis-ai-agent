"""Style Tokens V7 — 类型化制图样式 token（Goal 08 Phase E）.

``Template = Component Graph + Constraint Set + Style Tokens`` 的「token」
一侧。themes.py 的 TypographySpec/SpacingSpec 是 **chrome 结构契约**（字号
引用前端 token 名）；本模块补齐**制图语义 token 比例尺**：

- 线宽比例（minor/major/emphasis）× 输出预设（screen / publication）；
- 符号尺寸比例（point min/default/max、graduated 级差）；
- 视觉层级梯度（title/subtitle/legend/annotation —— 消费 TypographySpec
  的 token 名，不建第二事实源）；
- 组件样式解析：``resolve_component_style`` → 有界 style dict，live 与
  export 共用同一真值（parity 由 golden 测试锁）；
- 调色板 profile 迁移规划：``plan_palette_profile_migration`` 只重写
  **可精确反查为本库色带**的 paint（hex 全部命中同一已注册 ramp），目标
  色带按 kind/类数/print_safe 语义选取；不可识别 → 诚实披露（不猜、
  不改写用户自定义颜色 —— 与 themes 弃改写原则一致，只给建议补丁）。

红线：纯函数、确定性、有界载荷；本模块不做渲染，不改 MapSpec。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

#: hex 颜色（#rgb / #rrggbb；与本库色带常量同形）。
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$|^#[0-9a-fA-F]{3}$")

#: 输出预设词表。screen = 浏览器/投影（对比强、线粗）；publication =
#: 印刷/矢量 PDF（线细一档、字号梯度收紧）。
STYLE_PRESET_IDS = ("screen", "publication")


class LineTokenScale(BaseModel):
    """线宽比例（px）。minor=参考层/网格，major=行政边界，emphasis=主表达。"""

    minor: float
    major: float
    emphasis: float


class SymbolTokenScale(BaseModel):
    """点符号尺寸比例（px 直径）与分级步长。"""

    point_min_px: float
    point_default_px: float
    point_max_px: float
    graduated_step_px: float


class HierarchyLevel(BaseModel):
    """一个视觉层级的排版档（token 名引用 themes TypographySpec 词表）。"""

    font_size_token: str
    weight: int


class HierarchyTokens(BaseModel):
    title: HierarchyLevel
    subtitle: HierarchyLevel
    legend: HierarchyLevel
    annotation: HierarchyLevel


class StyleTokens(BaseModel):
    """一份可解析的制图 token 集（有界、可序列化）。"""

    preset: str
    theme_profile: str
    line: LineTokenScale
    symbol: SymbolTokenScale
    hierarchy: HierarchyTokens
    #: 背景/边框引用 themes ChromeTokenRefs 同名 token（颜色真值在前端）
    background_token: str = "map-chrome-bg"
    border_width_px: float = 1.0
    halo_width_px: float = 1.0

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "preset": self.preset,
            "themeProfile": self.theme_profile,
            "line": self.line.model_dump(),
            "symbol": self.symbol.model_dump(),
            "hierarchy": self.hierarchy.model_dump(),
            "background": self.background_token,
            "borderWidth": self.border_width_px,
            "haloWidth": self.halo_width_px,
        }


# ── 预设表（审定数值；改值必须双侧 parity 测试同步）─────────────────────

_PRESETS: Dict[str, Dict[str, Any]] = {
    "screen": {
        "line": LineTokenScale(minor=1.0, major=1.6, emphasis=2.5),
        "symbol": SymbolTokenScale(
            point_min_px=4.0, point_default_px=7.0, point_max_px=18.0,
            graduated_step_px=2.5),
        "hierarchy": HierarchyTokens(
            title=HierarchyLevel(font_size_token="text-title", weight=600),
            subtitle=HierarchyLevel(font_size_token="text-caption", weight=500),
            legend=HierarchyLevel(font_size_token="text-caption", weight=400),
            annotation=HierarchyLevel(font_size_token="text-micro", weight=400)),
        "border_width_px": 1.0,
        "halo_width_px": 1.0,
    },
    "publication": {
        "line": LineTokenScale(minor=0.6, major=1.0, emphasis=1.8),
        "symbol": SymbolTokenScale(
            point_min_px=3.0, point_default_px=6.0, point_max_px=14.0,
            graduated_step_px=2.0),
        "hierarchy": HierarchyTokens(
            title=HierarchyLevel(font_size_token="text-title", weight=700),
            subtitle=HierarchyLevel(font_size_token="text-caption", weight=500),
            legend=HierarchyLevel(font_size_token="text-micro", weight=400),
            annotation=HierarchyLevel(font_size_token="text-micro", weight=400)),
        "border_width_px": 0.8,
        "halo_width_px": 0.6,
    },
}

#: 输出目标 → 预设缺省（interactive/png → screen；pdf/svg/print → publication）。
_OUTPUT_PRESET: Dict[str, str] = {
    "interactive": "screen",
    "png": "screen",
    "pdf": "publication",
    "svg": "publication",
    "print": "publication",
}


def resolve_style_tokens(
    output_target: str = "interactive",
    theme_profile: str = "light",
    *,
    preset: str = "",
) -> StyleTokens:
    """输出目标 + 主题 profile → token 集（确定性；未知值回退缺省并收紧）。

    print/high_contrast 主题在 screen 输出下仍取 publication 线宽
    （高对比印刷语义），字号 token 不变。
    """
    preset_id = preset if preset in STYLE_PRESET_IDS else _OUTPUT_PRESET.get(
        output_target, "screen")
    if theme_profile in ("print", "high_contrast") and preset_id == "screen":
        preset_id = "publication"
    conf = _PRESETS[preset_id]
    return StyleTokens(
        preset=preset_id,
        theme_profile=theme_profile,
        line=conf["line"].model_copy(),
        symbol=conf["symbol"].model_copy(),
        hierarchy=conf["hierarchy"].model_copy(deep=True),
        border_width_px=conf["border_width_px"],
        halo_width_px=conf["halo_width_px"],
    )


# ── 组件样式解析 ─────────────────────────────────────────────────────────

#: 组件族 → 制图 token 消费面（有界 style 键；live/export 同真值）。
_COMPONENT_TOKEN_KEYS: Dict[str, Tuple[str, ...]] = {
    "title": ("hierarchy.title",),
    "subtitle": ("hierarchy.subtitle",),
    "legend": ("hierarchy.legend", "border_width"),
    "categorical_legend": ("hierarchy.legend", "border_width"),
    "continuous_colorbar": ("hierarchy.legend", "border_width"),
    "annotation": ("hierarchy.annotation",),
    "methodology_note": ("hierarchy.annotation",),
    "uncertainty_panel": ("hierarchy.annotation",),
    "decision_panel": ("hierarchy.annotation",),
    "attribution": ("hierarchy.annotation",),
    "statistics_panel": ("hierarchy.legend", "border_width"),
    "chart_panel": ("hierarchy.legend", "border_width"),
    "table_panel": ("hierarchy.legend", "border_width"),
    "scale_bar": ("line.minor", "hierarchy.annotation"),
    "north_arrow": ("line.minor",),
    "map_border": ("border_width", "line.major"),
    "graticule": ("line.minor",),
}


def resolve_component_style(
    component_type: str,
    output_target: str = "interactive",
    theme_profile: str = "light",
) -> Dict[str, Any]:
    """组件类型 → token 化 style（有界 dict；未登记组件 → 层级档兜底）。

    返回键：``fontSizeToken``/``fontWeight``/``lineWidth``/``borderWidth``
    /``haloWidth``（按该组件的 token 消费面裁剪）。这是 token 投影 ——
    不替代组件 options/style 的用户显式值（显式值优先级更高，由消费方
    merge）。
    """
    tokens = resolve_style_tokens(output_target, theme_profile)
    keys = _COMPONENT_TOKEN_KEYS.get(component_type, ("hierarchy.legend",))
    style: Dict[str, Any] = {}
    for key in keys:
        if key == "border_width":
            style["borderWidth"] = tokens.border_width_px
        elif key == "halo_width":
            style["haloWidth"] = tokens.halo_width_px
        elif key.startswith("line."):
            attr = key.split(".", 1)[1]
            style["lineWidth"] = float(getattr(tokens.line, attr))
        elif key.startswith("hierarchy."):
            level = getattr(tokens.hierarchy, key.split(".", 1)[1])
            style.setdefault("fontSizeToken", level.font_size_token)
            style.setdefault("fontWeight", level.weight)
    return style


# ── 调色板 profile 迁移规划（建议补丁，不改写 MapSpec）────────────────────


def _collect_paint_colors(paint: Any) -> List[str]:
    """paint 中可识别的 hex 颜色（形状无关：StyleMethod / MapLibre 原生
    interpolate/step/match / 裸色字符串都走同一深度遍历，出现序保留）。"""
    found: List[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, str):
            if _HEX_COLOR_RE.match(node):
                found.append(node.lower())
        elif isinstance(node, list):
            for item in node:
                _walk(item)
        elif isinstance(node, dict):
            for value in node.values():
                _walk(value)

    _walk(paint or {})
    return found


def _ramp_of_colors(colors: List[str]) -> Optional[str]:
    """全部颜色命中同一已注册色带（子集包含）→ 色带 id；否则 None。

    判定用「子集 + 最紧拟合」：真实制图常用色带的非连续子集（端点/隔级
    取色），连续窗口过严；多个色带同时包含时取**最短**色带（最紧拟合，
    tie 字典序）—— 确定性且语义上最贴合来源。
    """
    from app.lib.cartography.palettes import COLOR_PALETTES, NATIVE_HEATMAP_COLORS
    if not colors:
        return None
    uniq = list(dict.fromkeys(colors))
    best: Optional[Tuple[int, str]] = None
    for pid, hexes in COLOR_PALETTES.items():
        if all(c in hexes for c in uniq):
            key = (len(hexes), pid)
            if best is None or key < best:
                best = key
    for pid, hexes in NATIVE_HEATMAP_COLORS.items():
        if all(c in hexes for c in uniq):
            key = (len(hexes), pid)
            if best is None or key < best:
                best = key
    return best[1] if best else None


def _candidate_replacement(
    source_pid: str,
    class_count: int,
    *,
    require_print_safe: bool = False,
) -> Optional[str]:
    """同 kind、≥class_count 级、（可选）print_safe 的替代色带（确定性首选）。"""
    from app.lib.cartography.themes import build_palette_descriptors
    descs = {d.id: d for d in build_palette_descriptors()}
    source = descs.get(source_pid)
    if source is None:
        return None
    candidates = []
    for desc in descs.values():
        if desc.id == source_pid:
            continue
        if desc.kind != source.kind:
            continue
        if require_print_safe and not desc.print_safe:
            continue
        if desc.id not in _ramp_lengths():
            continue
        if _ramp_lengths()[desc.id] < class_count:
            continue
        candidates.append(desc.id)
    return candidates[0] if candidates else None


_RAMP_LENGTH_CACHE: Dict[str, int] = {}


def _ramp_lengths() -> Dict[str, int]:
    if not _RAMP_LENGTH_CACHE:
        from app.lib.cartography.palettes import COLOR_PALETTES, NATIVE_HEATMAP_COLORS
        for pid, hexes in COLOR_PALETTES.items():
            _RAMP_LENGTH_CACHE[pid] = len(hexes)
        for pid, hexes in NATIVE_HEATMAP_COLORS.items():
            _RAMP_LENGTH_CACHE[pid] = len(hexes)
    return _RAMP_LENGTH_CACHE


def plan_palette_profile_migration(
    paint: Any,
    *,
    target_profile: str = "print",
) -> Dict[str, Any]:
    """主题/输出 profile 切换时的调色板迁移建议（**只建议，不执行**）。

    识别规则：paint 的全部输出色（hex 大小写归一）为同一已注册色带的
    子集（最紧拟合，见 ``_ramp_of_colors``）→ 可迁移；在 print 目标下
    选取同 kind、同级数、print_safe 的替代带（语义位置等位替换）。用户
    自定义颜色（不在本库色带内）→ ``applicable=False`` + 披露，绝不猜
    近邻色改写（不虚构语义）。

    返回：``{applicable, source_palette, target_palette, patch, disclosures}``
    patch 为 paint 同形的等位色替换（消费方自行评审后经既有 mutation
    通道应用 —— quality_loop 的 AUTO_SAFE 语义不变）。
    """
    disclosures: List[str] = []
    colors = _collect_paint_colors(paint)
    if not colors:
        return {"applicable": False, "source_palette": "", "target_palette": "",
                "patch": None, "disclosures": ["paint 无可识别输出色"]}
    source_pid = _ramp_of_colors(colors)
    if source_pid is None:
        disclosures.append(
            "paint 颜色不完全命中任一已注册色带：不猜测替换（用户自定义色保留）")
        return {"applicable": False, "source_palette": "", "target_palette": "",
                "patch": None, "disclosures": disclosures}

    uniq = list(dict.fromkeys(colors))
    require_print_safe = target_profile in ("print", "high_contrast")
    target_pid = _candidate_replacement(
        source_pid, len(uniq), require_print_safe=require_print_safe)
    if target_pid is None or target_pid == source_pid:
        disclosures.append(
            f"色带 {source_pid} 无满足语义的替代（kind/级数/print_safe）")
        return {"applicable": False, "source_palette": source_pid,
                "target_palette": "", "patch": None,
                "disclosures": disclosures}

    from app.lib.cartography.palettes import COLOR_PALETTES, NATIVE_HEATMAP_COLORS
    target_hexes = COLOR_PALETTES.get(target_pid) or NATIVE_HEATMAP_COLORS.get(target_pid) or []
    source_hexes = COLOR_PALETTES.get(source_pid) or NATIVE_HEATMAP_COLORS.get(source_pid) or []
    if not target_hexes:
        disclosures.append(f"目标色带 {target_pid} hex 缺失（目录不一致）")
        return {"applicable": False, "source_palette": source_pid,
                "target_palette": "", "patch": None,
                "disclosures": disclosures}
    # 语义位置对齐：源色按其在源色带中的索引占比映射到目标带同级位置
    # （非连续子集不串序 —— 端点仍映射端点）。
    ordered = sorted(uniq, key=lambda c: source_hexes.index(c) if c in source_hexes else 0)
    remap: Dict[str, str] = {}
    for i, src in enumerate(ordered):
        frac = 0.0 if len(ordered) == 1 else i / (len(ordered) - 1)
        tgt_idx = min(len(target_hexes) - 1, round(frac * (len(target_hexes) - 1)))
        remap[src] = target_hexes[tgt_idx]
    patch = _remap_paint_colors(paint, remap)
    return {
        "applicable": True,
        "source_palette": source_pid,
        "target_palette": target_pid,
        "patch": patch,
        "disclosures": disclosures,
    }


def _remap_paint_colors(paint: Any, remap: Dict[str, str]) -> Any:
    """等位色替换（深度遍历 paint；结构与其余值原样保留）。hex 查找
    大小写无关 —— remap 键为小写（识别阶段归一），大写原色同样命中。"""
    if isinstance(paint, str):
        if _HEX_COLOR_RE.match(paint):
            return remap.get(paint.lower(), paint)
        return paint
    if isinstance(paint, list):
        return [_remap_paint_colors(item, remap) for item in paint]
    if isinstance(paint, dict):
        return {k: _remap_paint_colors(v, remap) for k, v in paint.items()}
    return paint


__all__ = [
    "STYLE_PRESET_IDS",
    "LineTokenScale",
    "SymbolTokenScale",
    "HierarchyLevel",
    "HierarchyTokens",
    "StyleTokens",
    "resolve_style_tokens",
    "resolve_component_style",
    "plan_palette_profile_migration",
]
