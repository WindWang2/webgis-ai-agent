"""Feature-edit 服务包（/goal 03）：持久数据要素编辑的预览/提交/撤销闭环。

- contracts   词表/finding codes/边界（单一登记点）
- journal     纯函数 journal（commit 与 undo/redo 同路径）
- identity    `_wfid` 稳定身份物化与目标解析
- attr_ops    属性编辑校验（类型/枚举，证据来自数据集自身）
- geometry_ops 几何编辑算子（repair/move/snap；米制显式，fail-closed）
- plan        EditPlan 构建（preview）与 derive_changes（commit 复用）
- executor    唯一写路径（锁透传/CAS/幂等/journal/盖章/广播）
- policy      owner-only 自动执行策略
- mapspec_stamp MapSpec 新鲜度盖章调用面
"""
from app.services.feature_edit.contracts import FINDING_CODES, WFID_PROP
from app.services.feature_edit.executor import (
    FeatureEditBlocked,
    FeatureEditConflict,
    apply_redo,
    apply_undo,
    commit_plan,
)

__all__ = [
    "FINDING_CODES",
    "WFID_PROP",
    "FeatureEditBlocked",
    "FeatureEditConflict",
    "commit_plan",
    "apply_undo",
    "apply_redo",
]
