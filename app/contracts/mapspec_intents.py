"""MapSpec presentation 面 intent dataclass（自 services/mapspec/lifecycle_engine.py 下沉，ADR-0216）。

跨层消费者：``app/services/mapspec``（lifecycle 引擎）与
``app/lib/cartography/scene_selfheal``（自愈意图白名单）。
lifecycle_engine 是重运行时模块（store/锁/visual heal 等重依赖），
lib 不得为其反向 import services，故纯形状 intent 归位 kernel；
引擎侧经 lifecycle_engine 的 re-export 保持全部既有 import path。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class SetViewIntent:
    center: Optional[List[float]] = None
    zoom: Optional[float] = None
    pitch: Optional[float] = None
    bearing: Optional[float] = None


@dataclass
class PatchLayerPresentationIntent:
    """User/agent chrome: visibility and opacity without re-ingesting data."""

    layer_id: str
    visible: Optional[bool] = None
    opacity: Optional[float] = None


@dataclass
class SetSceneIntent:
    """多尺度场景协议（ADR-0199）：顶层 ``scene`` 写入（presentation 面）。

    scene 是表达面决策（2d/2.5d/3d + terrain 参数 + 相机建议档），不是
    数据面 —— 切换模式绝不触碰 sources/layers/legend_spec/thresholds
    （统计/分级/图例不漂移由构造保证）。值经 MapSceneConfig 严格校验：
    mode 词表、terrain.source 非空字符串、exaggeration ∈ (0, 10]；非法
    输入整笔拒绝（is_error，last-known-good 不变）。``None`` = 清除场景
    配置（键移除，回到既有 2d 语义）。terrain.source 的悬空引用由
    coordinator.validate（SCENE_TERRAIN_SOURCE_REF）在 pre-compile 阻塞
    —— 与图层 INVALID_SOURCE_REF 同 fail-closed 口径。
    """

    scene: Optional[Dict[str, Any]] = None


__all__ = [
    "SetViewIntent",
    "PatchLayerPresentationIntent",
    "SetSceneIntent",
]
