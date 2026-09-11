"""V9 契约基石：统一错误信封测试（ADR-0138 / P3）。

四类断言：
1. 默认：HTTPException / 422 校验错误 → 统一信封 {code,success,message,data}；
2. settings.LEGACY_DETAIL_ENVELOPE=true → v1 旧体 {"detail"}；
3. 请求头 X-Error-Envelope: detail → 按请求回退旧体；
4. 错误分类学附加字段（category/retryable）在统一信封中出现。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def _raise_404(client, headers=None):
    return client.get(
        "/api/v1/projects/nonexistent-project-for-envelope-test",
        headers=headers or {},
    )


class TestUnifiedEnvelopeDefault:
    def test_http_exception_uses_envelope(self, client):
        r = _raise_404(client)
        assert r.status_code == 404
        body = r.json()
        assert body["success"] is False
        assert body["code"] == "NOT_FOUND"
        assert isinstance(body["message"], str) and body["message"]
        assert body["data"] is None
        assert "detail" not in body

    def test_validation_error_uses_envelope(self, client):
        r = client.post("/api/v1/auth/refresh", json={})
        assert r.status_code == 422
        body = r.json()
        assert body["success"] is False
        assert body["code"] == "VALIDATION_ERROR"
        assert body["data"] is None
        assert "detail" not in body

    def test_envelope_carries_taxonomy_fields(self, client):
        r = _raise_404(client)
        body = r.json()
        assert "category" in body
        assert "retryable" in body


class TestLegacySwitch:
    def test_settings_switch_restores_detail(self, client, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "LEGACY_DETAIL_ENVELOPE", True)
        r = _raise_404(client)
        assert r.status_code == 404
        assert r.json() == {"detail": "Project not found or permission denied"}

    def test_header_override_restores_detail(self, client):
        r = _raise_404(client, headers={"X-Error-Envelope": "detail"})
        assert r.status_code == 404
        assert "detail" in r.json()
        body = r.json()
        assert "code" not in body

    def test_header_other_value_keeps_envelope(self, client):
        r = _raise_404(client, headers={"X-Error-Envelope": "unified"})
        assert r.json()["code"] == "NOT_FOUND"

    def test_validation_legacy_switch(self, client, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "LEGACY_DETAIL_ENVELOPE", True)
        r = client.post("/api/v1/auth/refresh", json={})
        assert r.status_code == 422
        assert "detail" in r.json()


class TestV2RefusesLegacy:
    def test_v2_header_override_ignored(self, client):
        """v2 面（ADR-0138 D5）：即使带 X-Error-Envelope: detail 也保持新信封。"""
        r = client.get(
            "/api/v2/geocompute/runs/nonexistent-run",
            headers={"X-Error-Envelope": "detail"},
        )
        body = r.json()
        # 断言点 = 信封形状（非状态码——该端点鉴权在先，401/404 均可能）
        assert body["success"] is False
        assert "code" in body and "message" in body
        assert "detail" not in body
