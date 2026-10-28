"""瓦片服务 quota 执行层（G3D-V2-A A5 —— 清偿 #1771 D11 留白）。

此前 ``requests_per_minute`` 只进规划期成本 hint（planning/cost_model），
瓦片路由链（目录 MVT / PMTiles / 会话 terrain）无任何限流 —— 自托管瓦片
面可被单租户打满。本模块提供**进程内滑动窗口**限流：

- 键 = (scope, subject)：目录瓦片按 (用户/租户, item)；会话 terrain 按
  (session, ref)；出站 adapter 抓取按 (source, ...)（make_safe_session
  的 rate_limit 参数，见 security.make_safe_session）；
- 超限 → 429 + ``Retry-After`` + typed code（绝不静默抓取/静默丢弃）；
- env 配置：``DATA_FABRIC_TILE_RPM``（默认 600/键）、
  ``SESSION_TERRAIN_TILE_RPM``（默认 1200/键）；≤0 = 禁用；
- 已知限制（多 worker 部署各 worker 独立窗口，总预算 = N × rpm，保守
  方向；单进程多线程部署不受影响）—— 部署拓扑证据见 RISKS.md R3。

线程安全：路由 handler 在事件循环/线程池混合调度下触达，窗口更新持锁。
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Deque, Dict, Tuple

from fastapi import HTTPException

#: 滑动窗口宽度（秒）—— 与"每分钟"语义对齐。
WINDOW_S = 60.0

#: 单键窗口内最大采样数上限（防恶意键撑爆内存；超出即视同超限）。
_MAX_SAMPLES_PER_KEY = 4096

_LOCK = threading.Lock()
_WINDOWS: Dict[str, Deque[float]] = {}


def _rpm_from_env(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def data_fabric_tile_rpm() -> int:
    """目录瓦片路由的默认 rpm（env DATA_FABRIC_TILE_RPM，≤0 禁用）。"""
    return _rpm_from_env("DATA_FABRIC_TILE_RPM", 600)


def session_terrain_tile_rpm() -> int:
    """会话 terrain 瓦片路由的默认 rpm（env SESSION_TERRAIN_TILE_RPM，≤0 禁用）。"""
    return _rpm_from_env("SESSION_TERRAIN_TILE_RPM", 1200)


def check_rate(key: str, rpm: int) -> Tuple[bool, int]:
    """滑动窗口判定。返回 ``(allowed, retry_after_s)``。

    纯同步 O(窗口内样本)（≤ rpm 截断）；rpm ≤ 0 一律放行（禁用语义）。
    """
    if rpm <= 0:
        return True, 0
    now = time.monotonic()
    with _LOCK:
        window = _WINDOWS.get(key)
        if window is None:
            window = deque()
            _WINDOWS[key] = window
        cutoff = now - WINDOW_S
        while window and window[0] <= cutoff:
            window.popleft()
        if len(window) >= rpm or len(window) >= _MAX_SAMPLES_PER_KEY:
            retry_after = max(1, int(WINDOW_S - (now - window[0])) + 1)
            return False, retry_after
        window.append(now)
        # 有界键空间：极端多键（海量会话）下防无界增长。
        if len(_WINDOWS) > 65536:
            oldest_key = next(iter(_WINDOWS))
            _WINDOWS.pop(oldest_key, None)
        return True, 0


def enforce_tile_rate(key: str, rpm: int, *, scope: str) -> None:
    """路由守卫：超限抛 429（Retry-After + typed code；不泄漏键本体）。"""
    allowed, retry_after = check_rate(key, rpm)
    if allowed:
        return
    raise HTTPException(
        status_code=429,
        detail={
            "code": "TILE_RATE_LIMITED",
            "scope": scope,
            "limit_rpm": rpm,
            "message": "tile request rate exceeded for this source; retry after the window",
        },
        headers={"Retry-After": str(retry_after)},
    )


def reset_for_tests() -> None:
    """清空全部窗口（测试隔离专用）。"""
    with _LOCK:
        _WINDOWS.clear()


__all__ = [
    "check_rate",
    "data_fabric_tile_rpm",
    "enforce_tile_rate",
    "reset_for_tests",
    "session_terrain_tile_rpm",
]
