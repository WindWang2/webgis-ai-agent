"""Cartography Defaults — 兜底语义常量单点（V11 W0.4，ADR-0160，缺口 G8）。

V10 在业务代码里残留了散落的兜底字面量（缺省分级数 5、缺省分级方法
``quantiles``、缺省色带 ``YlOrRd`` 等）：同一语义在多处各自成文，改一处
漏一处。本模块是这些**兜底语义**的唯一成文点 —— 业务代码引用这里的名字，
禁止再写字面量。

边界（诚实圈定，防「归零」扩大化）：

- ``app/lib/cartography/palettes.py`` / ``themes.py`` / ``model_library.py``
  的色带与模型**注册表**是权威定义本身（名字存在即事实），不属于兜底
  字面量，不收敛进来；
- 前端对应兜底（thematic-apply / exporter 的 fallback）由 W3 的阈值策略
  函数统一处理（G10），本波只收后端业务代码；
- 值的变更走 ADR（它们是既有对外行为的缺省值，改动会影响既有出图）。
"""
from __future__ import annotations

#: 缺省分级数（class count）。历史缺省 5，QGIS/ESRI 惯例的中位起点。
DEFAULT_CLASS_COUNT = 5

#: 缺省分级方法。连续量（rate/连续指数）的稳健缺省 —— 对偏态分布不敏感。
DEFAULT_CLASSIFICATION_METHOD = "quantiles"

#: 缺省色带（sequential 族首选）。仅作未指定时的兜底，不做语义推断。
DEFAULT_PALETTE = "YlOrRd"

#: 缺省定性色带（categorical/名义量兜底；cartography_service categorical 分支）。
DEFAULT_CATEGORICAL_PALETTE = "Set2"

__all__ = [
    "DEFAULT_CLASS_COUNT",
    "DEFAULT_CLASSIFICATION_METHOD",
    "DEFAULT_PALETTE",
    "DEFAULT_CATEGORICAL_PALETTE",
]
