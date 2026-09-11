"""Workflow Runtime V6 —— 补偿钩子（partially-committed artifact cleanup）。

Phase C 取消/失败的「孤儿产物」语义（Phase A 审计风险 #10）：在飞执行
可能已经 materialize 出 session ref，随后才观察到取消/失败 —— 节点行没
有 output_ref（完成 CAS 未到），产物成为无主孤儿。本模块：

- ``compensate_ref(session_id, ref)``：按 ref 方案（scheme）分派到注册的
  补偿处理器；默认 ``ref:``（session store）→ ``delete_ref``；``wi:``
  （子工作流实例）不在此清理（归子工作流取消传播，Phase F）。
- 注册表（``register_handler``）：其他产物域（blob store / artifact
  registry）可挂接；**fail-open** —— 补偿失败只记 journal 事件，绝不
  倒灌取消/失败路径。

与 jobs ``atomic_output`` 的区别：jobs 层产物走 .part→os.replace 原子
门（半成品天然不可见）；workflow 层经 session ref 落存，可见性在
materialize 即发生 —— 所以取消边界需要显式补偿。两者各管一个爆炸半径。
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from app.services.workflow_runtime import contracts as C

logger = logging.getLogger(__name__)

#: 补偿处理器：(session_id, ref, detail) → 是否清理成功（async）。
CompensationHandler = Callable[[str, str, Dict[str, Any]], Awaitable[bool]]

_handlers: Dict[str, CompensationHandler] = {}
_default_handler: Optional[CompensationHandler] = None


def register_handler(ref_scheme: str, handler: CompensationHandler) -> None:
    """注册 ref 方案处理器（如 ``ref:``、``blob:``；测试可注入假件）。"""
    _handlers[str(ref_scheme)[:16]] = handler


def set_default_handler(handler: CompensationHandler) -> None:
    """注册兜底处理器（未识别方案时调用；测试隔离用）。"""
    global _default_handler
    _default_handler = handler


def reset_handlers() -> None:
    """测试隔离。"""
    global _default_handler
    _handlers.clear()
    _default_handler = None


def _handler_for(ref: str) -> Optional[CompensationHandler]:
    scheme = ref.split(":", 1)[0] + ":" if ":" in ref else ""
    return _handlers.get(scheme) or _default_handler


async def compensate_ref(
    session_id: str, ref: str, *, node_id: str = "",
    instance_id: str = "", attempt: int = 0,
    reason: str = "CANCELLED_WITH_ARTIFACT",
    store: Optional[Any] = None,
) -> bool:
    """清理一次未提交的产物（best-effort；结果进 journal，异常不外抛）。

    ``store``：journal 写入用 InstanceStore（调用方注入 —— 默认全局工厂
    会让测试/多租户场景写错库）。返回 False = 无处理器/清理失败 —— 调用方
    据此在 journal 记录 ``compensation_failed`` 证据（诚实暴露，绝不静默
    假装清理成功）。
    """
    if not ref or ref.startswith("wi:"):
        return False
    handler = _handler_for(ref)
    if handler is None:
        return False
    try:
        ok = bool(await handler(session_id, ref, {"node_id": node_id}))
    except Exception:  # noqa: BLE001 — 补偿绝不倒灌取消路径
        logger.warning(
            "[WorkflowRuntime] compensation handler failed ref=%s", ref[:48],
            exc_info=True)
        ok = False
    if ok and instance_id:
        if store is None:
            from app.services.workflow_runtime.store import InstanceStore

            store = InstanceStore()
        store.append_event(
            instance_id, kind=C.EventKind.COMPENSATION, node_id=node_id,
            reason=reason[:96], actor="compensation", attempt=attempt,
            payload={"ref": ref[:96]})
    return ok


async def _delete_session_ref(
    session_id: str, ref: str, _detail: Dict[str, Any],
) -> bool:
    """默认处理器：session store ref 删除（幂等；不存在也算清理完成）。"""
    from app.services.session_data import session_data_manager

    return bool(await session_data_manager.delete_ref(session_id, ref))


register_handler("ref:", _delete_session_ref)
