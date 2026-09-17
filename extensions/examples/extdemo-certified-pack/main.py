"""ExtDemo Certified 示例包入口（ADR-0199 活文档）。

与 extdemo-pack 的差别：本包在 manifest.json 声明 ``certification`` 节
（api >= 1.3.0），可被分级能力认证管线完整认证并产出指纹绑定的
``.certification.json``（激活 gate 消费）。本包随仓库发行
（trusted_builtin），是 `EXTENSIONS_BUILTIN_IDS` 认证豁免之外的「CI 内
认证」基线样例。

认证命令（在仓库根目录执行）::

    EXTENSIONS_DIRS=extensions/examples \
    python -m app.extensions_platform certify extdemo.certified --staged --save

声明与注册一一对应（声明 ↔ 注册 fail closed）；模块加载说明见同目录
extdemo-pack 的 main.py（entry 模块按文件路径加载，兄弟模块须用
``_load_sibling``）。
"""

from __future__ import annotations

from app.extensions_platform.sdk import (
    AlgorithmExtensionSpec,
    NumericalSmokeCase,
    ToolExtensionSpec,
)


def _rect_area_run(xmin: float, ymin: float, xmax: float, ymax: float) -> dict:
    """轴对齐 bbox 的平面面积；退化输入显式报错（诚实，不返回伪 0）。"""
    if xmax <= xmin or ymax <= ymin:
        raise ValueError("degenerate bbox: require xmax > xmin and ymax > ymin")
    width = xmax - xmin
    height = ymax - ymin
    return {"area": width * height, "width": width, "height": height, "unit": "crs"}


RECT_AREA_TOOL = ToolExtensionSpec(
    name="rect_area",
    description=(
        "Compute the planar area of an axis-aligned bounding box in CRS units. "
        "Offline demo tool of the extdemo certified example pack."
    ),
    func=_rect_area_run,
    summary="bbox 面积（认证示例：纯计算工具）",
    tier=1,
    side_effect="pure",
    deterministic=True,
    idempotent=True,
    latency_class="fast",
    result_size_policy="inline_small",
    param_descriptions={
        "xmin": "West edge of the bounding box, in CRS units.",
        "ymin": "South edge of the bounding box, in CRS units.",
        "xmax": "East edge of the bounding box, in CRS units.",
        "ymax": "North edge of the bounding box, in CRS units.",
    },
    tags=["extdemo", "geometry"],
)


RECT_AREA_ALGORITHM = AlgorithmExtensionSpec(
    id="rect_area_algo",
    name="Axis-aligned Rectangle Area",
    description=(
        "Planar area of an axis-aligned rectangle from bbox edges; the trivial "
        "certification demo algorithm bound to the rect_area tool."
    ),
    capabilities=[],
    category="geometry",
    tags=["extdemo", "geometry"],
    # 必须写「投影后的命名空间化工具名」，且工具先于算法注册。
    tool_candidates=["extdemo_rect_area"],
    deterministic=True,
    cpu_cost="low",
    memory_cost="low",
    io_cost="low",
    assumptions=["planar coordinates (CRS units); no geodesic correction"],
    limitations=["axis-aligned rectangles only; rotated quadrilaterals are out of scope"],
    scientific_status="EXPERIMENTAL",
    smoke_cases=[
        NumericalSmokeCase(
            arguments={"xmin": 0, "ymin": 0, "xmax": 2, "ymax": 2},
            expect_key="area",
            expect_value=4.0,
            tolerance=1e-9,
        ),
        NumericalSmokeCase(
            arguments={"xmin": 1, "ymin": 1, "xmax": 4, "ymax": 3},
            expect_key="area",
            expect_value=6.0,
            tolerance=1e-9,
        ),
    ],
)


TOOLS = [RECT_AREA_TOOL]
ALGORITHMS = [RECT_AREA_ALGORITHM]


def activate(ctx) -> None:
    for spec in TOOLS:
        ctx.register_tool(spec)
    for spec in ALGORITHMS:
        ctx.register_algorithm(spec)
