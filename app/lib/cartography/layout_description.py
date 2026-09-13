"""出版版面描述中间层（Publication Layout IR）—— Python 忠实镜像（ADR-0157 P6）。

前端单源：``frontend/lib/export/layout-description.ts``（含 extent 数学
``frontend/lib/export/extent.ts``）。本模块逐字段镜像其纯函数装配语义：

- 版面档（paper/orientation/dpi/colorMode/bleed/cropMarks）；
- 标题回退链（请求参数 > spec 组件 > 空串）；
- 比例尺 nice-number（1/2/5×10^k 就近，与 ``map-kit/scale-math.ts`` 同算法）；
- WYSIWYG 范围契约（遮罩 ⊆ 导出、Mercator 归一量纲纵横比、数据超界提示）。

双端 parity 由 golden corpus 锁定（``tests/cartography/golden_corpus/
layout_description/``：pytest 与 vitest 消费同一批 fixture，逐字段对拍）。
前端 ``composeLayout``（canvas 绘制面）不在镜像范围 —— 本模块只承载
**决策**（什么元素、什么文本、什么数字），不承载像素绘制。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

PUBLICATION_LAYOUT_VERSION = 1

#: C2 共享版面描述 IR 版本（V11 W0.1，ADR-0160）。v2 是「整饰组件级」IR：
#: 与上方 v1（页面级：纸张/文本/比例尺/范围）互补，是三套整饰渲染器
#: （React DOM / canvas / SVG）的共同输入。W0 定稿冻结；W5（版面自愈/
#: 多图版面）与 W6（三渲染器收敛）消费。
LAYOUT_IR_VERSION = 2

#: 出版档（cmyk）出血宽度（毫米）；与前端 PUBLICATION_BLEED_MM 同值。
PUBLICATION_BLEED_MM = 3

#: A4/A3 纵横比（width/height）；与前端 frameAspectWH / prepareExportCanvas 同口径。
_PAPER_RATIO = 1.414

BoundsWSEN = Tuple[float, float, float, float]


# ══ C2 共享版面描述 IR（LAYOUT_IR_VERSION = 2；W0 定稿，契约冻结）════════
#
# 设计约束（ADR-0160 §3）：
# - **承载决策不承载像素**：IR 描述「什么组件、什么几何约束、什么层级、
#   什么样式 token、什么排版指令」；具体绘制（DOM 树/canvas 指令/SVG 节点）
#   由各渲染器自行落地 —— 这是 G3 的解药：三套渲染器从同一 IR 渲染等价结果。
# - 确定性：纯函数装配，同输入恒同输出；golden corpus 双端对拍锁定。
# - 有界：组件数、层级数、token 引用均有上界（渲染面不接收无界载荷）。

#: 组件种类枚举（与 component_registry 类型词汇对齐；渲染器未实现的种类
#: 诚实降级并记 degradation，不得静默丢弃）。
LAYOUT_IR_COMPONENT_KINDS = (
    "north_arrow", "scale_bar", "legend", "title", "subtitle",
    "author", "data_source", "attribution", "chart_panel", "inset_map",
    "graticule", "text_note",
)

#: 组件角色（决定层级默认值与遮挡裁决优先级；W4/W5 优先级模型共享）。
LAYOUT_IR_ROLES = ("primary", "secondary", "decorative")

#: 锚点九宫格词汇（frame 表达方式之一；绝对 x/y 与 anchor 二选一，同给时
#: anchor 为准 —— 与 compose.ts 的 anchor 语义对齐）。
LAYOUT_IR_ANCHORS = (
    "top_left", "top_center", "top_right",
    "middle_left", "middle_center", "middle_right",
    "bottom_left", "bottom_center", "bottom_right",
)

#: 文本断行模式（W4 多语言断行契约的前置词汇：CJK 按字、拉丁按词）。
LAYOUT_IR_WRAP_MODES = ("none", "cjk_char", "latin_word", "auto")


def _ir_check(cond: bool, message: str, issues: List[str]) -> None:
    if not cond:
        issues.append(message)


def validate_layout_ir(ir: Dict[str, Any]) -> List[str]:
    """C2 IR 结构校验（fail-closed：渲染器拒绝非法 IR，返回问题列表）。

    锁定面：版本、种类/角色/锚点/断行枚举、组件几何在画布内且非负、
    z 层级为整数、组件 id 唯一、载荷有界。同层并列（z 相同）合法 ——
    同层内序由 id 字典序稳定排（跨语言 tie-break 契约）。
    """
    issues: List[str] = []
    _ir_check(ir.get("version") == LAYOUT_IR_VERSION, "version 必须为 2", issues)
    canvas = ir.get("canvas") or {}
    cw, ch = canvas.get("widthPx", 0), canvas.get("heightPx", 0)
    _ir_check(isinstance(cw, int) and cw > 0, "canvas.widthPx 必须为正整数", issues)
    _ir_check(isinstance(ch, int) and ch > 0, "canvas.heightPx 必须为正整数", issues)

    components = ir.get("components") or []
    _ir_check(len(components) <= 32, "components 超上界（32）", issues)
    seen_ids: set = set()
    for comp in components:
        cid = comp.get("id", "")
        _ir_check(bool(cid) and cid not in seen_ids, f"组件 id 重复/为空: {cid!r}", issues)
        seen_ids.add(cid)
        _ir_check(comp.get("kind") in LAYOUT_IR_COMPONENT_KINDS,
                  f"{cid}: kind 非法 {comp.get('kind')!r}", issues)
        _ir_check(comp.get("role") in LAYOUT_IR_ROLES,
                  f"{cid}: role 非法 {comp.get('role')!r}", issues)
        frame = comp.get("frame") or {}
        x, y = frame.get("x", 0), frame.get("y", 0)
        w, h = frame.get("width", 0), frame.get("height", 0)
        _ir_check(all(isinstance(v, (int, float)) and v >= 0 for v in (x, y, w, h)),
                  f"{cid}: frame 几何必须非负", issues)
        _ir_check(x + w <= cw + 1e-6 and y + h <= ch + 1e-6,
                  f"{cid}: frame 超出画布", issues)
        _ir_check(isinstance(frame.get("z"), int), f"{cid}: z 层级缺失/非整数", issues)
        _ir_check(frame.get("anchor") in LAYOUT_IR_ANCHORS,
                  f"{cid}: anchor 非法", issues)
        typ = comp.get("typography")
        if typ is not None:
            _ir_check(typ.get("wrapMode") in LAYOUT_IR_WRAP_MODES,
                      f"{cid}: typography.wrapMode 非法", issues)
    return issues


def build_layout_ir(
    *,
    canvas: Dict[str, int],
    components: List[Dict[str, Any]],
    degradations: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """装配 C2 共享版面描述 IR（确定性、JSON 可序列化 —— golden 对拍面）。

    ``components`` 入参形状（渲染前决策面，来自 compose / 组件裁决）：
    ``{id, kind, role, frame: {x, y, width, height, anchor, z},
    constraints?, style?, typography?}`` —— 本函数只做规范化（补默认值、
    定型化）与排序（z 升序稳定序），不裁剪、不重排语义。

    升序 z 即 ``layers``（bottom→top）；渲染器按序绘制即可获得一致层级。
    """
    normalized: List[Dict[str, Any]] = []
    for comp in components:
        frame = comp.get("frame") or {}
        style_in = comp.get("style") or {}
        entry = {
            "id": comp["id"],
            "kind": comp["kind"],
            "role": comp.get("role", "decorative"),
            "frame": {
                "x": frame.get("x", 0),
                "y": frame.get("y", 0),
                "width": frame.get("width", 0),
                "height": frame.get("height", 0),
                "anchor": frame.get("anchor", "top_left"),
                "z": frame.get("z", 0),
            },
            "constraints": comp.get("constraints") or {},
            "style": {**style_in, "token": style_in.get("token")},
            "typography": comp.get("typography"),
        }
        normalized.append(entry)
    normalized.sort(key=lambda c: (c["frame"]["z"], c["id"]))
    return {
        "version": LAYOUT_IR_VERSION,
        "canvas": {"widthPx": canvas["widthPx"], "heightPx": canvas["heightPx"]},
        "layers": [
            {"id": c["id"], "z": c["frame"]["z"]} for c in normalized
        ],
        "components": normalized,
        "degradations": list(degradations or []),
    }


# ── Mercator 范围数学（extent.ts 镜像）─────────────────────────────────


def merc_norm_y(lat: float) -> float:
    """Web Mercator 归一 y（0=赤道，1=纬 85.05°）；无效纬度钳到投影范围。"""
    clamped = max(-85.05112878, min(85.05112878, lat))
    return 0.5 - math.log(math.tan(math.pi / 4 + math.radians(clamped) / 2)) / (2 * math.pi)


def _lat_of_norm(y: float) -> float:
    clamped = max(0.0, min(1.0, y))
    merc_n = 2 * math.pi * (0.5 - clamped)
    return math.degrees(2 * math.atan(math.exp(merc_n)) - math.pi / 2)


def _is_finite_bounds(b: Optional[Sequence[float]]) -> bool:
    return (
        b is not None
        and len(b) == 4
        and all(isinstance(v, (int, float)) and math.isfinite(v) for v in b)
        and b[2] > b[0]
        and b[3] > b[1]
    )


def export_bounds_for_frame(mask: BoundsWSEN, aspect_wh: float) -> Optional[BoundsWSEN]:
    """遮罩范围 → 图框导出范围（⊇ 遮罩，Mercator 中心不动点扩张）。

    量纲契约：经度差归一化（/360）后再与归一纬差比 —— 与前端实现一致
    （此前 TS 侧度/归一混用的 bug 即由该 parity 测试面捕获）。
    """
    if not _is_finite_bounds(mask) or not (aspect_wh > 0):
        return None
    w, s, e, n = mask
    y0 = merc_norm_y(s)
    y1 = merc_norm_y(n)
    merc_w = (e - w) / 360.0
    merc_h = y0 - y1
    if not (merc_w > 0) or not (merc_h > 0):
        return None

    cx = (w + e) / 2.0
    cy = (y0 + y1) / 2.0
    if merc_w / merc_h > aspect_wh:
        out_w, out_h = merc_w, merc_w / aspect_wh
    else:
        out_h, out_w = merc_h, merc_h * aspect_wh

    west = cx - out_w / 2 * 360
    east = cx + out_w / 2 * 360
    south = _lat_of_norm(cy + out_h / 2)
    north = _lat_of_norm(cy - out_h / 2)
    return (west, south, east, north)


def bounds_contained(
    data: Optional[Sequence[float]], frame: Optional[Sequence[float]]
) -> bool:
    """数据范围是否完全落入导出范围（非法输入 → true，不误报）。"""
    if not _is_finite_bounds(data) or not _is_finite_bounds(frame):
        return True
    eps = 1e-9
    return (
        data[0] >= frame[0] - eps
        and data[1] >= frame[1] - eps
        and data[2] <= frame[2] + eps
        and data[3] <= frame[3] + eps
    )


# ── 比例尺 nice-number（map-kit/scale-math.ts 镜像）────────────────────


def compute_nice_scale(meters_per_px: float, target_px: float) -> Dict[str, float]:
    """就近 nice 距离（1/2/5×10^k），与 TS 实现同序同判（严格 <，先到先得）。"""
    mpp = meters_per_px if meters_per_px > 0 else 1
    target = target_px if target_px > 0 else 100
    raw = mpp * target
    magnitude = 10 ** math.floor(math.log10(raw))
    best = magnitude
    for n in (1, 2, 5, 10):
        candidate = n * magnitude
        if abs(candidate - raw) < abs(best - raw):
            best = candidate
    return {"meters": float(best), "px": best / mpp}


def format_scale_label(meters: float) -> str:
    """距离标签（1000+ 米转 km，至多 1 位小数）。"""
    if meters >= 1000:
        return f"{round(meters / 1000, 1):g} km"
    return f"{meters:g} m"


def frame_aspect_wh(orientation: str) -> float:
    return 1.0 / _PAPER_RATIO if orientation == "portrait" else _PAPER_RATIO


# ── 版面描述装配（layout-description.ts 镜像）──────────────────────────


@dataclass
class LayoutInput:
    """装配输入（与前端 BuildPublicationLayoutInput 同形）。"""

    paper_size: str  # 'screen' | 'A4' | 'A3'
    orientation: str  # 'landscape' | 'portrait'
    dpi: int
    frame_width: float
    frame_height: float
    request_title: str = ""
    spec_title: str = ""
    request_subtitle: str = ""
    spec_subtitle: str = ""
    author: str = ""
    data_source: str = ""
    attribution_text: str = ""
    meters_per_pixel: Optional[float] = None
    mask_extent: Optional[BoundsWSEN] = None
    export_extent: Optional[BoundsWSEN] = None
    data_overflow: bool = False
    color_mode: str = "srgb"  # 'srgb' | 'cmyk'
    scale_bar_target_px: float = 120.0


def build_publication_layout(inp: LayoutInput) -> Dict[str, Any]:
    """装配出版版面描述（确定性、JSON 可序列化 —— golden 对拍面）。"""
    color_mode = inp.color_mode if inp.color_mode in ("srgb", "cmyk") else "srgb"
    bleed_mm = PUBLICATION_BLEED_MM if color_mode == "cmyk" else 0
    page = {
        "paperSize": inp.paper_size,
        "orientation": inp.orientation,
        "dpi": inp.dpi,
        "widthPx": round(inp.frame_width),
        "heightPx": round(inp.frame_height),
        "colorMode": color_mode,
        "bleedMm": bleed_mm,
        "cropMarks": color_mode == "cmyk",
    }

    title = inp.request_title or inp.spec_title or ""
    subtitle = inp.request_subtitle or inp.spec_subtitle or ""
    texts: List[Dict[str, str]] = []
    if title:
        texts.append({"kind": "title", "text": title})
    if subtitle:
        texts.append({"kind": "subtitle", "text": subtitle})
    if inp.author:
        texts.append({"kind": "author", "text": inp.author})
    if inp.data_source:
        texts.append({"kind": "dataSource", "text": inp.data_source})
    if inp.attribution_text:
        texts.append({"kind": "attribution", "text": inp.attribution_text})

    scale_bar: Optional[Dict[str, Any]] = None
    mpp = inp.meters_per_pixel
    if mpp is not None and mpp > 0:
        nice = compute_nice_scale(mpp, inp.scale_bar_target_px)
        scale_bar = {
            "metersPerPixel": mpp,
            "nice": nice,
            "label": format_scale_label(nice["meters"]),
        }

    degradations: List[Dict[str, str]] = []
    if inp.data_overflow:
        degradations.append(
            {
                "code": "extent_overflow_data",
                "detail": "数据范围超出出图范围，超界部分以背景呈现（未静默裁切）",
            }
        )

    return {
        "version": PUBLICATION_LAYOUT_VERSION,
        "page": page,
        "mapFrame": {"x": 0, "y": 0, "width": page["widthPx"], "height": page["heightPx"]},
        "texts": texts,
        "scaleBar": scale_bar,
        "extent": {
            "mask": list(inp.mask_extent) if inp.mask_extent else None,
            "export": list(inp.export_extent) if inp.export_extent else None,
            "dataOverflow": inp.data_overflow,
        },
        "degradations": degradations,
    }
