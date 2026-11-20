"""G34 单位换算单一事实源（毫米 / pt / 逻辑 px / SVG viewBox user unit / 采样 px）。

为什么这些公式：

* ``publication_ir.PX_PER_MM = 4``：``compile_publication_page`` 用
  ``width=int(page_w*4)`` 把毫米转成 IR/浏览器设计的**逻辑 px**。
* ``mapspec_to_svg.py:1175`` ``dpi_scale = target_dpi/72``，
  ``scaled_width = width_val*dpi_scale``；SVG ``width`` 属性仍是未缩放
  逻辑 px，``viewBox`` 用 scaled 值。因此 1 viewBox user unit 对应的
  物理毫米 = ``18/target_dpi``（= ``72/(PX_PER_MM*dpi)``），而物理
  **毫米尺寸不随 DPI 变**、user unit 数随 DPI 线性变。
* 1 CSS px 在纸面上 = ``1/PX_PER_MM`` mm = ``PT_PER_MM/PX_PER_MM`` pt。

采样 px 是另一条轴：栅格化时 ``mm*dpi/25.4``，只影响 PNG 像素数量，
**不参与作者持久坐标**。CSS px / 逻辑 px / 采样 px 三者绝不混用。

Python 是权威；``frontend/lib/print-layout/units.ts`` 是同源镜像，由
``tests/cartography/golden_corpus/print_layout_units/basic.json`` 逐字段对拍。
"""
from __future__ import annotations

import math
from typing import Any, Dict

# 与 publication_ir.PX_PER_MM 同源；**不全局替换这个常量**。
PX_PER_MM = 4.0
#: mapspec_to_svg 的 dpi_scale 分母。
DPI_DIVISOR = 72.0
#: 1 mm = 72/25.4 pt（PostScript 点）。
PT_PER_MM = 72.0 / 25.4
MM_PER_INCH = 25.4

MIN_DPI = 72.0
MAX_DPI = 600.0          # 与 mapspec_to_svg.py:1174 的 clamp 一致


def clamp_dpi(dpi: Any) -> float:
    """把 DPI 夹到编译器实际生效的区间；非法值回落 300（同编译器口径）。"""
    try:
        d = float(dpi)
        if not math.isfinite(d) or d <= 0:
            return 300.0
    except (ValueError, TypeError, OverflowError):
        return 300.0
    return min(max(d, MIN_DPI), MAX_DPI)


def dpi_scale(dpi: Any) -> float:
    """逻辑 px → SVG viewBox user unit（与 mapspec_to_svg 完全同口径）。"""
    return clamp_dpi(dpi) / DPI_DIVISOR


# ── 毫米 ↔ 逻辑 px（IR / 浏览器设计面）─────────────────────────────────

def mm_to_logical_px(mm: float) -> float:
    return float(mm) * PX_PER_MM


def logical_px_to_mm(px: float) -> float:
    return float(px) / PX_PER_MM


# ── 毫米 ↔ SVG viewBox user unit（真实绘制面）───────────────────────────

def mm_to_canvas_units(mm: float, dpi: Any) -> float:
    """毫米 → ``compile_mapspec_to_svg_detailed`` 内部 user unit。"""
    return float(mm) * PX_PER_MM * dpi_scale(dpi)


def canvas_units_to_mm(units: float, dpi: Any) -> float:
    """产物 oracle 的反向换算（从 SVG 量回毫米）。"""
    return float(units) / (PX_PER_MM * dpi_scale(dpi))


# ── pt ↔ SVG viewBox user unit（字号/行距/字距）─────────────────────────

def pt_to_mm(pt: float) -> float:
    return float(pt) / PT_PER_MM


def mm_to_pt(mm: float) -> float:
    return float(mm) * PT_PER_MM


def pt_to_canvas_units(pt: float, dpi: Any) -> float:
    """pt → user unit：物理字号不随 DPI 漂移（修复 legacy 硬编码的缺陷）。"""
    return float(pt) * PX_PER_MM * dpi_scale(dpi) / PT_PER_MM


def canvas_units_to_pt(units: float, dpi: Any) -> float:
    return float(units) * PT_PER_MM / (PX_PER_MM * dpi_scale(dpi))


# ── 毫米 ↔ 栅格采样 px（PNG 输出，不参与持久坐标）───────────────────────

def mm_to_sample_px(mm: float, dpi: Any) -> float:
    return float(mm) * clamp_dpi(dpi) / MM_PER_INCH


def sample_px_to_mm(px: float, dpi: Any) -> float:
    return float(px) * MM_PER_INCH / clamp_dpi(dpi)


# ── 同源 corpus（golden parity + UI 消费）────────────────────────────────

def units_corpus() -> Dict[str, Any]:
    """返回确定性换算样本；Python/TS/golden 三方逐字段对拍。"""
    rows = []
    for dpi in (72, 96, 150, 300, 600):
        rows.append({
            "dpi": dpi,
            "dpi_scale": round(dpi_scale(dpi), 10),
            "mm_to_logical_px_1mm": round(mm_to_logical_px(1.0), 10),
            "mm_to_canvas_units_1mm": round(mm_to_canvas_units(1.0, dpi), 10),
            "canvas_units_to_mm_1unit": round(canvas_units_to_mm(1.0, dpi), 10),
            "pt_to_canvas_units_12pt": round(pt_to_canvas_units(12.0, dpi), 10),
            "mm_to_sample_px_1mm": round(mm_to_sample_px(1.0, dpi), 10),
        })
    return {
        "version": "print_layout_units.v1",
        "constants": {
            "px_per_mm": PX_PER_MM,
            "dpi_divisor": DPI_DIVISOR,
            "pt_per_mm": round(PT_PER_MM, 10),
            "mm_per_inch": MM_PER_INCH,
            "min_dpi": MIN_DPI,
            "max_dpi": MAX_DPI,
        },
        "rows": rows,
    }
