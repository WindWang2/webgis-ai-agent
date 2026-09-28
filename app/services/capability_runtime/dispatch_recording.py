"""dispatch → provider 健康台账的结果记录面(H05)。

ToolDispatchService 是全部 agent 路径的唯一调度咽喉(F06 供给先例);
本模块把「这次执行成功/失败/失败原因」喂给 provider 健康状态机,让下一轮
resolver 排序与 bind 执法看到的是**本进程刚发生的运行时事实**。

纪律:
- **记录绝不阻断调度**:本模块任何异常都被吞掉(记录面故障 = 无记录,
  不是执行失败);
- **typed 分类**:credential/policy 类失败不计熔断(上下文问题,不是
  provider-down 证据);timeout/transient 计入;取消只释放半开名额;
- **trial 消费点**::func:`provider_allow` 只在实际执行包装处调用 ——
  bind 的 OPEN 拒绝(只读 state)与后续早退路径(guardrail/复用)都
  不占半开名额,泄漏类问题在结构上不存在;
- kill switch ``GIS_PROVIDER_HEALTH`` =0 → 记录与执法双关。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "PROVIDER_UNAVAILABLE_CODE",
    "PROVIDER_UNAVAILABLE_KEY",
    "provider_allow",
    "release_provider_trial",
    "record_dispatch_outcome",
]

#: 断路 fail-fast 的对外错误码(retryable;语义同 DF SourceUnreachableError)。
PROVIDER_UNAVAILABLE_CODE = "PROVIDER_UNAVAILABLE"
PROVIDER_UNAVAILABLE_KEY = "provider_unavailable"


def _enabled() -> bool:
    try:
        from app.services.capability_runtime.health import provider_health_enabled

        return provider_health_enabled()
    except Exception:  # noqa: BLE001
        return False


def _registry():
    from app.services.capability_runtime.health import get_provider_health_registry

    return get_provider_health_registry()


def provider_key_for_tool(tool_name: str) -> str:
    return f"tool:{str(tool_name or '').strip()[:128]}"


def provider_allow(tool_name: str) -> bool:
    """执行前断路裁决(True = 放行;False = fail-fast)。

    HALF_OPEN 下此调用消耗 trial 名额 —— 必须与 record_dispatch_outcome /
    release_provider_trial 配对。kill switch / registry 缺席 → 放行。
    """
    if not _enabled():
        return True
    try:
        return _registry().allow(provider_key_for_tool(tool_name))
    except Exception:  # noqa: BLE001 — 健康面故障绝不阻断调度
        return True


def release_provider_trial(tool_name: str) -> None:
    """取消/跳过路径的 trial 名额确定性释放(幂等)。"""
    if not _enabled():
        return
    try:
        _registry().release_trial(provider_key_for_tool(tool_name))
    except Exception:  # noqa: BLE001
        pass


def _is_error_like_result(result: Any) -> bool:
    """与 dispatch 折叠口径同源的错误形状识别(#529/#589 族)。

    复用 ``llm_result_formatter.is_error_like_result``(单点权威),叠加上
    本面的 ``success is False`` 显式信号 —— 记账必须与 dispatch 的失败
    折叠同口径,否则 error-shape 失败会被记成 success(清零熔断计数,
    系统性低估故障 —— review P2-1)。
    """
    if isinstance(result, dict) and result.get("success") is False:
        return True
    try:
        from app.services.llm_result_formatter import is_error_like_result

        return bool(is_error_like_result(result))
    except Exception:  # noqa: BLE001 — 权威面缺席退化为显式信号
        return False


def record_dispatch_outcome(
    tool_name: str,
    *,
    started_at: Optional[float] = None,
    result: Any = None,
    exc: Optional[BaseException] = None,
) -> None:
    """回填一次执行结果(成功/typed 失败;绝不抛)。

    ``result`` 是 registry.dispatch 的工具结果 dict。错误形状识别与
    dispatch 折叠同源(:func:`_is_error_like_result`);失败按
    ``code``/``error_type``/``error``/``message``/``summary`` 走 typed
    分类 —— 仅 timeout/transient 证据计入熔断,其余失败只释放半开
    trial(工具语义失败 ≠ provider down,保守默认)。
    """
    if not _enabled() or not str(tool_name or "").strip():
        return
    key = provider_key_for_tool(tool_name)
    latency_ms: Optional[float] = None
    if started_at is not None:
        try:
            latency_ms = max(0.0, (time.monotonic() - started_at) * 1000.0)
        except Exception:  # noqa: BLE001
            latency_ms = None
    try:
        registry = _registry()
        if exc is not None:
            from app.services.capability_runtime.health import (
                ProviderFailureClass,
                classify_failure,
            )

            registry.record_failure(key, classify_failure(exc=exc))
            return
        if _is_error_like_result(result):
            from app.services.capability_runtime.health import (
                ProviderFailureClass,
                classify_failure,
            )

            result_dict = result if isinstance(result, dict) else {}
            failure_class = classify_failure(
                error_code=str(result_dict.get("code")
                                or result_dict.get("error_type") or ""),
                error_msg=str(
                    result_dict.get("error")
                    or result_dict.get("message")
                    or result_dict.get("summary")
                    or "")[:240],
            )
            # 未知错误码的失败结果不计熔断(工具语义失败 ≠ provider down;
            # 只有 typed timeout/transient 证据才触发断路 —— 保守默认)。
            if failure_class in (
                ProviderFailureClass.TIMEOUT,
                ProviderFailureClass.TRANSIENT,
            ):
                registry.record_failure(key, failure_class)
            else:
                registry.release_trial(key)
            return
        registry.record_success(key, latency_ms)
    except Exception:  # noqa: BLE001 — 记录面绝不阻断调度
        logger.debug("[provider-health] record failed for %s", key, exc_info=True)
