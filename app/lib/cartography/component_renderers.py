"""ComponentRendererRegistry —— 组件渲染/导出支持的机器真值.

组件 descriptor 的 ``renderer_support`` / ``exporter_support`` 声明的是
「机器上真实存在消费方」的程度。此前这两张表写在 descriptor 里且与
前端/导出器现实漂移（graticule/map_border 声称 live 可渲染、legend 族
声称导出器消费——均不属实，见 .scratch/component-library-audit.md §2）。

本模块是唯一权威（single source of truth）：

- ``_SUPPORT_MATRIX``：per-type 的 live renderer / exporter 支持清单，
  对齐前端 map-components/registry.ts（11 种 native）与
  frontend/lib/map-kit/exporter.ts（仅消费 title/north_arrow/scale_bar/
  export_layout 四类 spec 组件）；
- ``ComponentRegistry.validate()`` 交叉对账 descriptor 声明 ↔ 本矩阵，
  漂移即 issue（测试锁定，防止再次撒谎）；
- ``export_component_catalog`` 把矩阵随契约文件导出给前端。

支持矩阵只描述「已实现」，不描述「应该有」——planned 组件（inset_map）
必须留空并在 descriptor.runtime_status 上标记，不得伪装 native。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

LIVE_TARGET = "interactive"
EXPORT_TARGETS = ("png", "pdf", "svg", "print")


class ComponentRendererSupport(BaseModel):
    """一个组件类型在当前机器上的渲染/导出消费实况。"""

    component_type: str
    renderers: List[str] = Field(default_factory=list)   # live 渲染目标（"interactive"）
    exporters: List[str] = Field(default_factory=list)   # 导出器消费目标（png/pdf/svg/print）
    note: str = ""


_SUPPORT_MATRIX: Dict[str, ComponentRendererSupport] = {
    # ── live chrome 家族（前端 map-components/registry.ts 注册渲染器）──
    "title": ComponentRendererSupport(
        component_type="title", renderers=[LIVE_TARGET],
        exporters=["png", "pdf", "svg"],
        note="exporter runExport 读取 options.text 绘制画布标题",
    ),
    "subtitle": ComponentRendererSupport(
        component_type="subtitle", renderers=[LIVE_TARGET],
        exporters=["png", "pdf", "svg"],
        note="ADR-0081：exporter 经共享 resolveMapComponents 读 subtitle 组件（canvas 与 PDF 文本层同链）",
    ),
    "north_arrow": ComponentRendererSupport(
        component_type="north_arrow", renderers=[LIVE_TARGET],
        exporters=["png", "pdf", "svg"],
        note="exporter 读取 enabled 开关；缺省时 chrome 注入 fallback",
    ),
    "scale_bar": ComponentRendererSupport(
        component_type="scale_bar", renderers=[LIVE_TARGET],
        exporters=["png", "pdf", "svg"],
        note="同 north_arrow（enabled 开关）",
    ),
    "legend": ComponentRendererSupport(
        component_type="legend", renderers=[LIVE_TARGET],
        exporters=["png", "pdf", "svg"],
        note="ADR-0081：spec 组件在场时导出读组件（enabled/layerId/anchor），HUD 发现仅兜底",
    ),
    "categorical_legend": ComponentRendererSupport(
        component_type="categorical_legend", renderers=[LIVE_TARGET],
        exporters=["png", "pdf", "svg"],
        note="同 legend",
    ),
    "continuous_colorbar": ComponentRendererSupport(
        component_type="continuous_colorbar", renderers=[LIVE_TARGET],
        exporters=["png", "pdf", "svg"],
        note="ADR-0081：导出绘制渐变 ramp + min/max/unit（与 live colorbar 同形态）",
    ),
    "annotation": ComponentRendererSupport(
        component_type="annotation", renderers=[LIVE_TARGET], exporters=[],
    ),
    "attribution": ComponentRendererSupport(
        component_type="attribution", renderers=[LIVE_TARGET],
        exporters=["png", "pdf", "svg"],
        note="ADR-0081：导出读 spec attribution 组件（请求 author 仍在 metadata 行）",
    ),
    "statistics_panel": ComponentRendererSupport(
        component_type="statistics_panel", renderers=[LIVE_TARGET],
        exporters=["png", "pdf", "svg"],
        note="ADR-0081：canvas 导出绘制统计卡（placement 感知；collapsed 导出折叠条）",
    ),
    "chart_panel": ComponentRendererSupport(
        component_type="chart_panel", renderers=[LIVE_TARGET],
        exporters=["png", "pdf", "svg"],
        note="ADR-0081：canvas 导出绘制静态图表（与 live 同一数据协议 chart/chartRef）",
    ),
    # ── 仅导出/非 chrome 家族 ──────────────────────────────────────────
    "export_layout": ComponentRendererSupport(
        component_type="export_layout", renderers=[],
        exporters=["png", "pdf"],
        note="exporter 读取 paperSize/orientation/dpi 版面参数",
    ),
    "basemap": ComponentRendererSupport(
        component_type="basemap", renderers=[], exporters=[],
        note="类型占位：底图由 map-panel 底图逻辑承接，非 chrome 渲染",
    ),
    "graticule": ComponentRendererSupport(
        component_type="graticule", renderers=[], exporters=[],
        note="经纬网由导出请求参数 showGraticules 驱动，组件本身无消费方",
    ),
    "map_border": ComponentRendererSupport(
        component_type="map_border", renderers=[], exporters=[],
        note="导出边框不读该组件（无消费方）",
    ),
    # ── planned 家族（schema/registry/composition 支持，renderer 未实现）──
    "inset_map": ComponentRendererSupport(
        component_type="inset_map", renderers=[], exporters=[],
        note="runtime_status=planned：区位插图能力已建模，渲染器未实现",
    ),
}


class ComponentRendererRegistry:
    """type → 渲染/导出支持的只读索引（确定性、无 I/O）。"""

    def __init__(self) -> None:
        self._by_type: Dict[str, ComponentRendererSupport] = dict(_SUPPORT_MATRIX)

    def support_for(self, component_type: str) -> Optional[ComponentRendererSupport]:
        return self._by_type.get(component_type)

    def has_renderer(self, component_type: str, target: str = LIVE_TARGET) -> bool:
        support = self._by_type.get(component_type)
        return bool(support and target in support.renderers)

    def has_exporter(self, component_type: str, target: str) -> bool:
        support = self._by_type.get(component_type)
        return bool(support and target in support.exporters)

    def types_with_renderer(self, target: str = LIVE_TARGET) -> List[str]:
        return sorted(t for t in self._by_type if self.has_renderer(t, target))

    def types_with_exporter(self, target: str) -> List[str]:
        return sorted(t for t in self._by_type if self.has_exporter(t, target))

    def validate_against_descriptors(self) -> List[str]:
        """descriptor.renderer_support / exporter_support 必须与本矩阵一致。

        空列表 = 通过。返回的 issue 由 ComponentRegistry.validate() 与
        registry 校验测试消费——descriptor 撒谎会在测试层直接爆出。
        planned descriptor（inset_map）同样对账：支持声明必须诚实，
        与 runtime_status 无关。
        """
        from app.lib.cartography.component_registry import get_component_registry

        issues: List[str] = []
        registry = get_component_registry()
        for desc_id in registry.all_ids:
            desc = registry.get(desc_id)
            assert desc is not None
            support = self._by_type.get(desc.type)
            if support is None:
                issues.append(
                    f"component {desc.id}: type {desc.type} missing from renderer support matrix")
                continue
            if sorted(desc.renderer_support) != sorted(support.renderers):
                issues.append(
                    f"component {desc.id}: renderer_support {desc.renderer_support} "
                    f"drifts from matrix {support.renderers}")
            if sorted(desc.exporter_support) != sorted(support.exporters):
                issues.append(
                    f"component {desc.id}: exporter_support {desc.exporter_support} "
                    f"drifts from matrix {support.exporters}")
        # 矩阵反向覆盖：union 里的类型（如 basemap 无 descriptor）也必须有矩阵条目，
        # 否则 catalog 导出的支持字段会静默为空。
        from app.services.gis_harness.components import ComponentType
        from typing import get_args
        for t in get_args(ComponentType):
            if t not in self._by_type:
                issues.append(f"component type {t}: missing from renderer support matrix")
        return issues

    @property
    def all_types(self) -> List[str]:
        return sorted(self._by_type.keys())

    @property
    def count(self) -> int:
        return len(self._by_type)


_registry: Optional[ComponentRendererRegistry] = None


def get_component_renderer_registry() -> ComponentRendererRegistry:
    global _registry
    if _registry is None:
        _registry = ComponentRendererRegistry()
    return _registry


def reset_component_renderer_registry() -> None:
    global _registry
    _registry = None


__all__ = [
    "ComponentRendererSupport",
    "ComponentRendererRegistry",
    "get_component_renderer_registry",
    "reset_component_renderer_registry",
    "LIVE_TARGET",
]
