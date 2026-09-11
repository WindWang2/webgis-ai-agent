"""Workflow Runtime V6 —— 启动/周期恢复清扫（crash → recover 的主动面）。

V5 的孤儿复位只在 ``Driver._run_loop`` 波界被动触发（依赖有人再次调
``run_instance``）；进程崩溃后 RUNNING 实例/节点**永久滞留**（Phase A
审计风险 #1）。本模块把恢复变成主动事实：

- ``sweep_recoverable(store, ...)``：扫 ``list_recoverable_instances``，
  逐实例：消费遗留取消旗标 → 孤儿节点复位 READY（attempts 保留）→
  终态愈合计数（全部节点已决但实例行仍是 RUNNING = crash 在 finalize
  边界，补落终态）。
- 多副本安全：全部收敛走条件更新（CAS/状态门）；重复扫描幂等。
- fail-open：清扫异常只记日志，绝不倒灌 API 路径（与 jobs stale sweep
  同纪律）。

接线：``app/main.py`` lifespan 周期任务（``GIS_WORKFLOW_RECOVERY_INTERVAL_S``
门控，默认 60s；设 0 关闭）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.services.workflow_runtime import contracts as C
from app.services.workflow_runtime.store import (
    DEFAULT_LEASE_TTL_S,
    InstanceStore,
)

logger = logging.getLogger(__name__)

#: 单次清扫处理的实例上界（有界批；下一 tick 继续）。
SWEEP_BATCH = 16
#: 实例行 RUNNING 但全部节点已决 → 补 finalize 的最小已决节点数守卫
#: （0 节点已决 = 可能是 create 后未起步的实例，不碰 —— 由租约门负责）。
MIN_DECIDED_FOR_FINALIZE = 1


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def recovery_interval_s() -> float:
    """清扫周期（秒）；0/负 = 关闭。"""
    import os

    raw = os.getenv("GIS_WORKFLOW_RECOVERY_INTERVAL_S", "60")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 60.0


def sweep_recoverable(
    store: Optional[InstanceStore] = None, *,
    ttl_s: float = DEFAULT_LEASE_TTL_S,
    batch: int = SWEEP_BATCH,
    worker_registry: Optional[Any] = None,
) -> Dict[str, Any]:
    """一轮恢复清扫（同步；调用方负责 to_thread 卸载）。

    返回报告 {scanned, cancelled, orphan_reset, finalized, errors}。
    """
    store = store or InstanceStore()
    report: Dict[str, Any] = {
        "scanned": 0, "cancelled": 0, "orphan_reset": 0,
        "finalized": 0, "errors": 0, "workers_stale": [],
    }
    try:
        instances = store.list_recoverable_instances(
            ttl_s=ttl_s, limit=batch)
    except Exception:  # noqa: BLE001 — 读失败 = 本轮放弃（fail-open）
        logger.warning("[WorkflowRuntime] recovery list failed",
                       exc_info=True)
        report["errors"] += 1
        return report
    # worker 活性清扫（V6）：心跳过期 active → stale；其在飞节点由节点
    # 租约独立接管（两级真相解耦），这里只管注册表词表卫生。
    try:
        from app.services.workflow_runtime.cluster import WorkerRegistry

        registry = worker_registry or WorkerRegistry()
        report["workers_stale"] = registry.sweep_dead()
    except Exception:  # noqa: BLE001 — 注册表故障不阻断实例恢复
        report["workers_stale"] = []
    for inst in instances:
        report["scanned"] += 1
        instance_id = inst["instance_id"]
        try:
            if inst.get("cancel_requested"):
                report["cancelled"] += _consume_cancel(store, instance_id)
                continue
            report["orphan_reset"] += _reset_orphans(store, instance_id)
            report["orphan_reset"] += _consume_node_cancels(store,
                                                             instance_id)
            report["finalized"] += _finalize_if_decided(store, instance_id)
        except Exception:  # noqa: BLE001 — 单实例失败不拖垮整轮
            logger.warning(
                "[WorkflowRuntime] recovery sweep failed %s",
                instance_id, exc_info=True)
            report["errors"] += 1
    if report["scanned"]:
        logger.info(
            "[WorkflowRuntime] recovery sweep scanned=%(scanned)s "
            "cancelled=%(cancelled)s orphan_reset=%(orphan_reset)s "
            "finalized=%(finalized)s errors=%(errors)s" % report)
    return report


def _consume_cancel(store: InstanceStore, instance_id: str) -> int:
    """消费遗留取消旗标：非终态节点全部 CANCELLED + 实例落终态。

    返回取消的节点数。旗标已在（list 查询保证），这里只做收敛；多副本
    竞争下多写 CONDITION 拒绝 = 幂等 no-op。
    """
    states = store.get_node_states(instance_id)
    cancelled = 0
    for nid, st in states.items():
        if st in (C.NodeState.SUCCEEDED, C.NodeState.FAILED,
                  C.NodeState.SKIPPED, C.NodeState.CANCELLED):
            continue
        r = store.transition_node(
            instance_id, nid, C.NodeState.CANCELLED,
            reason="RECOVERY_CANCEL", event="recovery")
        if r.ok:
            cancelled += 1
    fresh = store.get_instance(instance_id)
    if fresh is not None and fresh["status"] == C.InstanceStatus.RUNNING:
        store.append_event(
            instance_id, kind=C.EventKind.RECOVERY_FINALIZE,
            reason="CANCEL_CONVERGED", actor="recovery",
            payload={"cancelled_nodes": cancelled})
        store.update_instance(
            instance_id,
            fields={"status": C.InstanceStatus.CANCELLED,
                    "terminal_at": _utcnow(),
                    "error_code": "RECOVERY_CANCELLED"})
    return cancelled


def _reset_orphans(store: InstanceStore, instance_id: str) -> int:
    """孤儿 RUNNING → READY（attempts 保留；journal 记恢复事件）。"""
    orphans = store.find_orphan_running_nodes(instance_id, current_token="")
    reset = 0
    for nid in orphans:
        r = store.transition_node(
            instance_id, nid, C.NodeState.READY,
            reason="ORPHAN_LEASE_EXPIRED", event="recovery")
        if r.ok:
            reset += 1
            store.append_event(
                instance_id, kind=C.EventKind.RECOVERY_ORPHAN_RESET,
                node_id=nid, reason="ORPHAN_LEASE_EXPIRED",
                actor="recovery")
    return reset


def _consume_node_cancels(store: InstanceStore, instance_id: str) -> int:
    """消费节点级取消旗标（driver 死亡后旗标残留的兜底收敛）。

    只动**非在飞**旗标节点：RUNNING 节点若租约仍活归 driver/孤儿路径管，
    这里只收 READY/PENDING/STALE/BLOCKED —— 与 driver 波界同语义。
    """
    flags = store.get_node_cancel_flags(instance_id)
    if not flags:
        return 0
    states = store.get_node_states(instance_id)
    consumed = 0
    for nid in flags:
        st = states.get(nid)
        if st in (None, C.NodeState.RUNNING, C.NodeState.SUCCEEDED,
                  C.NodeState.FAILED, C.NodeState.SKIPPED,
                  C.NodeState.CANCELLED):
            continue
        r = store.transition_node(
            instance_id, nid, C.NodeState.CANCELLED,
            reason="NODE_CANCELLED", event="recovery")
        if r.ok:
            consumed += 1
    return consumed


def _finalize_if_decided(store: InstanceStore, instance_id: str) -> int:
    """终态愈合：实例行 RUNNING 但全部节点已决 = crash 在 finalize 边界。

    以节点状态重算实例状态并补落（**不**改节点 —— 它们是既成事实）。
    返回 1 = 补落了终态；0 = 不需要/不满足。
    """
    inst = store.get_instance(instance_id)
    if inst is None or inst["status"] != C.InstanceStatus.RUNNING:
        return 0
    states = store.get_node_states(instance_id)
    if not states:
        return 0
    decided = [s for s in states.values()
               if s in (C.NodeState.SUCCEEDED, C.NodeState.FAILED,
                        C.NodeState.SKIPPED, C.NodeState.CANCELLED)]
    if len(decided) < MIN_DECIDED_FOR_FINALIZE or len(decided) != len(states):
        return 0
    # optional 事实从节点行不可得（optional 不落节点行）——愈合按全
    # required 裁决（保守：FAILED 在场即 failed；与 driver optional_map
    # 缺省 False 一致）。
    status = C.instance_status_from_nodes(states, optional_nodes={})
    store.append_event(
        instance_id, kind=C.EventKind.RECOVERY_FINALIZE,
        reason="CRASH_BEFORE_FINALIZE", actor="recovery",
        payload={"status": status})
    fields: Dict[str, Any] = {"status": status, "terminal_at": _utcnow()}
    if status == C.InstanceStatus.FAILED:
        failed = [n for n, s in states.items()
                  if s in (C.NodeState.FAILED, C.NodeState.BLOCKED)]
        fields["error_code"] = "NODES_INCOMPLETE"
        fields["error_detail"] = ",".join(failed[:6])[:255]
    store.update_instance(instance_id, fields=fields)
    return 1


async def sweep_recoverable_async(
    store: Optional[InstanceStore] = None, **kwargs: Any,
) -> Dict[str, Any]:
    """async 包装（lifespan 周期任务用）。"""
    import asyncio

    return await asyncio.to_thread(sweep_recoverable, store, **kwargs)


async def periodic_recovery_sweep(
    interval_seconds: Optional[float] = None,
) -> None:
    """lifespan 周期恢复任务（模式镜像 ``_periodic_stale_job_sweep``）。"""
    import asyncio

    interval = recovery_interval_s() if interval_seconds is None \
        else float(interval_seconds)
    if interval <= 0:
        return
    logger = logging.getLogger(__name__)
    while True:
        try:
            await asyncio.sleep(interval)
            await sweep_recoverable_async()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — 周期任务绝不退出
            logger.warning("[WorkflowRuntime] recovery tick failed: %s", e)
