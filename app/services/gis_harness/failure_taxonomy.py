"""Harness V5 统一失败分类与 typed remediation（ADR-0118 决策 D2）。

V4 基线的缺口（Phase-0 审计）：dispatch 面只有 4 个泛化 code
（VALIDATION_ERROR / NOT_FOUND / UNKNOWN_TOOL / TOOL_ERROR），planning
与 geocompute 各持一套 disjoint ``FailureClass``，CRS 失败逃逸为
pyproj 裸错被折叠进 generic TOOL_ERROR —— agent 拿不到可执行的修复
语义，只看到「工具坏了」。

V5 契约：
- **单一 harness 词汇**：``HarnessFailureClass`` 覆盖 tool/data/CRS/
  empty/stale-ref/renderer/timeout/partial/cancelled/budget 十一类；
  planning 与 geocompute 的既有枚举通过**适配器**（非替换）映射进来，
  两套原枚举照常工作 —— 不产生第二事实源。
- **typed remediation**：每类失败映射到确定性 ``RemediationAction``
  与**严格有界**重试预算（``max_attempts ≤ 3``，全表约束由测试钉死）；
  预算耗尽 → ``abort_with_disclosure``，杜绝无限 agent 循环。
- **进程级有界账本**：``RemediationLedger`` 以 (session, tool, class)
  记账尝试次数（LRU ≤512）。进程级 = 与 tool_metrics 聚合器同一诚实
  披露口径；会话级持久预算由 finalizer / runtime-repair 的既有账本
  （repair memory）负责，本模块不重复建账。
- 任何入口**绝不抛出**：分类失败 → ``UNKNOWN``，记录面不阻断业务。
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Literal, Optional, Tuple

RemediationAction = Literal[
    "retry",                # 同参重试（reload ref / 瞬态消除后）
    "retry_with_backoff",   # 有界退避重试（timeout / 瞬态）
    "fallback_tool",        # 换等价工具（empty result / 不可用）
    "substitute_operator",  # 换操作符/投影路径（CRS 失败）
    "requery_profile",      # 深挖 DatasetProfile 确认数据事实
    "replan",               # 重新规划剩余步骤
    "reobserve",            # 重新观测（渲染/部分完成后先取证）
    "abort_with_disclosure",  # 预算耗尽/不可恢复 → 诚实部分完成
]


class HarnessFailureClass(str, Enum):
    """Harness V5 失败词汇（11 类；覆盖 Epic V5 Diagnose 域）。"""

    TOOL_ERROR = "tool_error"                # 工具/参数/环境执行失败
    DATA_ERROR = "data_error"                # 数据本身非法/缺失/科学不适用
    CRS_ERROR = "crs_error"                  # 坐标系/投影失败（含 pyproj）
    EMPTY_RESULT = "empty_result"            # 成功返回但无可用数据
    STALE_REF = "stale_ref"                  # 引用失效/过期（session ref 类）
    RENDERER_FAILURE = "renderer_failure"    # 渲染/瓦片/图层源失败
    TIMEOUT = "timeout"                      # 超时/瞬态网络/远端不可达
    PARTIAL_COMPLETION = "partial_completion"  # 半写/部分完成
    CANCELLED = "cancelled"                  # 取消（非故障）
    BUDGET_EXHAUSTED = "budget_exhausted"    # 预算/资源上限
    UNKNOWN = "unknown"                      # 未归类


#: 每类失败的确定性 remediation 与严格重试上限（全表 max ≤3 —— 测试钉死）。
REMEDIATION_POLICY: Dict[HarnessFailureClass, Tuple[RemediationAction, int]] = {
    HarnessFailureClass.TOOL_ERROR: ("retry_with_backoff", 3),
    HarnessFailureClass.DATA_ERROR: ("requery_profile", 1),
    HarnessFailureClass.CRS_ERROR: ("substitute_operator", 2),
    HarnessFailureClass.EMPTY_RESULT: ("fallback_tool", 2),
    HarnessFailureClass.STALE_REF: ("retry", 2),
    HarnessFailureClass.RENDERER_FAILURE: ("reobserve", 2),
    HarnessFailureClass.TIMEOUT: ("retry_with_backoff", 2),
    HarnessFailureClass.PARTIAL_COMPLETION: ("reobserve", 1),
    HarnessFailureClass.CANCELLED: ("abort_with_disclosure", 0),
    HarnessFailureClass.BUDGET_EXHAUSTED: ("abort_with_disclosure", 0),
    HarnessFailureClass.UNKNOWN: ("replan", 1),
}

# 消息级标记（小写子串；表内顺序即优先级，确定性）。
_CRS_MARKERS = (
    "crs", "pyproj", "epsg", "投影", "坐标系", "坐标参考",
    "coordinate system", "projection", "srs",
)
_TIMEOUT_MARKERS = (
    "timeout", "timed out", "connection", "network", "socket",
    "refused", "reset", "超时", "网络", "连接",
)
_STALE_REF_MARKERS = (
    "stale", "expired", "已过期", "已失效", "失效", "ref 不存在",
)
_RENDERER_MARKERS = (
    "render", "渲染", "瓦片", "tile", "图层源", "source load",
    "map source", "symbol", "样式应用",
)
_PARTIAL_MARKERS = ("partial", "部分成功", "部分完成", "半写", "incomplete")
_EMPTY_MARKERS = (
    "empty", "未返回任何", "无要素", "0 条", "零要素", "no features",
    "no data", "无数据", "没有数据",
)


def _has_any(markers: Tuple[str, ...], text: str) -> bool:
    return any(m in text for m in markers)


def _is_crs_exception(exception: Optional[Exception]) -> bool:
    if exception is None:
        return False
    names = {type(exception).__name__}
    mro = {c.__name__ for c in type(exception).__mro__}
    # InvalidCRS（scientific_errors）按限定名识别 —— review R1 #9：
    # 收口后的 typed 错误进入分类器必须走类型而非消息巧合。
    if "InvalidCRS" in mro or "InvalidCRS" in names:
        return True
    if names & {"CRSError", "ProjError", "CRSException"}:
        return True
    try:  # pyproj 可选依赖，懒探测
        from pyproj.exceptions import ProjError  # noqa: F401

        return isinstance(exception, (ProjError,))
    except Exception:  # noqa: BLE001
        return False


def classify_harness_failure(
    *,
    status: Optional[str] = None,
    code: Optional[str] = None,
    error_type: Optional[str] = None,
    message: Optional[str] = None,
    exception: Optional[Exception] = None,
    tool_name: Optional[str] = None,
) -> HarnessFailureClass:
    """分类一次 harness 失败（确定性；取消 > CRS > 超时 > stale >
    renderer > partial > empty > planning 委托 > UNKNOWN）。

    CRS 优先于 planning 委托：否则 pyproj 裸错会被折叠进
    validation/internal（V4 KNOWN-GAP 的泛化路径）。
    """
    try:
        from app.services.jobs.cancellation import OperationCancelled

        cancelled_exc = isinstance(exception, OperationCancelled)
    except Exception:  # noqa: BLE001
        cancelled_exc = False
    text_l = " ".join(
        str(x).lower() for x in (status, code, error_type, message) if x
    )
    type_l = (error_type or "").lower()
    code_l = (code or "").lower()

    if cancelled_exc or "cancelled" in (code_l + " " + type_l + " " + text_l):
        return HarnessFailureClass.CANCELLED
    if _is_crs_exception(exception) or _has_any(_CRS_MARKERS, text_l):
        return HarnessFailureClass.CRS_ERROR
    if (
        isinstance(exception, TimeoutError)
        or "timeout" in type_l
        or _has_any(_TIMEOUT_MARKERS, text_l)
    ):
        return HarnessFailureClass.TIMEOUT
    if _has_any(_STALE_REF_MARKERS, text_l):
        return HarnessFailureClass.STALE_REF
    if _has_any(_RENDERER_MARKERS, text_l):
        return HarnessFailureClass.RENDERER_FAILURE
    if _has_any(_PARTIAL_MARKERS, text_l):
        return HarnessFailureClass.PARTIAL_COMPLETION

    # 委托 planning 分类器处理 dispatch 细分（validation/missing_ref/
    # empty/no_data/auth/tool_unavailable/resource_limit/...），再适配。
    try:
        from app.services.planning.recovery import classify_error

        planning_fc = classify_error(
            status=status, code=code, error_type=error_type,
            message=message, exception=exception,
        )
    except Exception:  # noqa: BLE001 — 分类器缺席 → 标记面兜底
        planning_fc = None

    if planning_fc is not None:
        if _has_any(_EMPTY_MARKERS, text_l):
            return HarnessFailureClass.EMPTY_RESULT
        return from_planning_failure_class(planning_fc)

    if _has_any(_EMPTY_MARKERS, text_l):
        return HarnessFailureClass.EMPTY_RESULT
    return HarnessFailureClass.UNKNOWN


def from_planning_failure_class(planning_fc: Any) -> HarnessFailureClass:
    """planning ``FailureClass`` → harness 词汇（适配器，不改原枚举）。"""
    name = getattr(planning_fc, "value", str(planning_fc))
    return {
        "validation": HarnessFailureClass.TOOL_ERROR,
        "missing_ref": HarnessFailureClass.STALE_REF,
        "empty_result": HarnessFailureClass.EMPTY_RESULT,
        "no_data": HarnessFailureClass.DATA_ERROR,
        "auth": HarnessFailureClass.TOOL_ERROR,
        "tool_unavailable": HarnessFailureClass.TOOL_ERROR,
        "resource_limit": HarnessFailureClass.BUDGET_EXHAUSTED,
        "transient_network": HarnessFailureClass.TIMEOUT,
        "cancelled": HarnessFailureClass.CANCELLED,
        "internal": HarnessFailureClass.UNKNOWN,
    }.get(name, HarnessFailureClass.UNKNOWN)


def from_geocompute_failure_class(geo_fc: Any) -> HarnessFailureClass:
    """geocompute ``FailureClass`` → harness 词汇（适配器，不改原枚举）。"""
    name = getattr(geo_fc, "value", str(geo_fc))
    return {
        "transient_remote": HarnessFailureClass.TIMEOUT,
        "transient_db": HarnessFailureClass.TIMEOUT,
        "worker_loss": HarnessFailureClass.TOOL_ERROR,
        "retry_safe_compute": HarnessFailureClass.TOOL_ERROR,
        "partial_materialization": HarnessFailureClass.PARTIAL_COMPLETION,
        "deterministic_unsupported": HarnessFailureClass.TOOL_ERROR,
        "invalid_data": HarnessFailureClass.DATA_ERROR,
        "budget_exceeded": HarnessFailureClass.BUDGET_EXHAUSTED,
        "deadline_exceeded": HarnessFailureClass.TIMEOUT,
        "cancelled": HarnessFailureClass.CANCELLED,
        "scientific": HarnessFailureClass.DATA_ERROR,
    }.get(name, HarnessFailureClass.UNKNOWN)


@dataclass(frozen=True)
class RemediationDecision:
    """一次失败对应的 remediation 裁决（含严格预算余量）。"""

    failure_class: HarnessFailureClass
    action: RemediationAction
    attempts: int
    max_attempts: int
    retry_allowed: bool

    @property
    def exhausted(self) -> bool:
        return not self.retry_allowed

    def to_payload(self) -> Dict[str, Any]:
        return {
            "class": self.failure_class.value,
            "remediation": self.action,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "retry_allowed": self.retry_allowed,
        }


#: 账本条目 TTL（秒）：超过后计数衰减归零重计 —— review R1 #7：无衰减
#: 时同 (session, tool, class) 的历史失败会渗漏到未来的 turn（几天后
#: 一次失败立即 abort）。进程级口径不变，衰减只影响时间上远离的旧账。
LEDGER_TTL_S = 3600.0


class RemediationLedger:
    """进程级 (session, tool, class) → attempts 有界账本（LRU ≤ cap + TTL）。

    诚实口径：进程级（与 tool_metrics 聚合器一致）；会话级持久预算由
    finalizer / runtime-repair 的既有账本负责。任何失败不抛出。
    """

    def __init__(self, cap: int = 512, ttl_s: float = LEDGER_TTL_S):
        self._cap = cap
        self._ttl = ttl_s
        self._counts: "OrderedDict[Tuple[str, str, str], Any]" = OrderedDict()
        self._lock = threading.Lock()

    def record(self, key: Tuple[str, str, str]) -> int:
        now = time.monotonic()
        with self._lock:
            entry = self._counts.get(key)
            if entry is None or now - entry[1] > self._ttl:
                self._counts[key] = [1, now]  # [attempts, last_ts]
            else:
                entry[0] += 1
                entry[1] = now
                self._counts.move_to_end(key)
            while len(self._counts) > self._cap:
                self._counts.popitem(last=False)
            return self._counts[key][0]

    def attempts(self, key: Tuple[str, str, str]) -> int:
        with self._lock:
            entry = self._counts.get(key)
            return int(entry[0]) if entry else 0

    def reset(self, key: Optional[Tuple[str, str, str]] = None) -> None:
        with self._lock:
            if key is None:
                self._counts.clear()
            else:
                self._counts.pop(key, None)


_global_ledger = RemediationLedger()


def remediation_for(
    failure_class: HarnessFailureClass,
    *,
    attempts: int = 0,
) -> RemediationDecision:
    """按类取确定性 remediation；预算耗尽 → abort_with_disclosure。"""
    action, max_attempts = REMEDIATION_POLICY.get(
        failure_class, ("replan", 1))
    retry_allowed = attempts < max_attempts and failure_class not in (
        HarnessFailureClass.CANCELLED,
        HarnessFailureClass.BUDGET_EXHAUSTED,
    )
    if not retry_allowed and failure_class not in (
        HarnessFailureClass.CANCELLED,
        HarnessFailureClass.BUDGET_EXHAUSTED,
    ):
        action = "abort_with_disclosure"
    return RemediationDecision(
        failure_class=failure_class,
        action=action,  # type: ignore[arg-type]
        attempts=attempts,
        max_attempts=max_attempts,
        retry_allowed=retry_allowed,
    )


def classify_and_remediate(
    *,
    status: Optional[str] = None,
    code: Optional[str] = None,
    error_type: Optional[str] = None,
    message: Optional[str] = None,
    exception: Optional[Exception] = None,
    tool_name: str = "",
    session_id: str = "",
    ledger: Optional[RemediationLedger] = None,
) -> Dict[str, Any]:
    """dispatch seam 单点入口：分类 → 记账 → 裁决 → LLM 可读 payload。"""
    try:
        fc = classify_harness_failure(
            status=status, code=code, error_type=error_type,
            message=message, exception=exception,
        )
        led = ledger if ledger is not None else _global_ledger
        key = (session_id or "", tool_name or "", fc.value)
        attempts = led.record(key)
        return remediation_for(fc, attempts=attempts).to_payload()
    except Exception:  # noqa: BLE001 — 记录面绝不阻断业务
        return {
            "class": HarnessFailureClass.UNKNOWN.value,
            "remediation": "replan",
            "attempts": 0,
            "max_attempts": 1,
            "retry_allowed": True,
        }


__all__ = [
    "HarnessFailureClass",
    "RemediationAction",
    "RemediationDecision",
    "RemediationLedger",
    "REMEDIATION_POLICY",
    "classify_harness_failure",
    "classify_and_remediate",
    "from_planning_failure_class",
    "from_geocompute_failure_class",
    "remediation_for",
]
