"""GeoCompute 执行平面：类型化失败语义（ADR-0096 D2；ADR-0101 D5 分类）。

执行图中的失败必须是类型化的、可分类的：
- 预算超限 → :class:`BudgetExceededError`（附可行动的降本建议）
- 截止时间超限 → :class:`DeadlineExceededError`
- 协作式取消 → 复用 ``OperationCancelled``（lib 叶子原语）
- 节点失败 → :class:`NodeExecutionError`（携带 ``failure_class`` 分类；
  只有显式安全的类别才允许重试，见 ``RETRYABLE_FAILURE_CLASSES``）
- 未实现/未接线的能力 → :class:`UnsupportedOperationError`（诚实暴露，
  绝不假装支持 —— 「声明即契约」红线）。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from app.lib.cancellation import OperationCancelled


class FailureClass(str, Enum):
    """失败分类（ADR-0101 D5）：重试策略只对显式安全的类别生效。"""

    TRANSIENT_REMOTE = "transient_remote"        # 远端超时/5xx/限流
    TRANSIENT_DB = "transient_db"                # DB 连接抖动
    WORKER_LOSS = "worker_loss"                  # durable worker 死亡（stale）
    RETRY_SAFE_COMPUTE = "retry_safe_compute"    # 幂等纯计算失败
    PARTIAL_MATERIALIZATION = "partial_materialization"  # 半写物化（需清理）
    DETERMINISTIC_UNSUPPORTED = "deterministic_unsupported"  # 能力未接线
    INVALID_DATA = "invalid_data"                # 参数/输入数据非法
    BUDGET_EXCEEDED = "budget_exceeded"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    CANCELLED = "cancelled"
    SCIENTIFIC = "scientific"                    # 确定性科学/域失败（绝不重试）


#: 默认可重试类别（retry_transient_only=True 时的白名单）。
RETRYABLE_FAILURE_CLASSES = frozenset({
    FailureClass.TRANSIENT_REMOTE,
    FailureClass.TRANSIENT_DB,
    FailureClass.WORKER_LOSS,
    FailureClass.RETRY_SAFE_COMPUTE,
})

#: ``retry_transient_only=False``（显式声明可重放）时额外允许的类别。
EXTENDED_RETRYABLE_FAILURE_CLASSES = RETRYABLE_FAILURE_CLASSES | {
    FailureClass.PARTIAL_MATERIALIZATION,
}


class GeoComputeError(Exception):
    """GeoCompute 执行平面错误基类。``code`` 与 Data Fabric 错误码同一风格。"""

    code = "GEOCOMPUTE_ERROR"

    def __init__(self, message: str, *, details: Optional[dict[str, Any]] = None):
        self.details: dict[str, Any] = dict(details or {})
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "details": self.details}


class UnsupportedOperationError(GeoComputeError):
    """节点类别/操作在当前运行时没有已接线的执行器。诚实失败，绝无假装。"""

    code = "OPERATION_UNSUPPORTED"


class BudgetExceededError(GeoComputeError):
    """准入或执行期资源预算超限。``details["suggestions"]`` 给出降本路径。"""

    code = "RESOURCE_BUDGET_EXCEEDED"

    def __init__(self, message: str, *, suggestions: Optional[list[str]] = None, **kw):
        super().__init__(message, **kw)
        if suggestions:
            self.details["suggestions"] = suggestions


class DeadlineExceededError(GeoComputeError):
    """节点/计划 wall-clock 截止时间超限。"""

    code = "DEADLINE_EXCEEDED"


class AuthorizationError(GeoComputeError):
    """调用者对该资源/操作没有授权（数据平面内：目录项可见性、run 归属等）。

    数据平面只做「允许/拒绝」的类型化判定，不决定 HTTP 状态码 —— REST 层
    负责把 deny 映射为 404（而非 403），避免跨租户行存在性预言机（与
    data_fabric 路由同一约定）。
    """

    code = "AUTHORIZATION_DENIED"


class NodeExecutionError(GeoComputeError):
    """节点执行失败。

    ``retry_safe=True`` 仅用于可安全重放的 transient 失败（远端超时、连接
    重置等）；副作用类失败（已写入产物、已 materialize）必须 False。
    ``failure_class`` 是细粒度分类（ADR-0101 D5）；未给出时由 ``retry_safe``
    推导（True → RETRY_SAFE_COMPUTE，False → SCIENTIFIC）。
    """

    code = "NODE_FAILED"

    def __init__(
        self,
        message: str,
        *,
        retry_safe: bool = False,
        failure_class: Optional[FailureClass] = None,
        node_id: Optional[str] = None,
        **kw,
    ):
        super().__init__(message, **kw)
        self.retry_safe = retry_safe
        if failure_class is None:
            failure_class = (
                FailureClass.RETRY_SAFE_COMPUTE if retry_safe else FailureClass.SCIENTIFIC
            )
        self.failure_class = FailureClass(failure_class)
        if node_id:
            self.details["node_id"] = node_id


def classify_failure(exc: BaseException) -> FailureClass:
    """把执行期异常映射为失败分类（重试决策与证据共用同一份真值）。"""
    if isinstance(exc, OperationCancelled):
        return FailureClass.CANCELLED
    if isinstance(exc, BudgetExceededError):
        return FailureClass.BUDGET_EXCEEDED
    if isinstance(exc, DeadlineExceededError):
        return FailureClass.DEADLINE_EXCEEDED
    if isinstance(exc, UnsupportedOperationError):
        return FailureClass.DETERMINISTIC_UNSUPPORTED
    if isinstance(exc, NodeExecutionError):
        return exc.failure_class
    if isinstance(exc, GeoComputeError):
        return FailureClass.SCIENTIFIC
    return FailureClass.INVALID_DATA


def wrap_unexpected(exc: Exception, *, node_id: str) -> Exception:
    """把算子抛出的未知异常收编为类型化节点失败。

    已是类型化失败（GeoComputeError 家族，含 Unsupported/Budget/Deadline）
    或协作式取消的原样透传 —— 类型即语义，绝不重新打包。
    """
    if isinstance(exc, (GeoComputeError, OperationCancelled)):
        return exc
    return NodeExecutionError(
        f"{type(exc).__name__}: {exc}", retry_safe=False, node_id=node_id
    )
