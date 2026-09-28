"""GISActionPlan 语义 diff（H10 / ADR-0217）。

回答四类问题：新增了什么 / 删除了什么 / 哪些动作参数变了 / 哪些动作
副作用语义变了。纯函数、确定性、有界输出 —— diff 本身可进 receipt 与
replay（不携带数据本体，只比较 IR 语义字段）。

判等基准 = action 指纹（含 params/side_effect/tool 等全部语义字段）；
param_changed 额外给出**变化的参数键名**（调参 token 小而 typed，键级
diff 才能回答"把分级数 5 改成 7 改了什么"）。
"""
from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field

from app.lib.gis.action_ir import GISAction, GISActionPlan

__all__ = ["ParamChange", "ActionPlanDiff", "diff_plans", "plans_equal"]

_MAX_LISTED = 32
_PARAM_KEY_MAX = 48


class ParamChange(BaseModel):
    """一个动作的参数级变化（只列键名；新旧值指纹级披露）。"""

    model_config = ConfigDict(frozen=True)

    key: str = Field(min_length=1, max_length=_PARAM_KEY_MAX)
    before_digest: str = Field(default="", max_length=24)
    after_digest: str = Field(default="", max_length=24)


class ActionPlanDiff(BaseModel):
    """两份 ActionPlan 的语义 diff（有界；可序列化进 receipt）。"""

    model_config = ConfigDict(frozen=True)

    before_plan_id: str = Field(default="", max_length=128)
    after_plan_id: str = Field(default="", max_length=128)
    before_fingerprint: str = Field(default="", max_length=80)
    after_fingerprint: str = Field(default="", max_length=80)

    added: List[str] = Field(default_factory=list)      # action_id
    removed: List[str] = Field(default_factory=list)    # action_id
    param_changed: Dict[str, List[ParamChange]] = Field(default_factory=dict)
    side_effect_changed: Dict[str, List[str]] = Field(default_factory=dict)  # id → [before, after]
    tool_changed: Dict[str, List[str]] = Field(default_factory=dict)         # id → [before, after]
    reordered: bool = False

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.param_changed
                    or self.side_effect_changed or self.tool_changed
                    or self.reordered)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()

    def describe(self) -> str:
        """有界中文摘要（LLM/日志视野；不是执行契约）。"""
        parts: List[str] = []
        if self.added:
            parts.append(f"新增 {len(self.added)} 动作")
        if self.removed:
            parts.append(f"删除 {len(self.removed)} 动作")
        if self.param_changed:
            keys = sorted({c.key for changes in self.param_changed.values()
                           for c in changes})[:8]
            parts.append(f"参数变化 {list(self.param_changed)[:6]} keys={keys}")
        if self.side_effect_changed:
            parts.append(f"副作用变化 {list(self.side_effect_changed)[:6]}")
        if self.tool_changed:
            parts.append(f"工具变化 {list(self.tool_changed)[:6]}")
        if self.reordered:
            parts.append("顺序变化")
        return "；".join(parts) if parts else "无语义变化"


def _param_changes(before: GISAction, after: GISAction) -> List[ParamChange]:
    from app.lib.gis.action_ir import digest_of

    changes: List[ParamChange] = []
    for key in sorted(set(before.params) | set(after.params))[:_MAX_LISTED]:
        b = before.params.get(key)
        a = after.params.get(key)
        # 判等与指纹同口径（canonical digest）—— `5 == 5.0` 在 Python 为真
        # 但指纹不同：diff 必须报告，否则"无语义变化"会吞掉真实重算。
        if key in before.params and key in after.params \
                and digest_of(b) == digest_of(a):
            continue
        changes.append(ParamChange(
            key=str(key)[:_PARAM_KEY_MAX],
            before_digest=digest_of(b)[:24] if key in before.params else "",
            after_digest=digest_of(a)[:24] if key in after.params else "",
        ))
    return changes


def diff_plans(before: GISActionPlan, after: GISActionPlan) -> ActionPlanDiff:
    """两份 ActionPlan 的确定性语义 diff（纯函数）。"""
    before_by_id = {a.action_id: a for a in before.actions}
    after_by_id = {a.action_id: a for a in after.actions}

    added = [a.action_id for a in after.actions if a.action_id not in before_by_id][:_MAX_LISTED]
    removed = [a.action_id for a in before.actions if a.action_id not in after_by_id][:_MAX_LISTED]

    param_changed: Dict[str, List[ParamChange]] = {}
    side_effect_changed: Dict[str, List[str]] = {}
    tool_changed: Dict[str, List[str]] = {}
    for action in after.actions:
        if action.action_id not in before_by_id:
            continue
        prev = before_by_id[action.action_id]
        if prev.action_fingerprint() == action.action_fingerprint():
            continue
        changes = _param_changes(prev, action)
        if changes:
            param_changed[action.action_id] = changes
        if prev.side_effect != action.side_effect:
            side_effect_changed[action.action_id] = [
                prev.side_effect, action.side_effect]
        if (prev.tool or "") != (action.tool or ""):
            tool_changed[action.action_id] = [
                (prev.tool or "")[:96], (action.tool or "")[:96]]

    # 顺序判定：共同动作的相对声明序是否保持（不比较绝对位置 —— 插入
    # 新动作不算 reorder）。
    common_before = [a.action_id for a in before.actions if a.action_id in after_by_id]
    common_after = [a.action_id for a in after.actions if a.action_id in before_by_id]
    reordered = common_before != common_after

    return ActionPlanDiff(
        before_plan_id=before.plan_id,
        after_plan_id=after.plan_id,
        before_fingerprint=before.plan_fingerprint(),
        after_fingerprint=after.plan_fingerprint(),
        added=added,
        removed=removed,
        param_changed=param_changed,
        side_effect_changed=side_effect_changed,
        tool_changed=tool_changed,
        reordered=reordered,
    )


def plans_equal(before: GISActionPlan, after: GISActionPlan) -> bool:
    """语义判等（指纹级；plan_id 不同但语义相同 → True）。"""
    if len(before.actions) != len(after.actions):
        return False
    return all(
        b.action_fingerprint() == a.action_fingerprint()
        for b, a in zip(before.actions, after.actions))
