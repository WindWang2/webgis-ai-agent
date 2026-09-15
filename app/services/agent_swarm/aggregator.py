"""Swarm 聚合器与凭证总线（ADR-0187 §6）。

依赖闭包全部到终态后：collect 汇集提货券 → ``SwarmAssetManifest``；
merge 经可注入 ``PlanSink`` 回写主会话。缺省 sink
``SessionPlanSwarmSink`` 在 fail-closed 会话锁内 upsert
``CapabilityProgress`` 并把 manifest 全量落 session store（``prefix=
"swarm"``），计划内只留提货券。

回写全程 fail-open：任何 sink 异常只记日志返回 None，绝不改变集群
settle 结果（与 workflow_runtime hooks 附加事实通道同纪律）。
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional, Protocol

from app.services.agent_swarm.delegation_contracts import (
    MAX_MANIFEST_ENTRIES,
    SwarmAssetEntry,
    SwarmAssetManifest,
    SwarmReceiptStatus,
    SwarmTaskDescriptor,
    SubagentReceipt,
)

logger = logging.getLogger(__name__)


class PlanSink(Protocol):
    """计划回写端口（测试注入假件 / 缺省 SessionPlanSwarmSink 同形）。"""

    async def merge(self, manifest: SwarmAssetManifest) -> Optional[str]: ...


class NullSink:
    """无操作 sink（纯调度测试 / 显式禁用回写）。"""

    async def merge(self, manifest: SwarmAssetManifest) -> Optional[str]:
        return None


class SessionPlanSwarmSink:
    """缺省回写：会话锁内 CapabilityProgress upsert + manifest 落 store。

    失败语义由调用方（``SwarmAggregator.merge``）fail-open 兜底；本类
    内部不吞异常（保持可测试的失败路径）。
    """

    def __init__(self, *, store: Any = None) -> None:
        self._store = store

    async def merge(self, manifest: SwarmAssetManifest) -> Optional[str]:
        from app.services.distributed_lock import session_lock_registry
        from app.services.session_plan import (  # 惰性：重依赖
            CapabilityProgress,
            SessionPlan,
            load_session_plan,
            save_session_plan,
        )

        async with session_lock_registry.lock(
            manifest.session_id, fail_on_degraded=True
        ):
            plan = await load_session_plan(
                manifest.session_id, store=self._store
            )
            if plan is None:
                plan = SessionPlan(
                    envelope_id=f"sp-{uuid.uuid4().hex[:12]}",
                    session_id=manifest.session_id,
                )
            for entry in manifest.entries:
                if not entry.capability:
                    continue
                self._upsert_progress(plan, entry, CapabilityProgress)
            await save_session_plan(plan, store=self._store)
        # manifest 全量在 session store（payload 不入计划信封）——凭证总线
        manifest_ref = await self._store_manifest(manifest)
        return manifest_ref

    @staticmethod
    def _upsert_progress(plan: Any, entry: SwarmAssetEntry, progress_cls: Any) -> None:
        status_vocab = "complete"
        for row in plan.progress:
            if row.capability == entry.capability:
                row.status = status_vocab
                row.bound_ref = entry.ref_id
                return
        plan.progress.append(
            progress_cls(
                capability=entry.capability,
                status=status_vocab,
                bound_ref=entry.ref_id,
            )
        )

    async def _store_manifest(self, manifest: SwarmAssetManifest) -> Optional[str]:
        backend = self._store
        if backend is None:
            from app.services.session_data import session_data_manager

            backend = session_data_manager
        return await backend.store(
            manifest.session_id, manifest.model_dump(), prefix="swarm"
        )


class SwarmAggregator:
    """凭证总线聚合：receipts → manifest →（fail-open）计划回写。"""

    def __init__(self, *, sink: Optional[PlanSink] = None, store: Any = None) -> None:
        self._sink = sink if sink is not None else SessionPlanSwarmSink(store=store)
        self._store = store

    async def collect(
        self,
        *,
        run_id: str,
        session_id: str,
        receipts: dict[str, SubagentReceipt],
        tasks: dict[str, SwarmTaskDescriptor],
    ) -> SwarmAssetManifest:
        entries: list[SwarmAssetEntry] = []
        dropped: list[str] = []
        failed_capabilities: list[str] = []
        for task_id, receipt in receipts.items():
            task = tasks.get(task_id)
            if receipt.status in (
                SwarmReceiptStatus.FAILED,
                SwarmReceiptStatus.DEGRADED,
            ):
                # 未干净交付的 capability（失败或降级）都要在提货单上披露
                cap = task.capability if task else ""
                if cap and cap not in failed_capabilities:
                    failed_capabilities.append(cap)
                if receipt.status == SwarmReceiptStatus.DEGRADED:
                    # 降级产物券仍入清单（附 degraded 标记由 summary 披露）
                    for ref in receipt.produced_refs:
                        entries.append(
                            SwarmAssetEntry(
                                ref_id=ref,
                                capability=cap,
                                role=receipt.role,
                                task_id=task_id,
                                summary=f"[degraded] {receipt.summary}".strip(),
                            )
                        )
                continue
            for ref in receipt.produced_refs:
                if self._store is not None and not await self._ref_exists(
                    session_id, ref
                ):
                    dropped.append(ref)
                    continue
                if len(entries) >= MAX_MANIFEST_ENTRIES:
                    dropped.append(ref)
                    continue
                entries.append(
                    SwarmAssetEntry(
                        ref_id=ref,
                        capability=task.capability if task else "",
                        role=receipt.role,
                        task_id=task_id,
                        summary=receipt.summary,
                    )
                )
        return SwarmAssetManifest(
            run_id=run_id,
            session_id=session_id,
            entries=entries,
            failed_capabilities=failed_capabilities,
            dropped_refs=dropped,
        )

    async def merge(self, manifest: SwarmAssetManifest) -> Optional[str]:
        """fail-open 回写：异常只披露，绝不影响集群 settle。"""
        try:
            return await self._sink.merge(manifest)
        except Exception:  # noqa: BLE001 — 附加事实通道
            logger.exception(
                "[Swarm] plan sink merge failed run=%s session=%s",
                manifest.run_id,
                manifest.session_id,
            )
            return None

    async def _ref_exists(self, session_id: str, ref_id: str) -> bool:
        probe = getattr(self._store, "ref_exists", None)
        if not callable(probe):
            return True  # 无验券能力 → 保留（下游取券时自然暴露）
        try:
            return bool(await probe(session_id, ref_id))
        except Exception:  # noqa: BLE001 — 验券是增值面
            return True
