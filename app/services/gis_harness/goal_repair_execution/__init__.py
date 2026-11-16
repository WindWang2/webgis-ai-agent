"""G42 —— 有界目标补全执行（goal repair execution）。

把 finalizer 生成的 ``goal_repair_plan``（此前仅披露）接成完整用户闭环：
预览 → 授权 → 有限动作执行（统一派发/既有服务）→ 真实证据 → 原终验
重新裁决。不新建 agent loop / 调度器 / 事实源 —— attempt 是 durable job
读模型，verdict 仍由既有 goal_satisfaction evaluator 单一判定。
"""
from .contracts import (  # noqa: F401
    GoalRepairErrorCode,
    TASK_TYPE,
    JOB_KIND,
)
from .service import (  # noqa: F401
    build_repair_preview,
    start_repair_attempt,
    get_attempt_status,
    cancel_repair_attempt,
)

__all__ = [
    "GoalRepairErrorCode",
    "TASK_TYPE",
    "JOB_KIND",
    "build_repair_preview",
    "start_repair_attempt",
    "get_attempt_status",
    "cancel_repair_attempt",
]
