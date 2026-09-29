"""进程内共享 MapSpecLifecycleEngine 单例（视觉修复通道共用）。

healer 收敛账本（``_visual_heal_ledger``：attempts / no_improvement /
ops-signature 重放三重硬停）是**实例状态** —— user 批准路径
（visual_repairs 路由）与 AUTO_SAFE 自动通道（auto_repair）必须共享
同一实例，收敛硬停才对两条路径同时生效；否则自动通道每次 pass 拿到
空账本，同一缺陷可被无限重复尝试（C13 review P1-1）。

只合并视觉修复两个消费方；其它路由各自持有 engine 实例的现状不变
（既有语义：账本为进程内、多 pod 独立计数 —— 已在 ADR-0186 披露）。
"""
from __future__ import annotations

from app.services.mapspec.lifecycle_engine import MapSpecLifecycleEngine

_SHARED: MapSpecLifecycleEngine | None = None


def get_shared_lifecycle_engine() -> MapSpecLifecycleEngine:
    global _SHARED
    if _SHARED is None:
        _SHARED = MapSpecLifecycleEngine()
    return _SHARED


def reset_shared_lifecycle_engine() -> None:
    """测试隔离面（替换共享实例；生产不调用）。"""
    global _SHARED
    _SHARED = None


__all__ = [
    "get_shared_lifecycle_engine",
    "reset_shared_lifecycle_engine",
]
