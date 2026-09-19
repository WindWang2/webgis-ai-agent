"""GeoAI 工具面 + HTTP 面测试（Platform 11 / WP-F/G 后端）。

- 工具：注册完整性 + 通过 registry._tools 直调（与服务夹具同进程域，
  避开全局单例的 DATA 落盘）；
- 路由：TestClient + monkeypatch 全局 service 工厂与 DATA_DIR 门。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

GEOAI_TOOL_NAMES = {
    "geoai_prompt_artifact_inspect",
    "geoai_run_promptable",
    "geoai_prompt_refine",
    "geoai_embed",
    "geoai_semantic_zero_shot",
}


def test_geoai_tools_registered():
    from app.tools.geoai_tools import register_geoai_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_geoai_tools(registry)
    registered = set(registry.tool_names()) & GEOAI_TOOL_NAMES
    assert registered == GEOAI_TOOL_NAMES, sorted(GEOAI_TOOL_NAMES - registered)


def test_tools_registry_table_includes_geoai():
    import app.tools as tools_init

    modules = [m for m, _ in tools_init._TOOL_MODULES]
    assert "app.tools.geoai_tools" in modules


@pytest.fixture()
def geoai_registry():
    from app.tools.geoai_tools import register_geoai_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_geoai_tools(registry)
    return registry


@pytest.fixture()
def patched_service(service, monkeypatch):
    """工具/路由内的 get_modelops_service → 测试夹具实例。"""
    monkeypatch.setattr(
        "app.services.modelops.service.get_modelops_service", lambda *a, **k: service
    )
    return service


def _polygon_artifact_payload():
    return {
        "crs": "EPSG:4326",
        "polygons": [[[118.0, -8.0], [123.0, -8.0], [123.0, -13.0], [118.0, -13.0]]],
    }


async def test_inspect_tool_valid_and_invalid(patched_service, geoai_registry, synthetic_raster):
    inspect = geoai_registry._tools["geoai_prompt_artifact_inspect"]
    good = await inspect(_polygon_artifact_payload())
    assert good["valid"] is True
    assert len(good["artifact_id"]) == 64
    assert good["geometry"]["polygons"] == 1
    bad = await inspect({"schema_version": 99, "points": [[1, 1]]})
    assert bad["valid"] is False and "schema_version" in bad["error"]
    # 编译 dry-run（提供 source）：
    with_compile = await inspect(_polygon_artifact_payload(), str(synthetic_raster))
    assert with_compile["compile_audit"]["coordinate_space"] == "map→pixel"
    assert with_compile["compile_audit"]["anchor_box"] == [2, 48, 7, 53]


async def test_run_promptable_tool_with_artifact(
    patched_service, geoai_registry, synthetic_raster
):
    run = geoai_registry._tools["geoai_run_promptable"]
    result = await run(
        model_id="tiny-promptable-seg",
        source_uri=str(synthetic_raster),
        artifact=_polygon_artifact_payload(),
        session_id="tool-run-1",
    )
    assert result["status"] == "completed"
    assert result["prompt_artifact_id"]
    assert "prompt_mask_geojson" in result["outputs"]
    assert result["manifest"]["prompt_audit"]["artifact_id"] == result["prompt_artifact_id"]


async def test_refine_tool_composes_candidate_prior(
    patched_service, geoai_registry, synthetic_raster
):
    run = geoai_registry._tools["geoai_run_promptable"]
    first = await run(
        model_id="tiny-promptable-seg",
        source_uri=str(synthetic_raster),
        artifact=_polygon_artifact_payload(),
        session_id="tool-refine",
        return_candidates=True,
    )
    refine = geoai_registry._tools["geoai_prompt_refine"]
    refined = await refine(
        model_id="tiny-promptable-seg",
        source_uri=str(synthetic_raster),
        run_outputs=first["outputs"],
        candidate=0,
        session_id="tool-refine",
    )
    assert refined["status"] == "completed"
    assert refined["refined_from"]["candidate"] == 0
    assert refined["refined_from"]["prior_pixels"] > 0
    assert refined["manifest"]["prompt_audit"]["derived_mask_source"] == "mask_sidecar"
    # 无候选产物 → typed 错误（correction hint）。
    from app.lib.modelops.errors import ModelOpsError

    with pytest.raises(ModelOpsError, match="no prompt_candidates artifact path"):
        await refine(
            model_id="tiny-promptable-seg",
            source_uri=str(synthetic_raster),
            run_outputs={},
            candidate=0,
            session_id="tool-refine",
        )


async def test_embed_tool_reports_cache(patched_service, geoai_registry, synthetic_raster):
    embed = geoai_registry._tools["geoai_embed"]
    result = await embed(
        model_id="tiny-chip-embedder",
        source_uri=str(synthetic_raster),
        session_id="tool-embed",
    )
    assert result["status"] == "completed"
    assert result["embedding_cache"]["enabled"] is True
    assert "embeddings" in result["outputs"]


async def test_semantic_zero_shot_tool_refuses_without_encoder(
    patched_service, geoai_registry, synthetic_raster
):
    from app.lib.modelops.errors import MultimodalUnsupported

    semantic = geoai_registry._tools["geoai_semantic_zero_shot"]
    with pytest.raises(MultimodalUnsupported, match="no text encoder wired"):
        await semantic(
            classes=["water", "forest"],
            model_id="tiny-chip-embedder",
            source_uri=str(synthetic_raster),
            session_id="tool-sem",
        )


# ── HTTP 面 ──────────────────────────────────────────────────────────


def _auth_headers():
    from app.core.auth import create_access_token

    return {
        "Authorization": "Bearer "
        + create_access_token({"sub": "geoai-tester", "username": "geoai-tester", "role": "editor"})
    }


@pytest.fixture()
def api_client(service, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.modelops.service.get_modelops_service", lambda *a, **k: service
    )
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "DATA_DIR", str(tmp_path))

    async def _passthrough_bind(**kwargs):
        from fastapi import HTTPException
        from app.services.modelops.service import normalize_scope

        sid = (kwargs.get("session_id") or "") or None
        pid = (kwargs.get("project_id") or "") or None
        if isinstance(sid, str):
            sid = sid.strip() or None
        if isinstance(pid, str):
            pid = pid.strip() or None
        if sid and pid:
            raise HTTPException(status_code=400, detail="provide exactly one of session_id / project_id")
        if kwargs.get("require") and not sid and not pid:
            raise HTTPException(status_code=400, detail="session_id or project_id is required")
        if sid:
            return normalize_scope(session_id=sid)
        if pid:
            return normalize_scope(project_id=pid)
        return {}

    async def _wide_roots(db, scope):
        from pathlib import Path as _P
        from app.core.config import settings as _settings
        from app.services.modelops.service import get_modelops_service as _gms

        return [
            _P(_settings.DATA_DIR).resolve(),
            _P(_gms()._settings.registry_dir).resolve(),
        ]

    monkeypatch.setattr("app.api.routes.geoai._bind_owner_scope", _passthrough_bind)
    monkeypatch.setattr("app.api.routes.geoai._roots_for_scope", _wide_roots)

    from app.main import app

    with TestClient(app) as client:
        client.headers.update(_auth_headers())
        yield client


def test_geoai_http_rejects_anonymous(service, monkeypatch, tmp_path):
    """#1379: GeoAI HTTP 面零鉴权关闭后匿名必须 401。"""
    monkeypatch.setattr(
        "app.services.modelops.service.get_modelops_service", lambda *a, **k: service
    )
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(app_settings, "AUTH_DISABLED", False, raising=False)
    from app.main import app

    with TestClient(app) as client:
        assert client.get("/api/v1/geoai/status").status_code == 401
        assert client.get("/api/v1/geoai/artifact-geojson", params={"path": "x"}).status_code == 401
        assert client.get("/api/v1/geoai/preview", params={"source_uri": "x"}).status_code == 401


def test_geoai_models_and_status_routes(api_client):
    resp = api_client.get("/api/v1/geoai/models", params={"session_id": "route-s1"})
    assert resp.status_code == 200
    models = {m["model_id"] for m in resp.json()["models"]}
    assert "tiny-promptable-seg" in models and "tiny-chip-embedder" in models
    status = api_client.get("/api/v1/geoai/status")
    assert status.status_code == 200
    body = status.json()
    assert body["semantic_encoder"]["wired"] is False
    assert body["embedding_cache"]["enabled"] is True


def test_prompt_segment_route_with_artifact(api_client, synthetic_raster):
    resp = api_client.post(
        "/api/v1/geoai/prompt-segment",
        json={
            "model_id": "tiny-promptable-seg",
            "source_uri": str(synthetic_raster),
            "artifact": _polygon_artifact_payload(),
            "session_id": "route-s1",
            "return_candidates": True,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert "prompt_candidates" in body["outputs"]


def test_prompt_segment_route_requires_prompt_input(api_client, synthetic_raster):
    resp = api_client.post(
        "/api/v1/geoai/prompt-segment",
        json={
            "model_id": "tiny-promptable-seg",
            "source_uri": str(synthetic_raster),
            "session_id": "route-s1",
        },
    )
    assert resp.status_code == 400


def test_source_uri_gate_rejects_outside_data_dir(api_client, synthetic_raster):
    resp = api_client.post(
        "/api/v1/geoai/prompt-segment",
        json={
            "model_id": "tiny-promptable-seg",
            "source_uri": "C:/Windows/System32/drivers/etc/hosts",
            "points": [[10.0, 10.0]],
            "session_id": "route-s1",
        },
    )
    assert resp.status_code == 400
    assert "data directory" in json.dumps(resp.json(), ensure_ascii=False)


def test_preview_route_returns_bounded_png_and_geo_metadata(api_client, synthetic_raster):
    resp = api_client.get(
        "/api/v1/geoai/preview", params={"source_uri": str(synthetic_raster), "session_id": "route-s1"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    import base64

    png = base64.b64decode(body["png_base64"])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert body["preview_width"] <= 512 and body["preview_height"] <= 512
    # 地理元数据与合成栅格已知网格一致（130x100、EPSG:4326、左上 (116,40)）。
    assert (body["source_width"], body["source_height"]) == (130, 100)
    assert body["crs"].startswith("EPSG:4326")
    assert abs(body["bounds"][0] - 116.0) < 1e-6
    assert abs(body["bounds"][3] - 40.0) < 1e-6


def test_preview_route_rejects_non_raster(api_client, tmp_path):
    not_raster = tmp_path / "not-a-raster.tif"
    not_raster.write_bytes(b"definitely not a tiff")
    resp = api_client.get(
        "/api/v1/geoai/preview", params={"source_uri": str(not_raster), "session_id": "route-s1"}
    )
    assert resp.status_code == 422


def test_refine_route_composes(api_client, synthetic_raster):
    first = api_client.post(
        "/api/v1/geoai/prompt-segment",
        json={
            "model_id": "tiny-promptable-seg",
            "source_uri": str(synthetic_raster),
            "artifact": _polygon_artifact_payload(),
            "session_id": "route-refine",
            "return_candidates": True,
        },
    )
    assert first.status_code == 200
    cand = first.json()["outputs"]["prompt_candidates"]
    resp = api_client.post(
        "/api/v1/geoai/prompt-refine",
        json={
            "model_id": "tiny-promptable-seg",
            "source_uri": str(synthetic_raster),
            "candidates_path": cand["path"],
            "candidate": 0,
            "session_id": "route-refine",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert body["refined_from"]["candidate"] == 0
    assert body["refined_from"]["prior_pixels"] > 0
