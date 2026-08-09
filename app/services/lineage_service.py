"""
Artifact Lineage Provenance Service: Manages execute lineage graphs (parents -> artifact -> consumers).
"""
import uuid
import logging
from typing import Optional, List, Dict, Any, Set
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.project import ArtifactLineage, Artifact

logger = logging.getLogger(__name__)


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
    ) -> List[ArtifactLineage]:
        # DATA-07: before recording, verify every parent artifact exists AND
        # belongs to the same project as the child. Previously arbitrary
        # parent_artifact_ids were accepted, enabling cross-project DAG links
        # that DATA-01's traversal then leaked across tenants.
        if parent_artifact_ids:
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

        lineage_records = []
        parents = parent_artifact_ids or [None]

        for parent_id in parents:
            lineage = ArtifactLineage(
                id=f"lin_{uuid.uuid4().hex[:16]}",
                artifact_id=artifact_id,
                parent_artifact_id=parent_id,
                producing_tool=producing_tool,
                tool_version=tool_version,
                workflow_run_id=workflow_run_id,
                parameters=parameters or {},
                created_at=datetime.now(timezone.utc),
            )
            db.add(lineage)
            lineage_records.append(lineage)

        db.commit()
        return lineage_records

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
        traversal walks parents/consumers without re-checking each belongs to
        the same tenant. ``project_id`` scopes the result: any node whose
        artifact does not belong to ``project_id`` is filtered out before
        return, closing a cross-tenant IDOR. Callers should pass the entry
        artifact's project_id.
        """
        parents: List[Dict[str, Any]] = []
        consumers: List[Dict[str, Any]] = []

        # Upstream recursive traversal
        visited_up: Set[str] = set()
        queue_up = [(artifact_id, 0)]

        while queue_up:
            curr_id, depth = queue_up.pop(0)
            if depth >= max_depth or curr_id in visited_up:
                continue
            visited_up.add(curr_id)

            stmt = select(ArtifactLineage).where(ArtifactLineage.artifact_id == curr_id)
            records = list(db.execute(stmt).scalars().all())
            for rec in records:
                if rec.parent_artifact_id and rec.parent_artifact_id not in visited_up:
                    parents.append({
                        "lineage_id": rec.id,
                        "artifact_id": rec.artifact_id,
                        "parent_artifact_id": rec.parent_artifact_id,
                        "producing_tool": rec.producing_tool,
                        "workflow_run_id": rec.workflow_run_id,
                        "parameters": rec.parameters,
                        "depth": depth + 1,
                        "created_at": rec.created_at.isoformat() if rec.created_at else None,
                    })
                    queue_up.append((rec.parent_artifact_id, depth + 1))

        # Downstream recursive traversal
        visited_down: Set[str] = set()
        queue_down = [(artifact_id, 0)]

        while queue_down:
            curr_id, depth = queue_down.pop(0)
            if depth >= max_depth or curr_id in visited_down:
                continue
            visited_down.add(curr_id)

            stmt = select(ArtifactLineage).where(ArtifactLineage.parent_artifact_id == curr_id)
            records = list(db.execute(stmt).scalars().all())
            for rec in records:
                if rec.artifact_id and rec.artifact_id not in visited_down:
                    consumers.append({
                        "lineage_id": rec.id,
                        "consumer_artifact_id": rec.artifact_id,
                        "parent_artifact_id": rec.parent_artifact_id,
                        "producing_tool": rec.producing_tool,
                        "workflow_run_id": rec.workflow_run_id,
                        "parameters": rec.parameters,
                        "depth": depth + 1,
                        "created_at": rec.created_at.isoformat() if rec.created_at else None,
                    })
                    queue_down.append((rec.artifact_id, depth + 1))

        # DATA-01: enforce tenant isolation on the traversed nodes. Collect
        # every artifact id reached and bulk-validate each belongs to the
        # entry artifact's project; drop any cross-project edges so a neighbor
        # from another tenant cannot leak via the lineage graph.
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
                    if c["consumer_artifact_id"] in same_project_ids
                ]

        return {
            "artifact_id": artifact_id,
            "parents": parents,
            "consumers": consumers,
        }
