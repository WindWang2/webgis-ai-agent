"""
Data Fabric Unified Adapter Contract Tests:
Verifies that all adapters comply with the GeospatialDataSourceAdapter lifecycle:
probe, capabilities, describe, preview, query, health.
"""
import pytest
from typing import Dict, Any
from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
from app.services.data_fabric.security import DataFabricSecurity, DataFabricSecurityError
from app.schemas.data_fabric_schema import (
    ConnectionProfile,
    DatasetDescriptor,
    QuerySpec,
    QueryResult,
    DataFabricHealth,
)


def verify_adapter_contract(adapter: GeospatialDataSourceAdapter, sample_dataset_id: str):
    """Generic contract compliance verifier for any Data Fabric adapter."""
    # 1. Probe
    is_alive = adapter.probe()
    assert isinstance(is_alive, bool)

    # 2. Capabilities
    caps = adapter.capabilities()
    assert isinstance(caps, list)
    assert len(caps) > 0

    # 3. List Datasets
    datasets = adapter.list_datasets()
    assert isinstance(datasets, list)

    # 4. Describe
    desc = adapter.describe(sample_dataset_id)
    assert isinstance(desc, DatasetDescriptor)
    assert desc.id == sample_dataset_id
    assert desc.source_type is not None

    # 5. Bounded Preview
    preview_res = adapter.preview(sample_dataset_id, limit=5)
    assert isinstance(preview_res, dict)
    assert "schema" in preview_res or "properties" in preview_res or "features" in preview_res

    # 6. Pushdown Query
    q_spec = QuerySpec(limit=3, bbox=[10.0, 10.0, 20.0, 20.0])
    q_res = adapter.query(sample_dataset_id, q_spec)
    assert isinstance(q_res, QueryResult)
    assert q_res.dataset_id == sample_dataset_id

    # 7. Health
    h = adapter.health()
    assert isinstance(h, DataFabricHealth)
    assert h.status in ["healthy", "unreachable", "degraded", "unknown"]


def test_ssrf_security_boundary():
    """Verify SSRF security enforcement on malicious URLs."""
    with pytest.raises(DataFabricSecurityError):
        DataFabricSecurity.validate_url("http://127.0.0.1/admin")

    with pytest.raises(DataFabricSecurityError):
        DataFabricSecurity.validate_url("http://localhost:8080/internal")

    with pytest.raises(DataFabricSecurityError):
        DataFabricSecurity.validate_url("http://169.254.169.254/latest/meta-data/")

    with pytest.raises(DataFabricSecurityError):
        DataFabricSecurity.validate_url("http://10.0.0.1/db")

    # Valid external URL should pass
    valid_url = "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/World_Cities/FeatureServer/0"
    assert DataFabricSecurity.validate_url(valid_url) == valid_url
