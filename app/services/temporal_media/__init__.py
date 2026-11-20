"""G35 — 可核验的时序地图动画媒体交付。

把已绑定 ``temporal_view.v1`` 的地图，冻结成一份有版本的媒体计划，
逐帧离屏渲染为真实 PNG 数字帧，编码为可播放动画，并交付逐帧证据包。

复用边界（不重建）：temporal 观测桶/时间过滤、MapSpec SVG 编译与 rsvg
栅格化、G23 durable jobs、既有 export 所有权与签名下载、Pi 标准工具面。

本包只拥有「表达计划 → 数字帧 → 媒体容器 → 证据/下载」这段增量。
"""
from __future__ import annotations

from .contracts import (  # noqa: F401
    MEDIA_FORMATS,
    SCHEMA_VERSION,
    TemporalMediaError,
)

__all__ = ["SCHEMA_VERSION", "MEDIA_FORMATS", "TemporalMediaError"]
