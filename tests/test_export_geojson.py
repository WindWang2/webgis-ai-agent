"""Export improvements tests — covers GeoJSON export endpoint.

Frontend changes (addExport, SVG dropdown) are tested via Vitest.
"""

import pytest

from app.main import app
from app.core.auth import get_current_user

_mock_user = {"user_id": "test-user"}


class TestGeoJSONExportEndpoint:
    """Backend should accept GeoJSON layer data and return a download URL."""

    @pytest.fixture(autouse=True)
    def clean_overrides(self):
        yield
        app.dependency_overrides.clear()

    def test_geojson_export_returns_download_url(self):
        from fastapi.testclient import TestClient
        app.dependency_overrides[get_current_user] = lambda: _mock_user
        client = TestClient(app)

        geojson = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
                "properties": {"name": "Beijing"},
            }],
        }
        resp = client.post(
            "/api/v1/export/geojson",
            json={"geojson": geojson, "filename": "test_export"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "url" in data
        assert data["filename"].endswith(".geojson")

    def test_geojson_export_rejects_invalid_data(self):
        from fastapi.testclient import TestClient
        app.dependency_overrides[get_current_user] = lambda: _mock_user
        client = TestClient(app)

        resp = client.post(
            "/api/v1/export/geojson",
            json={"geojson": "not valid", "filename": "bad"},
        )
        assert resp.status_code == 400

    def test_geojson_export_validates_feature_collection(self):
        from fastapi.testclient import TestClient
        app.dependency_overrides[get_current_user] = lambda: _mock_user
        client = TestClient(app)

        resp = client.post(
            "/api/v1/export/geojson",
            json={"geojson": {"type": "Point", "coordinates": [0, 0]}, "filename": "point"},
        )
        # Should accept any valid GeoJSON, not just FeatureCollections
        assert resp.status_code == 200

    def test_geojson_export_rejects_oversized_payload(self):
        """Serialized GeoJSON above MAX_EXPORT_SIZE (50MB) must 413, not write to disk."""
        import uuid
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        import app.api.routes.map as map_mod
        app.dependency_overrides[get_current_user] = lambda: _mock_user
        client = TestClient(app)

        geojson = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
                "properties": {"blob": "x" * 4096},
            }],
        }
        # Shrink the cap so the test doesn't build a 50MB payload in memory.
        with patch.object(map_mod, "MAX_EXPORT_SIZE", 1024):
            resp = client.post(
                "/api/v1/export/geojson",
                json={"geojson": geojson, "filename": f"big_{uuid.uuid4().hex[:6]}"},
            )
        assert resp.status_code == 413
