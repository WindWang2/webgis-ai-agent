"""ExtDemo 示例扩展包入口（ADR-0104 Wave 14）。

本包是扩展平台的「活文档 + 集成测试夹具」，同时被：
- 扩展作者文档（每个文件都可作为样板照抄，见同目录 README.md）；
- tests/unit/extensions_platform/test_example_pack.py（端到端激活 /
  投影 / 卸载零残留）。

内容（与 manifest.json 声明一一对应，声明 ↔ 注册 fail closed）：
- 2 个纯计算工具（离线、确定性、tier 1 常驻目录）；
- 1 个 EXPERIMENTAL 算法（Polsby-Popper 紧凑度，带数值 smoke cases）；
- 1 个离线栅格瓦片目录 provider（adapter 见 tile_catalog.py）；
- 1 个 planned 制图组件（extension 组件不得自称 native）；
- 1 个最小 recipe 包（id 强制 ``extdemo_`` 前缀）。

模块加载说明（扩展作者最容易踩的坑）：entry_point 指向的模块由宿主以
独立模块名（``webgis_ext_*``）按**文件路径**加载，扩展目录**不在**
sys.path 上——因此兄弟模块（tile_catalog.py / health.py）必须用下面的
``_load_sibling`` 显式按路径加载，普通 ``import tile_catalog`` 会失败。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from math import pi
from pathlib import Path
from typing import Any

from app.extensions_platform.sdk import (
    AlgorithmExtensionSpec,
    CartographyItemSpec,
    NumericalSmokeCase,
    ProviderExtensionSpec,
    ToolExtensionSpec,
    WorkflowPackSpec,
)
from app.services.gis_harness.recipes import CartographyRecipe

_PACK_DIR = Path(__file__).resolve().parent


def _load_sibling(module_name: str) -> Any:
    """按路径加载扩展目录内的兄弟模块（目录不在 sys.path 上）。"""
    path = _PACK_DIR / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(f"{__name__}_{module_name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load extension sibling module {path}")
    module = importlib.util.module_from_spec(spec)
    # 挂到本扩展的 webgis_ext_* 命名空间下：宿主 unload 时按公共前缀
    # 一并清理，不留陈旧模块句柄。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tile_catalog = _load_sibling("tile_catalog")
health = _load_sibling("health")


# ── 工具 1：bbox 面积（纯计算，CRS 单位）───────────────────────────────


def _bbox_area_run(xmin: float, ymin: float, xmax: float, ymax: float) -> dict:
    """轴对齐 bbox 的平面面积；退化输入显式报错（诚实，不返回伪 0）。"""
    if xmax < xmin or ymax < ymin:
        raise ValueError("degenerate bbox: require xmax >= xmin and ymax >= ymin")
    width = xmax - xmin
    height = ymax - ymin
    return {"area": width * height, "width": width, "height": height, "unit": "crs"}


BBOX_AREA_TOOL = ToolExtensionSpec(
    name="bbox_area",
    description=(
        "Compute the planar area of an axis-aligned bounding box in CRS units. "
        "Offline demo tool of the extdemo example pack."
    ),
    func=_bbox_area_run,
    summary="bbox 面积（示例：纯计算工具）",
    tier=1,
    side_effect="pure",
    deterministic=True,
    idempotent=True,
    param_descriptions={
        "xmin": "West edge of the bounding box, in CRS units.",
        "ymin": "South edge of the bounding box, in CRS units.",
        "xmax": "East edge of the bounding box, in CRS units.",
        "ymax": "North edge of the bounding box, in CRS units.",
    },
    tags=["extdemo", "geometry"],
)


# ── 工具 2：Polsby-Popper 紧凑度（纯计算，GeoJSON Polygon 首环）────────


def _ring_area(ring: list) -> float:
    """鞋厂公式（planar，坐标单位平方）；不 做 geodesic 修正。"""
    total = 0.0
    count = len(ring)
    for i in range(count - 1):
        x1, y1 = float(ring[i][0]), float(ring[i][1])
        x2, y2 = float(ring[i + 1][0]), float(ring[i + 1][1])
        total += x1 * y2 - x2 * y1
    return total / 2.0


def _ring_perimeter(ring: list) -> float:
    """环周长（planar，坐标单位）。"""
    total = 0.0
    for i in range(len(ring) - 1):
        dx = float(ring[i + 1][0]) - float(ring[i][0])
        dy = float(ring[i + 1][1]) - float(ring[i][1])
        total += (dx * dx + dy * dy) ** 0.5
    return total


def _polygon_compactness_run(geojson: str) -> dict:
    """Polsby-Popper 紧凑度 4πA/P²；只取首环（外环），输入须为 JSON 文本。"""
    try:
        data = json.loads(geojson)
    except ValueError as exc:
        raise ValueError(f"geojson is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("type") != "Polygon":
        raise ValueError("geojson must be a GeoJSON Polygon object")
    rings = data.get("coordinates")
    if (
        not isinstance(rings, list)
        or not rings
        or not isinstance(rings[0], list)
        or len(rings[0]) < 4
    ):
        raise ValueError("Polygon requires a closed first ring with >= 4 positions")
    ring = rings[0]
    area = abs(_ring_area(ring))
    perimeter = _ring_perimeter(ring)
    if area <= 0 or perimeter <= 0:
        raise ValueError("degenerate polygon: zero area or perimeter")
    return {
        "compactness": 4 * pi * area / (perimeter ** 2),
        "area": area,
        "perimeter": perimeter,
    }


POLYGON_COMPACTNESS_TOOL = ToolExtensionSpec(
    name="polygon_compactness",
    description=(
        "Compute Polsby-Popper compactness (4*pi*A/P^2) of a GeoJSON Polygon "
        "(first ring). Offline demo tool of the extdemo example pack."
    ),
    func=_polygon_compactness_run,
    summary="多边形紧凑度（示例：纯计算工具）",
    tier=1,
    side_effect="pure",
    deterministic=True,
    idempotent=True,
    param_descriptions={
        "geojson": "A GeoJSON Polygon as JSON text; only the first (outer) ring is used.",
    },
    tags=["extdemo", "morphometry"],
)


# ── 算法：紧凑度（EXPERIMENTAL，带数值 smoke cases）───────────────────

# smoke 夹具：单位正方形 A=1, P=4 → π/4；2:1 矩形 A=2, P=6 → 8π/36；
# 4:1 矩形 A=4, P=10 → 16π/100。全部可手算，容忍度 1e-3。
_SQUARE_GEOJSON = json.dumps(
    {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
)
_RECT_2X1_GEOJSON = json.dumps(
    {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 1], [0, 1], [0, 0]]]}
)
_RECT_4X1_GEOJSON = json.dumps(
    {"type": "Polygon", "coordinates": [[[0, 0], [4, 0], [4, 1], [0, 1], [0, 0]]]}
)

COMPACTNESS_ALGORITHM = AlgorithmExtensionSpec(
    id="compactness",
    name="Polsby-Popper Compactness",
    description=(
        "Scale-free shape compactness (Polsby-Popper 4*pi*A/P^2) for polygon features; "
        "values approach 1 for circular shapes."
    ),
    # CapabilityRegistry 是冻结 seam：不得虚构 capability id——示例刻意
    # 留空（引用既有 id 的写法见 workflow pack 的 preferred_analysis）。
    capabilities=[],
    category="morphometry",
    tags=["extdemo", "shape-metrics"],
    # 必须写「投影后的命名空间化工具名」，且工具先于算法注册。
    tool_candidates=["extdemo_polygon_compactness"],
    deterministic=True,
    cpu_cost="low",
    memory_cost="low",
    io_cost="low",
    assumptions=["planar coordinates (CRS units); no geodesic correction"],
    limitations=[
        "perimeter is sensitive to vertex densification; do not compare across "
        "differently simplified geometries",
    ],
    scientific_status="EXPERIMENTAL",
    # authoring harness 输入（不进入 descriptor）：tests 用 run_authoring_checks
    # 驱动「实现 → 数值 smoke」闭环。
    smoke_cases=[
        NumericalSmokeCase(
            arguments={"geojson": _SQUARE_GEOJSON},
            expect_key="compactness",
            expect_value=pi / 4,
            tolerance=1e-3,
        ),
        NumericalSmokeCase(
            arguments={"geojson": _RECT_2X1_GEOJSON},
            expect_key="compactness",
            expect_value=8 * pi / 36,
            tolerance=1e-3,
        ),
        NumericalSmokeCase(
            arguments={"geojson": _RECT_4X1_GEOJSON},
            expect_key="compactness",
            expect_value=16 * pi / 100,
            tolerance=1e-3,
        ),
    ],
)


# ── 数据 provider：离线静态瓦片目录（adapter 见 tile_catalog.py）───────

DEMO_TILE_PROVIDER = ProviderExtensionSpec(
    source_type="demo_tile_catalog",
    description=(
        "Offline demo raster-tile catalog backed by a bundled static JSON file; "
        "metadata-only, mirrors core raster semantics, no network access."
    ),
    adapter_cls=tile_catalog.DemoTileCatalogAdapter,
    is_raster_tile=True,
    # raster/tile 源不回答矢量要素查询（与核心 wms/pmtiles 同语义）。
    notes="demo raster-tile catalog; metadata-only, no vector feature query",
    # provider 契约演示：requires_network=True ⇒ manifest.permissions 必须
    # 声明 "network"（本 adapter 实现本身零网络，声明的是 provider 语义位）。
    requires_network=True,
)


# ── 制图组件：注记比例尺（planned——扩展组件不得自称 native）───────────

NOTE_SCALE_BAR = CartographyItemSpec(
    kind="component",
    id="note_scale_bar",
    description="Annotation-style scale bar variant; planned (no renderer evidence yet).",
    runtime_status="planned",
    # 诚实性声明：无渲染器证据 → 导出走降级披露路径。
    export_behavior="degraded",
    degradation_policy="omit_with_disclosure",
    supported_renderers=(),
    # MapComponentDescriptor 构造载荷：type 必须 ``extdemo_`` 前缀（context
    # 强制）；category 必须命中组件分类体系；renderer/exporter support 刻意
    # 留空——planned 组件不谎称渲染支持。
    payload={
        "type": "extdemo_note_scale_bar",
        "category": "navigation.scale_bar",
        "name": "Note Scale Bar (extdemo)",
        "name_zh": "注记比例尺（示例）",
        "description": "示例组件：注记式比例尺变体（仅目录声明，无前端实现）。",
        "placement_domain": "overlay",
        "default_variant": "default",
        "variants": ["default", "minimal"],
        "default_position": "bottom-left",
        "allowed_positions": ["bottom-left", "bottom-right", "none"],
        "cardinality": "single",
        "priority": 21,
        "states": ["visible", "hidden"],
        "collision_class": "chrome",
        "tags": ["extdemo", "navigation"],
        "accessibility": {"role": "img", "label_zh": "注记比例尺"},
    },
)


# ── Workflow pack：一个最小可编译的 CartographyRecipe ─────────────────

DEMO_RECIPE = CartographyRecipe(
    # id 强制 ``extdemo_`` 前缀（宿主 namespace 隔离规则）。
    id="extdemo_demo_overview",
    name="ExtDemo 概览图",
    description="示例 recipe：轻量点图产品——证明扩展可注入 recipe DSL 并通过编译期校验。",
    # intent_tasks 必须取自 intent.py 的 TaskType 词表。
    intent_tasks=["simple_view"],
    intent_cartography=["simple_point_map"],
    allowed_geometry=["Point", "MultiPoint"],
    # preferred_analysis 只引用 CapabilityRegistry 既有 id（编译期校验）。
    preferred_analysis=["poi_query", "point_profile"],
    primary_cartography="simple_point_map",
    secondary_cartography=["point_overlay"],
    default_components=["title", "north_arrow", "scale_bar"],
    export_profile={"formats": ["png"]},
    priority=90,
)

DEMO_WORKFLOW_PACK = WorkflowPackSpec(
    pack_id="demo_overview",
    description="示例 recipe 包：一个最小可编译的 CartographyRecipe。",
    recipes=[DEMO_RECIPE],
)


def activate(ctx) -> None:
    """入口契约：注册内容必须与 manifest.json 声明完全一致（fail closed）。

    顺序约束：算法注册期会对照「已注册工具」校验 tool_candidates，
    因此工具必须先于算法注册。
    """
    ctx.register_tool(BBOX_AREA_TOOL)
    ctx.register_tool(POLYGON_COMPACTNESS_TOOL)
    ctx.register_algorithm(COMPACTNESS_ALGORITHM)
    ctx.register_data_provider(DEMO_TILE_PROVIDER)
    ctx.register_cartography_item(NOTE_SCALE_BAR)
    ctx.register_workflow_pack(DEMO_WORKFLOW_PACK)
