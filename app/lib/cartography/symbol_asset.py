"""G40 —— 规范化制图符号资产文档（``symbol_asset.v1``）单一权威。

可复用点符号与面纹理资产的**内容契约**（ADR-0292）：

- **规范化形**：资产内容不是原始 SVG 字符串，而是一份有界、确定性、
  可跨端（Python 出版 / TS live / 图例 swatch）重复消费的图元文档。
  SVG 导入只在**入口**解析一次（`svg_import.py`），此后预览、live 注册、
  SVG/PNG/PDF 出版、图例全部消费同一规范形 —— 禁止两套清洗口径。
- **内容身份**：``content_digest`` = 规范形 canonical 序列化的 sha256。
  资产名只是显示名；身份 = asset_id + revision + content_digest。
- **内置资产**：代码定义（``cartoasset.builtin.*``，revision=1），与 DB
  资产走同一条规范化/校验/渲染路径，不落库。
- **纪律**：纯函数、无 IO、无随机、无时钟；同输入两次求值
  ``model_dump()`` 完全相等。二进制/任意 URL 绝不进 MapSpec —— MapSpec
  只携带绑定块（asset_id/revision/digest/params，见 mapspec_schema.py 的
  ``MapSpecLayerSymbolAsset``）。

诚实边界：
- 点符号旋转是 viewport 对齐的视觉旋转（icon-rotate 语义），不做地理
  朝向推断；map/pitch 对齐组合显式拒绝（UNSUPPORTED_ALIGNMENT）。
- 面纹理是二维图面表达：屏幕/纸面间距合同（spacing 恒为显示像素/物理
  单位，不暗示真实地面间距）。
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ── 冻结词表与资源上限（运行时同样强制，不只预检） ─────────────────────────

SYMBOL_ASSET_SCHEMA_VERSION = 1
SYMBOL_ASSET_SCHEMA = "symbol_asset.v1"

ASSET_KINDS: Tuple[str, ...] = ("point_symbol", "area_texture")
PATTERN_TYPES: Tuple[str, ...] = ("hatch", "cross_hatch", "dots", "svg_tile")

#: 资产 id 命名空间（服务层强制；builtin = 代码定义不落库）。
BUILTIN_ASSET_PREFIX = "cartoasset.builtin."
DB_ASSET_PREFIX = "cartoasset."

# 资源硬帽（S25/领域完成门：运行时超限同样停止）。
MAX_VIEWBOX = 256.0          # viewBox 每边用户单位上限
MIN_VIEWBOX = 4.0            # 每边下限（零/负尺寸拒绝）
MAX_PRIMITIVES = 200         # 每资产图元数
MAX_POINTS_PER_PRIM = 512    # 单图元坐标点数
MAX_COORD_ABS = 4096.0       # 坐标绝对值上限（含变换展平后）
MAX_STROKE_WIDTH = 16.0
MAX_ASSET_JSON_BYTES = 512 * 1024   # 规范形序列化字节上限

# 实例参数上下限（typed；S10 的 0/负/极大在此拒绝）。
POINT_SIZE_PX_MIN, POINT_SIZE_PX_MAX = 1.0, 128.0
TEXTURE_SPACING_MIN, TEXTURE_SPACING_MAX = 4.0, 64.0
TEXTURE_STROKE_MIN, TEXTURE_STROKE_MAX = 0.25, 8.0
TEXTURE_SCALE_MIN, TEXTURE_SCALE_MAX = 0.25, 4.0
ROTATION_MIN, ROTATION_MAX = -360.0, 360.0

ANCHORS: Tuple[str, ...] = (
    "center", "left", "right", "top", "bottom",
    "top-left", "top-right", "bottom-left", "bottom-right",
)

#: 已实现的对齐方式（诚实边界：map 对齐/pitch 组合未验证 → 拒绝）。
SUPPORTED_ALIGNMENTS: Tuple[str, ...] = ("viewport",)

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")
_PAINT_KEYS = ("fill", "stroke", "stroke_width", "opacity")


class SymbolAssetError(ValueError):
    """规范形/参数非法（typed；message 前缀 = 稳定 reason code）。"""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


# ── 图元（有界、绝对坐标；变换在导入期展平） ────────────────────────────────

_PRIMITIVE_OPS: Tuple[str, ...] = (
    "path", "circle", "rect", "ellipse", "line", "polyline", "polygon",
)


def _check_coord(value: float, *, where: str) -> float:
    v = float(value)
    if not math.isfinite(v) or abs(v) > MAX_COORD_ABS:
        raise SymbolAssetError(
            "COORD_OUT_OF_RANGE",
            f"{where}: {v!r} 非有限或超 |{MAX_COORD_ABS:g}|")
    return v


class SymbolPrimitive(BaseModel):
    """单个绘制图元。``path`` 用绝对命令元组序列（["M",x,y]/["L",x,y]/
    ["C",x1,y1,x2,y2,x,y]/["Q",cx,cy,x,y]/["Z"]/["A",rx,ry,rot,lf,sf,x,y]），
    导入期已归一 —— 无相对命令、无隐式重复。"""

    model_config = ConfigDict(extra="forbid")

    op: str
    fill: str = "none"
    stroke: str = "none"
    stroke_width: float = 0.0
    opacity: float = 1.0
    #: path 命令序列（op="path"；首元素 = 命令字符串，其余 = 数值）
    d: Optional[List[List[Any]]] = None
    cx: Optional[float] = None
    cy: Optional[float] = None
    r: Optional[float] = None
    rx: Optional[float] = None
    ry: Optional[float] = None
    x: Optional[float] = None
    y: Optional[float] = None
    w: Optional[float] = None
    h: Optional[float] = None
    points: Optional[List[Tuple[float, float]]] = None

    @field_validator("op")
    @classmethod
    def _known_op(cls, v: str) -> str:
        if v not in _PRIMITIVE_OPS:
            raise SymbolAssetError(
                "UNKNOWN_PRIMITIVE_OP", f"{v!r} 不在受控图元集 {_PRIMITIVE_OPS}")
        return v

    @field_validator("fill", "stroke")
    @classmethod
    def _color_or_none(cls, v: str) -> str:
        if v in ("none", "transparent"):
            return v
        if not _HEX_COLOR_RE.match(str(v or "")):
            raise SymbolAssetError(
                "INVALID_COLOR", f"{v!r} 不是受控 hex 色（不支持命名色/var/url）")
        return str(v).lower()

    @field_validator("stroke_width")
    @classmethod
    def _stroke_bound(cls, v: float) -> float:
        if not (0.0 <= float(v) <= MAX_STROKE_WIDTH) or not math.isfinite(float(v)):
            raise SymbolAssetError(
                "STROKE_OUT_OF_RANGE", f"stroke_width={v!r} ∉ [0,{MAX_STROKE_WIDTH:g}]")
        return float(v)

    @field_validator("opacity")
    @classmethod
    def _opacity_bound(cls, v: float) -> float:
        if not (0.0 <= float(v) <= 1.0):
            raise SymbolAssetError("OPACITY_OUT_OF_RANGE", f"opacity={v!r} ∉ [0,1]")
        return float(v)


def _bbox_of_primitives(prims: Sequence[SymbolPrimitive]) -> Optional[Tuple[float, float, float, float]]:
    """规范形图元的保守外接框（None = 空图元集）。"""
    xs: List[float] = []
    ys: List[float] = []
    for p in prims:
        pts: List[Tuple[float, float]] = list(p.points or [])
        if p.d:
            for cmd in p.d:
                nums = [c for c in cmd[1:] if isinstance(c, (int, float))]
                for i in range(0, len(nums) - 1, 2):
                    pts.append((float(nums[i]), float(nums[i + 1])))
        if p.op == "circle" and p.cx is not None and p.cy is not None and p.r is not None:
            pts += [(p.cx - p.r, p.cy - p.r), (p.cx + p.r, p.cy + p.r)]
        if p.op == "ellipse" and p.cx is not None and p.cy is not None:
            rx = p.rx or 0.0
            ry = p.ry or 0.0
            pts += [(p.cx - rx, p.cy - ry), (p.cx + rx, p.cy + ry)]
        if p.op == "rect" and p.x is not None and p.y is not None:
            pts += [(p.x, p.y), (p.x + (p.w or 0.0), p.y + (p.h or 0.0))]
        if p.op == "line":
            pts += [(p.x or 0.0, p.y or 0.0),
                    ((p.x or 0.0) + (p.w or 0.0), (p.y or 0.0) + (p.h or 0.0))]
        for x, y in pts:
            xs.append(float(x))
            ys.append(float(y))
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


# ── 纹理 pattern 描述（生成式 or 导入 tile） ───────────────────────────────

class TexturePattern(BaseModel):
    """面纹理设计。``hatch/cross_hatch/dots`` = 程序化生成（参数见
    default_params）；``svg_tile`` = 受控导入 tile（primitives + viewBox，
    按 viewBox 周期平铺）。"""

    model_config = ConfigDict(extra="forbid")

    type: str
    #: type=svg_tile 时的 tile 图元与尺寸
    tile_primitives: Optional[List[SymbolPrimitive]] = None
    tile_viewbox: Optional[Tuple[float, float]] = None

    @field_validator("type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        if v not in PATTERN_TYPES:
            raise SymbolAssetError(
                "UNKNOWN_PATTERN_TYPE", f"{v!r} ∉ {PATTERN_TYPES}")
        return v


# ── 规范形文档 ─────────────────────────────────────────────────────────────

class SymbolAssetDocument(BaseModel):
    """规范化资产文档（``symbol_asset.v1``）。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SYMBOL_ASSET_SCHEMA_VERSION
    kind: str
    #: 显示名（仅 UI；身份在服务层 asset_id+revision）
    name: str = Field(default="", max_length=120)
    #: 点符号：viewBox = {width,height}；面纹理：无意义时省略（None）
    viewbox: Optional[Tuple[float, float]] = None
    primitives: List[SymbolPrimitive] = Field(default_factory=list)
    pattern: Optional[TexturePattern] = None
    default_params: Dict[str, Any] = Field(default_factory=dict)
    source: Dict[str, str] = Field(default_factory=dict)
    content_digest: str = ""

    @field_validator("schema_version")
    @classmethod
    def _version(cls, v: int) -> int:
        if v != SYMBOL_ASSET_SCHEMA_VERSION:
            raise SymbolAssetError(
                "UNKNOWN_SYMBOL_ASSET_VERSION", f"schema_version={v!r}")
        return v

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in ASSET_KINDS:
            raise SymbolAssetError("UNKNOWN_ASSET_KIND", f"{v!r} ∉ {ASSET_KINDS}")
        return v

    @field_validator("viewbox")
    @classmethod
    def _viewbox(cls, v: Optional[Tuple[float, float]]) -> Optional[Tuple[float, float]]:
        if v is None:
            return v
        w, h = float(v[0]), float(v[1])
        for name, side in (("width", w), ("height", h)):
            if not math.isfinite(side) or not (MIN_VIEWBOX <= side <= MAX_VIEWBOX):
                raise SymbolAssetError(
                    "VIEWBOX_OUT_OF_RANGE",
                    f"{name}={side!r} ∉ [{MIN_VIEWBOX:g},{MAX_VIEWBOX:g}]")
        return (w, h)

    @field_validator("primitives")
    @classmethod
    def _prims_bound(cls, v: List[SymbolPrimitive]) -> List[SymbolPrimitive]:
        if len(v) > MAX_PRIMITIVES:
            raise SymbolAssetError(
                "TOO_MANY_PRIMITIVES", f"{len(v)} > {MAX_PRIMITIVES}")
        return v

    def model_post_init(self, __ctx: Any) -> None:
        # kind ↔ 内容形状一致性（fail-closed）。
        if self.kind == "point_symbol":
            if self.viewbox is None:
                raise SymbolAssetError("MISSING_VIEWBOX", "point_symbol 需要 viewbox")
            if not self.primitives:
                raise SymbolAssetError("EMPTY_PRIMITIVES", "point_symbol 需要图元")
            if self.pattern is not None:
                raise SymbolAssetError("KIND_SHAPE_MISMATCH", "point_symbol 不携带 pattern")
        else:
            if self.pattern is None:
                raise SymbolAssetError("MISSING_PATTERN", "area_texture 需要 pattern")
            if self.pattern.type == "svg_tile":
                if not self.pattern.tile_primitives or self.pattern.tile_viewbox is None:
                    raise SymbolAssetError(
                        "MISSING_TILE", "svg_tile 纹理需要 tile_primitives + tile_viewbox")
            if self.primitives:
                raise SymbolAssetError("KIND_SHAPE_MISMATCH", "area_texture 不携带顶层 primitives")
        if not self.content_digest:
            self.content_digest = compute_digest(self)

    # ── 消费面辅助 ──

    @property
    def intrinsic_size(self) -> Tuple[float, float]:
        """点符号内在尺寸（viewBox；icon-size 的换算基准）。"""
        if self.viewbox is None:
            raise SymbolAssetError("MISSING_VIEWBOX", "area_texture 无内在尺寸")
        return self.viewbox

    def anchor_offset(self, anchor: str) -> Tuple[float, float]:
        """锚点在 viewBox 坐标系中的偏移（anchor 点到中心的位移，单位 =
        viewBox 用户单位）。旋转围绕锚点：先平移 -anchor，再旋转/缩放。"""
        if anchor not in ANCHORS:
            raise SymbolAssetError("UNKNOWN_ANCHOR", f"{anchor!r} ∉ {ANCHORS}")
        w, h = self.intrinsic_size
        hx: Dict[str, float] = {"left": -w / 2.0, "center": 0.0, "right": w / 2.0}
        vy: Dict[str, float] = {"top": -h / 2.0, "center": 0.0, "bottom": h / 2.0}
        ax = hx.get(anchor.split("-")[-1] if "-" in anchor else anchor, 0.0)
        ay = vy.get(anchor.split("-")[0] if "-" in anchor else anchor, 0.0)
        return ax, ay

    def _drawing_primitives(self) -> List[SymbolPrimitive]:
        """顶层图元（点符号）或 tile 图元（导入纹理）。"""
        if self.pattern is not None and self.pattern.tile_primitives:
            return self.pattern.tile_primitives
        return self.primitives

    def conservative_bounds(self) -> Optional[Tuple[float, float, float, float]]:
        """规范形内容的保守外接框（含 stroke 外扩；None = 程序化纹理）。"""
        prims = self._drawing_primitives()
        box = _bbox_of_primitives(prims)
        if box is None:
            return None
        extra = max((p.stroke_width for p in prims), default=0.0) / 2.0
        return (box[0] - extra, box[1] - extra, box[2] + extra, box[3] + extra)


def compute_digest(doc: Union[SymbolAssetDocument, Dict[str, Any]]) -> str:
    """规范形 canonical 序列化摘要（``sha256:<hex>``）。digest 字段自身不参与。"""
    from app.lib.data.fingerprints import canonical_dumps

    if isinstance(doc, SymbolAssetDocument):
        payload = doc.model_dump(exclude={"content_digest"})
    else:
        payload = {k: v for k, v in dict(doc).items() if k != "content_digest"}
    blob = canonical_dumps(payload)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def validate_digest(doc: SymbolAssetDocument) -> bool:
    """自校验：digest 与内容一致（读回完整性检查，S23 的 typed 前置）。"""
    return doc.content_digest == compute_digest(doc)


# ── 实例参数（typed 上下限；服务层/工具/前端共用语义） ─────────────────────

def _hex_color(value: Any, *, where: str) -> str:
    s = str(value or "").strip()
    if not _HEX_COLOR_RE.match(s):
        raise SymbolAssetError("INVALID_COLOR", f"{where}: {value!r} 非 hex 色")
    return s.lower()


def _bounded_float(
    value: Any, *, lo: float, hi: float, where: str, default: Optional[float] = None,
) -> float:
    if value is None:
        if default is None:
            raise SymbolAssetError("MISSING_PARAM", f"{where}: 缺参数")
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SymbolAssetError(
            "PARAM_NOT_NUMERIC", f"{where}: {value!r} 非数值（拒绝字符串数值）")
    v = float(value)
    if not math.isfinite(v) or not (lo <= v <= hi):
        raise SymbolAssetError(
            "PARAM_OUT_OF_RANGE", f"{where}: {v!r} ∉ [{lo:g},{hi:g}]")
    return v


def normalize_point_params(
    raw: Optional[Dict[str, Any]], *, doc: Optional[SymbolAssetDocument] = None,
) -> Dict[str, Any]:
    """点符号实例参数归一（typed；未知键拒绝）。"""
    raw = dict(raw or {})
    unknown = set(raw) - {
        "size_px", "anchor", "rotation_deg", "color", "opacity",
        "rotation_alignment"}
    if unknown:
        raise SymbolAssetError(
            "UNKNOWN_PARAM", f"未知点符号参数: {sorted(unknown)}")
    defaults: Dict[str, Any] = (doc.default_params if doc else {}) or {}
    out: Dict[str, Any] = {
        "size_px": _bounded_float(
            raw.get("size_px", defaults.get("size_px", 16.0)),
            lo=POINT_SIZE_PX_MIN, hi=POINT_SIZE_PX_MAX, where="size_px"),
        "anchor": str(raw.get("anchor") or defaults.get("anchor") or "center"),
        "rotation_deg": _bounded_float(
            raw.get("rotation_deg", defaults.get("rotation_deg", 0.0)),
            lo=ROTATION_MIN, hi=ROTATION_MAX, where="rotation_deg"),
        "opacity": _bounded_float(
            raw.get("opacity", defaults.get("opacity", 1.0)),
            lo=0.0, hi=1.0, where="opacity"),
    }
    if out["anchor"] not in ANCHORS:
        raise SymbolAssetError("UNKNOWN_ANCHOR", f"anchor={out['anchor']!r}")
    color = raw.get("color", defaults.get("color"))
    if color is not None:
        out["color"] = _hex_color(color, where="color")
    alignment = raw.get("rotation_alignment", "viewport")
    if alignment not in SUPPORTED_ALIGNMENTS:
        raise SymbolAssetError(
            "UNSUPPORTED_ALIGNMENT",
            f"rotation_alignment={alignment!r}；已实现: {SUPPORTED_ALIGNMENTS}")
    return out


def normalize_texture_params(
    raw: Optional[Dict[str, Any]], *, doc: Optional[SymbolAssetDocument] = None,
) -> Dict[str, Any]:
    """面纹理实例参数归一（typed；未知键拒绝）。"""
    raw = dict(raw or {})
    unknown = set(raw) - {
        "spacing_px", "angle_deg", "stroke_width_px", "fg_color",
        "bg_color", "pattern_scale", "angle2_deg"}
    if unknown:
        raise SymbolAssetError(
            "UNKNOWN_PARAM", f"未知面纹理参数: {sorted(unknown)}")
    defaults: Dict[str, Any] = (doc.default_params if doc else {}) or {}
    out: Dict[str, Any] = {
        "spacing_px": _bounded_float(
            raw.get("spacing_px", defaults.get("spacing_px", 12.0)),
            lo=TEXTURE_SPACING_MIN, hi=TEXTURE_SPACING_MAX, where="spacing_px"),
        "angle_deg": _bounded_float(
            raw.get("angle_deg", defaults.get("angle_deg", 45.0)),
            lo=-90.0, hi=90.0, where="angle_deg"),
        "stroke_width_px": _bounded_float(
            raw.get("stroke_width_px", defaults.get("stroke_width_px", 1.5)),
            lo=TEXTURE_STROKE_MIN, hi=TEXTURE_STROKE_MAX, where="stroke_width_px"),
        "pattern_scale": _bounded_float(
            raw.get("pattern_scale", defaults.get("pattern_scale", 1.0)),
            lo=TEXTURE_SCALE_MIN, hi=TEXTURE_SCALE_MAX, where="pattern_scale"),
    }
    fg = raw.get("fg_color", defaults.get("fg_color", "#334155"))
    out["fg_color"] = _hex_color(fg, where="fg_color")
    bg = raw.get("bg_color", defaults.get("bg_color", "transparent"))
    if bg in ("transparent", "none"):
        out["bg_color"] = "transparent"
    else:
        out["bg_color"] = _hex_color(bg, where="bg_color")
    if out["pattern_scale"] <= 0:  # pragma: no cover - 已被上界挡住
        raise SymbolAssetError("PARAM_OUT_OF_RANGE", "pattern_scale ≤ 0")
    return out


def normalize_params(
    kind: str, raw: Optional[Dict[str, Any]], *,
    doc: Optional[SymbolAssetDocument] = None,
) -> Dict[str, Any]:
    if kind == "point_symbol":
        return normalize_point_params(raw, doc=doc)
    if kind == "area_texture":
        return normalize_texture_params(raw, doc=doc)
    raise SymbolAssetError("UNKNOWN_ASSET_KIND", kind)


def params_digest(kind: str, params: Dict[str, Any]) -> str:
    """实例参数指纹（注册 image id / defs 去重键；参数需已归一）。"""
    from app.lib.data.fingerprints import canonical_dumps

    return hashlib.sha256(
        canonical_dumps({"kind": kind, "params": params}).encode("utf-8")
    ).hexdigest()[:16]


# ── 内置资产（代码定义；revision=1；同一条校验/渲染路径） ───────────────────

def _prim_path(d: List[List[Union[str, float]]], *, fill: str = "none",
               stroke: str = "none", stroke_width: float = 0.0) -> SymbolPrimitive:
    return SymbolPrimitive(
        op="path", d=d, fill=fill, stroke=stroke, stroke_width=stroke_width)


def _star_points(cx: float, cy: float, r_out: float, r_in: float, n: int = 5) -> List[List[Union[str, float]]]:
    d: List[List[Union[str, float]]] = []
    for i in range(n * 2):
        ang = -math.pi / 2.0 + i * math.pi / n
        r = r_out if i % 2 == 0 else r_in
        x, y = cx + r * math.cos(ang), cy + r * math.sin(ang)
        d.append(["L", round(x, 3), round(y, 3)])
    d[0][0] = "M"
    d.append(["Z"])
    return d


def _builtin_point(name: str, d: List[List[Union[str, float]]], *,
                   fill: str = "#2563eb") -> SymbolAssetDocument:
    return SymbolAssetDocument(
        kind="point_symbol", name=name, viewbox=(24.0, 24.0),
        primitives=[_prim_path(d, fill=fill)],
        default_params={"size_px": 16.0, "anchor": "center", "rotation_deg": 0.0},
        source={"origin": "builtin", "license": "repo",
                "note": "WebGIS 内置图元（G40）"},
    )


_TRIANGLE_D = [["M", 12.0, 3.0], ["L", 22.0, 20.0], ["L", 2.0, 20.0], ["Z"]]
_SQUARE_D = [["M", 3.5, 3.5], ["L", 20.5, 3.5], ["L", 20.5, 20.5], ["L", 3.5, 20.5], ["Z"]]
_DIAMOND_D = [["M", 12.0, 2.0], ["L", 22.0, 12.0], ["L", 12.0, 22.0], ["L", 2.0, 12.0], ["Z"]]
_CROSS_D = [
    ["M", 9.5, 2.5], ["L", 14.5, 2.5], ["L", 14.5, 9.5], ["L", 21.5, 9.5],
    ["L", 21.5, 14.5], ["L", 14.5, 14.5], ["L", 14.5, 21.5], ["L", 9.5, 21.5],
    ["L", 9.5, 14.5], ["L", 2.5, 14.5], ["L", 2.5, 9.5], ["L", 9.5, 9.5], ["Z"],
]
_PIN_D = [
    ["M", 12.0, 22.0], ["C", 12.0, 22.0, 4.0, 13.0, 4.0, 8.8],
    ["C", 4.0, 4.8, 7.6, 1.8, 12.0, 1.8], ["C", 16.4, 1.8, 20.0, 4.8, 20.0, 8.8],
    ["C", 20.0, 13.0, 12.0, 22.0, 12.0, 22.0], ["Z"],
]


def _builtin_assets() -> List[Tuple[str, SymbolAssetDocument]]:
    points: List[Tuple[str, SymbolAssetDocument]] = [
        (BUILTIN_ASSET_PREFIX + "triangle",
         _builtin_point("三角形", _TRIANGLE_D, fill="#2563eb")),
        (BUILTIN_ASSET_PREFIX + "square",
         _builtin_point("方形", _SQUARE_D, fill="#16a34a")),
        (BUILTIN_ASSET_PREFIX + "diamond",
         _builtin_point("菱形", _DIAMOND_D, fill="#d97706")),
        (BUILTIN_ASSET_PREFIX + "star5",
         _builtin_point("五角星", _star_points(12.0, 12.0, 10.0, 4.2),
                        fill="#dc2626")),
        (BUILTIN_ASSET_PREFIX + "cross",
         _builtin_point("十字", _CROSS_D, fill="#7c3aed")),
        (BUILTIN_ASSET_PREFIX + "pin",
         _builtin_point("定位钉", _PIN_D, fill="#e11d48")),
    ]
    textures: List[Tuple[str, SymbolAssetDocument]] = [
        (BUILTIN_ASSET_PREFIX + "hatch",
         SymbolAssetDocument(
             kind="area_texture", name="斜线晕线",
             pattern=TexturePattern(type="hatch"),
             default_params={"spacing_px": 12.0, "angle_deg": 45.0,
                             "stroke_width_px": 1.5, "fg_color": "#334155",
                             "bg_color": "transparent"},
             source={"origin": "builtin", "license": "repo",
                     "note": "WebGIS 内置单方向晕线（G40）"})),
        (BUILTIN_ASSET_PREFIX + "cross_hatch",
         SymbolAssetDocument(
             kind="area_texture", name="交叉晕线",
             pattern=TexturePattern(type="cross_hatch"),
             default_params={"spacing_px": 12.0, "angle_deg": 45.0,
                             "angle2_deg": -45.0, "stroke_width_px": 1.2,
                             "fg_color": "#334155", "bg_color": "transparent"},
             source={"origin": "builtin", "license": "repo",
                     "note": "WebGIS 内置交叉晕线（G40）"})),
        (BUILTIN_ASSET_PREFIX + "dots",
         SymbolAssetDocument(
             kind="area_texture", name="点阵",
             pattern=TexturePattern(type="dots"),
             default_params={"spacing_px": 12.0, "stroke_width_px": 2.4,
                             "fg_color": "#334155", "bg_color": "transparent"},
             source={"origin": "builtin", "license": "repo",
                     "note": "WebGIS 内置点阵纹理（G40）"})),
    ]
    return points + textures


BUILTIN_SYMBOL_ASSETS: Dict[str, SymbolAssetDocument] = {
    asset_id: doc for asset_id, doc in _builtin_assets()
}


def get_builtin_asset(asset_id: str) -> Optional[SymbolAssetDocument]:
    """内置资产解析（无 IO；未命中 = None，由服务层按 404 语义处理）。"""
    doc = BUILTIN_SYMBOL_ASSETS.get(str(asset_id or ""))
    return doc.model_copy(deep=True) if doc is not None else None


def builtin_asset_index() -> List[Dict[str, Any]]:
    """内置资产目录投影（列表 API 的 builtin 页；有界）。"""
    out: List[Dict[str, Any]] = []
    for asset_id, doc in BUILTIN_SYMBOL_ASSETS.items():
        out.append({
            "asset_id": asset_id,
            "kind": doc.kind,
            "name": doc.name,
            "builtin": True,
            "revision": 1,
            "content_digest": doc.content_digest,
            "default_params": dict(doc.default_params),
        })
    return out


__all__ = [
    "SYMBOL_ASSET_SCHEMA_VERSION", "SYMBOL_ASSET_SCHEMA",
    "ASSET_KINDS", "PATTERN_TYPES", "ANCHORS", "SUPPORTED_ALIGNMENTS",
    "BUILTIN_ASSET_PREFIX", "DB_ASSET_PREFIX", "BUILTIN_SYMBOL_ASSETS",
    "MAX_PRIMITIVES", "MAX_VIEWBOX", "MAX_ASSET_JSON_BYTES",
    "SymbolAssetError", "SymbolAssetDocument", "SymbolPrimitive", "TexturePattern",
    "compute_digest", "validate_digest", "normalize_point_params",
    "normalize_texture_params", "normalize_params", "params_digest",
    "get_builtin_asset", "builtin_asset_index",
]
