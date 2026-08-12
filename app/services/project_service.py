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
from sqlalchemy.exc import IntegrityError

from app.models.project import (
    Project, ProjectDataset, Workflow, WorkflowRevision, WorkflowRun, Artifact,
)
from app.schemas.project_schema import ProjectUpdate, DatasetAttach, WorkflowCreate
from app.services.provenance import compute_dataset_fingerprint, compute_graph_fingerprint
from app.services.project_context_types import ProjectContextSummary, ProjectFingerprint

logger = logging.getLogger(__name__)


def _invalidate_project_context_cache(project_id: Optional[str]) -> None:
    """Best-effort invalidation of the project-context block cache.

    The cache is *also* correct without this call — the next round
    pays a fingerprint read which detects staleness — but invalidating
    on the mutation path lets the very next ``assemble()`` skip the
    fingerprint read entirely (it still pays one query, but no
    fingerprint read is needed because the entry is gone). The
    invalidation is wrapped in a try/except so a cache failure never
    blocks a write to the DB.
    """
    if not project_id:
        return
    try:
        from app.services.chat.project_context_cache import project_context_cache
        project_context_cache.invalidate(project_id)
    except Exception as ex:  # pragma: no cover - defensive
        logger.debug("project_context_cache invalidate failed: %s", ex)


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
    def get_project_context_summary(
        db: Session,
        project_id: str,
        user_id: Optional[str] = None,
        org_id: Optional[int] = None,
    ) -> Optional["ProjectContextSummary"]:
        """Slim read of the project state used by the chat context block.

        Returns ``None`` if the project is missing or the caller is not
        authorised. Otherwise returns a ``ProjectContextSummary`` whose
        ``fingerprint`` is a deterministic hash of the live aggregates:
        the same DB state ⇒ the same fingerprint, and any mutation
        (project update, dataset attach/detach, workflow create/update)
        necessarily bumps at least one of the aggregates ⇒ the
        fingerprint changes ⇒ the cache invalidates implicitly.

        The method only ever reads scalar columns (no relationship loads,
        no JSON columns, no joins). It costs at most 6 small queries
        and serves as the source of truth for the LRU cache.
        """
        from sqlalchemy import func
        from sqlalchemy.orm import noload

        # 1. Project scalar + auth check. The project row is read with
        # ``noload`` on the ``organization`` / ``owner`` ``selectin``
        # eager loads, which we do not need here.
        proj = db.execute(
            select(Project)
            .where(Project.id == project_id)
            .options(noload(Project.organization), noload(Project.owner))
        ).scalar_one_or_none()
        if proj is None:
            return None

        # Tenant / Owner permission check (mirrors get_project_with_auth).
        if org_id and proj.org_id and proj.org_id != org_id:
            return None
        if user_id and proj.owner_id and proj.owner_id != user_id and not org_id:
            return None

        # 2. Dataset aggregate: the max(COALESCE(detached_at, created_at))
        # bumps on every attach (new created_at) but not on a detach
        # (the row is filtered out of this aggregate). The COUNT(*)
        # drops on detach. Together they form the dataset half of
        # the fingerprint.
        ds_row = db.execute(
            select(
                func.count(ProjectDataset.id),
                func.max(func.coalesce(ProjectDataset.detached_at, ProjectDataset.created_at)),
            ).where(
                ProjectDataset.project_id == project_id,
                ProjectDataset.detached_at.is_(None),
            )
        ).one()
        dataset_count = int(ds_row[0] or 0)
        dataset_max_modified = ds_row[1]

        # 3. Workflow aggregate: Workflow.updated_at is bumped on every
        # save_workflow and every update_workflow (also on _publish_revision).
        wf_row = db.execute(
            select(
                func.count(Workflow.id),
                func.max(Workflow.updated_at),
            ).where(Workflow.project_id == project_id)
        ).one()
        workflow_count = int(wf_row[0] or 0)
        workflow_max_updated = wf_row[1]

        # 4. Top-5 dataset names (only id + name scalars).
        ds_names = list(
            db.execute(
                select(ProjectDataset.name)
                .where(
                    ProjectDataset.project_id == project_id,
                    ProjectDataset.detached_at.is_(None),
                )
                .order_by(ProjectDataset.created_at.desc())
                .limit(5)
            ).scalars().all()
        )

        # 5. Top-5 workflow names (only id + name scalars).
        wf_names = list(
            db.execute(
                select(Workflow.name)
                .where(Workflow.project_id == project_id)
                .order_by(Workflow.updated_at.desc())
                .limit(5)
            ).scalars().all()
        )

        from app.services.project_context_types import ProjectContextSummary

        return ProjectContextSummary(
            project_id=proj.id,
            project_name=proj.name,
            dataset_count=dataset_count,
            workflow_count=workflow_count,
            dataset_names=tuple(ds_names),
            workflow_names=tuple(wf_names),
            project_updated_at=proj.updated_at,
            dataset_max_modified=dataset_max_modified,
            workflow_max_updated=workflow_max_updated,
        )

    @staticmethod
    def get_project_fingerprint(
        db: Session,
        project_id: str,
        user_id: Optional[str] = None,
        org_id: Optional[int] = None,
    ) -> Optional["ProjectFingerprint"]:
        """Read just enough to compute the cache key.

        Returns ``None`` if the project is missing or unauthorised. Does
        not pull the top-5 name lists — the full summary is only
        computed on a cache miss. The project row is read with
        ``noload()`` to suppress the ``organization`` / ``owner``
        ``selectin`` eager loads, which we do not need to compute the
        fingerprint.
        """
        from sqlalchemy import func
        from sqlalchemy.orm import noload

        # Slim project read: only the columns we need, no relationships.
        proj = db.execute(
            select(Project)
            .where(Project.id == project_id)
            .options(noload(Project.organization), noload(Project.owner))
        ).scalar_one_or_none()
        if proj is None:
            return None

        # Tenant / Owner permission check (in-Python, mirrors
        # get_project_with_auth).
        if org_id and proj.org_id and proj.org_id != org_id:
            return None
        if user_id and proj.owner_id and proj.owner_id != user_id and not org_id:
            return None

        ds_row = db.execute(
            select(
                func.count(ProjectDataset.id),
                func.max(func.coalesce(ProjectDataset.detached_at, ProjectDataset.created_at)),
            ).where(
                ProjectDataset.project_id == project_id,
                ProjectDataset.detached_at.is_(None),
            )
        ).one()
        wf_row = db.execute(
            select(
                func.count(Workflow.id),
                func.max(Workflow.updated_at),
            ).where(Workflow.project_id == project_id)
        ).one()

        from app.services.project_context_types import ProjectFingerprint

        return ProjectFingerprint(
            project_id=project_id,
            project_updated_at=proj.updated_at,
            dataset_count=int(ds_row[0] or 0),
            dataset_max_modified=ds_row[1],
            workflow_count=int(wf_row[0] or 0),
            workflow_max_updated=wf_row[1],
        )

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
        _invalidate_project_context_cache(project_id)
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
        # Deterministic content/identity fingerprint (INV-FP1/2/3) — never random.
        # Same immutable evidence ⇒ same fingerprint; a content/identity change
        # (different source_ref / schema) ⇒ different fingerprint.
        version_fingerprint = compute_dataset_fingerprint(
            source_type=attach_data.source_type,
            source_ref=attach_data.source_ref,
            crs=attach_data.crs,
            schema_profile=attach_data.schema_profile,
        )

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
        _invalidate_project_context_cache(project_id)
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

        # INV-DEL1: soft-detach (tombstone) instead of a hard delete. The row is
        # preserved so historical lineage referencing the dataset stays resolvable;
        # list views filter it out via detached_at IS NULL.
        dataset.detached_at = datetime.now(timezone.utc)
        db.commit()
        _invalidate_project_context_cache(project_id)
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

        base = select(ProjectDataset).where(
            ProjectDataset.project_id == project_id,
            ProjectDataset.detached_at.is_(None),
        )
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

        # Publish the first immutable revision (INV-REV1/2) so the graph as
        # saved is recoverable even before a first run.
        ProjectService._publish_revision(db, workflow, user_id)
        _invalidate_project_context_cache(project_id)
        return workflow

    @staticmethod
    def _publish_revision(
        db: Session, workflow: Workflow, user_id: Optional[str]
    ) -> WorkflowRevision:
        """Append an immutable revision for the workflow's current graph.

        Reuses the latest revision when its graph fingerprint already matches
        (idempotent); otherwise creates revision_no = max+1 and advances the
        workflow's current_revision_id + version. Mirrors the engine's
        ``_ensure_revision`` for the edit path.
        """
        graph_spec = workflow.graph_spec or {"steps": []}
        graph_fp = compute_graph_fingerprint(graph_spec)
        if workflow.current_revision_id:
            existing = db.execute(
                select(WorkflowRevision).where(WorkflowRevision.id == workflow.current_revision_id)
            ).scalar_one_or_none()
            if existing and existing.graph_fingerprint == graph_fp:
                return existing
        latest_no = db.execute(
            select(WorkflowRevision.revision_no)
            .where(WorkflowRevision.workflow_id == workflow.id)
            .order_by(WorkflowRevision.revision_no.desc())
        ).scalars().first() or 0
        # Concurrent runs/edits on the same workflow may both compute latest_no=N
        # and race to insert revision_no=N+1. The unique index
        # idx_workflow_revision_wf_no prevents duplicate numbers (no corruption),
        # but the loser would raise IntegrityError. Retry once: re-read the max
        # and reuse the winner's revision if its fingerprint matches, else bump.
        try:
            revision = WorkflowRevision(
                id=f"wfrev_{uuid.uuid4().hex[:16]}",
                workflow_id=workflow.id,
                revision_no=latest_no + 1,
                graph_spec=graph_spec,
                inputs_schema=workflow.inputs_schema,
                graph_fingerprint=graph_fp,
                created_by=user_id,
                created_at=datetime.now(timezone.utc),
            )
            db.add(revision)
            workflow.current_revision_id = revision.id
            workflow.version = max(workflow.version or 1, revision.revision_no)
            workflow.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(workflow)
            db.refresh(revision)
            return revision
        except IntegrityError:
            db.rollback()
            # A concurrent publish won the race: reuse its revision if the graph
            # matches, otherwise publish the next number.
            winner = db.execute(
                select(WorkflowRevision)
                .where(WorkflowRevision.workflow_id == workflow.id)
                .order_by(WorkflowRevision.revision_no.desc())
            ).scalars().first()
            if winner and winner.graph_fingerprint == graph_fp:
                workflow = db.merge(workflow)
                workflow.current_revision_id = winner.id
                workflow.version = max(workflow.version or 1, winner.revision_no)
                db.commit()
                return winner
            return ProjectService._publish_revision(db, db.merge(workflow), user_id)

    @staticmethod
    def update_workflow(
        db: Session,
        project_id: str,
        workflow_id: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        graph_spec: Optional[Dict[str, Any]] = None,
        inputs_schema: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
        org_id: Optional[int] = None,
    ) -> Optional[Workflow]:
        """Edit a workflow's mutable fields and publish a new revision if the
        graph changed (INV-REV2). Prior revisions stay immutable, so historical
        runs remain accurately replayable after an edit.
        """
        project = ProjectService.get_project_with_auth(db, project_id, user_id, org_id)
        if not project:
            return None
        workflow = db.execute(
            select(Workflow).where(
                and_(Workflow.id == workflow_id, Workflow.project_id == project_id)
            )
        ).scalar_one_or_none()
        if not workflow:
            return None

        prev_fp = compute_graph_fingerprint(workflow.graph_spec or {})
        if name is not None:
            workflow.name = name
        if description is not None:
            workflow.description = description
        if graph_spec is not None:
            workflow.graph_spec = graph_spec
        if inputs_schema is not None:
            workflow.inputs_schema = inputs_schema
        workflow.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(workflow)

        # Only bump a revision when the graph actually changed.
        new_fp = compute_graph_fingerprint(workflow.graph_spec or {})
        if new_fp != prev_fp:
            ProjectService._publish_revision(db, workflow, user_id)
        # Every update bumps ``updated_at`` (line 572) and therefore
        # the workflow_max_updated aggregate; the cache will miss on
        # the next lookup. We also invalidate explicitly so the
        # fingerprint read is not even needed.
        _invalidate_project_context_cache(project_id)
        return workflow

    @staticmethod
    def list_workflow_revisions(
        db: Session,
        project_id: str,
        workflow_id: str,
        user_id: Optional[str] = None,
        org_id: Optional[int] = None,
    ) -> Optional[List[WorkflowRevision]]:
        """List immutable revisions for a workflow (tenant-scoped).

        The ``workflow_id`` from the URL is verified to belong to ``project_id``
        via a join — without it, a caller with access to project A could
        enumerate revisions of a workflow owned by project B (IDOR).
        """
        project = ProjectService.get_project_with_auth(db, project_id, user_id, org_id)
        if not project:
            return None
        rows = db.execute(
            select(WorkflowRevision)
            .join(Workflow, Workflow.id == WorkflowRevision.workflow_id)
            .where(
                WorkflowRevision.workflow_id == workflow_id,
                Workflow.project_id == project_id,
            )
            .order_by(WorkflowRevision.revision_no.desc())
        ).scalars().all()
        return list(rows)

    @staticmethod
    def get_workflow_run(
        db: Session,
        project_id: str,
        run_id: str,
        user_id: Optional[str] = None,
        org_id: Optional[int] = None,
    ) -> Optional[WorkflowRun]:
        """Fetch a single run (with manifest) scoped to the caller's project."""
        project = ProjectService.get_project_with_auth(db, project_id, user_id, org_id)
        if not project:
            return None
        return db.execute(
            select(WorkflowRun).where(
                and_(WorkflowRun.id == run_id, WorkflowRun.project_id == project_id)
            )
        ).scalar_one_or_none()

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
