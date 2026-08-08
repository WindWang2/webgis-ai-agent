"""
Artifact Lineage Provenance Service: Manages execute lineage graphs (parents -> artifact -> consumers).
"""
import uuid
import logging
from typing import Optional, List, Dict, Any
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
    def get_lineage_graph(db: Session, artifact_id: str) -> Dict[str, Any]:
        """
        Returns full upstream parents and downstream consumers for an artifact.
        """
        stmt = select(ArtifactLineage).where(
            or_(
                ArtifactLineage.artifact_id == artifact_id,
                ArtifactLineage.parent_artifact_id == artifact_id,
            )
        )
        records = list(db.execute(stmt).scalars().all())

        parents = []
        consumers = []
        for rec in records:
            if rec.artifact_id == artifact_id and rec.parent_artifact_id:
                parents.append({
                    "lineage_id": rec.id,
                    "parent_artifact_id": rec.parent_artifact_id,
                    "producing_tool": rec.producing_tool,
                    "workflow_run_id": rec.workflow_run_id,
                    "parameters": rec.parameters,
                    "created_at": rec.created_at.isoformat() if rec.created_at else None,
                })
            elif rec.parent_artifact_id == artifact_id:
                consumers.append({
                    "lineage_id": rec.id,
                    "consumer_artifact_id": rec.artifact_id,
                    "producing_tool": rec.producing_tool,
                    "workflow_run_id": rec.workflow_run_id,
                    "parameters": rec.parameters,
                    "created_at": rec.created_at.isoformat() if rec.created_at else None,
                })

        return {
            "artifact_id": artifact_id,
            "parents": parents,
            "consumers": consumers,
        }
