"""
Project Workspace, Persistent Workflow, Artifact, Lineage, and Quality API Schemas
"""
from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict


class ProjectCreate(BaseModel):
    name: str = Field(..., max_length=255, description="项目名称")
    description: Optional[str] = Field(None, description="项目描述")
    metadata_json: Optional[Dict[str, Any]] = Field(default_factory=dict, description="扩展元数据")


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    status: Optional[str] = Field(None, description="active / archived")
    metadata_json: Optional[Dict[str, Any]] = None


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    org_id: Optional[int] = None
    owner_id: Optional[str] = None
    name: str
    description: Optional[str] = None
    status: str
    metadata_json: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class DatasetAttach(BaseModel):
    name: str = Field(..., max_length=255)
    source_type: str = Field(..., description="upload / layer / external / vector / raster")
    source_ref: Optional[str] = Field(None, description="UploadRecord ID or Layer ID or URL")
    schema_profile: Optional[Dict[str, Any]] = Field(default_factory=dict)
    crs: str = Field(default="EPSG:4326")


class ProjectDatasetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    name: str
    source_type: str
    source_ref: Optional[str] = None
    schema_profile: Dict[str, Any] = Field(default_factory=dict)
    crs: Optional[str] = "EPSG:4326"
    quality_status: Optional[str] = "unchecked"
    version_fingerprint: Optional[str] = None
    created_at: datetime


class WorkflowStepSpec(BaseModel):
    step_id: str
    tool_name: str
    args_template: Dict[str, Any] = Field(default_factory=dict)
    input_bindings: Dict[str, str] = Field(default_factory=dict, description="e.g. {'geojson': 'step_1.output'}")
    dependencies: List[str] = Field(default_factory=list, description="List of step_ids")
    execution_policy: Optional[Dict[str, Any]] = Field(default_factory=dict)
    output_names: List[str] = Field(default_factory=list)
    retry_policy: Optional[Dict[str, Any]] = Field(default_factory=dict)
    # ── Reproducible GIS runtime (ADR-0092) ─────────────────────────────
    # Business semantics beyond the tool id: a promoted workflow keeps the
    # capability (and its resolved algorithm at save time) as the primary
    # meaning of a step; ``tool_name`` stays as execution evidence. On rerun a
    # capability-bearing step is re-resolved through AlgorithmResolver so a
    # renamed/retired tool does not silently replay a dead id.
    capability: Optional[str] = Field(None, description="GIS capability id this step serves")
    algorithm_preference: Optional[str] = Field(
        None, description="Algorithm resolved at promotion time (evidence, not a hard binding)"
    )
    input_roles: Dict[str, str] = Field(
        default_factory=dict,
        description="arg key → semantic role, e.g. {'geojson': 'primary_dataset'}",
    )
    description: str = ""


class WorkflowGraphSpec(BaseModel):
    steps: List[WorkflowStepSpec] = Field(default_factory=list)
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)


class WorkflowCreate(BaseModel):
    name: str = Field(..., max_length=255)
    description: Optional[str] = None
    graph_spec: WorkflowGraphSpec
    inputs_schema: Optional[Dict[str, Any]] = Field(default_factory=dict)
    created_from_session: Optional[str] = None


class WorkflowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    name: str
    description: Optional[str] = None
    version: int
    graph_spec: Dict[str, Any]
    inputs_schema: Dict[str, Any]
    current_revision_id: Optional[str] = None
    created_from_session: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class WorkflowRunRequest(BaseModel):
    input_bindings: Dict[str, Any] = Field(default_factory=dict, description="Override dataset/AOI/parameters")
    start_from_step: Optional[str] = Field(None, description="Optional step_id to run from")


class WorkflowRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workflow_id: str
    workflow_version: int
    project_id: Optional[str] = None
    workflow_revision_id: Optional[str] = None
    input_bindings: Dict[str, Any] = Field(default_factory=dict)
    input_dataset_fingerprints: Dict[str, Any] = Field(default_factory=dict)
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    execution_trace: List[Dict[str, Any]] = Field(default_factory=list)
    outputs: Dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None
    cost_perf_summary: Dict[str, Any] = Field(default_factory=dict)
    completed_steps: List[str] = Field(default_factory=list)
    run_manifest: Optional[Dict[str, Any]] = None
    run_fingerprint: Optional[str] = None
    created_at: datetime


class WorkflowRevisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workflow_id: str
    revision_no: int
    graph_fingerprint: str
    inputs_schema: Optional[Dict[str, Any]] = None
    created_by: Optional[str] = None
    created_at: datetime


class WorkflowRevisionSummary(BaseModel):
    """Slim revision row — graph_spec excluded (detail endpoint returns it)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    workflow_id: str
    revision_no: int
    graph_fingerprint: str
    created_at: datetime


class RunReplayRequest(BaseModel):
    mode: str = Field("exact", description="exact = reuse frozen graph+inputs; latest = current revision, same inputs")


class RunResumeRequest(BaseModel):
    allow_rerun: bool = Field(False, description="Fall back to a full rerun if resume preconditions fail")


class WorkflowRerunRequest(BaseModel):
    """Incremental re-run (ADR-0092 A5): re-execute a step and its descendants.

    Upstream steps that already completed keep their results (fingerprint-checked);
    ``from_step`` and everything downstream of it is invalidated and re-executed
    through CapabilityRegistry → AlgorithmResolver → ToolRegistry.
    """

    from_step: Optional[str] = Field(None, description="Re-execute this step and all its descendants")
    input_bindings: Dict[str, Any] = Field(
        default_factory=dict, description="Override dataset/AOI/parameters for the re-executed tail"
    )


class MapProductVersionCreate(BaseModel):
    """Record one Map Product version for a project (ADR-0092 A6)."""

    workflow_run_id: Optional[str] = None
    mapspec_fingerprint: Optional[str] = None
    mapspec_revision: Optional[int] = None
    recipe_id: Optional[str] = None
    artifact_ids: List[str] = Field(default_factory=list)
    input_dataset_fingerprints: Dict[str, str] = Field(default_factory=dict)
    product_fingerprint: Optional[str] = Field(None, description="Precomputed product fingerprint; computed when omitted")
    diff_summary: Optional[Dict[str, Any]] = Field(
        None, description="Precomputed diff vs previous version; computed when omitted"
    )


class MapProductVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    version_no: int
    product_fingerprint: Optional[str] = None
    input_dataset_fingerprints: Dict[str, Any] = Field(default_factory=dict)
    compute_plan: List[Dict[str, Any]] = Field(default_factory=list)
    workflow_id: Optional[str] = None
    workflow_run_id: Optional[str] = None
    mapspec_fingerprint: Optional[str] = None
    mapspec_revision: Optional[int] = None
    recipe_id: Optional[str] = None
    artifact_ids: List[str] = Field(default_factory=list)
    diff_summary: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    name: str
    artifact_type: str
    format: Optional[str] = None
    crs: Optional[str] = None
    storage_ref: Optional[str] = None
    upload_record_id: Optional[int] = None
    layer_id: Optional[int] = None
    content_fingerprint: Optional[str] = None
    metadata_json: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ArtifactLineageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    artifact_id: str
    parent_artifact_id: Optional[str] = None
    producing_tool: str
    tool_version: Optional[str] = "1.0"
    producing_capability: Optional[str] = None
    producing_algorithm: Optional[str] = None
    mapspec_fingerprint: Optional[str] = None
    workflow_run_id: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)
    source_dataset_id: Optional[str] = None
    source_dataset_fingerprint: Optional[str] = None
    content_fingerprint: Optional[str] = None
    created_at: datetime


class RunComparisonResponse(BaseModel):
    run_a_id: str
    run_b_id: str
    revision: Dict[str, Any] = Field(default_factory=dict)
    inputs_changed: Dict[str, Any] = Field(default_factory=dict)
    dataset_versions_changed: Dict[str, Any] = Field(default_factory=dict)
    tool_versions_changed: Dict[str, Any] = Field(default_factory=dict)
    params_changed: Dict[str, Any] = Field(default_factory=dict)
    output_artifacts_changed: Dict[str, Any] = Field(default_factory=dict)
    metrics_changed: Dict[str, Any] = Field(default_factory=dict)
    warnings_changed: Dict[str, Any] = Field(default_factory=dict)
    run_fingerprint: Dict[str, Any] = Field(default_factory=dict)


# =====================================================================
# Summary DTOs (F-FE-SD — list endpoints use these to slim payloads)
#
# The full `*Response` models above include heavy columns
# (graph_spec, execution_trace, schema_profile, etc.) that the list UI
# never renders. List endpoints serialize the *Summary variants and the
# detail endpoint returns the full model. Both come from the same ORM
# row — the field set is the only difference.
# =====================================================================


class ProjectSummary(BaseModel):
    """Slim project card — what the project list UI actually renders."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: Optional[str] = None
    status: str
    created_at: datetime
    updated_at: datetime


class ProjectDatasetSummary(BaseModel):
    """Slim dataset row — schema_profile is moved to the detail endpoint."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    name: str
    source_type: str
    crs: Optional[str] = "EPSG:4326"
    quality_status: Optional[str] = "unchecked"
    created_at: datetime


class WorkflowSummary(BaseModel):
    """Slim workflow row — graph_spec is moved to the detail endpoint."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    name: str
    description: Optional[str] = None
    version: int
    step_count: int = 0
    created_at: datetime
    updated_at: datetime


class WorkflowRunSummary(BaseModel):
    """Slim run row — execution_trace/outputs are moved to the detail
    endpoint. The list UI only needs status + a few metadata fields."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    workflow_id: str
    workflow_version: int
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    created_at: datetime


class ArtifactSummary(BaseModel):
    """Slim artifact row — storage_ref / metadata_json live on detail."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    name: str
    artifact_type: str
    format: Optional[str] = None
    crs: Optional[str] = "EPSG:4326"
    created_at: datetime
