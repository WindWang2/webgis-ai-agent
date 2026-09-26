"""Export work budget（ADR-0214 D7）。

与 :mod:`governor.render_budget` 同构的纯函数估工：export 面（PDF/PNG/
矢量导出）的形状输入 → governor 契约 :class:`ResourceEstimate`，供
workflow 节点与未来 render/export runtime（方向 13/14）经**同一** governor
通道申报资源。质量轴（好不好）完全分离 —— 本模块只回答"跑不跑得动"。

所有系数是 provisional 先验（``export_formula.v1``）；单调性由单测锁定
（页数/像素/图层数越多估工越大，绝不出现反向曲线）。校准回填与
render_formula.v1 同纪律：离线建议 + 人工 PR，生产零自修改。
"""
from __future__ import annotations

from typing import Optional

from app.services.governor.contract import (
    Dimension,
    DimValue,
    ResourceClass,
    ResourceEstimate,
    Subsystem,
)

#: 公式版本（进 estimate.source，校准可追溯）
FORMULA_VERSION = "export_formula.v1"

# ---- 工作量系数（provisional；单位 = render_work_units 同量纲）------------
_PER_PAGE_BASE = 600_000.0      # 每页排版/分页/合成底噪
_PER_VECTOR_LAYER = 120_000.0   # 每矢量图层的路径展开 + 填充描边
_PER_RASTER_PIXEL = 0.06        # 栅格像素重采样 + 色彩管理
_PER_CHART = 700_000.0          # 内嵌图表独立管线
_PER_FEATURE = 1_200.0          # 每要素几何投影

# ---- 内存系数（provisional）----------------------------------------------
_BASE_MEMORY = 96 * 1024 * 1024
_PER_PIXEL_MEM = 6.0            # 位图缓冲（RGBA × 导出倍率）
_PER_PAGE_MEM = 8 * 1024**2     # 每页矢量 display list
_PER_RASTER_PIXEL_MEM = 2.0

# ---- 墙钟（provisional throughput 模型）----------------------------------
_WORK_PER_SECOND = 1_200_000.0  # 导出管线慢于屏幕渲染（编码/字体嵌入）
_TAU_MIN = 0.4
_TAU_MAX = 5.0

#: 页数/图层有界性（防御输入爆炸；公式输入必须 bounded）
MAX_PAGES = 512
MAX_LAYERS = 256


def _clamp(v: float, cap: float) -> float:
    return max(0.0, min(float(v), cap))


class ExportWork:
    """导出面形状输入（全部可选缺省 = 单页空导出）。防御式：负值钳 0。"""

    __slots__ = ("page_count", "vector_layers", "raster_pixels",
                 "feature_count", "chart_count",
                 "pixel_width", "pixel_height", "dpi")

    def __init__(
        self,
        *,
        page_count: int = 1,
        vector_layers: int = 0,
        raster_pixels: int = 0,
        feature_count: int = 0,
        chart_count: int = 0,
        pixel_width: int = 0,
        pixel_height: int = 0,
        dpi: Optional[float] = None,
    ) -> None:
        self.page_count = max(1, min(int(page_count or 1), MAX_PAGES))
        self.vector_layers = max(0, min(int(vector_layers or 0), MAX_LAYERS))
        self.raster_pixels = max(0, int(raster_pixels or 0))
        self.feature_count = max(0, int(feature_count or 0))
        self.chart_count = max(0, int(chart_count or 0))
        self.pixel_width = max(0, int(pixel_width or 0))
        self.pixel_height = max(0, int(pixel_height or 0))
        self.dpi = float(dpi) if dpi and dpi > 0 else None

    @property
    def canvas_pixels(self) -> int:
        return self.pixel_width * self.pixel_height


def export_work_units(work: ExportWork) -> float:
    """确定性估工（同输入必同输出）。"""
    dpi_scale = 1.0
    if work.dpi and work.dpi > 96:
        dpi_scale = min(4.0, (work.dpi / 96.0) ** 2)
    units = (
        work.page_count * _PER_PAGE_BASE
        + work.vector_layers * _PER_VECTOR_LAYER
        + work.raster_pixels * _PER_RASTER_PIXEL
        + work.feature_count * _PER_FEATURE
        + work.chart_count * _PER_CHART
        + work.canvas_pixels * 0.03
    )
    return units * dpi_scale


def estimate_export(work: ExportWork) -> ResourceEstimate:
    """export 面 ResourceEstimate（work/memory/wall_time 三维，range 语义）。

    resource_class 恒为 EXPORT —— 背压面走 export 串行通道（全局容量 1，
    与 publication_export 的互斥纪律同向，但闸在 admission 而非进程锁）。
    """
    units = export_work_units(work)
    expected_mem = (
        _BASE_MEMORY
        + work.page_count * _PER_PAGE_MEM
        + work.canvas_pixels * _PER_PIXEL_MEM
        + work.raster_pixels * _PER_RASTER_PIXEL_MEM
    )
    mem = DimValue.estimated(
        expected_mem * 0.6, expected_mem, expected_mem * 2.5,
        confidence=0.55, source=FORMULA_VERSION,
        reason="provisional coefficients pending calibration",
    )
    expected_s = units / _WORK_PER_SECOND
    wall = DimValue.estimated(
        expected_s * _TAU_MIN, expected_s, expected_s * _TAU_MAX,
        confidence=0.5, source=FORMULA_VERSION,
        reason="throughput prior pending calibration",
    )
    work_dim = DimValue.estimated(
        units * 0.8, units, units * 1.5,
        confidence=0.6, source=FORMULA_VERSION,
        reason="deterministic formula; coefficient uncertainty",
    )
    return ResourceEstimate(
        subsystem=Subsystem.EXPORT,
        resource_class=ResourceClass.EXPORT,
        dims={
            Dimension.RENDER_WORK_UNITS: work_dim,
            Dimension.MEMORY_BYTES: mem,
            Dimension.WALL_TIME_S: wall,
        },
        confidence=0.55,
        source=FORMULA_VERSION,
        reason=(
            f"pages={work.page_count} layers={work.vector_layers} "
            f"canvas_px={work.canvas_pixels} dpi={work.dpi or 'default'}"
        ),
    )


def export_work_from_summary(summary) -> ExportWork:
    """宽松键 Mapping → ExportWork（消费方传什么算什么；纯投影零扫描）。"""
    if not isinstance(summary, dict):
        return ExportWork()

    def _first(*keys):
        for k in keys:
            v = summary.get(k)
            if isinstance(v, (int, float)) and v >= 0:
                return v
        return 0

    dpi = summary.get("export_dpi", summary.get("dpi"))
    if not isinstance(dpi, (int, float)) or dpi <= 0:
        dpi = None
    return ExportWork(
        page_count=int(_first("pages", "page_count") or 1),
        vector_layers=int(_first("layers", "vector_layers")),
        raster_pixels=int(_first("raster_pixels")),
        feature_count=int(_first("features", "feature_count")),
        chart_count=int(_first("charts", "chart_count")),
        pixel_width=int(_first("width", "pixel_width")),
        pixel_height=int(_first("height", "pixel_height")),
        dpi=dpi,
    )


__all__ = [
    "FORMULA_VERSION",
    "ExportWork",
    "export_work_units",
    "estimate_export",
    "export_work_from_summary",
    "MAX_PAGES",
    "MAX_LAYERS",
]
