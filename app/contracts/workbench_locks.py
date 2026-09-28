"""Workbench 图层锁共享守卫（自 services/mapspec/lifecycle_engine.py 下沉，ADR-0216）。

跨层消费者：``app/services``（lifecycle 引擎/repair planner/render 观察）、
``app/api/routes/visual_repairs`` 与 ``app/lib/cartography``
（quality_loop / runtime_repair 的 locked_refused 披露）。
纯函数 + 单码词表，零 app 内依赖；单码契约对齐前端
``frontend LOCK_CONFLICT_ERROR = 'layer_locked'``（见 LOCK_CONFLICT_CODE 注释）。
"""
from __future__ import annotations

from typing import Any, Dict, FrozenSet, List, Optional

# 锁冲突披露词：对齐前端 failed/layer_locked（前端 LOCK_CONFLICT_ERROR =
# 'layer_locked'，ack.error 机器可读；后端权威拒绝必须携带同一 token）。
# 单码契约（B/Q1 结论）：前端无 component_locked 消费端（仅识别
# layer_locked），组件锁拒绝复用 LOCK_CONFLICT_CODE，载荷
# locked_component_ids 指明被锁组件 —— 不引入前端无法识别的第二码。
LOCK_CONFLICT_CODE = "layer_locked"
# 锁集读取上界（与 repair_planner 既有 [:64] 口径一致，有界披露）。
_MAX_LOCK_IDS = 64


def locked_layer_ids_of(mapspec: Optional[Dict[str, Any]]) -> List[str]:
    """workbench doc lockedLayerIds 读取（缺席/非法 → 空，不过度承诺）。"""
    if not isinstance(mapspec, dict):
        return []
    wb = mapspec.get("workbench")
    if not isinstance(wb, dict):
        return []
    locked = wb.get("lockedLayerIds", [])
    if not isinstance(locked, list):
        return []
    return [x for x in locked[:_MAX_LOCK_IDS] if isinstance(x, str) and x]


def locked_component_ids_of(mapspec: Optional[Dict[str, Any]]) -> List[str]:
    """workbench doc lockedComponentIds 读取（缺席=空，版本兼容）。"""
    if not isinstance(mapspec, dict):
        return []
    wb = mapspec.get("workbench")
    if not isinstance(wb, dict):
        return []
    locked = wb.get("lockedComponentIds", [])
    if not isinstance(locked, list):
        return []
    return [x for x in locked[:_MAX_LOCK_IDS] if isinstance(x, str) and x]


def _lock_matches(locked_id: str, target_id: str) -> bool:
    """锁命中判定（含层族语义，与 _should_remove_layer 谓词一致）。

    精确命中 + 双向族前缀（locked 存逻辑层、目标是物理层，或反之）——
    任一方向命中即拒绝，不留「换个 id 拼法绕过用户锁」的缺口。
    """
    if not isinstance(locked_id, str) or not target_id:
        return False
    if locked_id == target_id:
        return True
    for sep in ("-", "__"):
        if target_id.startswith(f"{locked_id}{sep}"):
            return True
        if locked_id.startswith(f"{target_id}{sep}"):
            return True
    return False


def is_entity_locked(entity: str, locked_entities: FrozenSet[str]) -> bool:
    """共享锁命中谓词（repair planner 复用，不各自手写锁判断）。"""
    if not entity:
        return False
    return any(_lock_matches(locked, entity) for locked in locked_entities)


__all__ = [
    "LOCK_CONFLICT_CODE",
    "locked_layer_ids_of",
    "locked_component_ids_of",
    "is_entity_locked",
]
