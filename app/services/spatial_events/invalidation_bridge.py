"""invalidation bridge：版本变化 → 受影响子图 / evidence 失效。

复用面（不自建第二套失效数学）：
- workflow：``WorkflowRuntimeService.apply_changes`` → ``ChangeApplier`` →
  ``compute_affected_subgraph`` —— 只把受影响 descendants 标 STALE。
- evidence：``evidence_claim.freshness.invalidate_affected_claims`` ——
  本分支是其在仓内的**首个生产调用方**（baseline 零调用）。

失效对象发现：查 ``workflow_instances``（同 org，project 或 session 关联，
非终态，≤16）——直接读模型行（只读），不复制 InstanceStore。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: 触发失效语义的事件 kind（数据身份变化）
INVALIDATION_KINDS = {
    "dataset.version_changed",
    "artifact.revision_committed",
    "mapproduct.version_recorded",
}

_MAX_INSTANCES = 16


def _is_data_change(kind: str, payload: Dict[str, Any]) -> bool:
    if kind == "mapproduct.version_recorded":
        # 只有 data_changed=True 的版本才触发重算（五维 diff 语义）
        return bool(payload.get("data_changed"))
    return True


class InvalidationBridge:
    """事件 → PendingChange(data_hook) + evidence 失效（fail-open）。"""

    def __init__(
        self,
        *,
        workflow_service: Optional[Any] = None,
        factory: Optional[Any] = None,
    ) -> None:
        self._workflow = workflow_service
        self._factory = factory

    def _workflow_service(self) -> Any:
        if self._workflow is not None:
            return self._workflow
        from app.services.workflow_runtime.service import get_service

        return get_service()

    def find_affected_instances(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        """同 org 下与事件关联的非终态 workflow 实例（有界 ≤16）。"""
        from sqlalchemy import and_, or_, select

        from app.models.db_model import WorkflowInstanceRow

        factory = self._factory
        if factory is None:
            from app.core.database import SessionLocal as factory
        org_id = event.get("org_id")
        if not org_id:
            return []
        clauses = []
        if event.get("project_id"):
            clauses.append(WorkflowInstanceRow.project_id == event["project_id"])
        if event.get("session_id"):
            clauses.append(WorkflowInstanceRow.session_id == event["session_id"])
        if not clauses:
            return []
        with factory() as db:
            rows = db.execute(
                select(WorkflowInstanceRow)
                .where(
                    and_(
                        WorkflowInstanceRow.org_id == str(org_id),
                        WorkflowInstanceRow.status.in_(("running",)),
                        or_(*clauses),
                    )
                )
                .order_by(WorkflowInstanceRow.created_at.desc())
                .limit(_MAX_INSTANCES)
            ).scalars().all()
            out = []
            for r in rows:
                out.append(
                    {
                        "instance_id": r.instance_id,
                        "owner_scope": r.owner_scope,
                        "session_id": r.session_id,
                        "project_id": r.project_id,
                        "status": r.status,
                    }
                )
            return out

    async def apply_to_workflow(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        """对受影响实例投递 PendingChange(data, source=data_hook)。"""
        from app.services.workflow_runtime.contracts import PendingChange

        if event.get("kind") not in INVALIDATION_KINDS:
            return []
        if not _is_data_change(event.get("kind", ""), event.get("payload") or {}):
            return []
        results: List[Dict[str, Any]] = []
        svc = self._workflow_service()
        change = PendingChange(
            dimension="data",
            target_kind=str(event.get("subject_type") or "dataset")[:24],
            target=str(event.get("subject_key") or "")[:64],
            detail=str(event.get("kind") or "")[:200],
            source="data_hook",
        )
        for inst in self.find_affected_instances(event):
            try:
                res = await svc.apply_changes(
                    inst["instance_id"],
                    [change],
                    owner_scope=inst["owner_scope"],
                    source="data_hook",
                )
                results.append(
                    {
                        "instance_id": inst["instance_id"],
                        "ok": True,
                        "summary": _bounded_result(res),
                    }
                )
            except Exception as e:  # noqa: BLE001 — 单实例失败不阻断其余
                logger.warning(
                    "[spatial_events] invalidation failed for instance %s: %s",
                    inst["instance_id"], e,
                )
                results.append(
                    {
                        "instance_id": inst["instance_id"],
                        "ok": False,
                        "error": str(e)[:120],
                    }
                )
        return results

    async def apply_to_evidence(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """会话域 evidence/claim 失效（evidence_claim.freshness 首个接线）。

        仅当事件携带显式 ``ref``（payload.ref / payload_ref）——失效目标必须
        是真实 ref，subject_key 不冒充 evidence ref（诚实边界）。
        """
        session_id = event.get("session_id")
        ref = (event.get("payload") or {}).get("ref") or event.get("payload_ref")
        if not session_id or not ref:
            return {"skipped": True}
        try:
            from app.services.gis_harness.evidence_claim.freshness import (
                invalidate_affected_claims,
            )
            from app.services.gis_harness.hotpath_convergence.session_ctx import (
                get_or_create_claim_store,
            )

            store = get_or_create_claim_store(str(session_id))
            report = invalidate_affected_claims(store, str(ref))
            return {
                "skipped": False,
                "stale_evidence": len(
                    report.get("affected_evidence", []) or []
                ),
                "stale_claims": len(report.get("stale_claims", []) or []),
            }
        except Exception as e:  # noqa: BLE001 — evidence 失效是增值面
            logger.warning(
                "[spatial_events] evidence invalidation failed: %s", e
            )
            return {"skipped": True, "error": str(e)[:120]}


def _bounded_result(res: Any) -> Dict[str, Any]:
    """apply_changes 结果的有界摘要。"""
    try:
        if isinstance(res, dict):
            return {
                k: res.get(k)
                for k in ("marked_stale", "deferred", "changes_fingerprint")
                if k in res
            }
    except Exception:  # noqa: BLE001
        pass
    return {}


async def apply_event(
    event: Dict[str, Any],
    *,
    bridge: Optional[InvalidationBridge] = None,
) -> Dict[str, Any]:
    """入口（worker 调用；flag 已在 worker 层检查）。"""
    b = bridge or InvalidationBridge()
    workflow = await b.apply_to_workflow(event)
    evidence = await b.apply_to_evidence(event)
    return {
        "instances_touched": len(workflow),
        "workflow": workflow[:_MAX_INSTANCES],
        "evidence": evidence,
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }
