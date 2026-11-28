"""survey_sampling REST 路由合同测试（UI 面；与 Pi 工具同一 service）。

GIS_MANIFEST_STRICT=0：全 app 启动的 runtime manifest 审计在单测环境
无法满足兄弟线的 capability 绑定（master 基线即红，先例：多个
tests/integration journey 同款 setdefault）——本域路由合同与此无关。
"""
from __future__ import annotations

import os

os.environ.setdefault("GIS_MANIFEST_STRICT", "0")

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def api_client(survey_env, monkeypatch):
    """TestClient + require_owned_session override（raster_sampling 同款）。"""
    from app.main import app
    from app.services.auth_history_bridge import require_owned_session

    app.dependency_overrides[require_owned_session] = lambda: type(
        "C", (), {"session_id": "sess-api-survey", "owner_token": "tok"})()
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.pop(require_owned_session, None)


def _seed_raster(survey_env):
    import asyncio

    from .conftest import write_survey_raster
    from app.services.session_data import session_data_manager

    cls_path, _ = write_survey_raster(survey_env["raster_root"] / "cls_api.tif")
    ref = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        session_data_manager.store(
            "sess-api-survey",
            {"kind": "numeric_raster", "file_path": str(cls_path)},
            prefix="raster"))
    return str(ref)


def test_frame_and_survey_flow_via_rest(api_client, survey_env):
    """POST /api/v1/survey/frame → preview → create → progress → estimate
    全链 HTTP 合同（fastapi 校验 + owner scope）。"""
    ref = _seed_raster(survey_env)

    r = api_client.post(
        "/api/v1/survey/frame",
        params={"session_id": "sess-api-survey"},
        headers={"X-Session-Token": "tok"},
        json={"source_ref": ref, "band": 1,
              "class_dictionary": {"1": "forest", "2": "crop",
                                   "3": "water"}})
    assert r.status_code == 200, r.text
    frame = r.json()["payload"]
    digest = frame["frame_digest"]
    assert frame["total_units"] > 0

    r = api_client.post(
        "/api/v1/survey/design/preview",
        params={"session_id": "sess-api-survey"},
        headers={"X-Session-Token": "tok"},
        json={"frame_digest": digest, "strategy": "equal", "n_total": 30})
    assert r.status_code == 200
    assert r.json()["preview"]["allocation"]["water"] >= 2

    r = api_client.post(
        "/api/v1/survey/create",
        params={"session_id": "sess-api-survey"},
        headers={"X-Session-Token": "tok"},
        json={"frame_digest": digest, "objective": "REST 全链",
              "target_time": "2026-11", "strategy": "equal",
              "n_total": 30, "seed": 42, "backup_per_stratum": 2,
              "blind_labelling": True})
    assert r.status_code == 200, r.text
    survey_id = r.json()["survey_id"]

    r = api_client.get(
        f"/api/v1/survey/{survey_id}/progress",
        params={"session_id": "sess-api-survey"},
        headers={"X-Session-Token": "tok"})
    assert r.status_code == 200
    assert r.json()["progress"]["total_planned"] == 30

    # 样本详情（manager 视图含层名；默认盲标视图不含）
    r = api_client.get(
        f"/api/v1/survey/{survey_id}",
        params={"session_id": "sess-api-survey"},
        headers={"X-Session-Token": "tok"})
    assert r.status_code == 200
    blind_samples = r.json()["survey"]["samples"]
    assert all("stratum_id" not in s for s in blind_samples)
    r = api_client.get(
        f"/api/v1/survey/{survey_id}",
        params={"session_id": "sess-api-survey", "blind": "false"},
        headers={"X-Session-Token": "tok"})
    assert r.status_code == 200
    assert all("stratum_id" in s for s in r.json()["survey"]["samples"])

    # 估计（零响应 → not_evaluated 状态返回，不 500）
    r = api_client.post(
        f"/api/v1/survey/{survey_id}/estimate",
        params={"session_id": "sess-api-survey"},
        headers={"X-Session-Token": "tok"})
    assert r.status_code == 422, r.text[:400]          # ESTIMATION_NOT_EVALUABLE typed
    body = r.json()
    payload = body.get("data") if isinstance(body.get("data"), dict) else body.get("detail")
    assert isinstance(payload, dict)
    assert payload.get("code") == "ESTIMATION_NOT_EVALUABLE", r.text[:400]

    # 清单
    r = api_client.get(
        "/api/v1/surveys",
        params={"session_id": "sess-api-survey"},
        headers={"X-Session-Token": "tok"})
    assert r.status_code == 200
    assert any(s["survey_id"] == survey_id for s in r.json()["surveys"])


def test_frame_validation_errors_via_rest(api_client, survey_env):
    """入参边界：空类字典/未知 ref → typed 422/404（无 500）。"""
    r = api_client.post(
        "/api/v1/survey/frame",
        params={"session_id": "sess-api-survey"},
        headers={"X-Session-Token": "tok"},
        json={"source_ref": "ref:raster-nonexistent00", "band": 1,
              "class_dictionary": {"1": "forest"}})
    assert r.status_code in (404, 422)
    body = r.json()
    payload = body.get("data") if isinstance(body.get("data"), dict) else body.get("detail")
    assert isinstance(payload, dict)
    assert payload.get("code") == "SOURCE_UNAVAILABLE"

    r = api_client.post(
        "/api/v1/survey/frame",
        params={"session_id": "sess-api-survey"},
        headers={"X-Session-Token": "tok"},
        json={"source_ref": "ref:raster-x", "band": 1,
              "class_dictionary": {}})
    assert r.status_code == 422
