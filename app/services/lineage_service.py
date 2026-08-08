"""
Artifact Lineage Provenance Service: Manages execute lineage graphs (parents -> artifact -> consumers).
"""
import uuid
import logging
from typing import Optional, List, Dict, Any, Set
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import select, or_

from app.models.project import Artifact, ArtifactLineage

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
    def get_lineage_graph(db: Session, artifact_id: str, max_depth: int = 5) -> Dict[str, Any]:
        """
        Returns full multi-hop upstream parents and downstream consumers for an artifact safely.
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

        return {
            "artifact_id": artifact_id,
            "parents": parents,
            "consumers": consumers,
        }
