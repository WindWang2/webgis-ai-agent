"""Specialist 派发器（ADR-0187 §3）—— 编排器与子代理运行时之间的接缝。

- ``SpecialistRuntime`` Protocol：可注入的执行假面（测试）/ 生产运行时；
- ``SubagentDispatcherRuntime``：包装既有 ``SubagentDispatcher``（ADR-0104）
  的生产适配器（惰性 import，不在模块加载期拉重量级依赖）；
- ``normalize_receipt``：提货券归一化 —— 词表合法化、超长截断、非 ref
  产出剔除、期望产出零券降级。是「Zero Big Data in Context」的第二道
  强制点（第一道在 contracts validator）。

派发器永不向上抛业务异常：runtime 崩溃折算为诚实 FAILED 券
（error_code=retryable / timeout），由编排器决定重试与隔离。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from app.services.agent_swarm.delegation_contracts import (
    MAX_ERROR_CHARS,
    MAX_REFS_PER_RECEIPT,
    MAX_SUMMARY_CHARS,
    SpecialistAssignment,
    SubagentReceipt,
    SwarmErrorCode,
    SwarmReceiptStatus,
    SwarmSpecialistRole,
)

logger = logging.getLogger(__name__)

HeartbeatCallback = Optional[Callable[[str], None]]


@runtime_checkable
class SpecialistRuntime(Protocol):
    """专业子代理运行时最小协议（测试注入假件 / 生产适配器同形）。"""

    async def execute(
        self,
        assignment: SpecialistAssignment,
        *,
        on_heartbeat: HeartbeatCallback = None,
    ) -> SubagentReceipt: ...


class SubagentDispatcherRuntime:
    """生产适配器：把 SpecialistAssignment 交给既有 SubagentDispatcher。

    角色是策略不是执行体（ADR-0101）：``SwarmSpecialistRole`` 映射到
    ``subagent_roles`` 角色档，未知角色名降级为无角色委派并披露。
    任何装配/执行异常都折算 FAILED 券，绝不向上抛。
    """

    async def execute(
        self,
        assignment: SpecialistAssignment,
        *,
        on_heartbeat: HeartbeatCallback = None,
    ) -> SubagentReceipt:
        started = time.time()
        try:
            from app.services.subagent import SubagentDispatcher  # 惰性：重依赖

            registry = _resolve_tool_registry()
            dispatcher = SubagentDispatcher(
                registry, assignment.projection.session_id
            )
            result = await dispatcher.run(
                task=_compose_task_text(assignment),
                role=_role_name(assignment.task.role),
            )
        except Exception as exc:  # noqa: BLE001 — 派发面诚实折算
            return _failed_receipt(
                assignment,
                error=f"subagent runtime error: {exc}"[:MAX_ERROR_CHARS],
                error_code=(
                    SwarmErrorCode.TIMEOUT
                    if isinstance(exc, (asyncio.TimeoutError, TimeoutError))
                    else SwarmErrorCode.RETRYABLE
                ),
                started=started,
            )
        if getattr(result, "success", False):
            return SubagentReceipt(
                assignment_id=assignment.assignment_id,
                task_id=assignment.task.task_id,
                role=assignment.task.role,
                status=SwarmReceiptStatus.SUCCEEDED,
                produced_refs=[
                    r for r in (getattr(result, "refs", None) or [])[:MAX_REFS_PER_RECEIPT]
                ],
                summary=str(getattr(result, "summary", ""))[:MAX_SUMMARY_CHARS],
                wall_time_s=time.time() - started,
                finished_at=time.time(),
            )
        return _failed_receipt(
            assignment,
            error=str(getattr(result, "error", "") or "subagent honest failure")[
                :MAX_ERROR_CHARS
            ],
            error_code=SwarmErrorCode.NON_RETRYABLE,
            started=started,
        )


def _resolve_tool_registry() -> Any:
    """尽力解析共享工具注册表；失败返回 None（由 SubagentDispatcher 域裁决）。"""
    try:
        from app.agent_pi_bridge import get_tool_registry

        return get_tool_registry()
    except Exception:  # noqa: BLE001 — 装配面容错
        return None


def _role_name(role: SwarmSpecialistRole) -> Optional[str]:
    try:
        from app.services.subagent_roles import get_subagent_role

        get_subagent_role(role.value)
        return role.value
    except Exception:  # noqa: BLE001 — 未知角色降级为无角色
        return None


def _compose_task_text(assignment: SpecialistAssignment) -> str:
    """任务切片 + 投影摘要（refs-only），构成子代理 prompt 文本。"""
    proj = assignment.projection
    lines = [assignment.task.goal]
    if proj.facts:
        lines.append("上下文事实: " + " | ".join(proj.facts[:8]))
    if proj.relevant_refs:
        lines.append("可用上游产物券: " + " ".join(proj.relevant_refs[:12]))
    return "\n".join(lines)[:2000]


def _failed_receipt(
    assignment: SpecialistAssignment,
    *,
    error: str,
    error_code: str,
    started: float,
) -> SubagentReceipt:
    return SubagentReceipt(
        assignment_id=assignment.assignment_id,
        task_id=assignment.task.task_id,
        role=assignment.task.role,
        status=SwarmReceiptStatus.FAILED,
        produced_refs=[],
        error=error[:MAX_ERROR_CHARS],
        error_code=error_code,
        wall_time_s=time.time() - started,
        finished_at=time.time(),
    )


def normalize_receipt(
    assignment: SpecialistAssignment, raw: Any
) -> SubagentReceipt:
    """任意 runtime 返回值 → 合法提货券（契约第二道强制点）。

    - dict / SubagentReceipt / to_dict() 对象皆收；
    - 非 ``ref:`` 产出剔除（载荷走私防线）；声称 succeeded 但期望产出
      全部无效 → 诚实降级（degraded + non_compliant）；
    - 词表非法 / 字段缺失 → 尽力折算，不抛。
    """
    if isinstance(raw, SubagentReceipt):
        data = raw.model_dump()
    elif isinstance(raw, dict):
        data = dict(raw)
    else:
        to_dict = getattr(raw, "to_dict", None)
        data = dict(to_dict()) if callable(to_dict) else {}
    task = assignment.task
    try:
        role = SwarmSpecialistRole(data.get("role", task.role))
    except ValueError:
        role = task.role
    try:
        status = SwarmReceiptStatus(data.get("status"))
    except ValueError:
        status = SwarmReceiptStatus.FAILED
    refs = [
        r
        for r in (data.get("produced_refs") or [])
        if isinstance(r, str) and r.startswith("ref:")
    ][:MAX_REFS_PER_RECEIPT]
    degraded = bool(data.get("degraded", False))
    error_code = str(data.get("error_code", "") or "")
    if status == SwarmReceiptStatus.SUCCEEDED and task.expected_outputs and not refs:
        status = SwarmReceiptStatus.DEGRADED
        degraded = True
        error_code = error_code or "non_compliant"
    return SubagentReceipt(
        assignment_id=str(data.get("assignment_id") or assignment.assignment_id),
        task_id=str(data.get("task_id") or task.task_id),
        role=role,
        status=status,
        produced_refs=refs,
        summary=str(data.get("summary", "") or "")[:MAX_SUMMARY_CHARS],
        error=str(data.get("error", "") or "")[:MAX_ERROR_CHARS],
        error_code=error_code,
        degraded=degraded,
        attempts=int(data.get("attempts", 1) or 1),
        heartbeats=int(data.get("heartbeats", 0) or 0),
        wall_time_s=float(data.get("wall_time_s", 0.0) or 0.0),
        finished_at=float(data.get("finished_at", 0.0) or 0.0),
    )


class SpecialistDispatcher:
    """派发门面：runtime 调用 + 异常折算 + 提货券归一化。"""

    def __init__(self, runtime: Optional[SpecialistRuntime] = None) -> None:
        self._runtime: SpecialistRuntime = (
            runtime if runtime is not None else SubagentDispatcherRuntime()
        )

    async def dispatch(
        self,
        assignment: SpecialistAssignment,
        *,
        on_heartbeat: HeartbeatCallback = None,
    ) -> SubagentReceipt:
        started = time.time()
        try:
            raw = await self._runtime.execute(assignment, on_heartbeat=on_heartbeat)
        except asyncio.CancelledError:
            raise  # 取消是编排语义，交还编排器
        except (asyncio.TimeoutError, TimeoutError):
            return _failed_receipt(
                assignment,
                error=f"task timeout after {time.time() - started:.3f}s",
                error_code=SwarmErrorCode.TIMEOUT,
                started=started,
            )
        except Exception as exc:  # noqa: BLE001 — 失败隔离：异常折算为券
            logger.warning(
                "[Swarm] runtime raised for task %s: %s",
                assignment.task.task_id,
                exc,
            )
            return _failed_receipt(
                assignment,
                error=f"{type(exc).__name__}: {exc}"[:MAX_ERROR_CHARS],
                error_code=SwarmErrorCode.RETRYABLE,
                started=started,
            )
        return normalize_receipt(assignment, raw)
