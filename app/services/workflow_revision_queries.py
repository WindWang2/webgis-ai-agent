"""Bounded workflow revision reads; authorization remains with ProjectService."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.project import Workflow, WorkflowRevision
from app.core.database import SessionLocal


def workflow_belongs_to_project(project_id: str, workflow_id: str) -> bool:
    with SessionLocal() as db:
        return db.execute(select(Workflow.id).where(
            Workflow.id == workflow_id, Workflow.project_id == project_id,
        )).first() is not None


def revision_page(db: Session, project_id: str, workflow_id: str, limit: int, offset: int) -> tuple[list[WorkflowRevision], int]:
    predicate = (WorkflowRevision.workflow_id == workflow_id, Workflow.project_id == project_id)
    total = int(db.execute(select(func.count()).select_from(WorkflowRevision)
                           .join(Workflow, Workflow.id == WorkflowRevision.workflow_id)
                           .where(*predicate)).scalar_one())
    rows = db.execute(select(WorkflowRevision)
                      .join(Workflow, Workflow.id == WorkflowRevision.workflow_id)
                      .where(*predicate).order_by(WorkflowRevision.revision_no.desc())
                      .limit(limit).offset(offset)).scalars().all()
    return list(rows), total


def revision_by_id(db: Session, project_id: str, workflow_id: str, revision_id: str) -> WorkflowRevision | None:
    return db.execute(select(WorkflowRevision)
                      .join(Workflow, Workflow.id == WorkflowRevision.workflow_id)
                      .where(Workflow.project_id == project_id, WorkflowRevision.workflow_id == workflow_id,
                             WorkflowRevision.id == revision_id)).scalar_one_or_none()
