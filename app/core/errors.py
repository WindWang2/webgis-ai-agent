"""平台错误分类学（Platform V4，ADR-0131 D3）。

跨系统的**单一**错误分类生产路径：任何模块抛出的异常都可以在这里得到
(category, retryable, http_status, user_message) 四元组，Harness/Workflow/
jobs 基于类型做恢复决策，而不是解析字符串。

与既有错误面的关系（无第二事实源）：

- ``app/models/api_response.ErrCode``：API 响应层业务码（VALIDATE_ERROR/
  NOT_FOUND/...），保持不动；分类学是它的**正交维度**（category 回答
  "这是什么类型的故障"，code 回答"接口层语义是什么"）。
- ``app/services/jobs/lifecycle.py``：job 状态机（retryable 只对 durable
  job 转移有效）；本模块的 retryable 语义对任意执行路径成立，jobs 的
  重试预算继续以状态机为准。
- ``app/extensions_platform/diagnostics.py`` DiagnosticCode：扩展平台域
  稳定码（append-only 纪律的参照系）；本词表同纪律：**只增不改名**。

红线：

- 词表封闭：``ErrorCategory`` 枚举外无裸字符串分类；分类映射对未知异常
  诚实落 ``permanent``（不猜、不静默 retry）。
- user_message 永远是固定短语，**绝不**内插异常原文（防信息泄露——
  生产响应路径由 exception.py 决定展示级别，但这里给的消息必须本身安全）。
- 分类永不抛异常：任何映射步骤失败都退回 permanent 兜底。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ErrorCategory(str, Enum):
    """跨系统错误分类（封闭词表，append-only）。

    `retryable` / `permanent` 是两个"只知道重试语义、无更具体域"的兜底
    类目——具体域（timeout 等）优先，它们服务"调用方只想声明重试意图"的
    场景。retryable 语义以 :data:`CATEGORY_DEFAULTS` 的 retryable 列为准。
    """

    VALIDATION = "validation"
    PERMISSION = "permission"
    DATA_UNAVAILABLE = "data_unavailable"
    CRS = "crs"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    TIMEOUT = "timeout"
    CANCELLATION = "cancellation"
    RETRYABLE = "retryable"
    PERMANENT = "permanent"
    DEPENDENCY_FAILURE = "dependency_failure"
    WORKER_LOST = "worker_lost"
    MODEL_FAILURE = "model_failure"
    RENDER_FAILURE = "render_failure"
    STORAGE_CORRUPTION = "storage_corruption"


@dataclass(frozen=True)
class CategorySpec:
    """类目默认裁决：是否可重试、对外 HTTP 状态、用户安全短语。"""

    retryable: bool
    http_status: int
    user_message: str


#: 类目默认表（封闭；新类目必须同时在此登记裁决）
CATEGORY_DEFAULTS: Dict[ErrorCategory, CategorySpec] = {
    ErrorCategory.VALIDATION: CategorySpec(False, 400, "请求参数不合法"),
    ErrorCategory.PERMISSION: CategorySpec(False, 403, "没有执行该操作的权限"),
    ErrorCategory.DATA_UNAVAILABLE: CategorySpec(False, 404, "所需数据不可用或不存在"),
    ErrorCategory.CRS: CategorySpec(False, 400, "坐标参考系不支持或无法转换"),
    ErrorCategory.RESOURCE_EXHAUSTED: CategorySpec(True, 429, "资源配额已耗尽，请稍后重试"),
    ErrorCategory.TIMEOUT: CategorySpec(True, 504, "操作超时，请稍后重试"),
    ErrorCategory.CANCELLATION: CategorySpec(False, 499, "操作已被取消"),
    ErrorCategory.RETRYABLE: CategorySpec(True, 503, "服务暂时不可用，请稍后重试"),
    ErrorCategory.PERMANENT: CategorySpec(False, 500, "服务器内部错误，请稍后重试"),
    ErrorCategory.DEPENDENCY_FAILURE: CategorySpec(True, 503, "依赖服务暂不可用，请稍后重试"),
    ErrorCategory.WORKER_LOST: CategorySpec(True, 503, "执行节点失联，任务将被重新调度"),
    ErrorCategory.MODEL_FAILURE: CategorySpec(True, 502, "模型服务异常，请稍后重试"),
    ErrorCategory.RENDER_FAILURE: CategorySpec(False, 500, "渲染失败，请调整参数后重试"),
    ErrorCategory.STORAGE_CORRUPTION: CategorySpec(False, 500, "存储数据损坏，请联系管理员"),
}


def category_defaults(category: ErrorCategory) -> CategorySpec:
    """类目裁决查询（未知类目退 permanent——枚举扩展漏登记时的兜底）。"""
    return CATEGORY_DEFAULTS.get(category, CATEGORY_DEFAULTS[ErrorCategory.PERMANENT])


def localized_user_message(
    classification_or_category: Any,
    locale: Optional[str] = None,
) -> str:
    """输出层入口（ADR-0144 P6）：类目/裁决 → 按 Accept-Language 的用户文案。

    委托 app.core.i18n（惰性导入避免环）；i18n 面缺失时回落分类学默认短语，
    与"分类永不抛"纪律一致。（此处与 i18n.localized_user_message 的兜底形状
    有意重复：本函数是 import 失败时的最后防线，不得反向依赖 i18n 面。）
    """
    try:
        from app.core.i18n import localized_user_message as _impl

        return _impl(classification_or_category, locale)
    except Exception:  # noqa: BLE001 — 输出层绝不抛
        cat = (
            classification_or_category
            if isinstance(classification_or_category, ErrorCategory)
            else getattr(classification_or_category, "category", ErrorCategory.PERMANENT)
        )
        return category_defaults(cat).user_message


def http_status_for(category: ErrorCategory) -> int:
    return category_defaults(category).http_status


# ── PlatformError ──────────────────────────────────────────────────────────

_MAX_CONTEXT_ENTRIES = 16
_MAX_CONTEXT_VALUE_CHARS = 256


def _bounded_context(context: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """有界化上下文（沿用 trace.bound_meta 的纪律：条目数/值长双上界）。"""
    if not context:
        return {}
    out: Dict[str, str] = {}
    for k, v in list(context.items())[:_MAX_CONTEXT_ENTRIES]:
        try:
            out[str(k)[:64]] = str(v)[:_MAX_CONTEXT_VALUE_CHARS]
        except Exception:  # noqa: BLE001 — __str__ 爆炸项跳过（review R1-m4）
            continue
    return out


class PlatformError(Exception):
    """平台统一错误基类：抛出点声明 category，恢复点按类型决策。

    任何域都可以继承它收窄默认裁决（如 GeocomputeError(PlatformError,
    category=TIMEOUT)），也可以直接抛 PlatformError。message 仅供日志/
    服务端诊断，``user_message`` 是唯一允许进生产响应的文案。
    """

    def __init__(
        self,
        message: str,
        *,
        category: ErrorCategory = ErrorCategory.PERMANENT,
        retryable: Optional[bool] = None,
        user_message: Optional[str] = None,
        degraded: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        http_status: Optional[int] = None,
    ):
        super().__init__(message)
        self.category = category
        spec = category_defaults(category)
        #: 显式覆盖 > 类目默认（如 TIMEOUT 类目下某个不可重试的调用点）
        self.retryable = spec.retryable if retryable is None else bool(retryable)
        self.user_message = user_message or spec.user_message
        #: 降级提示（degraded mode 建议，如 "chatengine" / "cache_only"）
        self.degraded = degraded
        self.context = _bounded_context(context)
        self.http_status = http_status or spec.http_status


# ── 分类（单一生产路径：任意异常 → 结构化裁决）────────────────────────────


@dataclass(frozen=True)
class ErrorClassification:
    """一次分类裁决（不可变；恢复决策的唯一输入）。"""

    category: ErrorCategory
    retryable: bool
    http_status: int
    user_message: str
    #: PlatformError 才有的降级提示（其余来源为 None）
    degraded: Optional[str] = None
    #: 是否来自 PlatformError 的显式声明（vs 类型推断）
    declared: bool = False


def classify_exception(exc: BaseException) -> ErrorClassification:
    """把任意异常映射为结构化裁决（永不抛、永不用字符串匹配异常消息）。

    已知类型的映射是**类型级**的（isinstance 链）；未知异常诚实落
    permanent（不猜 retryable——错误的重试比诚实的失败更贵）。
    可选依赖（httpx/sqlalchemy）惰性导入，缺席时跳过对应分支。
    """
    # 1) 显式声明优先
    if isinstance(exc, PlatformError):
        return ErrorClassification(
            category=exc.category,
            retryable=exc.retryable,
            http_status=exc.http_status,
            user_message=exc.user_message,
            degraded=exc.degraded,
            declared=True,
        )

    # 2) 取消语义（BaseException 的 CancelledError 也要能分类——恢复面
    #    需要区分"用户取消"与"真失败"；调用方自行决定是否吞）。
    if isinstance(exc, asyncio.CancelledError):
        return _from_category(ErrorCategory.CANCELLATION)
    try:
        from app.lib.cancellation import OperationCancelled

        if isinstance(exc, OperationCancelled):
            return _from_category(ErrorCategory.CANCELLATION)
    except Exception:  # noqa: BLE001 — 分类面绝不抛
        pass

    # 3) 类型级映射（顺序即优先级：特化在前，泛化在后）
    try:
        import httpx

        if isinstance(exc, httpx.TimeoutException):
            return _from_category(ErrorCategory.TIMEOUT)
        if isinstance(exc, (httpx.ConnectError, httpx.RemoteProtocolError,
                            httpx.ReadError, httpx.WriteError)):
            return _from_category(ErrorCategory.DEPENDENCY_FAILURE)
        if isinstance(exc, httpx.HTTPStatusError):
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if isinstance(status, int) and status in (429,) or (
                isinstance(status, int) and status >= 500
            ):
                return _from_category(ErrorCategory.DEPENDENCY_FAILURE)
            if isinstance(status, int) and status == 404:
                return _from_category(ErrorCategory.DATA_UNAVAILABLE)
            if isinstance(status, int) and status >= 400:
                return _from_category(ErrorCategory.VALIDATION)
        if isinstance(exc, httpx.InvalidURL):
            return _from_category(ErrorCategory.VALIDATION)
    except ImportError:
        pass

    try:
        from sqlalchemy import exc as sa_exc

        if isinstance(exc, sa_exc.TimeoutError):
            return _from_category(ErrorCategory.TIMEOUT)
        if isinstance(exc, sa_exc.OperationalError):
            # 连接抖动/锁等待是典型瞬时故障；语法/约束类 OperationalError
            # 少见且归属 DBA 面——诚实按 retryable 依赖故障上报。
            return _from_category(ErrorCategory.DEPENDENCY_FAILURE)
        if isinstance(exc, sa_exc.DataError):
            return _from_category(ErrorCategory.VALIDATION)
        if isinstance(exc, sa_exc.DatabaseError):
            return _from_category(ErrorCategory.DEPENDENCY_FAILURE)
    except ImportError:
        pass

    if isinstance(exc, PermissionError):
        return _from_category(ErrorCategory.PERMISSION)
    if isinstance(exc, FileNotFoundError):
        return _from_category(ErrorCategory.DATA_UNAVAILABLE)
    # HTTP 语义（HTTPException 及同形鸭子类型）：category 与既有 code 映射
    # 对齐（M5 表），保证响应里 code/category 两个字段讲同一个故事。
    try:
        _status = getattr(exc, "status_code", None)
    except Exception:  # noqa: BLE001 — property 爆炸不破坏"分类永不抛"
        _status = None
    if isinstance(_status, int) and 400 <= _status < 600:
        if _status == 429:
            return _from_category(ErrorCategory.RESOURCE_EXHAUSTED)
        if _status == 404:
            return _from_category(ErrorCategory.DATA_UNAVAILABLE)
        if _status in (401, 403):
            return _from_category(ErrorCategory.PERMISSION)
        if _status == 504:
            return _from_category(ErrorCategory.TIMEOUT)
        if 400 <= _status < 500:
            return _from_category(ErrorCategory.VALIDATION)
        return _from_category(ErrorCategory.PERMANENT)
    if isinstance(exc, (TimeoutError,)):  # 内置超时（含 asyncio.timeout）
        return _from_category(ErrorCategory.TIMEOUT)
    if isinstance(exc, ConnectionError):
        return _from_category(ErrorCategory.DEPENDENCY_FAILURE)
    if isinstance(exc, MemoryError):
        return _from_category(ErrorCategory.RESOURCE_EXHAUSTED)
    if isinstance(exc, OSError):
        return _oserror_classification(exc)
    if isinstance(exc, ValueError):
        # 解析/参数类失败归 validation（调用方可修）；TypeError/KeyError
        # 通常是服务端 bug——诚实落 permanent，不指责调用方（review R1-m3）。
        return _from_category(ErrorCategory.VALIDATION)
    if isinstance(exc, (TypeError, KeyError, LookupError)):
        return _from_category(ErrorCategory.PERMANENT)

    return _from_category(ErrorCategory.PERMANENT)


def _from_category(category: ErrorCategory, **overrides: Any) -> ErrorClassification:
    spec = category_defaults(category)
    kwargs = {
        "category": category,
        "retryable": spec.retryable,
        "http_status": spec.http_status,
        "user_message": spec.user_message,
    }
    kwargs.update(overrides)
    return ErrorClassification(**kwargs)


def _oserror_classification(exc: OSError) -> ErrorClassification:
    errno = getattr(exc, "errno", None)
    if errno == 28:  # ENOSPC
        return _from_category(ErrorCategory.RESOURCE_EXHAUSTED)
    if errno in (13,):  # EACCES
        return _from_category(ErrorCategory.PERMISSION)
    if errno in (2,):  # ENOENT（部分平台不抛 FileNotFoundError）
        return _from_category(ErrorCategory.DATA_UNAVAILABLE)
    if errno in (5, 121):  # EIO / ERROR_IO_DEVICE
        return _from_category(ErrorCategory.STORAGE_CORRUPTION)
    return _from_category(ErrorCategory.PERMANENT)


# ── 重试决策（类型驱动，不解析字符串）────────────────────────────────────


@dataclass(frozen=True)
class RetryPolicy:
    """声明式重试预算。attempt 从 1 计数（第 attempt 次已失败后再问）。"""

    max_attempts: int = 3
    base_delay_s: float = 0.5
    max_delay_s: float = 8.0

    def delay_for(self, attempt: int) -> float:
        """指数退避 + 上界钳制（无 jitter——确定性纪律，调用方可加）。"""
        delay = self.base_delay_s * (2 ** max(0, attempt - 1))
        return min(delay, self.max_delay_s)


DEFAULT_RETRY_POLICY = RetryPolicy()


@dataclass(frozen=True)
class RetryDecision:
    """一次"失败了要不要重试"的裁决。"""

    category: ErrorCategory
    will_retry: bool
    #: 本次失败是第几次尝试（1 起）
    attempt: int
    #: 建议等待秒数（will_retry=False 时为 0.0）
    delay_s: float
    user_message: str


def decide_retry(
    exc: BaseException,
    attempt: int,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
) -> RetryDecision:
    """分类 + 预算裁决：Harness/Workflow/jobs 的统一重试判定点。

    语义：``attempt`` 是**已完成的失败次数**；will_retry=True 表示还应发起
    第 ``attempt+1`` 次。取消/不可重试类目/预算耗尽都会诚实说 no。
    """
    cls = classify_exception(exc)
    if attempt is None:
        raise ValueError("attempt is required and 1-based (got None)")
    attempt = max(1, int(attempt))
    if not cls.retryable or attempt >= policy.max_attempts:
        return RetryDecision(
            category=cls.category, will_retry=False, attempt=attempt,
            delay_s=0.0, user_message=cls.user_message,
        )
    return RetryDecision(
        category=cls.category, will_retry=True, attempt=attempt,
        delay_s=policy.delay_for(attempt), user_message=cls.user_message,
    )


__all__ = [
    "ErrorCategory",
    "CategorySpec",
    "CATEGORY_DEFAULTS",
    "category_defaults",
    "http_status_for",
    "PlatformError",
    "ErrorClassification",
    "classify_exception",
    "RetryPolicy",
    "DEFAULT_RETRY_POLICY",
    "RetryDecision",
    "decide_retry",
]
