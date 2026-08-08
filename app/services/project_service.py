"""
Project Service: Handles Project Workspace lifecycle, Dataset attachment, Workflow persistence,
Artifact tracking, and tenant isolation (IDOR protection).
"""
import uuid
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import select, and_, or_

from app.models.project import Project, ProjectDataset, Workflow, WorkflowRun, Artifact
from app.schemas.project_schema import ProjectUpdate, DatasetAttach, WorkflowCreate

logger = logging.getLogger(__name__)


class ProjectService:
    @staticmethod
    def create_project(
        db: Session,
        name: str,
        description: Optional[str] = None,
        org_id: Optional[int] = None,
        owner_id: Optional[str] = None,
        metadata_json: Optional[Dict[str, Any]] = None,
        commit: bool = True,
    ) -> Project:
        project_id = f"proj_{uuid.uuid4().hex[:16]}"
        project = Project(
            id=project_id,
            org_id=org_id,
            owner_id=owner_id,
            name=name,
            description=description,
            status="active",
            metadata_json=metadata_json or {},
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(project)
        if commit:
            db.commit()
            db.refresh(project)
        return project

    @staticmethod
    def get_project_with_auth(
        db: Session,
        project_id: str,
        user_id: Optional[str] = None,
        org_id: Optional[int] = None,
    ) -> Optional[Project]:
        """Tenant & Owner safe project lookup (IDOR protection)"""
        stmt = select(Project).where(Project.id == project_id)
        project = db.execute(stmt).scalar_one_or_none()
        if not project:
            return None

        # Tenant / Owner permission check
        if org_id and project.org_id and project.org_id != org_id:
            logger.warning(f"IDOR attempt: user {user_id} (org {org_id}) tried accessing project {project_id} (org {project.org_id})")
            return None
        if user_id and project.owner_id and project.owner_id != user_id and not org_id:
            logger.warning(f"IDOR attempt: user {user_id} tried accessing private project {project_id} (owner {project.owner_id})")
            return None

        return project

    @staticmethod
    def list_projects(
        db: Session,
        user_id: Optional[str] = None,
        org_id: Optional[int] = None,
        status: str = "active",
    ) -> List[Project]:
        stmt = select(Project).where(Project.status == status)
        if org_id:
            stmt = stmt.where(Project.org_id == org_id)
        elif user_id:
            stmt = stmt.where(or_(Project.owner_id == user_id, Project.owner_id.is_(None)))

        stmt = stmt.order_by(Project.updated_at.desc())
        return list(db.execute(stmt).scalars().all())

    @staticmethod
    def update_project(
        db: Session,
        project_id: str,
        data: ProjectUpdate,
        user_id: Optional[str] = None,
        org_id: Optional[int] = None,
    ) -> Optional[Project]:
        project = ProjectService.get_project_with_auth(db, project_id, user_id, org_id)
        if not project:
            return None

        if data.name is not None:
            project.name = data.name
        if data.description is not None:
            project.description = data.description
        if data.status is not None:
            project.status = data.status
        if data.metadata_json is not None:
            existing_meta = project.metadata_json or {}
            project.metadata_json = {**existing_meta, **data.metadata_json}

        project.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(project)
        return project

    @staticmethod
    def attach_dataset(
        db: Session,
        project_id: str,
        attach_data: DatasetAttach,
        user_id: Optional[str] = None,
        org_id: Optional[int] = None,
    ) -> Optional[ProjectDataset]:
        project = ProjectService.get_project_with_auth(db, project_id, user_id, org_id)
        if not project:
            return None

        dataset_id = f"ds_{uuid.uuid4().hex[:16]}"
        version_fingerprint = uuid.uuid4().hex[:12]

        dataset = ProjectDataset(
            id=dataset_id,
            project_id=project_id,
            name=attach_data.name,
            source_type=attach_data.source_type,
            source_ref=attach_data.source_ref or "",
            schema_profile=attach_data.schema_profile or {},
            crs=attach_data.crs,
            quality_status="unchecked",
            version_fingerprint=version_fingerprint,
            created_at=datetime.now(timezone.utc),
        )
        db.add(dataset)
        db.commit()
        db.refresh(dataset)
        return dataset

    @staticmethod
    def detach_dataset(
        db: Session,
        project_id: str,
        dataset_id: str,
        user_id: Optional[str] = None,
        org_id: Optional[int] = None,
    ) -> bool:
        project = ProjectService.get_project_with_auth(db, project_id, user_id, org_id)
        if not project:
            return False

        stmt = select(ProjectDataset).where(
            and_(ProjectDataset.id == dataset_id, ProjectDataset.project_id == project_id)
        )
        dataset = db.execute(stmt).scalar_one_or_none()
        if not dataset:
            return False

        db.delete(dataset)
        db.commit()
        return True

    @staticmethod
    def list_project_datasets(
        db: Session,
        project_id: str,
        user_id: Optional[str] = None,
        org_id: Optional[int] = None,
    ) -> List[ProjectDataset]:
        project = ProjectService.get_project_with_auth(db, project_id, user_id, org_id)
        if not project:
            return []

        stmt = select(ProjectDataset).where(ProjectDataset.project_id == project_id).order_by(ProjectDataset.created_at.desc())
        return list(db.execute(stmt).scalars().all())

    @staticmethod
    def list_project_artifacts(
        db: Session,
        project_id: str,
        user_id: Optional[str] = None,
        org_id: Optional[int] = None,
    ) -> List[Artifact]:
        project = ProjectService.get_project_with_auth(db, project_id, user_id, org_id)
        if not project:
            return []

        stmt = select(Artifact).where(Artifact.project_id == project_id).order_by(Artifact.created_at.desc())
        return list(db.execute(stmt).scalars().all())

    @staticmethod
    def save_workflow(
        db: Session,
        project_id: str,
        workflow_data: WorkflowCreate,
        user_id: Optional[str] = None,
        org_id: Optional[int] = None,
    ) -> Optional[Workflow]:
        project = ProjectService.get_project_with_auth(db, project_id, user_id, org_id)
        if not project:
            return None

        workflow_id = f"wf_{uuid.uuid4().hex[:16]}"
        workflow = Workflow(
            id=workflow_id,
            project_id=project_id,
            name=workflow_data.name,
            description=workflow_data.description,
            version=1,
            graph_spec=workflow_data.graph_spec.model_dump(),
            inputs_schema=workflow_data.inputs_schema or {},
            created_from_session=workflow_data.created_from_session,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(workflow)
        db.commit()
        db.refresh(workflow)
        return workflow

    @staticmethod
    def list_project_workflows(
        db: Session,
        project_id: str,
        user_id: Optional[str] = None,
        org_id: Optional[int] = None,
    ) -> List[Workflow]:
        project = ProjectService.get_project_with_auth(db, project_id, user_id, org_id)
        if not project:
            return []

        stmt = select(Workflow).where(Workflow.project_id == project_id).order_by(Workflow.updated_at.desc())
        return list(db.execute(stmt).scalars().all())

    @staticmethod
    def list_workflow_runs(
        db: Session,
        project_id: str,
        workflow_id: Optional[str] = None,
        user_id: Optional[str] = None,
        org_id: Optional[int] = None,
    ) -> List[WorkflowRun]:
        project = ProjectService.get_project_with_auth(db, project_id, user_id, org_id)
        if not project:
            return []

        stmt = select(WorkflowRun).join(Workflow).where(Workflow.project_id == project_id)
        if workflow_id:
            stmt = stmt.where(WorkflowRun.workflow_id == workflow_id)

        stmt = stmt.order_by(WorkflowRun.created_at.desc())
        return list(db.execute(stmt).scalars().all())
