"""
Unit tests for Project Workspace domain SQLAlchemy models:
Project, ProjectDataset, Workflow, WorkflowRun, Artifact, ArtifactLineage.
"""
import uuid
import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError

from app.core.database import Base
from app.models.db_model import Organization, User, Layer
from app.models.upload import UploadRecord
from app.models.project import (
    Project,
    ProjectDataset,
    Workflow,
    WorkflowRun,
    Artifact,
    ArtifactLineage,
)


@pytest.fixture
def db_session():
    """Create an in-memory SQLite database session with foreign keys enabled."""
    engine = create_engine("sqlite:///:memory:", echo=False)

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def test_project_crud(db_session):
    """Test Project creation, read, update, delete."""
    org = Organization(name="GIS Corp", slug="gis-corp")
    user = User(id="u_101", username="alice", email="alice@gis.com")
    db_session.add_all([org, user])
    db_session.commit()

    proj = Project(
        name="Urban Planning 2026",
        description="Spatial master plan project",
        org_id=org.id,
        owner_id=user.id,
        status="active",
        metadata_json={"tags": ["urban", "zoning"]},
    )
    db_session.add(proj)
    db_session.commit()

    assert proj.id is not None
    fetched = db_session.query(Project).filter_by(id=proj.id).first()
    assert fetched is not None
    assert fetched.name == "Urban Planning 2026"
    assert fetched.org_id == org.id
    assert fetched.owner_id == user.id
    assert fetched.metadata_json["tags"] == ["urban", "zoning"]

    # Update
    fetched.name = "Urban Master Plan 2026"
    db_session.commit()
    updated = db_session.query(Project).filter_by(id=proj.id).first()
    assert updated.name == "Urban Master Plan 2026"

    # Delete
    db_session.delete(updated)
    db_session.commit()
    assert db_session.query(Project).filter_by(id=proj.id).first() is None


def test_project_dataset_relationship(db_session):
    """Test ProjectDataset creation, quality status, and cascade delete."""
    proj = Project(name="Environmental Impact", status="active")
    db_session.add(proj)
    db_session.commit()

    dataset = ProjectDataset(
        project_id=proj.id,
        name="River Pollution 2025",
        source_type="vector",
        source_ref="layer_42",
        schema_profile={"fields": ["pH", "dissolved_oxygen"]},
        crs="EPSG:4326",
        quality_status="valid",
        version_fingerprint="abc123def456",
    )
    db_session.add(dataset)
    db_session.commit()

    fetched = db_session.query(ProjectDataset).filter_by(id=dataset.id).first()
    assert fetched is not None
    assert fetched.project_id == proj.id
    assert fetched.name == "River Pollution 2025"
    assert fetched.quality_status == "valid"
    assert fetched.project.name == "Environmental Impact"

    # Test cascade delete from Project
    db_session.delete(proj)
    db_session.commit()
    assert db_session.query(ProjectDataset).filter_by(id=dataset.id).first() is None


def test_workflow_and_workflow_run(db_session):
    """Test Workflow and WorkflowRun models, relationships, and traces."""
    proj = Project(name="Land Cover Workflow", status="active")
    db_session.add(proj)
    db_session.commit()

    wf = Workflow(
        project_id=proj.id,
        name="Land Use Classification",
        description="Automated Sentinel-2 classification",
        version=1,
        graph_spec={"nodes": [{"id": "n1", "type": "clip"}], "edges": []},
        inputs_schema={"raster": "string"},
    )
    db_session.add(wf)
    db_session.commit()

    run = WorkflowRun(
        workflow_id=wf.id,
        workflow_version=wf.version,
        input_bindings={"raster": "s2_20260801.tif"},
        status="completed",
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        execution_trace={"steps_executed": ["n1"]},
        outputs={"layer_id": 101},
        cost_perf_summary={"duration_ms": 1450, "memory_mb": 512},
    )
    db_session.add(run)
    db_session.commit()

    fetched_run = db_session.query(WorkflowRun).filter_by(id=run.id).first()
    assert fetched_run is not None
    assert fetched_run.workflow_id == wf.id
    assert fetched_run.status == "completed"
    assert fetched_run.outputs["layer_id"] == 101
    assert fetched_run.workflow.name == "Land Use Classification"

    # Verify back-reference
    assert len(wf.runs) == 1
    assert wf.runs[0].id == run.id


def test_artifact_and_artifact_lineage(db_session):
    """Test Artifact, UploadRecord/Layer relations, and ArtifactLineage tracking."""
    org = Organization(name="Geo Labs", slug="geo-labs")
    db_session.add(org)
    db_session.commit()

    layer = Layer(
        id=1,
        org_id=org.id,
        name="Source Buildings",
        layer_type="vector",
        geometry_type="Polygon",
    )
    upload = UploadRecord(
        filename="buildings.geojson",
        original_name="buildings.geojson",
        file_type="vector",
        format="geojson",
        file_size=1024,
    )
    proj = Project(name="Artifact Pipeline Project", org_id=org.id)
    db_session.add_all([layer, upload, proj])
    db_session.commit()

    # Source artifact
    art_parent = Artifact(
        project_id=proj.id,
        name="Raw Buildings GeoJSON",
        artifact_type="vector",
        format="geojson",
        crs="EPSG:4326",
        upload_record_id=upload.id,
        layer_id=layer.id,
        metadata_json={"feature_count": 500},
    )
    db_session.add(art_parent)
    db_session.commit()

    # Derived artifact
    art_child = Artifact(
        project_id=proj.id,
        name="Building Buffers 50m",
        artifact_type="vector",
        format="geojson",
        crs="EPSG:3857",
        metadata_json={"buffer_distance": 50},
    )
    db_session.add(art_child)
    db_session.commit()

    # Lineage tracking
    lineage = ArtifactLineage(
        artifact_id=art_child.id,
        parent_artifact_id=art_parent.id,
        producing_tool="buffer_analysis",
        tool_version="1.2.0",
        parameters={"distance": 50, "unit": "meters"},
    )
    db_session.add(lineage)
    db_session.commit()

    fetched_lineage = db_session.query(ArtifactLineage).filter_by(id=lineage.id).first()
    assert fetched_lineage is not None
    assert fetched_lineage.artifact_id == art_child.id
    assert fetched_lineage.parent_artifact_id == art_parent.id
    assert fetched_lineage.producing_tool == "buffer_analysis"
    assert fetched_lineage.artifact.name == "Building Buffers 50m"
    assert fetched_lineage.parent_artifact.name == "Raw Buildings GeoJSON"


def test_project_check_constraints(db_session):
    """Test that check constraints reject invalid status values in SQLite."""
    proj = Project(name="Invalid Status Project", status="invalid_status")
    db_session.add(proj)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_all_models_exported_in_models_init():
    """Verify that app.models exports all models properly."""
    import app.models as models

    exported = set(models.__all__)
    expected_project_models = {
        "Project",
        "ProjectDataset",
        "Workflow",
        "WorkflowRun",
        "Artifact",
        "ArtifactLineage",
    }
    assert expected_project_models.issubset(exported)

    for model_name in expected_project_models:
        assert hasattr(models, model_name)
