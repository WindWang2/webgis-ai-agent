"""仿真子域错误层次（ADR-0192 D8）。

全部继承平台统一基类 :class:`app.core.errors.PlatformError`，携带稳定
``code``；``user_message`` 是固定短语（绝不内插异常原文）。工具面捕获
域异常转 ``std_error_response``，绝不让异常栈泄漏给 LLM。
"""

from app.core.errors import ErrorCategory, PlatformError


class SimulationError(PlatformError):
    """仿真域错误基类。"""

    code = "SIMULATION_ERROR"

    def __init__(
        self,
        message: str,
        *,
        category: ErrorCategory = ErrorCategory.PERMANENT,
        retryable=None,
        user_message: str = "时空仿真推演执行失败，请稍后重试或调整参数。",
        context=None,
        **kwargs,
    ):
        super().__init__(
            message,
            category=category,
            retryable=retryable,
            user_message=user_message,
            context=context,
            **kwargs,
        )


class SimulationConfigError(SimulationError):
    """参数 / 配置不合法（网格形状、边引用、步长组合等）。"""

    code = "SIMULATION_CONFIG_INVALID"

    def __init__(self, message: str, *, context=None, **kwargs):
        super().__init__(
            message,
            category=ErrorCategory.VALIDATION,
            user_message="仿真参数不合法，请检查网格、路网或步长配置。",
            context=context,
            **kwargs,
        )


class SimulationStabilityError(SimulationError):
    """数值稳定域 / 模型有效性包络违例（CFL 型守卫，附 dt_max 证据）。"""

    code = "SIMULATION_STABILITY_VIOLATED"

    def __init__(self, message: str, *, context=None, **kwargs):
        super().__init__(
            message,
            category=ErrorCategory.VALIDATION,
            user_message="时间步长超出数值稳定域，请减小 dt_seconds 后重试。",
            context=context,
            **kwargs,
        )


class SimulationStateError(SimulationError):
    """状态完整性破坏（守恒对账失败 / 非有限值）—— fail-loud，绝不静默。"""

    code = "SIMULATION_STATE_CORRUPT"

    def __init__(self, message: str, *, context=None, **kwargs):
        super().__init__(
            message,
            category=ErrorCategory.PERMANENT,
            user_message="仿真状态校验失败，推演已终止。",
            context=context,
            **kwargs,
        )


class SimulationCancelledError(SimulationError):
    """用户取消语义（区别于真失败）。"""

    code = "SIMULATION_CANCELLED"

    def __init__(self, message: str = "simulation cancelled by user", **kwargs):
        super().__init__(
            message,
            category=ErrorCategory.CANCELLATION,
            user_message="仿真推演已被取消。",
            **kwargs,
        )


class SimulationBudgetError(SimulationError):
    """墙钟预算耗尽（诚实终止，不输出半截结果）。"""

    code = "SIMULATION_BUDGET_EXCEEDED"

    def __init__(self, message: str, *, context=None, **kwargs):
        super().__init__(
            message,
            category=ErrorCategory.TIMEOUT,
            user_message="仿真推演超出执行预算，请减小步数或网格规模。",
            context=context,
            **kwargs,
        )


class InvalidSimulationTransition(SimulationError):
    """生命周期非法迁移（如 completed → running）。"""

    code = "SIMULATION_INVALID_TRANSITION"

    def __init__(self, message: str, *, context=None, **kwargs):
        super().__init__(
            message,
            category=ErrorCategory.PERMANENT,
            user_message="仿真任务状态不允许该操作。",
            context=context,
            **kwargs,
        )
