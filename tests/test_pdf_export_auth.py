"""PDF auth gates must reject unowned sessions before rendering or writing.

Use the real optional-auth dependency, session query, ownership predicate and
receipt publisher. Only PDF rendering and optional lineage are stubbed: these
tests must run without WeasyPrint, fonts, external services or a full app boot.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api.routes import map as route
from app.core.auth import get_current_user_optional_with_version
from app.core.database import get_async_db
from app.models.db_model import Conversation


@pytest.fixture
async def export_client(tmp_path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Conversation.__table__.create)
    try:
        async with AsyncSession(engine) as db:
            db.add_all([
                Conversation(id="anonymous-session", owner_token="session-secret"),
                Conversation(
                    id="private-session", user_id="another-user", owner_token="session-secret",
                ),
                Conversation(id="legacy-session"),
            ])
            await db.commit()

            async def override_db():
                yield db

            app = FastAPI()
            app.include_router(route.router, prefix="/api/v1")
            app.dependency_overrides[get_async_db] = override_db
            monkeypatch.setattr("app.services.export_paths.exports_root", lambda: tmp_path)
            monkeypatch.setattr(route, "_EXPORT_OWNERS", route._OD())
            monkeypatch.setattr(route, "_record_lineage", AsyncMock(return_value=None))

            def render_raster(filename, *_args):
                (tmp_path / filename).write_bytes(b"%PDF-test")

            raster = Mock(side_effect=render_raster)
            vector = Mock(return_value=SimpleNamespace(
                pdf=b"%PDF-test", page_count=1, frames_rendered=1,
                frames_skipped=0, target_dpi=300, diagnostics=[], disclosures=[],
                component_coverage={}, layout_version=None, spec_fingerprint=None,
                atlas=False, atlas_pages=None,
            ))
            monkeypatch.setattr(route, "_render_pdf_to_file", raster)
            monkeypatch.setattr(
                "app.services.publication_render_worker.render_publication_bounded", vector,
            )
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                yield client, app, tmp_path, raster, vector
    finally:
        await engine.dispose()


@pytest.mark.parametrize("kind", ["pdf", "vector-pdf"])
@pytest.mark.parametrize(
    ("session_id", "token", "identity", "expected_status"),
    [
        (None, None, "anonymous", 401),
        ("", "session-secret", "anonymous", 401),
        ("missing-session", "session-secret", "anonymous", 404),
        ("anonymous-session", None, "anonymous", 404),
        ("anonymous-session", "wrong-token", "anonymous", 404),
        ("private-session", "session-secret", "anonymous", 404),
        ("legacy-session", "session-secret", "anonymous", 404),
        ("anonymous-session", "session-secret", "invalid-bearer", 401),
        ("anonymous-session", "session-secret", "anonymous", 200),
        (None, None, "authenticated", 200),
    ],
    ids=[
        "missing-session-id", "empty-session-id", "unknown-session", "missing-token",
        "wrong-token", "another-users-session", "legacy-unowned-session",
        "invalid-bearer", "anonymous-owner", "authenticated-user",
    ],
)
async def test_pdf_export_auth_before_rendering(
    export_client, kind, session_id, token, identity, expected_status,
):
    client, app, export_dir, raster, vector = export_client
    if identity == "authenticated":
        # Production dependency verifies live user/token version. Override only
        # that boundary for the signed-in case, never the export ownership gate.
        app.dependency_overrides[get_current_user_optional_with_version] = lambda: {
            "user_id": "export-user",
        }
    headers = {"X-Session-Token": token} if token is not None else {}
    if identity == "invalid-bearer":
        headers["Authorization"] = "Bearer not-a-valid-jwt"

    if kind == "pdf":
        response = await client.post(
            "/api/v1/export/pdf", headers=headers,
            files={"file": ("map.png", b"rendering-is-stubbed", "image/png")},
            data={"session_id": session_id} if session_id is not None else {},
        )
    else:
        response = await client.post(
            "/api/v1/export/vector-pdf", headers=headers,
            json={"mapspec": {}, "session_id": session_id},
        )
    assert response.status_code == expected_status, response.text

    if expected_status != 200:
        raster.assert_not_called()
        vector.assert_not_called()
        assert not list(export_dir.iterdir())
        assert not route._EXPORT_OWNERS
        if expected_status == 404:
            assert response.json()["detail"] == "Session not found"
        return

    selected, other = (raster, vector) if kind == "pdf" else (vector, raster)
    selected.assert_called_once()
    other.assert_not_called()
    data = response.json()
    principal = "export-user" if identity == "authenticated" else "session:anonymous-session"
    assert (export_dir / f"{data['filename']}.owner").read_text() == principal
    assert route._EXPORT_OWNERS[data["filename"]] == principal
    download = await client.get(data["url"])
    assert download.status_code == 200
    assert download.content == b"%PDF-test"
    if identity == "anonymous":
        assert "sig=" in data["url"] and "session-secret" not in data["url"]
        assert (await client.get(data["url"].split("?")[0])).status_code == 401
    else:
        app.dependency_overrides[get_current_user_optional_with_version] = lambda: {
            "user_id": "intruder",
        }
        assert (await client.get(data["url"])).status_code == 403
