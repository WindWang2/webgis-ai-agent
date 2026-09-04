"""Map Product version workspace — pairwise diff service + REST contract.

The per-row ``diff_summary`` (vs previous) was already covered by
test_reproducible_gis_runtime; these pin the NEW pairwise surface the
version workspace UI consumes: ``diff_versions_pairwise`` semantics
(style-only ⇒ no recomputation; algorithm/data/parameter ⇒ recomputation
with drill-down evidence) and the ``GET /map-products/{a}/diff/{b}`` route
(404s, payload shape).
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.database import Base, Engine, SessionLocal
from app.main import app
from app.services.map_product_service import MapProductService

client = TestClient(app)


def _auth_headers():
    """Project reads are ownership-scoped (SEC-F1) — authenticate as the
    fixture project's owner."""
    from app.core.auth import create_access_token

    return {"Authorization": "Bearer " + create_access_token(
        {"sub": "u_vw", "username": "vw", "role": "viewer"})}


@pytest.fixture()
def versioned_project():
    """Project with 3 versions: V1 base, V2 style-only, V3 algorithm+data.

    Self-sufficient schema setup (the sibling reproducible suite assumes a
    prior full-schema init that a standalone run cannot rely on): ensure
    all metadata tables, then reset the project domain tables exactly like
    ``test_reproducible_gis_runtime.db`` does.
    """
    from tests.unit.test_reproducible_gis_runtime import _PROJECT_DOMAIN_TABLES

    import app.models.db_model  # noqa: F401 — register metadata
    import app.models.project  # noqa: F401

    Base.metadata.create_all(bind=Engine, checkfirst=True)
    domain = [t for t in Base.metadata.sorted_tables if t.name in _PROJECT_DOMAIN_TABLES]
    for tbl in reversed(domain):
        tbl.drop(bind=Engine, checkfirst=True)
    for tbl in domain:
        tbl.create(bind=Engine, checkfirst=True)

    from app.models.db_model import User
    from app.models.project import Project

    project_id = f"proj_{uuid.uuid4().hex[:8]}"
    with SessionLocal() as s:
        s.merge(User(id="u_vw", username="vw", email="vw@example.com",
                     password_hash="x", role="viewer", is_active=True))
        s.add(Project(id=project_id, name="version-workspace", owner_id="u_vw"))
        s.commit()

        base_manifest = {
            "steps": [
                {"step_id": "s1", "tool_name": "poi_query", "algorithm": "poi.query",
                 "args": {"q": "小学"}},
                {"step_id": "s2", "tool_name": "admin_aggregate", "algorithm": "admin.aggregate",
                 "args": {"by": "district"}},
            ],
            "artifacts": [{"id": "a1", "content_fingerprint": "out1"}],
        }
        v1 = MapProductService.record_version(
            s, project_id, mapspec_fingerprint="carto-v1", recipe_id="poi_distribution_overview",
            input_dataset_fingerprints={"ds1": "fpA"}, run_manifest=base_manifest,
        )
        # V2: style-only (same compute plan + inputs, new MapSpec fingerprint)
        v2 = MapProductService.record_version(
            s, project_id, mapspec_fingerprint="carto-v2",
            input_dataset_fingerprints={"ds1": "fpA"}, run_manifest=base_manifest,
        )
        # V3: algorithm + parameter + data + output all change
        v3 = MapProductService.record_version(
            s, project_id, mapspec_fingerprint="carto-v3",
            input_dataset_fingerprints={"ds1": "fpB", "ds2": "fpC"},
            run_manifest={
                "steps": [
                    {"step_id": "s1", "tool_name": "poi_query", "algorithm": "poi.query",
                     "args": {"q": "小学"}},
                    {"step_id": "s2", "tool_name": "admin_aggregate",
                     "algorithm": "admin.aggregate_v2", "args": {"by": "street"}},
                ],
                "artifacts": [{"id": "a1", "content_fingerprint": "out1"},
                              {"id": "a2", "content_fingerprint": "out9"}],
            },
        )
        # Extract scalars INSIDE the session (ORM objects detach on close).
        versions = (int(v1.version_no), int(v2.version_no), int(v3.version_no))
    return project_id, versions


# ── service: pairwise diff semantics ────────────────────────────────────────


def test_pairwise_style_only_no_recomputation(versioned_project):
    project_id, (v1, v2, _) = versioned_project
    with SessionLocal() as s:
        diff = MapProductService.diff_versions_pairwise(s, project_id, v1, v2)
    assert diff["from_version_no"] == v1 and diff["to_version_no"] == v2
    assert diff["style_changed"] is True
    assert diff["data_changed"] is False
    assert diff["algorithm_changed"] is False
    assert diff["parameter_changed"] is False
    assert diff["output_changed"] is False
    assert diff["analysis_recomputation_expected"] is False, (
        "style-only change must never demand analysis recomputation"
    )
    # drill-down: mapspec fingerprints differ, nothing else moved
    assert diff["details"]["mapspec_fingerprint"]["from"] == "carto-v1"
    assert diff["details"]["mapspec_fingerprint"]["to"] == "carto-v2"
    assert diff["details"]["algorithm_steps"] == []
    assert diff["details"]["parameter_steps"] == []
    assert diff["details"]["artifacts"]["added"] == []
    assert diff["details"]["artifacts"]["removed"] == []


def test_pairwise_algorithm_change_demands_recomputation(versioned_project):
    project_id, (v1, _, v3) = versioned_project
    with SessionLocal() as s:
        diff = MapProductService.diff_versions_pairwise(s, project_id, v1, v3)
    assert diff["algorithm_changed"] is True
    assert diff["data_changed"] is True
    assert diff["parameter_changed"] is True
    assert diff["output_changed"] is True
    assert diff["analysis_recomputation_expected"] is True
    # drill-down evidence: the changed algorithm step is identifiable
    algo_steps = diff["details"]["algorithm_steps"]
    assert [st["step_id"] for st in algo_steps] == ["s2"]
    assert algo_steps[0]["from"] == "admin.aggregate"
    assert algo_steps[0]["to"] == "admin.aggregate_v2"
    param_steps = diff["details"]["parameter_steps"]
    assert [st["step_id"] for st in param_steps] == ["s2"]
    assert diff["details"]["input_dataset_fingerprints"]["changed_keys"] == ["ds1", "ds2"]
    assert diff["details"]["artifacts"]["added"] == ["out9"]
    assert diff["details"]["artifacts"]["unchanged_count"] == 1


def test_pairwise_same_version_is_all_unchanged(versioned_project):
    project_id, (v1, _, _) = versioned_project
    with SessionLocal() as s:
        diff = MapProductService.diff_versions_pairwise(s, project_id, v1, v1)
    assert diff["analysis_recomputation_expected"] is False
    assert all(
        diff[k] is False for k in
        ("data_changed", "algorithm_changed", "parameter_changed", "style_changed", "output_changed")
    )


def test_pairwise_missing_version_raises(versioned_project):
    project_id, (v1, _, _) = versioned_project
    with SessionLocal() as s:
        with pytest.raises(ValueError, match="not found: 99"):
            MapProductService.diff_versions_pairwise(s, project_id, v1, 99)


# ── REST contract ───────────────────────────────────────────────────────────


def test_rest_diff_endpoint_contract(versioned_project):
    project_id, (v1, v2, v3) = versioned_project
    # style-only
    resp = client.get(f"/api/v1/projects/{project_id}/map-products/{v1}/diff/{v2}", headers=_auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["analysis_recomputation_expected"] is False
    assert body["style_changed"] is True
    # algorithm change
    resp = client.get(f"/api/v1/projects/{project_id}/map-products/{v1}/diff/{v3}", headers=_auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["analysis_recomputation_expected"] is True
    assert body["details"]["algorithm_steps"][0]["step_id"] == "s2"


def test_rest_diff_endpoint_404s(versioned_project):
    project_id, (v1, _, _) = versioned_project
    resp = client.get(f"/api/v1/projects/{project_id}/map-products/{v1}/diff/999", headers=_auth_headers())
    assert resp.status_code == 404
    resp = client.get("/api/v1/projects/proj_missing/map-products/1/diff/2", headers=_auth_headers())
    assert resp.status_code == 404


def test_rest_version_list_and_detail(versioned_project):
    project_id, (v1, _, v3) = versioned_project
    resp = client.get(f"/api/v1/projects/{project_id}/map-products", headers=_auth_headers())
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [v["version_no"] for v in items] == [v3, v3 - 1, v1]  # newest first
    resp = client.get(f"/api/v1/projects/{project_id}/map-products/{v1}", headers=_auth_headers())
    assert resp.status_code == 200
    assert resp.json()["version_no"] == v1
    assert isinstance(resp.json()["compute_plan"], list)
