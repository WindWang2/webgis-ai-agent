"""Heatmap radius contract: visual pixels vs analytical meters.

历史上 ``radius`` 一个词同时承担两种语义：

- 工具 schema 声明「搜索半径（米）」（raster/grid 模式下确为米，参与
  ``sigma = radius / cell_size`` 的真实换算）；
- native 渲染链路却把它当 MapLibre ``heatmap-radius`` 的**像素**值消费，
  各层用「>100 视为米制误传回落」「4–60 直通」「15 兜底」等互相矛盾的
  隐式猜测消化同一个数字。

本模块是唯一的单位归一化边界（compatibility adapter）：

- ``radius_px``  —— 视觉热力图核半径，MapLibre 屏幕像素；
- ``bandwidth_m`` —— 分析密度带宽，米（raster/grid/KDE）；
- legacy ``radius`` —— 旧参数（schema 文档为米）。在此**一次性、显式、
  可记录地**归一化为上述两个字段；核心链路（converter / palettes /
  renderer / export）此后只消费显式字段，不再猜测单位。

legacy 归一化规则（确定性，非猜测）：

1. ``bandwidth_m = radius``（尊重 schema 声明的米语义，raster 路径本就
   按米消费）；
2. ``radius_px`` **只认显式** ``radius_px``，否则 ``DEFAULT_RADIUS_PX``。
   不再把 4–60 的 legacy 米值当像素直通（schema 写的是米）。迁移警示
   写入 ``warnings``，进程内 ``logger.warning`` 只发一次。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 视觉热力半径（像素）契约区间：与前端 renderer 的最终 clamp 一致。
DEFAULT_RADIUS_PX = 30
RADIUS_PX_MIN = 4
RADIUS_PX_MAX = 80

# 分析带宽（米）契约区间：沿用旧 schema 的 10–10000。
BANDWIDTH_M_MIN = 10
BANDWIDTH_M_MAX = 10000
DEFAULT_BANDWIDTH_M = 1000

_LEGACY_VISUAL_DEFAULT_REASON = "legacy_radius_visual_default_applied"
_legacy_visual_warned = False


def clamp_radius_px(value: int) -> int:
    """Clamp an explicit pixel radius into the contractual window."""
    return max(RADIUS_PX_MIN, min(RADIUS_PX_MAX, int(value)))


@dataclass
class HeatmapRadiusContract:
    """归一化后的热力半径契约。核心链路只读显式字段。"""

    radius_px: int = DEFAULT_RADIUS_PX
    bandwidth_m: Optional[int] = None
    # explicit | default | legacy_radius_visual_default_applied
    source: str = "default"
    warnings: List[str] = field(default_factory=list)

    def to_metadata(self) -> Dict[str, Any]:
        """Bounded, JSON-safe projection for tool/MapSpec metadata."""
        meta: Dict[str, Any] = {
            "radius_px": self.radius_px,
            "radius_source": self.source,
        }
        if self.bandwidth_m is not None:
            meta["bandwidth_m"] = self.bandwidth_m
        if self.warnings:
            meta["radius_warnings"] = list(self.warnings)
        return meta


def _coerce_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def normalize_heatmap_radius(
    *,
    radius_px: Any = None,
    bandwidth_m: Any = None,
    legacy_radius: Any = None,
) -> HeatmapRadiusContract:
    """归一化热力半径。显式 ``radius_px`` / ``bandwidth_m`` 永远优先。

    这是 legacy ``radius`` 的唯一消化点；调用方（工具、converter、前端
    镜像）不得再自行猜测单位。
    """
    contract = HeatmapRadiusContract()
    warnings: List[str] = []

    explicit_px = _coerce_int(radius_px)
    explicit_bw = _coerce_int(bandwidth_m)
    legacy = _coerce_int(legacy_radius)

    if explicit_px is not None:
        clamped = clamp_radius_px(explicit_px)
        if clamped != explicit_px:
            warnings.append(
                f"radius_px {explicit_px} clamped to {clamped} "
                f"([{RADIUS_PX_MIN}, {RADIUS_PX_MAX}] px)"
            )
        contract.radius_px = clamped
        contract.source = "explicit"
    elif legacy is not None:
        # legacy schema 语义为米：带宽忠实继承；视觉半径不得猜测。
        # 4–60 也曾被当像素直通 —— 已停止（#1389 C01）。
        contract.bandwidth_m = legacy
        contract.radius_px = DEFAULT_RADIUS_PX
        contract.source = _LEGACY_VISUAL_DEFAULT_REASON
        warnings.append(
            f"legacy radius={legacy} is meters (schema semantics); visual "
            f"radius_px cannot be derived without guessing — applied default "
            f"{DEFAULT_RADIUS_PX}px. Pass explicit radius_px or bandwidth_m."
        )
        global _legacy_visual_warned
        if not _legacy_visual_warned:
            logger.warning(
                "heatmap_contract: legacy radius is meters; radius_px defaulted "
                "to %spx (warn once)",
                DEFAULT_RADIUS_PX,
            )
            _legacy_visual_warned = True

    if explicit_bw is not None:
        contract.bandwidth_m = explicit_bw
    elif contract.bandwidth_m is None:
        contract.bandwidth_m = (
            legacy if legacy is not None else DEFAULT_BANDWIDTH_M
        )

    if explicit_bw is not None and legacy is not None and explicit_bw != legacy:
        warnings.append(
            f"conflicting bandwidth: explicit bandwidth_m={explicit_bw} wins over "
            f"legacy radius={legacy}"
        )

    contract.warnings = warnings
    return contract


def resolve_paint_radius_px(metadata: Optional[Dict[str, Any]]) -> HeatmapRadiusContract:
    """从工具/分析结果 metadata 解析视觉热力半径（converter 消费面）。

    metadata 可能来自新工具（携带 radius_px/bandwidth_m）或历史会话的
    ref（只携带旧 ``radius``）。两条路径都经过唯一归一化边界。
    """
    meta = metadata if isinstance(metadata, dict) else {}
    return normalize_heatmap_radius(
        radius_px=meta.get("radius_px"),
        bandwidth_m=meta.get("bandwidth_m"),
        legacy_radius=meta.get("radius"),
    )


__all__ = [
    "DEFAULT_RADIUS_PX",
    "RADIUS_PX_MIN",
    "RADIUS_PX_MAX",
    "BANDWIDTH_M_MIN",
    "BANDWIDTH_M_MAX",
    "DEFAULT_BANDWIDTH_M",
    "HeatmapRadiusContract",
    "clamp_radius_px",
    "normalize_heatmap_radius",
    "resolve_paint_radius_px",
]
