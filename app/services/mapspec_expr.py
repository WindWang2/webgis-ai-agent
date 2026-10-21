"""MapLibre 表达式导出求值与停靠插值（#1726 项 2 / #1385 F04）。

与前端孪生 ``frontend/lib/mapspec-compiler/mapspec-to-svg.ts`` 的
``evaluateMapLibreExpression`` / ``resolvePaintValue`` / ``interpolateValue``
逐分支对齐 —— 同一份 live 合成 spec 经两个导出路径（headless Python /
live TS）必须产出相同几何。词表外算子不求值、原样返回，由调用方
``_safe_float`` 回落缺省（fail-open，绝不产出 NaN）。

模块还持有「本次编译的导出 zoom」ContextVar（Celery 线程/协程并发导出
互不污染；与 ``app/core/runtime_context.py`` 的 ContextVar 纪律同款）：
编译入口 ``compile_mapspec_to_svg_detailed`` set/reset，求值器只读。
"""
from __future__ import annotations

import contextvars
import logging
import math
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: MapLibre 表达式算子白名单 —— 与前端孪生 ``MAPLIBRE_EXPR_OPS`` 逐项对齐
#: （双路径对同一份 live 合成 spec 必须产出相同几何）。词表外算子不求值、
#: 原样返回，由调用方 ``_safe_float`` 回落缺省（fail-open，绝不产出 NaN
#: —— 与 TS 孪生 ``return val`` 分支同口径）。
MAPLIBRE_EXPR_OPS = frozenset({
    "interpolate",
    "interpolate-hcl",
    "interpolate-lab",
    "step",
    "match",
    "literal",
})

#: 导出 zoom 缺省值（spec.view.zoom 缺席时）—— 与前端孪生
#: mapspec-to-svg.ts ``activeExportZoom = 8`` 及
#: ``app.lib.cartography.representation.export_zoom_of_mapspec`` 同口径。
DEFAULT_EXPORT_ZOOM = 8.0

#: 本次编译的导出 zoom（ContextVar）。编译入口 set/reset，求值器只读。
ACTIVE_EXPORT_ZOOM: contextvars.ContextVar[float] = contextvars.ContextVar(
    "mapspec_to_svg_export_zoom", default=DEFAULT_EXPORT_ZOOM,
)


def parse_color(c: Any) -> Optional[Tuple[int, int, int]]:
    if not isinstance(c, str):
        return None
    s = c.strip().lower()
    if s.startswith("#"):
        hex_str = s[1:]
        if len(hex_str) == 3:
            return (int(hex_str[0] * 2, 16), int(hex_str[1] * 2, 16), int(hex_str[2] * 2, 16))
        elif len(hex_str) in (6, 8):
            return (int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16))
    elif s.startswith("rgb"):
        import re
        m = re.search(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", s)
        if m:
            return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def rgb_to_hex(r: float, g: float, b: float) -> str:
    r_c = max(0, min(255, int(round(r))))
    g_c = max(0, min(255, int(round(g))))
    b_c = max(0, min(255, int(round(b))))
    return f"#{r_c:02x}{g_c:02x}{b_c:02x}"


def interpolate_value(v0: Any, v1: Any, t: float) -> Any:
    try:
        n0, n1 = float(v0), float(v1)
        if math.isfinite(n0) and math.isfinite(n1):
            return n0 + t * (n1 - n0)
    except (ValueError, TypeError) as exc:
        logger.debug("[mapspec_expr] numeric interpolation skipped: %s", exc)

    c0 = parse_color(v0)
    c1 = parse_color(v1)
    if c0 and c1:
        r = c0[0] + t * (c1[0] - c0[0])
        g = c0[1] + t * (c1[1] - c0[1])
        b = c0[2] + t * (c1[2] - c0[2])
        return rgb_to_hex(r, g, b)

    return v0 if t < 0.5 else v1


def js_number(value: Any) -> float:
    """JS ``Number()`` 语义的最小镜像（表达式输入项/停靠点求值用）。

    TS 孪生 ``Number(...)``：空/纯空白串 → 0、单元素数组 → 其元素值、
    ``undefined``/不可解析 → NaN。Python ``float()`` 在空串上抛
    ValueError（孪生分歧点），故显式收口；非有限值一律折成 NaN（
    ``Number.isFinite`` 门同结果）。
    """
    if value is None:
        return float("nan")
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        fv = float(value)
        return fv if math.isfinite(fv) else float("nan")
    if isinstance(value, str):
        if value.strip() == "":
            return 0.0
        try:
            return float(value)
        except ValueError:
            return float("nan")
    if isinstance(value, (list, tuple)):
        if not value:
            return 0.0
        if len(value) == 1:
            return js_number(value[0])
        return float("nan")
    return float("nan")


def js_strict_eq(a: Any, b: Any) -> bool:
    """JS ``===`` 的最小镜像（``match`` 标签比对用）：类型敏感、布尔不与
    数值混比（Python ``True == 1`` 为真，JS ``true === 1`` 为假）。"""
    if a is None or b is None:
        return a is None and b is None
    a_bool, b_bool = isinstance(a, bool), isinstance(b, bool)
    if a_bool != b_bool:
        return False
    if a_bool:
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    return False


def expression_input(value: Any, props: Any, zoom: float) -> Any:
    """表达式输入项求值（``zoom`` / ``get`` / 字面量）—— TS 孪生
    ``expressionInput`` 镜像。不可解析 → None（≈ JS ``undefined``）。"""
    if value == "zoom" or (
        isinstance(value, (list, tuple)) and value and value[0] == "zoom"
    ):
        return zoom
    if (
        isinstance(value, (list, tuple))
        and len(value) >= 2
        and value[0] == "get"
        and isinstance(value[1], str)
    ):
        if isinstance(props, dict) and value[1] in props:
            return props[value[1]]
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, str)):
        return value
    return None


def evaluate_map_libre_expression(
    expr: Any, props: Any, zoom: float, fallback: Any
) -> Any:
    """MapLibre 表达式数组在导出 zoom 处求值 —— TS 孪生
    ``evaluateMapLibreExpression`` 逐分支镜像（#1385 F04 / #1726 项 2）。

    覆盖 ``interpolate``（含 hcl/lab 别名）、``step``、``match``、
    ``literal``；无法求值（非有限输入、空停靠表）→ 回落 ``fallback``，
    绝不产出 NaN。
    """
    if not isinstance(expr, (list, tuple)) or not expr:
        return fallback
    op = expr[0]

    if op == "literal":
        # TS: ``val[1] !== undefined ? val[1] : fallback`` —— 长度≥2 即取
        # 该位（含 null），长度不足才回落。
        return expr[1] if len(expr) >= 2 else fallback

    if op in ("interpolate", "interpolate-hcl", "interpolate-lab"):
        input_val = js_number(
            expression_input(expr[2], props, zoom) if len(expr) > 2 else None
        )
        if not math.isfinite(input_val):
            return fallback
        stops: List[Tuple[float, Any]] = []
        i = 3
        while i + 1 < len(expr):
            sx = js_number(expr[i])
            if math.isfinite(sx):
                stops.append((sx, expr[i + 1]))
            i += 2
        if not stops:
            return fallback
        if input_val <= stops[0][0]:
            return stops[0][1]
        last = stops[-1]
        if input_val >= last[0]:
            return last[1]
        for idx in range(len(stops) - 1):
            x0, v0 = stops[idx]
            x1, v1 = stops[idx + 1]
            if x0 <= input_val <= x1:
                if x1 == x0:
                    return v0
                return interpolate_value(v0, v1, (input_val - x0) / (x1 - x0))
        return last[1]

    if op == "step":
        input_val = js_number(
            expression_input(expr[1], props, zoom) if len(expr) > 1 else None
        )
        if not math.isfinite(input_val):
            return fallback
        # TS ``let res = val[2]``：缺基值时为 undefined（下游折算缺省）。
        res: Any = expr[2] if len(expr) > 2 else None
        i = 3
        while i + 1 < len(expr):
            thresh = js_number(expr[i])
            if math.isfinite(thresh) and input_val >= thresh:
                res = expr[i + 1]
            i += 2
        return res

    if op == "match":
        input_val = (
            expression_input(expr[1], props, zoom) if len(expr) > 1 else None
        )
        i = 2
        while i + 1 < len(expr) - 1:
            label = expr[i]
            # TS: ``inputVal === label || String(inputVal) === String(label)``。
            # None 侧不走串比（Python ``str(None)`` = "None" ≠ JS
            # ``String(undefined)`` = "undefined"），None 输入在真实 spec 中
            # 不构成命中。
            if js_strict_eq(input_val, label) or (
                input_val is not None and str(input_val) == str(label)
            ):
                return expr[i + 1]
            i += 2
        return expr[-1] if len(expr) >= 3 else fallback

    return fallback
