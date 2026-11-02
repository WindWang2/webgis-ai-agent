"""导出/渲染同合同裁决（M6）—— 窗口源覆盖的唯一读取面。

v1.9 的 artifact 窗（generalized/aggregate_grid）携带 ``window.source``
（spec.sources 内的派生工件源 id）。本模块提供跨后端 SVG 导出与前端
孪生（TS 侧 carto-ir/representation.ts 同语义）共用的**纯读取语义**：

- zoom → 活跃窗（半开区间，越界钳到首/末窗 —— 与
  representation.representation_mode_at_zoom 同族）；
- 活跃窗模式 ∈ artifact 词表且 ``source`` 为非空字符串 → 覆盖生效；
- 其余（raw 窗 / 覆盖缺席 / 无窗口声明）→ None（主层 source 语义）。

不修改既有 representation.py 的任何函数（只读复用 normalize 的窗口
词表常量）；本模块是 window.source 键的**唯一**权威读取点 —— 双端新
增消费者一律 import 这里（TS 侧为同语义孪生），防止第三处解读漂移。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.lib.cartography.representation import (
    ARTIFACT_MODES,
    REPRESENTATION_MODES,
    ZOOM_DOMAIN_MAX,
    ZOOM_DOMAIN_MIN,
)

_EPS = 1e-9


def active_representation_window(
    layer: Dict[str, Any], zoom: float
) -> Optional[Dict[str, Any]]:
    """层 dict + zoom → 活跃表达窗 dict（原始窗，含 source 键）。

    非法窗口（mode 词表外 / 非数值域）逐窗跳过 —— 坏文档可诊断（消费方
    已有 representation 相关诊断），本读取面不重复裁决、绝不猜。
    """
    if not isinstance(layer, dict):
        return None
    rep = layer.get("representation")
    if not isinstance(rep, dict):
        return None
    windows = rep.get("windows")
    if not isinstance(windows, list) or not windows:
        return None
    z = min(ZOOM_DOMAIN_MAX - _EPS, max(ZOOM_DOMAIN_MIN, float(zoom)))
    fallback: Optional[Dict[str, Any]] = None
    for w in windows:
        if not isinstance(w, dict):
            continue
        mode = w.get("mode")
        mn, mx = w.get("min_zoom"), w.get("max_zoom")
        if mode not in REPRESENTATION_MODES:
            continue
        if not isinstance(mn, (int, float)) or not isinstance(mx, (int, float)):
            continue
        if isinstance(mn, bool) or isinstance(mx, bool):
            continue
        # 越界回退 = 末窗（max_zoom 最大者）—— 与前端孪生
        # representationWindowAtZoom / representation_mode_at_zoom 同语义
        # （评审 R1-P2-4：缺口计划不得让后端导出应用与前端不同的窗）。
        if fallback is None or float(mx) > float(fallback.get("max_zoom", 0)):
            fallback = w
        if float(mn) <= z < float(mx):
            return w
    return fallback


def export_source_override(
    layer: Dict[str, Any], zoom: float
) -> Optional[str]:
    """导出/渲染数据源覆盖：活跃 artifact 窗的 ``source``（无则 None）。"""
    window = active_representation_window(layer, zoom)
    if window is None:
        return None
    if window.get("mode") not in ARTIFACT_MODES:
        return None
    src = window.get("source")
    if isinstance(src, str) and src:
        return src
    return None


def active_window_modes(layer: Dict[str, Any]) -> List[str]:
    """层声明窗的模式序列（QA/测试便捷面；按 min_zoom 排序）。"""
    if not isinstance(layer, dict):
        return []
    rep = layer.get("representation")
    if not isinstance(rep, dict):
        return []
    windows = rep.get("windows")
    if not isinstance(windows, list):
        return []
    rows = [
        w for w in windows
        if isinstance(w, dict) and w.get("mode") in REPRESENTATION_MODES
    ]
    rows.sort(key=lambda w: float(w.get("min_zoom", 0) or 0))
    return [str(w.get("mode")) for w in rows]


__all__ = [
    "active_representation_window",
    "export_source_override",
    "active_window_modes",
]
