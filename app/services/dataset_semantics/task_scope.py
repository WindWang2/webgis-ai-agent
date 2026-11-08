"""任务范围（城市/视口）资格粒度（G03 GD-05 / ADR-0286 D6）。

背景（BASELINE 断链 6）：既有 staleness 只由数据指纹变化触发——用户切换
城市/视口后，旧城市名下绑定的数据与资格不会被质疑（「旧资料凭名字复用」）。

本模块提供**范围指纹**与**范围 staleness 判定**（纯函数）：
- ``scope_fingerprint(place, bbox)``：确定性哈希。place 与 bbox 是不同的
  资格粒度——「成都」（用户事实）与「成都 + 精确视口」不可混用；
- ``evaluate_scope_staleness(recorded, current)``：范围变化 → stale +
  可解释 reason（城市切换是数据适用性的实质变化，不是缓存失效）。

不建第二状态权威：staleness 的执行权威仍是 ref_lifecycle + 指纹对账；
本模块只提供「范围变了没有」这一判定维度，由 MapSpec 复用门/绑定记录
消费（绑定记录 ``task_scope_fingerprint``，重绑定时对账）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import hashlib
import json

#: 指纹前缀（与 descriptor/ref 指纹同风格：可读的域标识）。
SCOPE_FINGERPRINT_PREFIX = "scope-"


def _normalize_bbox(bbox: Any) -> Optional[list]:
    """bbox → [minx, miny, maxx, maxy]（float，round 6；坏形状 → None）。

    非有限分量（NaN/±Inf）→ None（评审 F3：NaN 与任何值比较恒 False，
    会伪通过 min/max 序检查并让范围判据伪报 pass——必须显式拒绝）。
    """
    import math

    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    try:
        out = [round(float(b), 6) for b in bbox]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(b) for b in out):
        return None
    if out[0] > out[2] or out[1] > out[3]:
        return None
    return out


def scope_fingerprint(place: Optional[str] = None, bbox: Any = None) -> str:
    """任务范围 → 确定性指纹（同范围恒同值；粒度不同即不同值）。

    - place 归一：strip + 大小写不敏感（「成都」与「 成都 」同范围）；
    - bbox 归一：round 6（浮点噪声不产生新范围）；
    - 两者都缺 → 空范围指纹（"scope-empty"——无范围声明也是一种粒度）。
    """
    payload = {
        "place": str(place or "").strip().lower() or None,
        "bbox": _normalize_bbox(bbox),
    }
    if payload["place"] is None and payload["bbox"] is None:
        return SCOPE_FINGERPRINT_PREFIX + "empty"
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:24]
    return SCOPE_FINGERPRINT_PREFIX + digest


@dataclass
class ScopeStaleness:
    """范围 staleness 判定（有界、可解释）。"""

    stale: bool
    recorded: str = ""
    current: str = ""
    reason: str = ""

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "stale": self.stale,
            "recorded_scope": self.recorded[:48],
            "current_scope": self.current[:48],
            "reason": self.reason[:256],
        }


def evaluate_scope_staleness(
    recorded: Optional[str], current: Optional[str]
) -> ScopeStaleness:
    """记录面范围指纹 vs 当前范围指纹 → 是否需要重资格判定。

    空记录面（旧绑定无范围概念）不判 stale（向后兼容）；非空且不同 →
    stale + 原因（用户可解释：任务范围已从 A 切换到 B，旧数据资格过期）。
    """
    rec = str(recorded or "").strip()
    cur = str(current or "").strip()
    if not rec or not cur:
        return ScopeStaleness(stale=False, recorded=rec, current=cur)
    if rec == cur:
        return ScopeStaleness(stale=False, recorded=rec, current=cur)
    return ScopeStaleness(
        stale=True,
        recorded=rec,
        current=cur,
        reason=(
            f"任务范围已变化（{rec[:16]}… → {cur[:16]}…）：旧范围下绑定的"
            "数据资格不再适用于当前范围，需重新执行数据资格判定"
            "（城市/视口切换是数据适用性的实质变化，不是缓存失效）。"
        ),
    )


def task_scope_from_dict(scope: Any) -> Dict[str, Any]:
    """task_scope 输入归一：{place?, bbox?}（坏形状 → 空声明）。"""
    if not isinstance(scope, dict):
        return {}
    out: Dict[str, Any] = {}
    if scope.get("place"):
        out["place"] = str(scope["place"])[:128]
    normalized_bbox = _normalize_bbox(scope.get("bbox"))
    if normalized_bbox is not None:
        out["bbox"] = normalized_bbox
    return out


def scope_fingerprint_from_task_scope(task_scope: Any) -> str:
    """归一化 task_scope → 范围指纹（preflight/绑定记录共用口径）。"""
    norm = task_scope_from_dict(task_scope)
    if not norm:
        return ""
    return scope_fingerprint(
        place=norm.get("place"), bbox=norm.get("bbox"))


__all__ = [
    "SCOPE_FINGERPRINT_PREFIX",
    "ScopeStaleness",
    "scope_fingerprint",
    "evaluate_scope_staleness",
    "task_scope_from_dict",
    "scope_fingerprint_from_task_scope",
]
