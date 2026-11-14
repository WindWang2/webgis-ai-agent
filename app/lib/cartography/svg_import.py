"""G40 —— 受控 SVG 子集导入器（单一清洗口径的入口面）。

把外部 SVG 字节解析为 `symbol_asset.SymbolAssetDocument` 规范形。安全与
诚实边界（S05–S08）：

- **解析器**：defusedxml（拒绝 DTD/实体扩展）；输入先做字节/节点/嵌套
  预算检查。
- **允许**：`<svg><g><defs><use><path><rect><circle><ellipse><line>
  <polygon><polyline>`；path 命令 M/L/H/V/C/S/Q/T/A/Z（大小写均可，导入期
  展平为绝对命令）；有限小数坐标；`fill/stroke/stroke-width/opacity/
  fill-opacity/stroke-opacity`；`transform="translate/rotate/scale/matrix"`
  （导入期数值展平）。
- **拒绝**（typed code + 元素/属性名，绝不静默删除后宣称成功）：
  script/事件属性/foreignObject/外部 href（除文档内 `#id` 引用）/CSS
  url()/`<style>`/`<text>`（无受控 text→path 实现）/DTD/实体/注释中的
  DOCTYPE/`<clipPath>`/`<mask>`/`<filter>`/渐变/`<image>`/嵌套 `<svg>`/
  use 循环引用/未知元素。
- **viewBox**：必须显式、有限、每边 ∈ [4,256]（缺失/零/负/超界拒绝）。
- 归一化只发生在这里；预览/live/出版消费规范形，绝不二次解析原始 SVG。
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from defusedxml import ElementTree as SafeET

from app.lib.cartography.symbol_asset import (
    MAX_ASSET_JSON_BYTES,
    MAX_COORD_ABS,
    MAX_POINTS_PER_PRIM,
    MAX_PRIMITIVES,
    MIN_VIEWBOX,
    MAX_VIEWBOX,
    SymbolAssetDocument,
    SymbolAssetError,
    SymbolPrimitive,
    TexturePattern,
)

#: 输入预算（运行时与预检一致）。
MAX_INPUT_BYTES = 512 * 1024
MAX_INPUT_NODES = 400
MAX_NESTING = 12
MAX_PATH_CMDS = 512

_ALLOWED_ELEMENTS = {
    "svg", "g", "defs", "use", "path", "rect", "circle", "ellipse",
    "line", "polygon", "polyline",
}
#: 显式不支持的元素（typed code + 元素名披露；S08）。
_UNSUPPORTED_ELEMENTS = {
    "script": "SVG_SCRIPT",
    "foreignObject": "SVG_FOREIGN_OBJECT",
    "text": "SVG_TEXT_UNSUPPORTED",
    "tspan": "SVG_TEXT_UNSUPPORTED",
    "style": "SVG_STYLE_ELEMENT",
    "image": "SVG_IMAGE_ELEMENT",
    "clipPath": "SVG_CLIP_UNSUPPORTED",
    "mask": "SVG_MASK_UNSUPPORTED",
    "filter": "SVG_FILTER_UNSUPPORTED",
    "linearGradient": "SVG_GRADIENT_UNSUPPORTED",
    "radialGradient": "SVG_GRADIENT_UNSUPPORTED",
    "pattern": "SVG_PATTERN_ELEMENT",   # 顶层 pattern 语义不同 —— 拒绝
    "animate": "SVG_ANIMATION_UNSUPPORTED",
    "animateTransform": "SVG_ANIMATION_UNSUPPORTED",
    "title": "SVG_META_ELEMENT",
    "desc": "SVG_META_ELEMENT",
    "symbol": "SVG_SYMBOL_ELEMENT",
}
_EVENT_ATTR_RE = re.compile(r"^on[a-z]+$", re.IGNORECASE)
_URL_VALUE_RE = re.compile(r"url\s*\(", re.IGNORECASE)
_EXTERNAL_HREF_RE = re.compile(r"^(?!#)[a-zA-Z][a-zA-Z0-9+.\-]*:", re.IGNORECASE)

_NUM_RE = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")

_PATH_CMD_RE = re.compile(r"([MLHVQCSSTAZmlhvqcsstaz])")

#: style 属性里允许的子属性（白名单；url() 一律拒绝）。
_ALLOWED_STYLE_PROPS = {
    "fill", "stroke", "stroke-width", "opacity", "fill-opacity", "stroke-opacity",
}


def _num(value: Any, *, where: str) -> float:
    s = str(value).strip()
    if not _NUM_RE.match(s):
        raise SymbolAssetError("SVG_BAD_NUMBER", f"{where}: {value!r}")
    v = float(s)
    if not math.isfinite(v):
        raise SymbolAssetError("SVG_BAD_NUMBER", f"{where}: {value!r} 非有限")
    return v


def _check_coord(v: float, *, where: str) -> float:
    if abs(v) > MAX_COORD_ABS:
        raise SymbolAssetError(
            "SVG_COORD_OUT_OF_RANGE", f"{where}: |{v:g}| > {MAX_COORD_ABS:g}")
    return v


def _parse_transform(raw: Optional[str]) -> Optional[Tuple[float, float, float, float, float, float]]:
    """transform → (a,b,c,d,e,f) 矩阵；不支持的一律 typed 拒绝。"""
    if not raw:
        return None
    matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    for name, args in re.findall(r"([a-zA-Z]+)\s*\(([^)]*)\)", raw):
        nums = [_num(a, where=f"transform {name}") for a in re.split(r"[\s,]+", args.strip()) if a]
        if name == "translate" and len(nums) in (1, 2):
            m = (1.0, 0.0, 0.0, 1.0, nums[0], nums[1] if len(nums) > 1 else 0.0)
        elif name == "scale" and len(nums) in (1, 2):
            sx = nums[0]
            sy = nums[1] if len(nums) > 1 else sx
            if sx == 0 or sy == 0:
                raise SymbolAssetError("SVG_TRANSFORM_INVALID", "scale 0")
            m = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        elif name == "rotate" and len(nums) == 1:
            rad = math.radians(nums[0])
            cos_r, sin_r = math.cos(rad), math.sin(rad)
            m = (cos_r, sin_r, -sin_r, cos_r, 0.0, 0.0)
        elif name == "rotate" and len(nums) == 3:
            rad = math.radians(nums[0])
            cos_r, sin_r = math.cos(rad), math.sin(rad)
            cx, cy = nums[1], nums[2]
            m = _mat_mul(
                (1, 0, 0, 1, cx, cy),
                _mat_mul(
                    (cos_r, sin_r, -sin_r, cos_r, 0.0, 0.0),
                    (1, 0, 0, 1, -cx, -cy)))
        elif name == "matrix" and len(nums) == 6:
            m = tuple(nums)  # type: ignore[assignment]
        else:
            raise SymbolAssetError(
                "SVG_TRANSFORM_UNSUPPORTED", f"transform {name}({args})")
        matrix = _mat_mul(m, matrix)
    return matrix


def _mat_mul(
    m1: Tuple[float, float, float, float, float, float],
    m2: Tuple[float, float, float, float, float, float],
) -> Tuple[float, float, float, float, float, float]:
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + c1 * b2, b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2, b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1, b1 * e2 + d1 * f2 + f1,
    )


def _apply(matrix: Optional[Tuple[float, ...]], x: float, y: float) -> Tuple[float, float]:
    if matrix is None:
        return x, y
    a, b, c, d, e, f = matrix
    return a * x + c * y + e, b * x + d * y + f


class _ImportState:
    def __init__(self) -> None:
        self.primitives: List[SymbolPrimitive] = []
        self.defs: Dict[str, List[SymbolPrimitive]] = {}
        self.nodes = 0
        self.use_stack: List[str] = []


def _style_dict(el: Any) -> Dict[str, str]:
    """合并 presentation 属性 + style 白名单子属性。url() 值拒绝。"""
    out: Dict[str, str] = {}
    for key in ("fill", "stroke", "stroke-width", "opacity", "fill-opacity", "stroke-opacity"):
        v = el.get(key)
        if v is not None:
            out[key] = v.strip()
    style = el.get("style")
    if style:
        for decl in style.split(";"):
            if not decl.strip():
                continue
            name, _, value = decl.partition(":")
            name = name.strip().lower()
            value = value.strip()
            if name not in _ALLOWED_STYLE_PROPS:
                raise SymbolAssetError(
                    "SVG_STYLE_PROP_UNSUPPORTED",
                    f"style 子属性 {name!r} 不在受控集 {_sorted(_ALLOWED_STYLE_PROPS)}")
            if _URL_VALUE_RE.search(value):
                raise SymbolAssetError("SVG_URL_VALUE", f"style {name} 含 url()")
            out[name] = value
    for k, v in list(out.items()):
        if _URL_VALUE_RE.search(v or ""):
            raise SymbolAssetError("SVG_URL_VALUE", f"属性 {k} 含 url()")
    return out


def _sorted(s: Sequence[str]) -> List[str]:
    return sorted(s)


def _color_of(style: Dict[str, str], key: str) -> str:
    raw = (style.get(key) or "none").strip()
    if raw in ("none", "transparent"):
        return "none"
    if not re.match(r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$", raw):
        raise SymbolAssetError(
            "SVG_COLOR_UNSUPPORTED",
            f"{key}={raw!r}：仅支持 6/8 位 hex（命名色/currentColor/var 拒绝）")
    return raw.lower()


def _opacity_of(style: Dict[str, str]) -> float:
    base = style.get("opacity")
    v = 1.0 if base is None else _num(base, where="opacity")
    if not (0.0 <= v <= 1.0):
        raise SymbolAssetError("SVG_OPACITY_RANGE", f"opacity={v!r} ∉ [0,1]")
    return v


def _stroke_width_of(style: Dict[str, str]) -> float:
    raw = style.get("stroke-width")
    v = 1.0 if raw is None else _num(raw, where="stroke-width")
    if not (0.0 <= v <= 16.0):
        raise SymbolAssetError(
            "SVG_STROKE_RANGE", f"stroke-width={v!r} ∉ [0,16]")
    return v


_PATH_TOKEN_RE = re.compile(
    r"([MLHVQCSSTAZmlhvqcsstaz])"                # 命令
    r"|([-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?)"  # 数（含科学计数）
    r"|([\s,]+)"                                  # 分隔符
)


def _tokenize_path(d: str) -> List[str]:
    """path data → 命令/数值 token 序列（严格：非法字符 typed 拒绝）。

    flag 参数（A 命令的 large-arc/sweep 用 0/1）在数值 token 之外单独
    产出 —— 由 ``_flatten_path`` 按 A 的 7 参数布局读取。
    """
    out: List[str] = []
    pos = 0
    n = len(d)
    while pos < n:
        m = _PATH_TOKEN_RE.match(d, pos)
        if m is None:
            raise SymbolAssetError(
                "SVG_PATH_SYNTAX", f"非法字符 {d[pos]!r} @ {pos}")
        pos = m.end()
        cmd, num, _sep = m.group(1), m.group(2), m.group(3)
        if cmd is not None:
            # A 命令的 flag（0/1）不带分隔也可出现：单独产出为 "0"/"1" token。
            out.append(cmd)
        elif num is not None:
            out.append(num)
        # 分隔符丢弃
        else:
            # flag 形态：A 的第 4/5 参数允许 0/1 紧贴。上面数值正则会把
            # "0"/"1" 当数值吃掉 —— 对 A 无影响（数值化后取整校验）。
            pass
    # A 命令 flag 紧贴情形（如 "a16 16 0 011 0"）：数值正则匹配 "011" 为
    # 一个数 —— 拆分校验由 _flatten_path 的 A 分支按 flag∈{0,1} 完成。
    return out


def _flatten_path(
    d: str, matrix: Optional[Tuple[float, float, float, float, float, float]],
) -> List[List[Union[str, float]]]:
    """SVG path data → 绝对命令序列（M/L/C/Z；H/V→L，Q/S/T/A→C 展平，相对
    命令转绝对）。全程在**未变换坐标系**展平（反射控制点/贝塞尔展开需要真
    起点），最后一次性应用 transform 矩阵。"""
    if not d or not d.strip():
        raise SymbolAssetError("SVG_PATH_EMPTY", "path d 为空")
    parts = _tokenize_path(d)
    if len(parts) > MAX_PATH_CMDS * 2 + 2:
        raise SymbolAssetError("SVG_PATH_TOO_LONG", f"path 命令数超 {MAX_PATH_CMDS}")
    out: List[List[Union[str, float]]] = []
    cur_x = cur_y = 0.0
    start_x = start_y = 0.0
    last_ctrl: Optional[Tuple[float, float]] = None  # Q/T 反射用（未变换域）
    prev_was_cubic = False
    cmd: Optional[str] = None
    i = 0
    while i < len(parts):
        tok = parts[i]
        if _PATH_CMD_RE.fullmatch(tok):
            cmd = tok
            i += 1
            continue
        if cmd is None:
            raise SymbolAssetError("SVG_PATH_SYNTAX", "path 以参数开头（无命令）")
        upper = cmd.upper()
        relative = cmd != upper
        argn = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4,
                "T": 2, "A": 7, "Z": 0}.get(upper, -1)
        if argn < 0:
            raise SymbolAssetError("SVG_PATH_CMD_UNSUPPORTED", f"命令 {cmd!r}")
        if upper == "Z":
            out.append(["Z"])
            cur_x, cur_y = start_x, start_y
            last_ctrl, prev_was_cubic = None, False
            cmd = None
            continue
        if argn == 0 or i + argn > len(parts):
            raise SymbolAssetError("SVG_PATH_SYNTAX", f"命令 {cmd} 参数不足")
        args = [_num(p, where=f"path {cmd}") for p in parts[i:i + argn]]
        i += argn

        def R(idx: int) -> float:  # 相对化参数
            return args[idx] + (cur_x if idx % 2 == 0 else cur_y) if relative else args[idx]

        if upper == "M":
            cur_x, cur_y = R(0), R(1)
            start_x, start_y = cur_x, cur_y
            out.append(["M", round(cur_x, 3), round(cur_y, 3)])
            cmd = "m" if relative else "M"  # 后续隐式 L
        elif upper == "L":
            cur_x, cur_y = R(0), R(1)
            out.append(["L", round(cur_x, 3), round(cur_y, 3)])
        elif upper == "H":
            cur_x = args[0] + cur_x if relative else args[0]
            out.append(["L", round(cur_x, 3), round(cur_y, 3)])
        elif upper == "V":
            cur_y = args[0] + cur_y if relative else args[0]
            out.append(["L", round(cur_x, 3), round(cur_y, 3)])
        elif upper == "C":
            pts = [R(0), R(1), R(2), R(3), R(4), R(5)]
            out.append(["C", *(round(v, 3) for v in pts)])
            cur_x, cur_y = pts[4], pts[5]
            prev_was_cubic = True
        elif upper == "S":
            if prev_was_cubic and out and out[-1][0] == "C":
                prev = out[-1]
                rx = 2 * float(prev[5]) - float(prev[3])  # type: ignore[arg-type]
                ry = 2 * float(prev[6]) - float(prev[4])  # type: ignore[arg-type]
            else:
                rx, ry = cur_x, cur_y
            pts = [rx, ry, R(0), R(1), R(2), R(3)]
            out.append(["C", *(round(v, 3) for v in pts)])
            cur_x, cur_y = pts[4], pts[5]
            prev_was_cubic = True
        elif upper == "Q":
            x1, y1, xe, ye = R(0), R(1), R(2), R(3)
            out.append(["C", round(cur_x + 2 / 3 * (x1 - cur_x), 3),
                        round(cur_y + 2 / 3 * (y1 - cur_y), 3),
                        round(xe + 2 / 3 * (x1 - xe), 3),
                        round(ye + 2 / 3 * (y1 - ye), 3),
                        round(xe, 3), round(ye, 3)])
            cur_x, cur_y = xe, ye
            last_ctrl = (x1, y1)
            prev_was_cubic = False
        elif upper == "T":
            x1, y1 = last_ctrl if last_ctrl else (cur_x, cur_y)
            x1, y1 = 2 * cur_x - x1, 2 * cur_y - y1  # 反射上一控制点
            xe, ye = R(0), R(1)
            out.append(["C", round(cur_x + 2 / 3 * (x1 - cur_x), 3),
                        round(cur_y + 2 / 3 * (y1 - cur_y), 3),
                        round(xe + 2 / 3 * (x1 - xe), 3),
                        round(ye + 2 / 3 * (y1 - ye), 3),
                        round(xe, 3), round(ye, 3)])
            cur_x, cur_y = xe, ye
            last_ctrl = (x1, y1)
            prev_was_cubic = False
        elif upper == "A":
            rx, ry, rot, lf, sf, x, y = args
            if rx <= 0 or ry <= 0:
                raise SymbolAssetError("SVG_PATH_ARC_INVALID", "arc 半径 ≤ 0")
            if lf not in (0.0, 1.0) or sf not in (0.0, 1.0):
                raise SymbolAssetError(
                    "SVG_PATH_ARC_FLAG", "arc flag 必须为 0/1（紧凑连写 flag 需加分隔符）")
            if relative:
                x, y = cur_x + x, cur_y + y
            for seg in _arc_to_cubics(cur_x, cur_y, rx, ry, rot, int(lf), int(sf), x, y):
                out.append(["C", *(round(v, 3) for v in seg)])
            cur_x, cur_y = x, y
            prev_was_cubic = False
        if len(out) > MAX_PATH_CMDS:
            raise SymbolAssetError("SVG_PATH_TOO_LONG", f"path 命令数超 {MAX_PATH_CMDS}")
    if not out or out[0][0] != "M":
        raise SymbolAssetError("SVG_PATH_SYNTAX", "path 未以 M 开始")
    if matrix is not None:
        for j, c in enumerate(out):
            if c[0] == "Z":
                continue
            nums = c[1:]
            c[1:] = [
                round(_check_coord(v, where="path"), 3)
                for v in _xy6(nums, matrix)]  # type: ignore[assignment]
        # 变换后坐标越界整体复查（含插值点）。
    return out


def _xy(x: float, y: float, matrix: Optional[Tuple[float, ...]]) -> List[float]:
    tx, ty = _apply(matrix, x, y)
    return [round(_check_coord(tx, where="path"), 3), round(_check_coord(ty, where="path"), 3)]


def _xy6(pts: Sequence[float], matrix: Optional[Tuple[float, ...]]) -> List[float]:
    out: List[float] = []
    for i in range(0, len(pts), 2):
        out.extend(_xy(pts[i], pts[i + 1], matrix))
    return out


def _arc_to_cubics(
    x1: float, y1: float, rx: float, ry: float, phi: float,
    large_arc: int, sweep: int, x2: float, y2: float,
) -> List[List[float]]:
    """SVG arc → 至多 4 段三次贝塞尔（标准端点参数化；无网络/字体依赖）。"""
    if x1 == x2 and y1 == y2:
        return []
    rx_abs, ry_abs = abs(rx), abs(ry)
    cos_p, sin_p = math.cos(math.radians(phi)), math.sin(math.radians(phi))
    dx, dy = (x1 - x2) / 2.0, (y1 - y2) / 2.0
    x1p = cos_p * dx + sin_p * dy
    y1p = -sin_p * dx + cos_p * dy
    lam = (x1p * x1p) / (rx_abs * rx_abs) + (y1p * y1p) / (ry_abs * ry_abs)
    if lam > 1.0:
        s = math.sqrt(lam)
        rx_abs, ry_abs = s * rx_abs, s * ry_abs
    num = rx_abs * rx_abs * ry_abs * ry_abs - rx_abs * rx_abs * y1p * y1p - ry_abs * ry_abs * x1p * x1p
    den = rx_abs * rx_abs * y1p * y1p + ry_abs * ry_abs * x1p * x1p
    co = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large_arc == sweep:
        co = -co
    cxp = co * rx_abs * y1p / ry_abs
    cyp = -co * ry_abs * x1p / rx_abs
    cx = cos_p * cxp - sin_p * cyp + (x1 + x2) / 2.0
    cy = sin_p * cxp + cos_p * cyp + (y1 + y2) / 2.0
    def angle(ux: float, uy: float, vx: float, vy: float) -> float:
        dot = ux * vx + uy * vy
        len_prod = math.hypot(ux, uy) * math.hypot(vx, vy)
        a = math.acos(max(-1.0, min(1.0, dot / len_prod))) if len_prod else 0.0
        if ux * vy - uy * vx < 0:
            a = -a
        return a
    th1 = angle(1.0, 0.0, (x1p - cxp) / rx_abs, (y1p - cyp) / ry_abs)
    dth = angle((x1p - cxp) / rx_abs, (y1p - cyp) / ry_abs,
                (-x1p - cxp) / rx_abs, (-y1p - cyp) / ry_abs)
    if not sweep and dth > 0:
        dth -= 2 * math.pi
    elif sweep and dth < 0:
        dth += 2 * math.pi
    segments = max(1, min(4, int(abs(dth) / (math.pi / 2)) + 1))
    delta = dth / segments
    t = 4.0 / 3.0 * math.tan(delta / 4.0)
    out: List[List[float]] = []
    th = th1
    px, py = x1, y1
    for _ in range(segments):
        th2 = th + delta
        cos1, sin1 = math.cos(th), math.sin(th)
        cos2, sin2 = math.cos(th2), math.sin(th2)
        e_x = cx + rx_abs * cos2 * cos_p - ry_abs * sin2 * sin_p
        e_y = cy + rx_abs * cos2 * sin_p + ry_abs * sin2 * cos_p
        d1x, d1y = -rx_abs * sin1 * cos_p - ry_abs * cos1 * sin_p, \
            -rx_abs * sin1 * sin_p + ry_abs * cos1 * cos_p
        d2x, d2y = -rx_abs * sin2 * cos_p - ry_abs * sin2 * sin_p, \
            -rx_abs * sin2 * sin_p + ry_abs * cos2 * cos_p
        out.append([
            px + t * d1x, py + t * d1y,
            e_x - t * d2x, e_y - t * d2y,
            e_x, e_y])
        px, py = e_x, e_y
        th = th2
    return out


# ── 元素树遍历与主入口 ─────────────────────────────────────────────────────

def _reject_attrs(el: Any) -> None:
    for k in el.keys():
        kl = str(k).lower()
        if _EVENT_ATTR_RE.match(kl):
            raise SymbolAssetError("SVG_EVENT_ATTR", f"事件属性 {k!r} 拒绝")
        v = el.get(k) or ""
        if kl in ("href", "xlink:href"):
            if not str(v).startswith("#"):
                raise SymbolAssetError(
                    "SVG_EXTERNAL_HREF", f"{k}={v!r} 外部引用拒绝")
        if isinstance(v, str) and _URL_VALUE_RE.search(v):
            raise SymbolAssetError("SVG_URL_VALUE", f"属性 {k} 含 url()")


def _points_attr(raw: str) -> List[Tuple[float, float]]:
    nums = [_num(p, where="points") for p in re.split(r"[\s,]+", (raw or "").strip()) if p]
    if not nums or len(nums) % 2 != 0:
        raise SymbolAssetError("SVG_POINTS_SYNTAX", "points 非偶数坐标")
    if len(nums) // 2 > MAX_POINTS_PER_PRIM:
        raise SymbolAssetError(
            "SVG_TOO_MANY_POINTS", f"points {len(nums) // 2} > {MAX_POINTS_PER_PRIM}")
    return [(nums[i], nums[i + 1]) for i in range(0, len(nums), 2)]


def _emit(
    state: _ImportState, el: Any, matrix: Optional[Tuple[float, ...]],
    inherited: Dict[str, str],
) -> None:
    tag = el.tag.split("}")[-1] if isinstance(el.tag, str) else ""
    style = dict(inherited)
    style.update(_style_dict(el))
    fill = _color_of(style, "fill")
    stroke = _color_of(style, "stroke")
    stroke_w = _stroke_width_of(style)
    opacity = _opacity_of(style)

    def transform_of() -> Optional[Tuple[float, float, float, float, float, float]]:
        own = _parse_transform(el.get("transform"))
        if own is None:
            return matrix or None
        if matrix is None:
            return own
        return _mat_mul(own, matrix)

    m = matrix
    if tag == "path":
        d = _flatten_path(el.get("d", ""), transform_of())
        state.primitives.append(SymbolPrimitive(
            op="path", d=d, fill=fill, stroke=stroke,
            stroke_width=stroke_w if stroke != "none" else 0.0, opacity=opacity))
    elif tag == "rect":
        m = transform_of()
        x = _check_coord(_num(el.get("x", "0"), where="rect x"), where="rect")
        y = _check_coord(_num(el.get("y", "0"), where="rect y"), where="rect")
        w = _num(el.get("width"), where="rect width")
        h = _num(el.get("height"), where="rect height")
        if w <= 0 or h <= 0:
            raise SymbolAssetError("SVG_RECT_SIZE", "rect 宽高 ≤ 0")
        rx = el.get("rx")
        corners: List[Tuple[float, float]] = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        if rx:
            r = _num(rx, where="rect rx")
            if not (0 < r <= min(w, h) / 2):
                raise SymbolAssetError("SVG_RECT_RX", "rx 越界")
            # 圆角矩形 → 圆角三次贝塞尔近似（标准 k=0.5523）
            k = r * 0.5523
            d: List[List[Union[str, float]]] = [["M", x + r, y],
                 ["L", x + w - r, y],
                 ["C", x + w - r + k, y, x + w, y + r - k, x + w, y + r],
                 ["L", x + w, y + h - r],
                 ["C", x + w, y + h - r + k, x + w - r + k, y + h, x + w - r, y + h],
                 ["L", x + r, y + h],
                 ["C", x + r - k, y + h, x, y + h - r + k, x, y + h - r],
                 ["L", x, y + r],
                 ["C", x, y + r - k, x + r - k, y, x + r, y],
                 ["Z"]]
            if m is not None:
                for c in d:
                    if c[0] == "Z":
                        continue
                    c[1:] = [round(v, 3) for v in _xy6(c[1:], m)]  # type: ignore[assignment]
            state.primitives.append(SymbolPrimitive(
                op="path", d=d, fill=fill, stroke=stroke,
                stroke_width=stroke_w if stroke != "none" else 0.0, opacity=opacity))
        else:
            pts = [_apply(m, px, py) if m else (px, py) for px, py in corners]
            d = [["M", round(pts[0][0], 3), round(pts[0][1], 3)]]
            for px, py in pts[1:]:
                d.append(["L", round(px, 3), round(py, 3)])
            d.append(["Z"])
            state.primitives.append(SymbolPrimitive(
                op="polygon", points=[(round(_check_coord(px, where="rect"), 3),
                                       round(_check_coord(py, where="rect"), 3))
                                      for px, py in pts],
                fill=fill, stroke=stroke,
                stroke_width=stroke_w if stroke != "none" else 0.0, opacity=opacity))
    elif tag == "circle":
        m = transform_of()
        cx = _num(el.get("cx", "0"), where="circle cx")
        cy = _num(el.get("cy", "0"), where="circle cy")
        r = _num(el.get("r"), where="circle r")
        if r <= 0:
            raise SymbolAssetError("SVG_CIRCLE_R", "circle r ≤ 0")
        if m is None:
            prim = SymbolPrimitive(
                op="circle", cx=round(cx, 3), cy=round(cy, 3), r=round(r, 3),
                fill=fill, stroke=stroke,
                stroke_width=stroke_w if stroke != "none" else 0.0, opacity=opacity)
        else:
            # 非等比/旋转变换下圆不再保形 → path 化（等比则保持 circle）
            a, _b, c, d_, _e, _f = m
            uniform = abs(a - d_) < 1e-9 and abs(c) < 1e-9 and abs(_b) < 1e-9 and a != 0
            if uniform:
                tx, ty = _apply(m, cx, cy)
                prim = SymbolPrimitive(
                    op="circle", cx=round(tx, 3), cy=round(ty, 3),
                    r=round(abs(a) * r, 3), fill=fill, stroke=stroke,
                    stroke_width=stroke_w if stroke != "none" else 0.0, opacity=opacity)
            else:
                segs = 16
                d2: List[List[Union[str, float]]] = []
                for k in range(segs):
                    ang = 2 * math.pi * k / segs
                    px, py = _apply(m, cx + r * math.cos(ang), cy + r * math.sin(ang))
                    d2.append((["M"] if k == 0 else ["L"]) +
                              [round(px, 3), round(py, 3)])
                d2.append(["Z"])
                prim = SymbolPrimitive(
                    op="path", d=d2, fill=fill, stroke=stroke,
                    stroke_width=stroke_w if stroke != "none" else 0.0, opacity=opacity)
        state.primitives.append(prim)
    elif tag == "ellipse":
        m = transform_of()
        cx = _num(el.get("cx", "0"), where="ellipse cx")
        cy = _num(el.get("cy", "0"), where="ellipse cy")
        rx = _num(el.get("rx"), where="ellipse rx")
        ry = _num(el.get("ry"), where="ellipse ry")
        if rx <= 0 or ry <= 0:
            raise SymbolAssetError("SVG_ELLIPSE_R", "ellipse 半轴 ≤ 0")
        segs = 24
        d3: List[List[Union[str, float]]] = []
        for k in range(segs):
            ang = 2 * math.pi * k / segs
            px, py = cx + rx * math.cos(ang), cy + ry * math.sin(ang)
            if m is not None:
                px, py = _apply(m, px, py)
            d3.append((["M"] if k == 0 else ["L"]) + [round(px, 3), round(py, 3)])
        d3.append(["Z"])
        state.primitives.append(SymbolPrimitive(
            op="path", d=d3, fill=fill, stroke=stroke,
            stroke_width=stroke_w if stroke != "none" else 0.0, opacity=opacity))
    elif tag == "line":
        m = transform_of()
        x1 = _num(el.get("x1", "0"), where="line x1")
        y1 = _num(el.get("y1", "0"), where="line y1")
        x2 = _num(el.get("x2", "0"), where="line x2")
        y2 = _num(el.get("y2", "0"), where="line y2")
        p1 = _apply(m, x1, y1) if m else (x1, y1)
        p2 = _apply(m, x2, y2) if m else (x2, y2)
        prim = SymbolPrimitive(
            op="line", x=round(p1[0], 3), y=round(p1[1], 3),
            w=round(p2[0] - p1[0], 3), h=round(p2[1] - p1[1], 3),
            fill="none", stroke=stroke if stroke != "none" else "#000000",
            stroke_width=stroke_w if stroke_w > 0 else 1.0, opacity=opacity)
        state.primitives.append(prim)
    elif tag in ("polygon", "polyline"):
        m = transform_of()
        pts = _points_attr(el.get("points", ""))
        if len(pts) < 2:
            raise SymbolAssetError("SVG_POINTS_SYNTAX", f"{tag} 点数 < 2")
        if m is not None:
            pts = [_apply(m, px, py) for px, py in pts]
        checked = [(round(_check_coord(px, where=tag), 3),
                    round(_check_coord(py, where=tag), 3)) for px, py in pts]
        state.primitives.append(SymbolPrimitive(
            op="polygon" if tag == "polygon" else "polyline", points=checked,
            fill=fill, stroke=stroke,
            stroke_width=stroke_w if stroke != "none" else 0.0, opacity=opacity))
    elif tag == "use":
        m = transform_of()
        href = el.get("href") or el.get("{http://www.w3.org/1999/xlink}href") or ""
        if not href.startswith("#") or len(href) < 2:
            raise SymbolAssetError("SVG_USE_TARGET", f"use href={href!r} 必须为文档内 #id")
        target_id = href[1:]
        if target_id in state.use_stack:
            raise SymbolAssetError(
                "SVG_USE_CYCLE", f"use 引用循环: {' -> '.join(state.use_stack + [target_id])}")
        target = state.defs.get(target_id)
        if target is None:
            raise SymbolAssetError(
                "SVG_USE_UNKNOWN_TARGET", f"use #{target_id} 目标不在 defs 内（仅支持 defs 引用）")
        state.use_stack.append(target_id)
        try:
            for prim in target:
                shifted = prim.model_copy(deep=True)
                if shifted.d:
                    shifted.d = [([c] if isinstance(c[0], str) else c) for c in shifted.d]
                    for c in shifted.d:
                        if c[0] == "Z":
                            continue
                        c[1:] = [round(v, 3) for v in _xy6(c[1:], m)]  # type: ignore[assignment]
                elif shifted.points:
                    shifted.points = [_apply(m, px, py) for px, py in shifted.points]
                else:
                    if shifted.cx is not None:
                        shifted.cx, shifted.cy = _apply(m, shifted.cx, shifted.cy or 0.0)
                    if shifted.x is not None:
                        shifted.x, shifted.y = _apply(m, shifted.x, shifted.y or 0.0)
                state.primitives.append(shifted)
        finally:
            state.use_stack.pop()
    # g/defs/svg 由 _walk 递归；其余 tag 不该到这里（_walk 已过滤）。


def _walk(
    state: _ImportState, el: Any, matrix: Optional[Tuple[float, float, float, float, float, float]],
    inherited: Dict[str, str], *, depth: int, in_defs: bool, root: Any,
) -> None:
    state.nodes += 1
    if state.nodes > MAX_INPUT_NODES:
        raise SymbolAssetError("SVG_TOO_MANY_NODES", f"节点数超 {MAX_INPUT_NODES}")
    if depth > MAX_NESTING:
        raise SymbolAssetError("SVG_TOO_DEEP", f"嵌套深度超 {MAX_NESTING}")
    tag = el.tag.split("}")[-1] if isinstance(el.tag, str) else ""
    if tag in _UNSUPPORTED_ELEMENTS:
        raise SymbolAssetError(
            _UNSUPPORTED_ELEMENTS[tag],
            f"元素 <{tag}> 不在受控 SVG 子集（明确拒绝，不静默删除）")
    if tag not in _ALLOWED_ELEMENTS:
        raise SymbolAssetError("SVG_ELEMENT_UNSUPPORTED", f"未知元素 <{tag}>")
    _reject_attrs(el)
    own_matrix = matrix
    if tag in ("g", "svg", "use", "defs") or el.get("transform"):
        own = _parse_transform(el.get("transform"))
        if own is not None:
            own_matrix = _mat_mul(own, matrix) if matrix is not None else own
    inherited2 = dict(inherited)
    inherited2.update(_style_dict(el))
    if tag == "defs":
        in_defs = True
    if in_defs and tag != "defs":
        # defs 内只允许**具名**图元/图元组（use 引用目标）；无名 defs 内容
        # 与嵌套 use 显式拒绝 —— 不静默丢弃（S08）。
        if tag == "use":
            raise SymbolAssetError(
                "SVG_USE_IN_DEFS", "defs 内嵌 use 不支持（组合请在顶层展开）")
        if tag in ("path", "rect", "circle", "ellipse", "line",
                   "polygon", "polyline", "g"):
            if not el.get("id"):
                raise SymbolAssetError(
                    "SVG_DEFS_UNNAMED", "defs 内元素必须带 id（use 引用目标）")
            sub = _ImportState()
            if tag == "g":
                # 已进入具名组：子元素按普通图元收集（不再要求 id）。
                for child in el:
                    _walk(sub, child, own_matrix, inherited2, depth=depth + 1,
                          in_defs=False, root=root)
            else:
                _emit(sub, el, own_matrix, inherited2)
            state.defs[el.get("id", "")] = sub.primitives
            return
    if tag in ("path", "rect", "circle", "ellipse", "line", "polygon", "polyline", "use"):
        if not in_defs:
            _emit(state, el, own_matrix, inherited2)
        return
    for child in el:
        _walk(state, child, own_matrix, inherited2, depth=depth + 1,
              in_defs=in_defs, root=root)


def import_svg_asset(
    svg_bytes: bytes, *, kind: str, name: str = "",
    license_note: str = "",
) -> SymbolAssetDocument:
    """受控 SVG 子集导入主入口（S05：预览/live/出版消费同一规范形摘要）。

    kind=point_symbol：导入为点符号（viewBox = 符号内在尺寸）；
    kind=area_texture：导入为 svg_tile 纹理（viewBox = tile 周期）。
    """
    from app.lib.cartography.symbol_asset import ASSET_KINDS

    if kind not in ASSET_KINDS:
        raise SymbolAssetError("UNKNOWN_ASSET_KIND", kind)
    if not isinstance(svg_bytes, (bytes, bytearray)):
        raise SymbolAssetError("SVG_INPUT_NOT_BYTES", "输入必须是字节")
    if len(svg_bytes) > MAX_INPUT_BYTES:
        raise SymbolAssetError(
            "SVG_INPUT_TOO_LARGE", f"{len(svg_bytes)} > {MAX_INPUT_BYTES}")
    text = bytes(svg_bytes).decode("utf-8", errors="strict")
    low = text[:4096].lower()
    if "<!doctype" in low or "<!entity" in low or "<?xml-stylesheet" in low:
        raise SymbolAssetError("SVG_DTD_REJECTED", "DOCTYPE/实体/样式表指令拒绝")
    try:
        root = SafeET.fromstring(text)
    except Exception as exc:  # defusedxml 抛各类解析错误
        raise SymbolAssetError("SVG_PARSE_FAILED", f"XML 解析失败: {exc}") from exc
    if root.tag.split("}")[-1] != "svg":
        raise SymbolAssetError("SVG_ROOT_NOT_SVG", "根元素必须是 <svg>")

    vb_raw = root.get("viewBox")
    if not vb_raw:
        raise SymbolAssetError("SVG_VIEWBOX_MISSING", "viewBox 必须显式声明")
    vb_parts = [_num(p, where="viewBox") for p in re.split(r"[\s,]+", vb_raw.strip()) if p]
    if len(vb_parts) != 4:
        raise SymbolAssetError("SVG_VIEWBOX_SYNTAX", "viewBox 必须 4 个数")
    min_x, min_y, vb_w, vb_h = vb_parts
    if vb_w < MIN_VIEWBOX or vb_h < MIN_VIEWBOX or vb_w > MAX_VIEWBOX or vb_h > MAX_VIEWBOX:
        raise SymbolAssetError(
            "SVG_VIEWBOX_OUT_OF_RANGE",
            f"viewBox {vb_w:g}x{vb_h:g} ∉ [{MIN_VIEWBOX:g},{MAX_VIEWBOX:g}]")
    if min_x != 0 or min_y != 0:
        raise SymbolAssetError(
            "SVG_VIEWBOX_ORIGIN", "viewBox 原点必须为 0 0（导入期不隐式平移）")

    state = _ImportState()
    root_matrix = (1.0 / vb_w, 0.0, 0.0, 1.0 / vb_h, 0.0, 0.0) \
        if kind == "area_texture" else None
    inherited: Dict[str, str] = {}
    for child in root:
        _walk(state, child, root_matrix, inherited, depth=1, in_defs=False, root=root)
    if not state.primitives:
        raise SymbolAssetError("SVG_EMPTY_CONTENT", "无可导入图元（空 defs/空图）")
    if len(state.primitives) > MAX_PRIMITIVES:
        raise SymbolAssetError(
            "SVG_TOO_MANY_PRIMITIVES", f"{len(state.primitives)} > {MAX_PRIMITIVES}")
    source = {"origin": "svg_import", "license": license_note or "unspecified",
              "note": "受控 SVG 子集导入（G40 svg_import 单一清洗口径）"}
    if kind == "point_symbol":
        doc = SymbolAssetDocument(
            kind="point_symbol", name=name, viewbox=(vb_w, vb_h),
            primitives=state.primitives, default_params={}, source=source)
    else:
        doc = SymbolAssetDocument(
            kind="area_texture", name=name,
            pattern=TexturePattern(
                type="svg_tile", tile_primitives=state.primitives,
                tile_viewbox=(vb_w, vb_h)),
            default_params={}, source=source)
    payload = doc.model_dump_json()
    if len(payload.encode("utf-8")) > MAX_ASSET_JSON_BYTES:
        raise SymbolAssetError("SVG_ASSET_TOO_LARGE", "规范形超字节预算")
    return doc


__all__ = ["import_svg_asset", "MAX_INPUT_BYTES", "MAX_INPUT_NODES"]
