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
    input_bindings: Dict[str, Any]
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    execution_trace: List[Dict[str, Any]] = Field(default_factory=list)
    outputs: Dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None
    cost_perf_summary: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    name: str
    artifact_type: str
    format: Optional[str] = None
    crs: Optional[str] = "EPSG:4326"
    storage_ref: Optional[str] = None
    upload_record_id: Optional[int] = None
    layer_id: Optional[int] = None
    metadata_json: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ArtifactLineageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    artifact_id: str
    parent_artifact_id: Optional[str] = None
    producing_tool: str
    tool_version: Optional[str] = "1.0"
    workflow_run_id: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class RunComparisonResponse(BaseModel):
    run_a_id: str
    run_b_id: str
    inputs_changed: Dict[str, Any]
    params_changed: Dict[str, Any]
    output_artifacts_changed: Dict[str, Any]
    metrics_changed: Dict[str, Any]
    warnings_changed: Dict[str, Any]
