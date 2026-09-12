"""V9 契约基石：API v2 挂载层 + v1 弃用头测试（ADR-0138 / P7）。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


class TestV2Mounted:
    def test_v2_demo_subsystems_reachable(self, client):
        """三个示范子系统在 v2 可访问（契约与 v1 同源）。"""
        for path in (
            "/api/v2/lakehouse/datasets",
            "/api/v2/geocompute/runs",
            "/api/v2/workflow-runtime/instances",
        ):
            r = client.get(path)
            assert r.status_code != 404, f"{path} 未挂载"

    def test_v2_paths_in_openapi_with_unique_operation_ids(self):
        from app.main import app

        app.openapi_schema = None
        schema = app.openapi()
        app.openapi_schema = None
        v2_ops = [
            op.get("operationId")
            for path, methods in schema["paths"].items()
            if path.startswith("/api/v2/")
            for m, op in methods.items()
            if m in ("get", "post", "put", "delete", "patch")
        ]
        assert len(v2_ops) >= 50
        assert len(v2_ops) == len(set(v2_ops)), "v2/v1 operation_id 冲突"

    def test_v1_untouched(self, client):
        """v1 端点不受 v2 挂载影响（可用性抽查）。"""
        assert client.get("/api/v1/version").status_code == 200
        assert client.get("/api/v1/health").status_code == 200


class TestV1DeprecationHeaders:
    def test_v1_response_carries_deprecation_headers(self, client):
        r = client.get("/api/v1/version")
        assert r.headers.get("Deprecation") == "true"
        assert r.headers.get("Sunset")
        assert "successor-version" in r.headers.get("Link", "")

    def test_v2_response_has_no_deprecation_headers(self, client):
        r = client.get("/api/v2/geocompute/runs")
        assert r.headers.get("Deprecation") is None
        assert r.headers.get("Sunset") is None
