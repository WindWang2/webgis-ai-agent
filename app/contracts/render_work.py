"""RenderWorkInput 制图面形状契约 + 纯函数估工模型（自 services/governor/render_budget.py 下沉，ADR-0216）。

跨层消费者：``app/services/governor``（admission/预算扣减）与
``app/lib/cartography/render_work_projection``（harness 供数投影）。
lib 不得反向 import services，故形状契约与工作量公式归位 kernel；
``estimate_render``（需要 services/governor/contract 的 ResourceEstimate）
留在 services。

所有系数是 **provisional 先验**（source 标注 ``render_formula.v1``，
版本常量定义在 services/governor/render_budget.py），由
``scripts/perf/calibrate_governor.py`` 的 synthetic corpus 校准后更新；
公式单调性由单测锁定（要素越多估工越大，绝不出现反向曲线）。
"""
from __future__ import annotations

from typing import Optional

# ---- 工作量系数（provisional；单位 = render_work_units）--------------------
_BASE_WORK = 200_000.0          # 空图底噪：初始化 + 投影 + 画布
_PER_LAYER = 150_000.0          # 每图层的样式解析/绘制通道开销
_PER_SOURCE = 100_000.0         # 每 source 的加载/协调开销
_PER_FEATURE = 2_000.0          # 每要素的几何变换 + 填充描边
_PER_LABEL = 6_000.0            # 每标注（碰撞检测显著贵于要素本体）
_PER_PIXEL = 0.02               # 栅格化面积项（w*h）
_PER_RASTER_PIXEL = 0.05        # 栅格图层像素（重采样 + 色彩映射）
_PER_CHART = 800_000.0          # 内嵌图表（独立渲染管线）
_PER_FLOATING = 250_000.0       # 浮动组件（图例/指北针/比例尺/插页）
_SYMBOL_COMPLEXITY_CAP = 8.0    # 符号复杂度封顶（防输入爆炸）


def export_dpi_factor(dpi: Optional[float]) -> float:
    """导出 DPI 相对屏幕渲染（96dpi）的面积缩放；None = 屏幕渲染（×1）。

    真高 DPI 重渲染（ADR-0157）按面积平方律放大；并给 1.2× 的导出管线
    （编码/字体嵌入/CJK）常数。
    """
    if not dpi or dpi <= 0:
        return 1.0
    scale = (float(dpi) / 96.0) ** 2
    if scale <= 1.0:
        return 1.0
    return 1.2 * scale


def _clamp_positive(v: float, cap: float) -> float:
    return max(0.0, min(float(v), cap))


class RenderWorkInput:
    """制图面形状输入（全部可选缺省 = 空图）。防御式：负值钳 0。"""

    __slots__ = (
        "layer_count", "feature_count", "label_count", "source_count",
        "pixel_width", "pixel_height", "symbol_complexity",
        "chart_count", "floating_components",
        "raster_layer_count", "raster_pixels", "export_dpi",
    )

    def __init__(
        self,
        *,
        layer_count: int = 0,
        feature_count: int = 0,
        label_count: int = 0,
        source_count: int = 0,
        pixel_width: int = 0,
        pixel_height: int = 0,
        symbol_complexity: float = 1.0,
        chart_count: int = 0,
        floating_components: int = 0,
        raster_layer_count: int = 0,
        raster_pixels: int = 0,
        export_dpi: Optional[float] = None,
    ) -> None:
        self.layer_count = max(0, int(layer_count))
        self.feature_count = max(0, int(feature_count))
        self.label_count = max(0, int(label_count))
        self.source_count = max(0, int(source_count))
        self.pixel_width = max(0, int(pixel_width))
        self.pixel_height = max(0, int(pixel_height))
        self.symbol_complexity = _clamp_positive(symbol_complexity, _SYMBOL_COMPLEXITY_CAP)
        self.chart_count = max(0, int(chart_count))
        self.floating_components = max(0, int(floating_components))
        self.raster_layer_count = max(0, int(raster_layer_count))
        self.raster_pixels = max(0, int(raster_pixels))
        self.export_dpi = export_dpi

    @property
    def canvas_pixels(self) -> int:
        return self.pixel_width * self.pixel_height


def render_work_units(inp: RenderWorkInput) -> float:
    """确定性估工（同输入必同输出；spec §23 同款纪律）。"""
    complexity = max(0.1, inp.symbol_complexity)
    work = _BASE_WORK
    work += inp.layer_count * _PER_LAYER
    work += inp.source_count * _PER_SOURCE
    work += inp.feature_count * _PER_FEATURE * complexity
    work += inp.label_count * _PER_LABEL
    work += inp.canvas_pixels * _PER_PIXEL
    work += inp.raster_layer_count * _PER_LAYER
    work += inp.raster_pixels * _PER_RASTER_PIXEL
    work += inp.chart_count * _PER_CHART
    work += inp.floating_components * _PER_FLOATING
    return work * export_dpi_factor(inp.export_dpi)


__all__ = [
    "RenderWorkInput",
    "export_dpi_factor",
    "render_work_units",
]
