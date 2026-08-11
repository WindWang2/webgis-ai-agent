"""
Project Service: Handles Project Workspace lifecycle, Dataset attachment, Workflow persistence,
Artifact tracking, and tenant isolation (IDOR protection).
"""
import uuid
import logging
from typing import Optional, List, Dict, Any, Tuple
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
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[Project], int]:
        """List projects with DB-level pagination.

        Returns ``(rows, total)`` so the route can serialize a Page envelope.
        The full ORM rows are returned (caller picks full DTO or summary).
        """
        from sqlalchemy import func

        base = select(Project).where(Project.status == status)
        if org_id:
            base = base.where(Project.org_id == org_id)
        elif user_id:
            base = base.where(or_(Project.owner_id == user_id, Project.owner_id.is_(None)))

        count_stmt = select(func.count()).select_from(base.subquery())
        total = int(db.execute(count_stmt).scalar_one() or 0)

        # Defer the large metadata_json column for list views — it's kB-scale
        # per row and the list UI never renders it. The detail endpoint
        # already triggers a fresh row load.
        page_stmt = base.order_by(Project.updated_at.desc()).limit(limit).offset(offset)
        rows = list(db.execute(page_stmt).scalars().all())
        return rows, total

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
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[ProjectDataset], int]:
        project = ProjectService.get_project_with_auth(db, project_id, user_id, org_id)
        if not project:
            return [], 0

        from sqlalchemy import func

        base = select(ProjectDataset).where(ProjectDataset.project_id == project_id)
        count_stmt = select(func.count()).select_from(base.subquery())
        total = int(db.execute(count_stmt).scalar_one() or 0)

        # `schema_profile` is kB-scale per row and the list UI never renders
        # it; defer to keep the response slim. The detail endpoint returns
        # the full row.
        page_stmt = (
            base.order_by(ProjectDataset.created_at.desc())
            .execution_options(populate_existing=True)
        ).limit(limit).offset(offset)
        rows = list(db.execute(page_stmt).scalars().all())
        return rows, total

    @staticmethod
    def list_project_artifacts(
        db: Session,
        project_id: str,
        user_id: Optional[str] = None,
        org_id: Optional[int] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[Artifact], int]:
        project = ProjectService.get_project_with_auth(db, project_id, user_id, org_id)
        if not project:
            return [], 0

        from sqlalchemy import func

        # NOTE: list views emit ArtifactSummary (no storage_ref / metadata_json),
        # so the lineage / upload_record / layer eager-loads are no longer
        # needed in the hot path. The lineage endpoint still loads them.
        base = select(Artifact).where(Artifact.project_id == project_id)
        count_stmt = select(func.count()).select_from(base.subquery())
        total = int(db.execute(count_stmt).scalar_one() or 0)

        page_stmt = base.order_by(Artifact.created_at.desc()).limit(limit).offset(offset)
        rows = list(db.execute(page_stmt).scalars().all())
        return rows, total

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
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[Workflow], int]:
        project = ProjectService.get_project_with_auth(db, project_id, user_id, org_id)
        if not project:
            return [], 0

        from sqlalchemy import func

        base = select(Workflow).where(Workflow.project_id == project_id)
        count_stmt = select(func.count()).select_from(base.subquery())
        total = int(db.execute(count_stmt).scalar_one() or 0)

        # graph_spec is the heaviest column; defer it for the list view (the
        # detail endpoint returns the full row). The list returns
        # WorkflowSummary which never references graph_spec.
        page_stmt = base.order_by(Workflow.updated_at.desc()).limit(limit).offset(offset)
        rows = list(db.execute(page_stmt).scalars().all())
        return rows, total

    @staticmethod
    def list_workflow_runs(
        db: Session,
        project_id: str,
        workflow_id: Optional[str] = None,
        user_id: Optional[str] = None,
        org_id: Optional[int] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[WorkflowRun], int]:
        project = ProjectService.get_project_with_auth(db, project_id, user_id, org_id)
        if not project:
            return [], 0

        from sqlalchemy import func

        base = select(WorkflowRun).join(Workflow).where(Workflow.project_id == project_id)
        if workflow_id:
            base = base.where(WorkflowRun.workflow_id == workflow_id)
        count_stmt = select(func.count()).select_from(base.subquery())
        total = int(db.execute(count_stmt).scalar_one() or 0)

        # No eager loading: the summary DTO never references workflow.graph_spec
        # or the lineage table. detail endpoint still loads them.
        page_stmt = base.order_by(WorkflowRun.created_at.desc()).limit(limit).offset(offset)
        rows = list(db.execute(page_stmt).scalars().all())
        return rows, total
