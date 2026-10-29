"""Feature-edit 自动执行策略（owner-only，模型不可写）。

mode：
- confirm_all（默认）        —— 一切提交都需用户在提案卡上显式 approve；
- allow_safe_attribute       —— 「纯属性 + 校验零 findings + ≤50 要素」的
  safe plan 可由 Agent 工具直接提交；几何操作（move/snap/repair）恒不
  自动 —— 坐标变更必须显式确认（任务红线）。

存储在 map_state ``_feature_edit_policy``；仅 owner 经 REST 设置。
"""
from __future__ import annotations

from typing import Dict

from app.services.feature_edit.contracts import (
    DEFAULT_POLICY_MODE,
    POLICY_MODES,
    POLICY_STATE_KEY,
)
from app.services.session_data import session_data_manager


async def get_policy(session_id: str) -> Dict[str, str]:
    raw = await session_data_manager.get_state_field(session_id, POLICY_STATE_KEY)
    mode = raw.get("mode") if isinstance(raw, dict) else None
    if mode not in POLICY_MODES:
        return {"mode": DEFAULT_POLICY_MODE}
    return {"mode": mode}


async def set_policy(session_id: str, mode: str) -> Dict[str, str]:
    if mode not in POLICY_MODES:
        raise ValueError(f"unknown policy mode: {mode}")
    await session_data_manager.set_map_state_fields(session_id, {POLICY_STATE_KEY: {"mode": mode}})
    return {"mode": mode}


def policy_allows_auto_commit(mode: str, plan: Dict) -> bool:
    """Agent 工具直提门槛：policy 放行 且 plan.safe（纯属性安全面）。"""
    return mode == "allow_safe_attribute" and bool(plan.get("safe")) and bool(plan.get("committable"))
