"""Dasymetric 重分配工具（dasymetric_map 原生化的 D1-D3 链路）。

``dasymetric_reallocation`` 把源统计面（行政区等总量字段）按控制要素面
（土地利用/居住区，可带权重字段）切割重分配，产出有界、总量守恒的碎片
面要素层 —— 直接渲染为（现已 native 的）``dasymetric_map`` 模型。

与 od_flow_edges 同构（ADR-0092 D 的原生化模式）：工具只做 validate →
调实现（app/lib/geo_analysis/dasymetric.py）→ 挂证据块/图例的薄包装；
复杂度与守恒语义由库层契约承担（sindex 剪枝近似线性，输入面数硬上限）。
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


def _ylorrd_colors() -> List[str]:
    """单一色源：MapModel default_palette（YlOrRd）→ 图例色阶。"""
    try:
        from app.lib.cartography.palettes import COLOR_PALETTES

        return [str(c) for c in (COLOR_PALETTES.get("YlOrRd") or [])[:8]]
    except Exception:  # noqa: BLE001 — 图例是 best-effort
        return []


def register_dasymetric_tools(registry: ToolRegistry):

    @tool(registry,
        tier=2, domains=["statistics"],
        name="dasymetric_reallocation",
        description=(
            "分区密度重分配（dasymetric）：把源统计面的总量字段（人口/户数/"
            "建筑面积等可加总量）按控制要素面（土地利用/居住区；可带权重"
            "字段）的面积-权重比例切分到相交碎片。总量守恒（碎片值之和="
            "源值）；权重缺失/全零的源退化为纯面积比例并披露。输出渲染为 "
            "dasymetric_map 专题面（值越高端色越深，YlOrRd）。"
            "\n何时用：『行政区人口在居住区内的真实密度分布』『把面统计落到"
            "更精细的控制分区』。"
            "\n何时不用：(1) 无控制要素面 — 直接用 choropleth/spatial_"
            "aggregate；(2) 比率/密度字段（不可加）— 先乘分母换算成总量。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "source_geojson": {
                    "type": "string",
                    "description": "源统计面 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
                },
                "ancillary_geojson": {
                    "type": "string",
                    "description": "控制要素面 GeoJSON FeatureCollection 或数据引用(ref:xxx)",
                },
                "value_field": {
                    "type": "string",
                    "description": "源面总量字段名（必须可加；缺失字段结构化拒绝）",
                },
                "weight_field": {
                    "type": "string",
                    "description": "控制面权重字段名（如居住人口/建筑面积；空=纯面积权重）",
                },
            },
            "required": ["source_geojson", "ancillary_geojson", "value_field"],
        },
        side_effect="deterministic_compute",
        network=False,
        deterministic=True,
        latency_class="medium",
        memory_class="medium",
        scale_class="medium",
        tags=("分区密度", "dasymetric", "面插值", "重分配", "控制要素"),
        output_semantic_type="geojson_fc",
        result_size_policy="ref_offload",
        crs_semantics="auto_project",
        failure_modes=("invalid_args", "missing_data"))
    def dasymetric_reallocation(
        source_geojson: Any,
        ancillary_geojson: Any,
        value_field: str,
        weight_field: str = "",
        session_id: Optional[str] = None,
    ) -> dict:
        from app.lib.geo_analysis.dasymetric import dasymetric_reallocation as _impl

        res = _impl(source_geojson, ancillary_geojson, value_field,
                    weight_field=weight_field)
        if not res.success:
            return {
                "success": False,
                "error": res.summary,
                **({"correction_hint": res.correction_hint}
                   if res.correction_hint else {}),
            }
        fc = dict(res.data) if isinstance(res.data, dict) else res.data
        if not isinstance(fc, dict):
            return {"success": False, "error": "dasymetric 实现返回了非 FC 载荷"}
        meta = fc.get("metadata") or {}
        # 渲染接线（与 od_flow_edges 同模式）：type_hint → dasymetric_map，
        # render/legend/export 走 generic 分级面链（模型已是 native）。
        fc["command"] = "add_layer"
        fc["type_hint"] = "dasymetric_map"
        vals = [
            (f.get("properties") or {}).get(value_field)
            for f in fc.get("features", [])
            if isinstance(f.get("properties") or {}, dict)
        ]
        finite = [float(v) for v in vals if isinstance(v, (int, float))]
        if finite:
            fc["legend_spec"] = {
                "type": "continuous",
                "field": value_field,
                "min": min(finite),
                "max": max(finite),
                "palette_colors": _ylorrd_colors(),
                "title": f"{value_field}（dasymetric 重分配）",
            }
        fc["scientific_evidence"] = res.evidence
        fc["summary"] = res.summary
        return fc
