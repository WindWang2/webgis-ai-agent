"""LayerTypeCapability —— 图层类型渲染支持的机器真值（F13，ADR-0214 D6）。

与 :mod:`app.lib.cartography.component_renderers`（组件族矩阵）同纪律：

- **只描述「已实现」，不描述「应该有」**。truth = 前端
  ``frontend/lib/mapspec-compiler/compiler.ts`` 的逐类型编译实况
  （AC-06 补齐 background/hillshade 后 9 种词表全部有编译路径）；
- ``MapSpecLayer.type`` 词表（mapspec_schema.py）里的**每一种**都必须在
  本矩阵有声明 —— 词表扩了而矩阵没跟上，由交叉对账测试立刻爆（组合/
  规划面不得对未声明类型静默放行）；
- unsupported 的显式化分两级：组合/规划面**执行前**经
  :func:`disclose_unsupported`（随 ``design_system.layerCapability``
  导出 + 投影 ``unsupported_layer_types``）；运行时**执行后**在
  RenderApplyAck 以 ``unsupported_layer_type`` reason code 显式。

本模块是纯声明 + 纯函数，零 I/O、零状态。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

#: 支持级别（封闭词表）：
#: - full     前端 runtime 有逐类型编译路径，样式开放面直通；
#: - partial  可渲染但受已知条件约束（note 说明）；
#: - none     词表内声明但机器上无消费方（当前无 —— 保留枚举位）。
SUPPORT_FULL = "full"
SUPPORT_PARTIAL = "partial"
SUPPORT_NONE = "none"

_SUPPORT_LEVELS = (SUPPORT_FULL, SUPPORT_PARTIAL, SUPPORT_NONE)


@dataclass(frozen=True)
class LayerTypeSupport:
    """一个图层类型在当前机器上的渲染消费实况。"""

    layer_type: str
    level: str
    note: str = ""


LAYER_TYPE_SUPPORT: Dict[str, LayerTypeSupport] = {
    # 词表权威 = MapSpecLayer.type（mapspec_schema.py Literal）；编译实况
    # 权威 = frontend/lib/mapspec-compiler/compiler.ts 逐类型分支。
    "circle": LayerTypeSupport(
        layer_type="circle", level=SUPPORT_FULL,
        note="native/cluster/heatmap 三态编译（点密度裁决，compiler.ts:321+）",
    ),
    "line": LayerTypeSupport(
        layer_type="line", level=SUPPORT_FULL,
        note="符号律 line 通道直通",
    ),
    "fill": LayerTypeSupport(
        layer_type="fill", level=SUPPORT_FULL,
        note="符号律 fill 通道直通",
    ),
    "symbol": LayerTypeSupport(
        layer_type="symbol", level=SUPPORT_FULL,
        note="标注通道（MapSpecLayerLabel）与 text-field 编译",
    ),
    "heatmap": LayerTypeSupport(
        layer_type="heatmap", level=SUPPORT_FULL,
        note="heatmap-* 键映射 + circle→heatmap 密度改写（compiler.ts:593+）",
    ),
    "raster": LayerTypeSupport(
        layer_type="raster", level=SUPPORT_FULL,
        note="RasterMapSpecSource（imageRef/bounds/imageSize）消费",
    ),
    "fill-extrusion": LayerTypeSupport(
        layer_type="fill-extrusion", level=SUPPORT_FULL,
        note="color/opacity/height/base 编译（compiler.ts:680+）；3D 依赖地图 pitch",
    ),
    "background": LayerTypeSupport(
        layer_type="background", level=SUPPORT_FULL,
        note="无数据面（source="" 哨兵），compiler.ts:574",
    ),
    "hillshade": LayerTypeSupport(
        layer_type="hillshade", level=SUPPORT_FULL,
        note="消费 raster-dem 源；opacity=exaggeration 语义（compiler.ts:580+）",
    ),
}


def support_for(layer_type: str) -> Optional[LayerTypeSupport]:
    """类型 → 支持声明；词表外类型返回 None（由对账测试兜底披露）。"""
    return LAYER_TYPE_SUPPORT.get(str(layer_type or ""))


def is_supported(layer_type: str) -> bool:
    """类型是否可渲染（full/partial 都算「机器存在消费方」）。"""
    sup = support_for(layer_type)
    return sup is not None and sup.level != SUPPORT_NONE


def disclose_unsupported(mapspec: Optional[dict]) -> List[str]:
    """MapSpec → 不支持/未声明类型的有界去重披露（稳定序）。

    - ``none`` 级与词表外（矩阵未声明）都算 unsupported；
    - 只读 layers[].type；spec 缺 layers → 空表（不是错误）；
    - 有界：返回 ≤16 条（去重后类型数天然有界，防御性上限）。
    """
    if not isinstance(mapspec, dict):
        return []
    seen: List[str] = []
    for layer in mapspec.get("layers") or []:
        if not isinstance(layer, dict):
            continue
        ltype = str(layer.get("type") or "")
        if not ltype or ltype in seen:
            continue
        if not is_supported(ltype):
            seen.append(ltype)
            if len(seen) >= 16:
                break
    return seen


__all__ = [
    "SUPPORT_FULL",
    "SUPPORT_PARTIAL",
    "SUPPORT_NONE",
    "LayerTypeSupport",
    "LAYER_TYPE_SUPPORT",
    "support_for",
    "is_supported",
    "disclose_unsupported",
]
