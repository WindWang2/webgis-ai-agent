"""Workflow Runtime V6 —— 节点重试策略与错误分类（严格语义）。

单一分类纪律：workflow 层不新造错误本体 —— geocompute 的
``FailureClass``（ADR-0101 D5）是执行面失败分类的真相，本模块把 workflow
层自产错误码（超时/驱动异常）与 geocompute ``failure_class`` 投影到同一个
「可重试/不可重试」裁决。

语义（Phase C 验收面）：
- retryable：瞬时类（超时/驱动崩溃/DB busy/worker 丢失/远端抖动）；
- non-retryable：确定性类（算子未接线/输入截断/绑定阻断/取消/科学失败）；
- 未知错误码**保守不重试**（fail-closed —— 盲目重试非幂等副作用更危险）；
- 退避：指数 + 比例抖动（jitter），有界上界；退避门持久化在节点行
  ``next_ready_at``（crash-safe：重启后退避门仍然生效）。
"""
from __future__ import annotations

import random
from typing import Optional

from pydantic import BaseModel, Field

from app.services.workflow_runtime import contracts as C


#: workflow 层自产错误码 → 可重试（瞬时类；重试 = 新 attempt 重执行）。
RETRYABLE_ERROR_CODES: frozenset = frozenset({
    "NODE_TIMEOUT",          # 本模块：per-node 超时（wait_for 截断）
    "NODE_EXCEPTION",        # driver 捕获的未预期异常（进程内 worker 崩溃等价）
    "DB_BUSY",               # store：SQLite busy（事务未提交，安全重试）
    "WORKER_LOSS",           # 分布式通道：执行 worker 死亡
    "ATTEMPT_INTERRUPTED",   # 分布式通道：attempt 被接管/中断
})

#: workflow 层自产错误码 → 不可重试（确定性失败；重试只会复现同一结果）。
NON_RETRYABLE_ERROR_CODES: frozenset = frozenset({
    "NODE_NOT_EXECUTABLE",       # 适配器：无已接线算子（编译期问题）
    "INPUT_TRUNCATED",           # 适配器：内联输入超上界（需上游物化）
    "MISSING_BINDING",           # 绑定门：data 角色无绑定
    "BINDING_BLOCKED",           # 绑定门：typed port 校验阻断
    "SUBWORKFLOW_UNSUPPORTED",   # 子工作流执行器缺席
    "SUBWORKFLOW_DEPTH",         # 深度越界（确定性）
    "SUBWORKFLOW_CYCLE",         # 环（确定性）
    "SUBWORKFLOW_CAP",           # 每 owner 活跃子实例上限
    "CANCELLED",                 # 取消不是失败，更不可"重试"
    "RESOURCE_BUDGET_EXCEEDED",  # 预算超限：重试只会再超
    "AUTHORIZATION_DENIED",      # 授权：确定性拒绝
})


def _retryable_failure_classes() -> frozenset:
    """geocompute RETRYABLE_FAILURE_CLASSES 的值集（延迟导入防环）。"""
    from app.services.geocompute.errors import RETRYABLE_FAILURE_CLASSES

    return frozenset(fc.value for fc in RETRYABLE_FAILURE_CLASSES)


def error_retryable(error_code: str, failure_class: str = "") -> bool:
    """错误码 → 可重试裁决（纯函数；保守 fail-closed）。

    - 词表命中优先；
    - 否则 geocompute failure_class 白名单；
    - 未知 → False（绝不盲目重试）。
    """
    code = str(error_code or "")
    if code in NON_RETRYABLE_ERROR_CODES:
        return False
    if code in RETRYABLE_ERROR_CODES:
        return True
    if failure_class:
        return str(failure_class) in _retryable_failure_classes()
    return False


class RetryPolicy(BaseModel):
    """节点重试策略（有界；持久化退避门在节点行 ``next_ready_at``）。

    ``max_attempts`` 与 ``contracts.MAX_NODE_ATTEMPTS`` 上界一致 —— attempts
    计数含**所有**执行路径（执行/复用不计/恢复复位不重置 —— 恶性循环
    防护：崩溃重启不重新获得完整预算）。
    """

    max_attempts: int = Field(default=2, ge=1, le=C.MAX_NODE_ATTEMPTS)
    base_delay_s: float = Field(default=0.5, gt=0, le=60)
    factor: float = Field(default=2.0, ge=1.0, le=10)
    max_delay_s: float = Field(default=8.0, gt=0, le=300)
    jitter_ratio: float = Field(default=0.2, ge=0, le=1)

    def delay_for(self, attempts_done: int, *,
                  rng: Optional[random.Random] = None) -> float:
        """第 N 次失败后的退避（指数 + 比例抖动；有界）。

        attempts_done=1 → base；=2 → base*factor …钳到 max_delay_s。
        """
        rng = rng or random.Random()
        expo = float(self.base_delay_s) * (self.factor ** max(0, attempts_done - 1))
        delay = min(expo, float(self.max_delay_s))
        jitter = delay * float(self.jitter_ratio)
        return max(0.0, delay + rng.uniform(-jitter, jitter))

    def attempts_exhausted(self, attempts: int) -> bool:
        return int(attempts) >= int(self.max_attempts)


def default_policy() -> RetryPolicy:
    """进程级默认策略（环境变量可调；单实例内一致）。"""
    import os

    def _int(key: str, default: int) -> int:
        try:
            return int(os.getenv(key, str(default)))
        except (TypeError, ValueError):
            return default

    def _float(key: str, default: float) -> float:
        try:
            return float(os.getenv(key, str(default)))
        except (TypeError, ValueError):
            return default

    return RetryPolicy(
        max_attempts=max(1, min(_int(
            "GIS_WORKFLOW_RETRY_MAX_ATTEMPTS", 2), C.MAX_NODE_ATTEMPTS)),
        base_delay_s=_float("GIS_WORKFLOW_RETRY_BASE_DELAY_S", 0.5),
        max_delay_s=_float("GIS_WORKFLOW_RETRY_MAX_DELAY_S", 8.0),
    )
