"""Map render work budget（R13，ADR-0182 D10）。

与 V11 quality 轴**完全分离**：quality 回答"好不好"，本模块只回答
"跑不跑得动"。纯函数估工模型——输入制图面形状（layer/feature/label/pixel/
export DPI），输出 governor 契约的 :class:`ResourceEstimate`
（RENDER_WORK_UNITS / memory / wall_time），供 admission 与 render 预算
扣减用。

形状契约 :class:`RenderWorkInput` 与工作量公式（``render_work_units`` /
``export_dpi_factor``）自 ADR-0216 起归位 ``app/contracts/render_work``
（lib 层消费方不得反向 import services）；本模块保留 governor 侧
``ResourceEstimate`` 装配（memory/wall-time 维度）与供数投影。

所有系数是 **provisional 先验**（source 标注 `render_formula.v1`），
由 `scripts/perf/calibrate_governor.py` 的 synthetic corpus 校准后更新；
公式单调性由单测锁定（要素越多估工越大，绝不出现反向曲线）。
"""
from __future__ import annotations


from app.contracts.render_work import (
    RenderWorkInput,
    export_dpi_factor,
    render_work_units,
)
from app.services.governor.contract import (
    Dimension,
    DimValue,
    ResourceClass,
    ResourceEstimate,
    Subsystem,
)

#: 公式版本（进 estimate.source，校准可追溯）
FORMULA_VERSION = "render_formula.v1"

# ---- 内存系数（provisional）------------------------------------------------
_BASE_MEMORY = 64 * 1024 * 1024
_PER_FEATURE_MEM = 1_500.0      # 要素几何 + 渲染中间态
_PER_PIXEL_MEM = 4.0            # 画布 RGBA 每 px（×过绘制系数）
_OVERDRAW_FACTOR = 2.5          # 画布缓冲/合成层过绘制
_PER_LABEL_MEM = 3_000.0

# ---- 墙钟（provisional throughput 模型）-----------------------------------
_WORK_PER_SECOND = 2_000_000.0  # 单进程渲染吞吐先验
_TAU_MIN = 0.35                 # min = expected × τ_min（快路径余量）
_TAU_MAX = 4.0                  # max = expected × τ_max（冷缓存/争抢）


def estimate_render(inp: RenderWorkInput) -> ResourceEstimate:
    """render 面 ResourceEstimate（work/memory/wall_time 三维，range 语义）。"""
    work = render_work_units(inp)
    dpi_factor = export_dpi_factor(inp.export_dpi)
    canvas_px = inp.canvas_pixels

    expected_mem = (
        _BASE_MEMORY
        + inp.feature_count * _PER_FEATURE_MEM
        + inp.label_count * _PER_LABEL_MEM
        + canvas_px * _PER_PIXEL_MEM * _OVERDRAW_FACTOR * min(dpi_factor, 4.0)
        + inp.raster_pixels * 2.0
    )
    mem = DimValue.estimated(
        expected_mem * 0.6, expected_mem, expected_mem * 2.5,
        confidence=0.55, source=FORMULA_VERSION,
        reason="provisional coefficients pending calibration",
    )
    expected_s = work / _WORK_PER_SECOND
    wall = DimValue.estimated(
        expected_s * _TAU_MIN, expected_s, expected_s * _TAU_MAX,
        confidence=0.5, source=FORMULA_VERSION,
        reason="throughput prior pending calibration",
    )
    work_dim = DimValue.estimated(
        work * 0.8, work, work * 1.5,
        confidence=0.6, source=FORMULA_VERSION,
        reason="deterministic formula; coefficient uncertainty",
    )
    est = ResourceEstimate(
        subsystem=Subsystem.RENDER,
        resource_class=ResourceClass.BROWSER if inp.export_dpi else ResourceClass.MEDIUM,
        dims={
            Dimension.RENDER_WORK_UNITS: work_dim,
            Dimension.MEMORY_BYTES: mem,
            Dimension.WALL_TIME_S: wall,
        },
        browser_required=True,
        confidence=0.55,
        source=FORMULA_VERSION,
        reason=(
            f"layers={inp.layer_count} features={inp.feature_count} "
            f"labels={inp.label_count} canvas_px={canvas_px} dpi={inp.export_dpi or 'screen'}"
        ),
    )
    return est


def render_input_from_spec_summary(summary) -> RenderWorkInput:
    """制图面摘要（宽松键 Mapping）→ RenderWorkInput（R13 供数投影）。

    消费方传什么算什么（全部可选缺省 = 空图）；键词表 = RenderWorkInput
    构造参数的同义别名（layers/features/labels/sources/width/height/dpi/
    raster_layers/raster_pixels/charts/floating）。**纯投影，零扫描** ——
    不读数据本体；摘要缺维就按空图/屏幕渲染保守估。生产接线点
    （cartography_runtime / publication_export 喂真实摘要）为 ADR-0213
    out-of-scope，本投影先行锁定口径与单调性测试。
    """
    if not isinstance(summary, dict):
        return RenderWorkInput()

    def _first(*keys):
        for k in keys:
            v = summary.get(k)
            if isinstance(v, (int, float)) and v >= 0:
                return v
        return 0

    dpi = summary.get("export_dpi", summary.get("dpi"))
    if not isinstance(dpi, (int, float)) or dpi <= 0:
        dpi = None
    return RenderWorkInput(
        layer_count=int(_first("layers", "layer_count", "map_layer_count")),
        feature_count=int(_first("features", "feature_count")),
        label_count=int(_first("labels", "label_count")),
        source_count=int(_first("sources", "source_count")),
        pixel_width=int(_first("width", "pixel_width")),
        pixel_height=int(_first("height", "pixel_height")),
        raster_layer_count=int(_first("raster_layers", "raster_layer_count")),
        raster_pixels=int(_first("raster_pixels")),
        chart_count=int(_first("charts", "chart_count")),
        floating_components=int(_first("floating", "floating_components")),
        export_dpi=dpi,
    )


__all__ = [
    "FORMULA_VERSION",
    "RenderWorkInput",
    "export_dpi_factor",
    "render_work_units",
    "estimate_render",
    "render_input_from_spec_summary",
]
