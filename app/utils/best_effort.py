"""best_effort —— 「绝不阻断」旁路操作的统一降级面（issue #1549）。

仓库里有大量 ``except Exception: pass`` 形态的兜底（CODE_REVIEW.md 不变量 #4
「Exception As Thought」）：多数带 ``# noqa: BLE001 — 绝不阻断…`` 意图注释，语义
是刻意的——记录面 / 证据面 / 增值面的失败不允许阻塞主链路。但静默吞掉意味着故障
发生时没有任何可观测痕迹，degraded evidence 凭空消失。

本模块把该习语收敛成一个包装：**保留「绝不阻断」语义**（捕获 ``Exception`` 后吞掉、
绝不向调用方上抛），同时让每次吞掉变成可见降级证据：

1. ``logger.warning`` 一行：``[<code>] <op> skipped: <exc>``（带 exc_info 与可选
   有界 ctx）；``code`` 由调用方传入的稳定字符串（如 ``tool-dispatch-<op>-best-effort``），
   供日志检索与告警规则锚定；
2. 可选 turn 级证据计数：活跃 turn 内经 ``app.lib.runtime.evidence`` 的
   ``add_warning(code, detail)`` 计入 bounded warnings（≤32/turn）；turn 外为
   no-op。该计数是既有证据面（无新基础设施），且整个记录步骤被二次兜底——
   本模块自身绝不 raise（API 契约）。

用法（上下文管理器，替代 try/except-pass）::

    with best_effort("evidence-chain-emit", "tool-dispatch-evidence-best-effort",
                     ctx={"tool": tool_name}):
        emit_chain_once(Stage.TOOL_CALLS, tool=tool_name)

with 体内可以自由赋值（作用域即外层函数）；「失败回退默认值」的站点把默认值
赋在 with 之前即可::

    fallback = None
    with best_effort(...):
        fallback = compute()

用法（函数调用式，替代 try/except-return-default）::

    value = best_effort_call(fetch_descriptor, "descriptor-fetch", code, default=None)

语义承诺：
- 只捕获 ``Exception``；``BaseException``（含 ``asyncio.CancelledError`` /
  ``KeyboardInterrupt``）照常上抛——取消与硬中断绝不被吞；
- ``reraise`` 指定的异常类型先于兜底分支匹配、原样上抛（typed-swallow
  passthrough，供取消类型等必须穿透的站点）；
- 吞掉后执行继续（上下文管理器体之后的代码照常运行）。

ctx 只放 id 级有界值（tool 名、截断后的 session id），不要放 payload。
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, Optional, Tuple, Type

logger = logging.getLogger(__name__)


def _record_skipped(
    op: str, code: str, ctx: Optional[Dict[str, Any]], exc: Exception
) -> None:
    """吞掉异常前的可见化：warning 日志 + 可选 turn 级计数。绝不 raise。"""
    try:
        logger.warning(
            "[%s] %s skipped: %s | ctx=%s",
            code, op, exc, ctx or {},
            exc_info=True,
        )
        # 可选指标：活跃 turn 内计入 bounded warnings（≤32/turn），turn 外 no-op。
        # 惰性导入保持本模块 import 期零 app 依赖；失败由外层兜底吞掉。
        from app.lib.runtime.evidence import current_turn_evidence

        _ev = current_turn_evidence()
        if _ev is not None:
            _ev.add_warning(code, detail=f"{op}: {exc}"[:200])
    except Exception:  # noqa: BLE001 — 绝不上抛：本函数是兜底的兜底
        pass


@contextmanager
def best_effort(
    op: str,
    code: str,
    *,
    ctx: Optional[Dict[str, Any]] = None,
    reraise: Tuple[Type[BaseException], ...] = (),
) -> Iterator[None]:
    """「绝不阻断」上下文管理器：体内任何 ``Exception`` 被吞并记录，绝不外抛。

    op: 人读的操作名（如 ``"evidence-chain-emit"``），进日志一行式；
    code: 稳定检索码（如 ``"tool-dispatch-evidence-best-effort"``）；
    ctx: 可选 id 级上下文（tool 名 / session id），随日志输出；
    reraise: 穿透类型元组——命中即原样上抛，不吞（默认空 = 全吞 Exception）。
    """
    try:
        yield
    except reraise:
        raise
    except Exception as exc:  # noqa: BLE001 — 绝不阻断即本 API 的契约
        _record_skipped(op, code, ctx, exc)


def best_effort_call(
    fn: Callable[[], Any],
    op: str,
    code: str,
    *,
    ctx: Optional[Dict[str, Any]] = None,
    reraise: Tuple[Type[BaseException], ...] = (),
    default: Any = None,
) -> Any:
    """「绝不阻断」函数调用式：``fn()`` 抛 ``Exception`` 时记录并返回 ``default``。

    适用于「单表达式 + 失败回退默认值」的站点；多语句体请用 :func:`best_effort`。
    """
    try:
        return fn()
    except reraise:
        raise
    except Exception as exc:  # noqa: BLE001 — 绝不阻断即本 API 的契约
        _record_skipped(op, code, ctx, exc)
        return default


__all__ = ["best_effort", "best_effort_call"]
