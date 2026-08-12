"""
Project Workspace, Dataset, Workflow, and Artifact SQLAlchemy ORM models.
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, BigInteger, ForeignKey, Index, JSON, CheckConstraint
)
from sqlalchemy.orm import relationship
from app.core.database import Base


class Project(Base):
    """项目工作空间表"""
    __tablename__ = "projects"

    id = Column(String(255), primary_key=True, default=lambda: str(uuid.uuid4()))
    org_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True)
    owner_id = Column(String(255), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="active")
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_project_org_id", "org_id"),
        Index("idx_project_owner_id", "owner_id"),
        Index("idx_project_status", "status"),
        CheckConstraint("status IN ('active', 'archived', 'deleted')", name="ck_project_status"),
    )

    organization = relationship("Organization", backref="projects", lazy="selectin")
    owner = relationship("User", backref="projects", lazy="selectin")
    datasets = relationship("ProjectDataset", back_populates="project", cascade="all, delete-orphan")
    workflows = relationship("Workflow", back_populates="project", cascade="all, delete-orphan")
    artifacts = relationship("Artifact", back_populates="project", cascade="all, delete-orphan")


class ProjectDataset(Base):
    """项目数据集关联表"""
    __tablename__ = "project_datasets"

    id = Column(String(255), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String(255), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    source_type = Column(String(50), nullable=False)
    source_ref = Column(String(255), nullable=True)
    schema_profile = Column(JSON, nullable=True)
    crs = Column(String(100), default="EPSG:4326")
    quality_status = Column(String(20), default="unchecked")
    # Deterministic content/identity fingerprint (computed, never random — see
    # app.services.provenance.fingerprint). Same immutable evidence ⇒ same
    # fingerprint; content/identity change ⇒ different fingerprint.
    version_fingerprint = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    # Soft-detach tombstone (INV-DEL1). Detaching sets this instead of deleting
    # the row so historical lineage referencing the dataset stays resolvable.
    # List views filter detached_at IS NULL.
    detached_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_project_dataset_project_id", "project_id"),
        Index("idx_project_dataset_source_type", "source_type"),
        Index("idx_project_dataset_pid_created", "project_id", "created_at"),
        # Cheap "active datasets for a project" lookup that skips tombstones.
        Index("idx_project_dataset_pid_detached", "project_id", "detached_at"),
        CheckConstraint(
            "quality_status IN ('unchecked', 'valid', 'invalid', 'warning', 'unknown', 'pending', 'verified')",
            name="ck_project_dataset_quality_status",
        ),
    )

    project = relationship("Project", back_populates="datasets")


class Workflow(Base):
    """工作流定义表"""
    __tablename__ = "workflows"

    id = Column(String(255), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String(255), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    version = Column(Integer, nullable=False, default=1)
    graph_spec = Column(JSON, nullable=True)
    inputs_schema = Column(JSON, nullable=True)
    created_from_session = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    # Pointer to the latest immutable WorkflowRevision (INV-REV). The Workflow
    # row itself stays mutable for editing, but every published graph creates an
    # append-only revision row that runs snapshot against.
    current_revision_id = Column(String(255), nullable=True)

    __table_args__ = (
        Index("idx_workflow_project_id", "project_id"),
        Index("idx_workflow_session", "created_from_session"),
        Index("idx_workflow_current_revision", "current_revision_id"),
        CheckConstraint("version >= 1", name="ck_workflow_version_pos"),
    )

    project = relationship("Project", back_populates="workflows")
    runs = relationship("WorkflowRun", back_populates="workflow", cascade="all, delete-orphan")
    revisions = relationship(
        "WorkflowRevision",
        back_populates="workflow",
        cascade="all, delete-orphan",
        order_by="WorkflowRevision.revision_no",
    )
    # NOTE: no direct relationship to the current revision — current_revision_id
    # is a plain pointer column (no FK constraint, to avoid a circular-FK
    # create_alter dance). Load it explicitly via the revisions collection.


class WorkflowRevision(Base):
    """Immutable, append-only snapshot of a workflow's graph (INV-REV1/REV2).

    A run references a revision so the *exact* graph that executed can always be
    recovered even after the parent Workflow row is edited. graph_fingerprint is
    sha256 of the canonical graph_spec, so identical graphs collapse to the same
    revision identity.
    """
    __tablename__ = "workflow_revisions"

    id = Column(String(255), primary_key=True, default=lambda: str(uuid.uuid4()))
    workflow_id = Column(String(255), ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False)
    revision_no = Column(Integer, nullable=False, default=1)
    graph_spec = Column(JSON, nullable=False)
    inputs_schema = Column(JSON, nullable=True)
    graph_fingerprint = Column(String(64), nullable=False)
    created_by = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        # A workflow has at most one revision per number.
        Index("idx_workflow_revision_wf_no", "workflow_id", "revision_no", unique=True),
        Index("idx_workflow_revision_wf_created", "workflow_id", "created_at"),
        Index("idx_workflow_revision_fingerprint", "graph_fingerprint"),
    )

    workflow = relationship("Workflow", foreign_keys=[workflow_id], back_populates="revisions")


class WorkflowRun(Base):
    """工作流执行实例记录表"""
    __tablename__ = "workflow_runs"

    id = Column(String(255), primary_key=True, default=lambda: str(uuid.uuid4()))
    workflow_id = Column(String(255), ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False)
    workflow_version = Column(Integer, nullable=False, default=1)
    # Denormalized tenant column: lets compare/list filter by project without a
    # join and closes the _WR.project_id AttributeError latent bug (F10).
    project_id = Column(String(255), nullable=True)
    # Exact immutable revision that executed (INV-SNAP1). The run is
    # self-describing via graph_snapshot even if this revision is later removed.
    workflow_revision_id = Column(String(255), nullable=True)
    graph_snapshot = Column(JSON, nullable=True)
    input_bindings = Column(JSON, nullable=True)
    # Fingerprints of input datasets captured at run start (INV-SNAP2). Resume
    # compares these against the current dataset fingerprints to detect staleness.
    input_dataset_fingerprints = Column(JSON, nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    execution_trace = Column(JSON, nullable=True)
    outputs = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    cost_perf_summary = Column(JSON, nullable=True)
    # step_ids that succeeded (INV-PART1/2/3). A failed run with populated
    # completed_steps is a partial run whose earlier artifacts are still valid.
    completed_steps = Column(JSON, nullable=True)
    # Canonical reproducibility manifest + its fingerprint (INV-MAN1/2).
    run_manifest = Column(JSON, nullable=True)
    run_fingerprint = Column(String(64), nullable=True)
    # Optional link to the unified durable job runtime (ADR-0052). Integration,
    # not a rewrite: a run MAY be backed by a durable AnalysisTask row.
    durable_job_id = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_workflow_run_workflow_id", "workflow_id"),
        Index("idx_workflow_run_status", "status"),
        Index("idx_workflow_run_wid_created", "workflow_id", "created_at"),
        # Tenant-scoped run list/compare without a Workflow join.
        Index("idx_workflow_run_project_created", "project_id", "created_at"),
        Index("idx_workflow_run_fingerprint", "run_fingerprint"),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_workflow_run_status",
        ),
    )

    workflow = relationship("Workflow", back_populates="runs")
    lineages = relationship("ArtifactLineage", back_populates="workflow_run")


class Artifact(Base):
    """项目产物记录表"""
    __tablename__ = "artifacts"

    id = Column(String(255), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String(255), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    artifact_type = Column(String(50), nullable=False)
    format = Column(String(50), nullable=True)
    # No Python default: CRS is derived from the real tool result. Unknown CRS is
    # persisted as NULL (truthful) rather than a fabricated "EPSG:4326" (INV-ART1).
    crs = Column(String(100), nullable=True)
    storage_ref = Column(String(500), nullable=True)
    upload_record_id = Column(Integer, ForeignKey("uploads.id", ondelete="SET NULL"), nullable=True)
    layer_id = Column(BigInteger, ForeignKey("layers.id", ondelete="SET NULL"), nullable=True)
    metadata_json = Column(JSON, nullable=True)
    # Truthful content/descriptor fingerprint extracted from the tool result
    # (INV-ART1). Ref_id + feature_count + bbox when available, else a stable
    # hash of the result descriptor. Never fabricated.
    content_fingerprint = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_artifact_project_id", "project_id"),
        Index("idx_artifact_type", "artifact_type"),
        Index("idx_artifact_layer_id", "layer_id"),
        Index("idx_artifact_upload_record_id", "upload_record_id"),
        Index("idx_artifact_content_fingerprint", "content_fingerprint"),
    )

    project = relationship("Project", back_populates="artifacts")
    upload_record = relationship("UploadRecord", foreign_keys=[upload_record_id], lazy="selectin")
    layer = relationship("Layer", foreign_keys=[layer_id], lazy="selectin")
    lineages = relationship(
        "ArtifactLineage",
        foreign_keys="[ArtifactLineage.artifact_id]",
        back_populates="artifact",
        cascade="all, delete-orphan",
    )
    parent_lineages = relationship(
        "ArtifactLineage",
        foreign_keys="[ArtifactLineage.parent_artifact_id]",
        back_populates="parent_artifact",
    )


class ArtifactLineage(Base):
    """产物血缘关系追踪表"""
    __tablename__ = "artifact_lineages"

    id = Column(String(255), primary_key=True, default=lambda: str(uuid.uuid4()))
    artifact_id = Column(String(255), ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False)
    parent_artifact_id = Column(String(255), ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=True)
    producing_tool = Column(String(100), nullable=True)
    tool_version = Column(String(50), nullable=True)
    workflow_run_id = Column(String(255), ForeignKey("workflow_runs.id", ondelete="SET NULL"), nullable=True)
    parameters = Column(JSON, nullable=True)
    # Input-dataset provenance (INV-LIN4 / §26). A root artifact (no parent
    # artifact) can still record the input dataset + its frozen fingerprint that
    # seeded this branch of the lineage, without fabricating a synthetic artifact.
    source_dataset_id = Column(String(255), nullable=True)
    source_dataset_fingerprint = Column(String(64), nullable=True)
    # Denormalized content fingerprint of the child artifact for cheap duplicate
    # detection along the lineage edge.
    content_fingerprint = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_lineage_artifact_id", "artifact_id"),
        Index("idx_lineage_parent_artifact_id", "parent_artifact_id"),
        Index("idx_lineage_workflow_run_id", "workflow_run_id"),
        Index("idx_lineage_source_dataset_id", "source_dataset_id"),
    )

    artifact = relationship("Artifact", foreign_keys=[artifact_id], back_populates="lineages", lazy="selectin")
    parent_artifact = relationship("Artifact", foreign_keys=[parent_artifact_id], back_populates="parent_lineages", lazy="selectin")
    workflow_run = relationship("WorkflowRun", foreign_keys=[workflow_run_id], back_populates="lineages", lazy="selectin")


__all__ = [
    "Project",
    "ProjectDataset",
    "Workflow",
    "WorkflowRevision",
    "WorkflowRun",
    "Artifact",
    "ArtifactLineage",
]
