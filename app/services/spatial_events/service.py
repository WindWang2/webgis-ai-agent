"""SpatialEventService — ingest / drain / worker 组合面。

处理流水（每事件；顺序即纪律）：
1. **Situation 投影**（带 session 的事件 → 会话事实环；fail-open 增值面）
2. **Watch 求值**（持久状态；fire 幂等 (watch,event) UQ；动作执行）
   - mission_* 动作：``GIS_SPATIAL_EVENT_MISSION_BRIDGE`` 门 + governor 背压门
     + MissionBridge（确定性 mission_id 幂等）
3. **失效桥**（版本变化 → 受影响 workflow 子图 STALE + evidence/claim 失效；
   ``GIS_SPATIAL_EVENT_INVALIDATION`` 门）

可靠性：
- 事件行 CAS 抢批（ledger.claim_batch）；崩溃恢复 = stale-processing 清扫 +
  at-least-once 重投 + 副作用幂等（fire UQ / 确定性 mission_id / 投影去重）。
- deferred 动作 → 事件回 pending 带退避（持久重试，不丢）。
- 租户公平：批内按 org 轮转重排（防单租户垄断）。
- coalesce：pending 超过阈值时同 (kind,subject) 合并到最新（burst 收敛）。
- durable cursor：水位 = min(非终态)-1，只前进。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.services.spatial_events import flags
from app.services.spatial_events import situation_projection as SP
from app.services.spatial_events.contracts import (
    EventKind,
    EventPriority,
    SpatialEventEnvelope,
    SpatialWatch,
    SubjectType,
    WatchFireRecord,
    WatchState,
)
from app.services.spatial_events.governor_gate import GovernorGate
from app.services.spatial_events.invalidation_bridge import (
    INVALIDATION_KINDS,
    InvalidationBridge,
    apply_event as apply_invalidation,
)
from app.services.spatial_events.ledger import AppendResult, SpatialEventLedger
from app.services.spatial_events.mission_bridge import MissionBridge
from app.services.spatial_events.watch import (
    evaluate_watch,
    watch_matches_event,
)

logger = logging.getLogger(__name__)

COALESCE_TRIGGER_PENDING = 64
DEFERRED_BACKOFF_S = 15.0


class SpatialEventService:
    def __init__(
        self,
        ledger: Optional[SpatialEventLedger] = None,
        *,
        mission_runtime: Optional[Any] = None,
        workflow_service: Optional[Any] = None,
        gate: Optional[GovernorGate] = None,
        invalidation_bridge: Optional[InvalidationBridge] = None,
        session_store: Optional[Any] = None,
    ) -> None:
        self._ledger = ledger or SpatialEventLedger()
        self._mission_runtime = mission_runtime
        self._workflow_service = workflow_service
        self._gate = gate or GovernorGate()
        self._invalidation = invalidation_bridge
        self._session_store = session_store
        self._wake = asyncio.Event()

    @property
    def ledger(self) -> SpatialEventLedger:
        return self._ledger

    def _mission_bridge(self) -> MissionBridge:
        runtime = self._mission_runtime
        if runtime is None:
            from app.services.mission_runtime.service import get_mission_runtime

            runtime = get_mission_runtime()
        return MissionBridge(runtime)

    def _invalidation_bridge(self) -> InvalidationBridge:
        if self._invalidation is not None:
            return self._invalidation
        return InvalidationBridge(workflow_service=self._workflow_service)

    # ── ingest ───────────────────────────────────────────────────────

    def ingest_sync(
        self,
        envelope: SpatialEventEnvelope,
        *,
        org_id: Optional[str] = None,
    ) -> Optional[AppendResult]:
        """同步入账（适配器 hook 用；fail-open，绝不外溢）。"""
        try:
            result = self._ledger.append(envelope, org_id=org_id)
            self.notify()
            return result
        except Exception as e:  # noqa: BLE001 — hook 绝不阻断业务路径
            logger.debug("[spatial_events] ingest skipped: %s", e)
            return None

    async def ingest(
        self, envelope: SpatialEventEnvelope, *, org_id: Optional[str] = None
    ) -> Optional[AppendResult]:
        try:
            result = await asyncio.to_thread(
                self._ledger.append, envelope, org_id=org_id
            )
            self.notify()
            return result
        except Exception as e:  # noqa: BLE001
            logger.warning("[spatial_events] async ingest failed: %s", e)
            return None

    def notify(self) -> None:
        """唤醒本进程 worker（低延迟路径；轮询是兜底）。"""
        try:
            self._wake.set()
        except RuntimeError:
            pass  # 无事件循环（同步 hook 进程）——由轮询兜底

    # ── drain ────────────────────────────────────────────────────────

    async def drain_once(self, worker_id: str, *, batch: Optional[int] = None) -> Dict[str, Any]:
        """处理一批事件。返回有界摘要（可观测；不泄露 payload）。"""
        n = batch or flags.batch_size()
        summary: Dict[str, Any] = {
            "worker_id": worker_id,
            "claimed": 0,
            "processed": 0,
            "deferred": 0,
            "failed": 0,
            "skipped": 0,
            "fires": 0,
            "coalesced": 0,
            "cursor": 0,
        }
        # 崩溃恢复：陈旧 processing 复位（幂等；多副本安全）
        try:
            await asyncio.to_thread(
                self._ledger.requeue_stale_processing,
                older_than_s=flags.stale_claim_s(),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[spatial_events] stale requeue failed: %s", e)

        # burst 背压第一级：pending 超阈值 → coalesce
        try:
            stats = await asyncio.to_thread(self._ledger.stats)
            if int(stats.get("pending", 0)) >= COALESCE_TRIGGER_PENDING:
                cs = await asyncio.to_thread(self._ledger.coalesce_pending)
                summary["coalesced"] = int(cs.get("coalesced", 0))
        except Exception as e:  # noqa: BLE001
            logger.warning("[spatial_events] coalesce failed: %s", e)

        rows = await asyncio.to_thread(
            self._ledger.claim_batch, worker_id, limit=n
        )
        rows = _interleave_by_org(rows)
        summary["claimed"] = len(rows)

        last_processed_id = 0
        for row in rows:
            try:
                outcome = await self._process_event(row)
            except Exception as e:  # noqa: BLE001 — 单事件异常不拖垮批
                logger.warning(
                    "[spatial_events] event %s processing error: %s",
                    row.get("id"), e,
                )
                outcome = {"deferred": True, "seconds": None}
            if outcome.get("deferred"):
                await asyncio.to_thread(
                    self._ledger.mark_failed, row["id"],
                    error_code=outcome.get("error_code", "DEFERRED"),
                    backoff_s=float(outcome.get("seconds") or DEFERRED_BACKOFF_S),
                )
                summary["deferred"] += 1
            elif outcome.get("skipped"):
                summary["skipped"] = summary.get("skipped", 0) + 1
            else:
                await asyncio.to_thread(self._ledger.mark_processed, row["id"])
                summary["processed"] += 1
                last_processed_id = max(last_processed_id, int(row["id"]))
            summary["fires"] += int(outcome.get("fires", 0))

        _ = last_processed_id
        try:
            watermark = await asyncio.to_thread(
                self._ledger.cursor_high_watermark
            )
            await asyncio.to_thread(
                self._ledger.advance_cursor, "spatial-event-worker", watermark
            )
            summary["cursor"] = watermark
        except Exception as e:  # noqa: BLE001
            logger.warning("[spatial_events] cursor update failed: %s", e)
        return summary

    async def run_worker(
        self,
        worker_id: str = "spatial-event-worker",
        *,
        stop: Optional[asyncio.Event] = None,
        interval_s: Optional[float] = None,
        max_iterations: Optional[int] = None,
    ) -> int:
        """worker 循环（lifespan 启动；测试用 max_iterations 收敛）。

        返回处理的迭代次数。stop 置位/超时即退出（不吞取消）。
        """
        interval = (
            interval_s if interval_s is not None else flags.worker_interval_s()
        )
        iterations = 0
        while True:
            if stop is not None and stop.is_set():
                break
            if max_iterations is not None and iterations >= max_iterations:
                break
            try:
                await self.drain_once(worker_id)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning("[spatial_events] worker tick failed: %s", e)
            iterations += 1
            if max_iterations is not None and iterations >= max_iterations:
                break
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=max(0.05, interval))
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
        return iterations

    # ── 单事件处理 ───────────────────────────────────────────────────

    async def _process_event(self, row: Dict[str, Any]) -> Dict[str, Any]:
        outcome: Dict[str, Any] = {
            "event_id": row.get("event_id"),
            "projection": False,
            "fires": 0,
            "actions": [],
            "deferred": False,
            "invalidation": None,
        }
        # 1) Situation 投影（先投影、再决策）
        if self._session_store is not None or flags.runtime_enabled():
            try:
                outcome["projection"] = await SP.project_event(
                    row, store=self._session_store
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("[spatial_events] projection error: %s", e)

        envelope = self._envelope_from_row(row)
        if envelope is None:
            outcome["error_code"] = "MALFORMED_ROW"
            await asyncio.to_thread(
                self._ledger.mark_skipped, row["id"], reason="MALFORMED_ROW"
            )
            outcome["skipped"] = True
            return outcome

        # 2) Watch 求值 + 动作
        watch_ids = await asyncio.to_thread(
            self._ledger.list_watches, org_id=row["org_id"], enabled_only=True
        )
        for wid in watch_ids:
            watch = await asyncio.to_thread(
                self._ledger.get_watch, wid, org_id=row["org_id"]
            )
            if watch is None or not watch_matches_event(watch, envelope):
                continue
            state = await asyncio.to_thread(
                self._ledger.get_watch_state, wid, org_id=row["org_id"]
            ) or WatchState()
            result = evaluate_watch(
                watch, state, envelope, resolve_aoi=self._resolve_aoi
            )
            await asyncio.to_thread(
                self._ledger.update_watch_state, wid, result.state,
                org_id=row["org_id"],
            )
            if not result.fired:
                continue
            outcome["fires"] += 1
            action_results = await self._run_actions(watch, row)
            outcome["actions"].extend(action_results)
            if any(a.get("outcome") == "deferred" for a in action_results):
                outcome["deferred"] = True
                outcome["error_code"] = "MISSION_ACTION_DEFERRED"
        if outcome.get("skipped"):
            return outcome

        # 3) 失效桥（版本变化 → 受影响子图/evidence）
        if flags.invalidation_enabled() and row.get("kind") in INVALIDATION_KINDS:
            try:
                outcome["invalidation"] = await apply_invalidation(
                    row, bridge=self._invalidation_bridge()
                )
            except Exception as e:  # noqa: BLE001 — 失效失败不阻断事件面
                logger.warning("[spatial_events] invalidation error: %s", e)
                outcome["invalidation"] = {"error": str(e)[:120]}

        # 4) mission 决策来自投影事实（红线）——digest 进 goal，可对账
        return outcome

    async def _run_actions(
        self, watch: SpatialWatch, event_row: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for action in watch.actions:
            try:
                results.append(
                    await self._run_single_action(action, watch, event_row)
                )
            except Exception as e:  # noqa: BLE001 — 单动作失败 → deferred 重试
                logger.warning(
                    "[spatial_events] action %s failed: %s", action, e
                )
                results.append(
                    {"action": action, "outcome": "deferred",
                     "detail": {"error": str(e)[:120]}}
                )
        return results

    async def _run_single_action(
        self, action: str, watch: SpatialWatch, event_row: Dict[str, Any]
    ) -> Dict[str, Any]:
        watch_id = watch.watch_id
        event_id = str(event_row.get("event_id") or "")
        session_id = event_row.get("session_id")

        digest = ""
        if session_id:
            facts = await SP.projected_facts(
                str(session_id), store=self._session_store
            )
            digest = SP.facts_digest(facts)

        if action == "notify_only":
            rec = WatchFireRecord(
                watch_id=watch_id, org_id=watch.org_id, event_id=event_id,
                fired_at=datetime.now(timezone.utc), action=action,
                outcome="notified",
                detail={"facts_digest": digest} if digest else {},
            )
            _, created = await asyncio.to_thread(self._ledger.record_fire, rec)
            return {
                "action": action,
                "outcome": "notified" if created else "duplicate",
            }

        # mission 动作：桥 flag 门
        if not flags.mission_bridge_enabled():
            rec = WatchFireRecord(
                watch_id=watch_id, org_id=watch.org_id, event_id=event_id,
                fired_at=datetime.now(timezone.utc), action=action,
                outcome="suppressed",
                detail={"reason": "mission_bridge_off"},
            )
            _, created = await asyncio.to_thread(self._ledger.record_fire, rec)
            return {
                "action": action,
                "outcome": "suppressed" if created else "duplicate",
            }

        existing = await asyncio.to_thread(
            self._ledger.get_fire, watch_id, event_id
        )
        if existing is not None:
            done = existing["outcome"] in (
                "mission_created", "mission_revised", "mission_resumed",
                "rejected", "suppressed",
            )
            if done:
                return {
                    "action": action,
                    "outcome": "duplicate",
                    "detail": {"prior": existing["outcome"]},
                }
            # pending/deferred → 重试路径（at-least-once + 确定性幂等键）
        else:
            rec = WatchFireRecord(
                watch_id=watch_id, org_id=watch.org_id, event_id=event_id,
                fired_at=datetime.now(timezone.utc), action=action,
                outcome="pending", detail={},
            )
            _, created = await asyncio.to_thread(self._ledger.record_fire, rec)
            if not created and existing is None:
                return {"action": action, "outcome": "duplicate"}

        ticket = await self._gate.acquire(
            org_id=watch.org_id, priority=str(event_row.get("priority") or "normal")
        )
        if ticket is None:
            await asyncio.to_thread(
                self._ledger.update_fire_outcome, watch_id, event_id,
                "deferred", {"reason": "governor_defer"},
            )
            return {
                "action": action,
                "outcome": "deferred",
                "detail": {"reason": "governor_defer"},
            }
        try:
            res = await asyncio.to_thread(
                self._mission_bridge().execute,
                action, watch, event_row, digest=digest,
            )
        finally:
            await self._gate.release(ticket)
        await asyncio.to_thread(
            self._ledger.update_fire_outcome, watch_id, event_id,
            str(res.get("outcome") or "rejected"),
            res.get("detail") if isinstance(res.get("detail"), dict) else None,
        )
        return {
            "action": action,
            "outcome": res.get("outcome"),
            "detail": res.get("detail"),
        }

    # ── 辅助 ─────────────────────────────────────────────────────────

    def _envelope_from_row(
        self, row: Dict[str, Any]
    ) -> Optional[SpatialEventEnvelope]:
        try:
            occurred_raw = str(row.get("occurred_at") or "")
            occurred = datetime.fromisoformat(occurred_raw)
            if occurred.tzinfo is None:
                occurred = occurred.replace(tzinfo=timezone.utc)
            kwargs = dict(
                schema_version="spatial-event.v1",
                event_id=str(row.get("event_id") or ""),
                kind=EventKind(str(row.get("kind"))),
                org_id=str(row.get("org_id") or ""),
                source=str(row.get("source") or "internal"),
                subject_type=SubjectType(str(row.get("subject_type"))),
                subject_key=str(row.get("subject_key") or ""),
                session_id=row.get("session_id"),
                project_id=row.get("project_id"),
                occurred_at=occurred,
                dedupe_key=row.get("dedupe_key"),
                payload=dict(row.get("payload") or {}),
                payload_ref=row.get("payload_ref"),
                priority=EventPriority(str(row.get("priority") or "normal")),
                correlation_id=row.get("correlation_id"),
            )
            # 行来自本账本（已在 append 时验签/盖章）→ 信任上下文
            return SpatialEventEnvelope.model_validate(
                kwargs, context={"trusted_webhook": True}
            )
        except Exception as e:  # noqa: BLE001 — 行损坏按 malformed 跳过
            logger.warning("[spatial_events] malformed row %s: %s", row.get("id"), e)
            return None

    def _resolve_aoi(self, ref: str) -> Optional[Dict[str, Any]]:
        """AOI ref → GeoJSON（会话 ref 存储；解析失败返回 None=不可判）。"""
        if not ref or self._session_store is None:
            return None
        try:
            payload = self._session_store.get_ref_data_sync(ref)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("type") in ("Polygon", "MultiPolygon"):
            return payload
        gj = payload.get("geojson")
        if isinstance(gj, dict) and gj.get("type") in ("Polygon", "MultiPolygon"):
            return gj
        return None


def _interleave_by_org(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """批内 per-org 轮转（租户公平：单租户不得垄断批）。"""
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    order: List[str] = []
    for r in rows:
        org = str(r.get("org_id") or "")
        if org not in buckets:
            buckets[org] = []
            order.append(org)
        buckets[org].append(r)
    out: List[Dict[str, Any]] = []
    idx = 0
    while True:
        added = False
        for org in order:
            b = buckets[org]
            if idx < len(b):
                out.append(b[idx])
                added = True
        if not added:
            break
        idx += 1
    return out


_SERVICE: Optional[SpatialEventService] = None


def get_spatial_event_service() -> SpatialEventService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = SpatialEventService()
    return _SERVICE


def reset_spatial_event_service_for_tests() -> None:
    global _SERVICE
    _SERVICE = None
