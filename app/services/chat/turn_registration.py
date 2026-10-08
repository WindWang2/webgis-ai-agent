"""turn 任务注册表（clear_session quiesce 依据，deep-review P1）。

P1（deep-review）：turn 任务注册必须先于任何 await —— 冷缓存
``_get_or_create_session`` 的 DB 加载与锁等待期间 ``clear_session`` 才能
发现并 cancel+quiesce 在飞 turn（原实现注册在该 await 之后，存在 quiesce
空窗，在飞消息可复活已删会话）。并发下「后注册者覆盖」：持锁进入 turn
主体时应再次调用夺回注册，quiesce 才等得到在跑的 turn。

red-team 补强：早注册的锁等待者死亡（断连/取消/LockContentionError）时，
直接注销会把在跑 turn 的注册一并清空（quiesce 失效）—— 未进入 turn 主体
的 waiter 退出时须还原先前仍存活的注册。

本模块从 execution_engine 抽出（god_modules 棘轮，>2000 行清单只减不增），
注册表本体仍是 ``ChatExecutionEngine._active_turn_tasks``。
"""
from __future__ import annotations

import asyncio
from typing import Dict, Optional


def register_active_turn_task(
    tasks: Dict[str, asyncio.Task], session_id: str
) -> Optional[asyncio.Task]:
    """把当前 task 注册为该会话的活跃 turn（覆盖语义，见模块 docstring）。"""
    task = asyncio.current_task()
    if task is not None:
        tasks[session_id] = task
    return task


def deregister_active_turn_task(
    tasks: Dict[str, asyncio.Task],
    session_id: str,
    task: Optional[asyncio.Task],
) -> None:
    """仅当注册项仍是本 task 时移除 —— 后继 turn 已覆盖注册时不得误删。"""
    if task is not None and tasks.get(session_id) is task:
        tasks.pop(session_id, None)


def abandon_active_turn_registration(
    tasks: Dict[str, asyncio.Task],
    session_id: str,
    task: Optional[asyncio.Task],
    prior: Optional[asyncio.Task],
    *,
    entered: bool,
) -> None:
    """早注册 task 退出时的兜底收尾（red-team P1 补强）。

    早注册会覆盖在跑 turn 的注册；若本 task 死于锁等待（断连/取消/
    LockContentionError）而从未进入 turn 主体，直接注销会让
    clear_session 的 quiesce 找不到任何任务（注册表空 → 不 quiesce →
    在飞消息复活已删会话）。此时还原先前仍存活的注册；prior 已终局
    或本 task 曾进入 turn 主体（自行结算过）时按普通注销处理。
    """
    if entered or task is None:
        deregister_active_turn_task(tasks, session_id, task)
        return
    if prior is not None and not prior.done():
        tasks[session_id] = prior
        return
    deregister_active_turn_task(tasks, session_id, task)
