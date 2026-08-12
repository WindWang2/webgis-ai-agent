"""
Artifact Lineage Provenance Service: Manages execute lineage graphs (parents -> artifact -> consumers).

Invariants enforced here (see .scratch/workflow-lineage-v2/invariants.md):
  * INV-LIN1 — no self-cycle (artifact_id == parent_artifact_id rejected at write)
  * INV-LIN2 — no multi-hop cycle (bounded downstream reachability check at write)
  * INV-LIN3 — every edge is intra-project (same tenant)
  * INV-LIN4 — input datasets enter provenance via source_dataset_id
  * INV-TX2 — record_lineage does not commit when commit=False (engine owns the
              per-step atomic boundary: artifact + lineage + trace together)
"""
import uuid
import logging
from typing import Optional, List, Dict, Any, Set
from collections import deque
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.project import ArtifactLineage, Artifact

logger = logging.getLogger(__name__)

#: Max hops for the write-time cycle-prevention traversal. Bounds cost; lineage
#: depth in practice is far smaller than this.
_CYCLE_CHECK_MAX_DEPTH = 64


class LineageCycleError(ValueError):
    """Raised when a lineage edge would create a self- or multi-hop cycle."""


class LineageService:
    @staticmethod
    def record_lineage(
        db: Session,
        artifact_id: str,
        producing_tool: str,
        tool_version: str = "1.0",
        parent_artifact_ids: Optional[List[str]] = None,
        workflow_run_id: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
        source_dataset_id: Optional[str] = None,
        source_dataset_fingerprint: Optional[str] = None,
        content_fingerprint: Optional[str] = None,
        commit: bool = True,
    ) -> List[ArtifactLineage]:
        """Record one lineage edge per parent (or a single root edge for inputs).

        ``commit``: by default the service commits for direct callers. The
        WorkflowEngine passes ``commit=False`` so the engine's per-step commit is
        the atomic boundary (INV-TX2) — artifact + lineage + run-trace land
        together, and a failed step never leaves orphan lineage behind.

        Raises ``LineageCycleError`` for self- or multi-hop cycle attempts.
        """
        # Track the caller's original request so a fully filtered-out list is NOT
        # silently rewritten into a root edge (None means "genuine root").
        requested_parents = list(parent_artifact_ids) if parent_artifact_ids else None
        if parent_artifact_ids:
            # INV-LIN1: explicit self-cycle rejection.
            for pid in parent_artifact_ids:
                if pid == artifact_id:
                    raise LineageCycleError(
                        f"self-cycle lineage rejected: artifact '{artifact_id}' "
                        f"cannot be its own parent"
                    )
            # INV-LIN3: before recording, verify every parent artifact exists AND
            # belongs to the same project as the child (DATA-07). Arbitrary
            # parent ids previously enabled cross-project DAG links that traversal
            # then leaked across tenants.
            child = db.execute(
                select(Artifact).where(Artifact.id == artifact_id)
            ).scalar_one_or_none()
            child_project_id = child.project_id if child else None
            if child_project_id is not None:
                valid_parents = set(db.execute(
                    select(Artifact.id).where(
                        Artifact.id.in_(parent_artifact_ids),
                        Artifact.project_id == child_project_id,
                    )
                ).scalars().all())
                rejected = [p for p in parent_artifact_ids if p not in valid_parents]
                if rejected:
                    logger.warning(
                        "Rejected cross-project lineage parents for artifact '%s' "
                        "(project '%s'): %s",
                        artifact_id, child_project_id, rejected,
                    )
                    parent_artifact_ids = [p for p in parent_artifact_ids if p in valid_parents]

            # INV-LIN2: multi-hop cycle prevention. Adding edge parent→child
            # closes a cycle iff child can already reach parent downstream. Bound
            # the check so a pathological graph cannot make a write expensive.
            for pid in list(parent_artifact_ids):
                if LineageService._would_create_cycle(db, artifact_id, pid):
                    raise LineageCycleError(
                        f"multi-hop cycle lineage rejected: edge '{pid}' -> "
                        f"'{artifact_id}' would close a cycle"
                    )

        lineage_records: List[ArtifactLineage] = []
        # If the caller supplied parents but every one was rejected (cross-project
        # / missing), record NOTHING rather than silently synthesizing a root
        # edge that misrepresents parentage. ``None`` still means a genuine root.
        if parent_artifact_ids is not None and requested_parents and not parent_artifact_ids:
            logger.warning(
                "All supplied parents for artifact '%s' were rejected; recording "
                "no lineage edge instead of a synthetic root",
                artifact_id,
            )
            if commit:
                db.commit()
            else:
                db.flush()
            return lineage_records
        # None (genuine root) and [] (a deps-less step from the engine) both map
        # to a single root edge; a non-empty list maps to one edge per parent.
        parents = parent_artifact_ids or [None]
        # Only the FIRST/root edge carries the input-dataset provenance, so an
        # artifact fed by a dataset records DatasetVersion → Artifact without
        # fabricating a synthetic parent artifact (INV-LIN4).
        first = True
        for parent_id in parents:
            lineage = ArtifactLineage(
                id=f"lin_{uuid.uuid4().hex[:16]}",
                artifact_id=artifact_id,
                parent_artifact_id=parent_id,
                producing_tool=producing_tool,
                tool_version=tool_version,
                workflow_run_id=workflow_run_id,
                parameters=parameters or {},
                source_dataset_id=source_dataset_id if first else None,
                source_dataset_fingerprint=source_dataset_fingerprint if first else None,
                content_fingerprint=content_fingerprint,
                created_at=datetime.now(timezone.utc),
            )
            db.add(lineage)
            lineage_records.append(lineage)
            first = False

        # INV-TX2: flush (so ids/defaults materialize and the caller can read
        # them) but do not commit when the orchestrator owns the transaction.
        if commit:
            db.commit()
        else:
            db.flush()
        return lineage_records

    @staticmethod
    def _would_create_cycle(db: Session, child_id: str, parent_id: str) -> bool:
        """True iff adding edge ``parent_id -> child_id`` would close a cycle.

        Data-flow direction is parent -> child. A cycle is closed iff there is
        already a path ``child ->* parent``. We BFS downstream from ``child``
        (following rows where ``parent_artifact_id == current``) and check
        whether ``parent_id`` is reachable. One batched IN-query per depth level.
        """
        if child_id == parent_id:
            return True
        visited: Set[str] = {child_id}
        frontier: deque[str] = deque([child_id])
        depth = 0
        while frontier and depth < _CYCLE_CHECK_MAX_DEPTH:
            depth += 1
            current_ids = list(frontier)
            frontier.clear()
            rows = db.execute(
                select(ArtifactLineage.artifact_id).where(
                    ArtifactLineage.parent_artifact_id.in_(current_ids)
                )
            ).scalars().all()
            for downstream_id in rows:
                if downstream_id == parent_id:
                    return True
                if downstream_id not in visited:
                    visited.add(downstream_id)
                    frontier.append(downstream_id)
        return False

    @staticmethod
    def get_lineage_graph(
        db: Session,
        artifact_id: str,
        max_depth: int = 5,
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Returns full multi-hop upstream parents and downstream consumers for an artifact safely.

        DATA-01: the entry artifact is authorized by the caller, but the
        traversal walks parents/consumers without re-checking each belongs to the
        same tenant. ``project_id`` scopes the result: any node whose artifact
        does not belong to ``project_id`` is filtered out before return, closing
        a cross-tenant IDOR. Callers should pass the entry artifact's project_id.

        Performance: BFS is level-batched (one IN-query per depth level, not one
        query per node) and uses a deque, avoiding the prior O(n) ``list.pop(0)``
        and N+1 query pattern.
        """
        parents: List[Dict[str, Any]] = []
        consumers: List[Dict[str, Any]] = []

        # ── Upstream (parents): level-batched BFS over artifact_id ──────────
        depth_up: Dict[str, int] = {artifact_id: 0}
        frontier_up: deque[str] = deque([artifact_id])
        while frontier_up:
            # Only expand nodes within the depth budget.
            level = [n for n in frontier_up if depth_up.get(n, 0) < max_depth]
            frontier_up = deque(n for n in frontier_up if depth_up.get(n, 0) >= max_depth)
            if not level:
                break
            rows = list(
                db.execute(
                    select(ArtifactLineage).where(ArtifactLineage.artifact_id.in_(level))
                ).scalars().all()
            )
            for rec in rows:
                pid = rec.parent_artifact_id
                if pid and pid not in depth_up:
                    depth_up[pid] = depth_up.get(rec.artifact_id, 0) + 1
                    parents.append(
                        LineageService._parent_dict(rec, depth_up[pid])
                    )
                    frontier_up.append(pid)

        # ── Downstream (consumers): level-batched BFS over parent_artifact_id ──
        depth_down: Dict[str, int] = {artifact_id: 0}
        frontier_down: deque[str] = deque([artifact_id])
        while frontier_down:
            level = [n for n in frontier_down if depth_down.get(n, 0) < max_depth]
            frontier_down = deque(n for n in frontier_down if depth_down.get(n, 0) >= max_depth)
            if not level:
                break
            rows = list(
                db.execute(
                    select(ArtifactLineage).where(ArtifactLineage.parent_artifact_id.in_(level))
                ).scalars().all()
            )
            for rec in rows:
                cid = rec.artifact_id
                if cid and cid not in depth_down:
                    depth_down[cid] = depth_down.get(rec.parent_artifact_id, 0) + 1
                    consumers.append(LineageService._consumer_dict(rec, depth_down[cid]))
                    frontier_down.append(cid)

        # DATA-01: enforce tenant isolation on the traversed nodes. Collect every
        # artifact id reached and bulk-validate each belongs to the entry
        # artifact's project; drop cross-project edges so a neighbor from another
        # tenant cannot leak via the lineage graph.
        if project_id is not None:
            reached_ids: Set[str] = set()
            for entry in parents:
                reached_ids.add(entry["artifact_id"])
                reached_ids.add(entry["parent_artifact_id"])
            for entry in consumers:
                reached_ids.add(entry["consumer_artifact_id"])
                reached_ids.add(entry["parent_artifact_id"])
            reached_ids.discard(artifact_id)
            reached_ids.discard(None)
            if reached_ids:
                same_project_ids = set(db.execute(
                    select(Artifact.id).where(
                        Artifact.id.in_(reached_ids),
                        Artifact.project_id == project_id,
                    )
                ).scalars().all())
                parents = [
                    p for p in parents
                    if p["artifact_id"] == artifact_id or p["artifact_id"] in same_project_ids
                ]
                parents = [
                    p for p in parents
                    if p["parent_artifact_id"] == artifact_id or p["parent_artifact_id"] in same_project_ids
                ]
                consumers = [
                    c for c in consumers
                    if (c["consumer_artifact_id"] == artifact_id or c["consumer_artifact_id"] in same_project_ids)
                    and (c["parent_artifact_id"] == artifact_id or c["parent_artifact_id"] in same_project_ids)
                ]

        return {
            "artifact_id": artifact_id,
            "parents": parents,
            "consumers": consumers,
        }

    @staticmethod
    def _parent_dict(rec: ArtifactLineage, depth: int) -> Dict[str, Any]:
        return {
            "lineage_id": rec.id,
            "artifact_id": rec.artifact_id,
            "parent_artifact_id": rec.parent_artifact_id,
            "producing_tool": rec.producing_tool,
            "tool_version": rec.tool_version,
            "workflow_run_id": rec.workflow_run_id,
            "parameters": rec.parameters,
            "source_dataset_id": rec.source_dataset_id,
            "source_dataset_fingerprint": rec.source_dataset_fingerprint,
            "depth": depth,
            "created_at": rec.created_at.isoformat() if rec.created_at else None,
        }

    @staticmethod
    def _consumer_dict(rec: ArtifactLineage, depth: int) -> Dict[str, Any]:
        return {
            "lineage_id": rec.id,
            "consumer_artifact_id": rec.artifact_id,
            "parent_artifact_id": rec.parent_artifact_id,
            "producing_tool": rec.producing_tool,
            "tool_version": rec.tool_version,
            "workflow_run_id": rec.workflow_run_id,
            "parameters": rec.parameters,
            "depth": depth,
            "created_at": rec.created_at.isoformat() if rec.created_at else None,
        }
