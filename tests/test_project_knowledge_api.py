"""ProjectKnowledge API 与隔离测试（router surface / kill-switch / IDOR）。

红线：
- flag off → 全部端点 503（kill-switch 语义）；
- flag on → 项目 auth 门（IDOR：越权 404，不区分缺失与拒绝）；
- 响应只含 ids/摘要/指针/计数 —— 绝无 payload；
- openapi additive 变化显式刷新快照（单独由 API_SNAPSHOT_UPDATE 流程管）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

import app.models.project_knowledge  # noqa: F401,E402  — 注册进 metadata
from app.main import app  # noqa: E402

client = TestClient(app)


def _token(sub: str, org_id=None):
    from app.core.auth import create_access_token

    payload = {"sub": sub, "username": sub, "role": "viewer"}
    if org_id is not None:
        payload["org_id"] = org_id
    return {"Authorization": "Bearer " + create_access_token(payload)}


@pytest.fixture()
def flag_on(monkeypatch):
    monkeypatch.setenv("GIS_PROJECT_KNOWLEDGE", "1")
    yield
    monkeypatch.delenv("GIS_PROJECT_KNOWLEDGE", raising=False)


@pytest.fixture()
def flag_off(monkeypatch):
    monkeypatch.setenv("GIS_PROJECT_KNOWLEDGE", "0")
    yield
    monkeypatch.delenv("GIS_PROJECT_KNOWLEDGE", raising=False)


@pytest.fixture()
def setup_db():
    from app.core.database import Base, Engine

    Base.metadata.create_all(bind=Engine)
    from app.core.database import SessionLocal
    from app.models.db_model import User

    with SessionLocal() as db:
        for uid in ("pkx-owner", "pkx-other"):
            db.merge(User(id=uid, username=uid, email=f"{uid}@example.com",
                          password_hash="not-a-real-hash", role="viewer",
                          is_active=True))
        db.commit()
    yield


def _create_project(sub: str) -> str:
    res = client.post(
        "/api/v1/projects",
        json={"name": f"知识投影项目 {sub}"},
        headers=_token(sub),
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


# ── kill-switch ───────────────────────────────────────────────────────


def test_flag_off_all_endpoints_503(setup_db, flag_off):
    proj_id = _create_project("pkx-owner")
    for method, path in (
        ("get", f"/api/v1/projects/{proj_id}/knowledge/card"),
        ("get", f"/api/v1/projects/{proj_id}/knowledge/search"),
        ("get", f"/api/v1/projects/{proj_id}/knowledge/reuse-candidates"),
        ("post", f"/api/v1/projects/{proj_id}/knowledge/rebuild"),
    ):
        res = getattr(client, method)(path, headers=_token("pkx-owner"))
        assert res.status_code == 503, (method, path, res.status_code)
        # 全局异常处理器把 detail 装进统一 envelope.message
        assert res.json()["message"] == "project_knowledge_disabled"


def test_flag_on_endpoints_live(setup_db, flag_on):
    proj_id = _create_project("pkx-owner")
    # attach 一个数据集 → rebuild 应产生一条投影
    attach = client.post(
        f"/api/v1/projects/{proj_id}/datasets",
        json={"name": "dem_2024", "source_type": "upload",
              "source_ref": "upload_1", "crs": "EPSG:4326"},
        headers=_token("pkx-owner"),
    )
    assert attach.status_code == 200, attach.text

    rebuild = client.post(
        f"/api/v1/projects/{proj_id}/knowledge/rebuild",
        headers=_token("pkx-owner"),
    )
    assert rebuild.status_code == 200, rebuild.text
    body = rebuild.json()
    assert body["upserted"].get("dataset_version") == 1
    assert body["errors"] == 0
    # 幂等：第二遍 rebuild 不翻倍
    rebuild2 = client.post(
        f"/api/v1/projects/{proj_id}/knowledge/rebuild",
        headers=_token("pkx-owner"),
    )
    assert rebuild2.json()["upserted"] == body["upserted"]

    card = client.get(
        f"/api/v1/projects/{proj_id}/knowledge/card",
        headers=_token("pkx-owner"),
    )
    assert card.status_code == 200
    card_body = card.json()
    assert card_body["chars"] <= 1600
    assert card_body["items"] <= 12
    assert "dem_2024" in card_body["text"]

    search = client.get(
        f"/api/v1/projects/{proj_id}/knowledge/search?q=dem",
        headers=_token("pkx-owner"),
    )
    assert search.status_code == 200
    assert search.json()["count"] == 1
    entry = search.json()["entries"][0]
    assert entry["authority"]["id"].startswith("ds_")
    assert entry["entity_kind"] == "dataset_version"

    reuse = client.get(
        f"/api/v1/projects/{proj_id}/knowledge/reuse-candidates",
        headers=_token("pkx-owner"),
    )
    assert reuse.status_code == 200
    # 无作用域请求 → 不给 exact（fail-closed）
    assert all(c["verdict"] != "exact" for c in reuse.json()["candidates"])


# ── IDOR / 隔离 ──────────────────────────────────────────────────────


def test_foreign_user_gets_404_not_data(setup_db, flag_on):
    proj_id = _create_project("pkx-owner")
    client.post(
        f"/api/v1/projects/{proj_id}/datasets",
        json={"name": "secret_layer", "source_type": "upload",
              "source_ref": "upload_2", "crs": "EPSG:4326"},
        headers=_token("pkx-owner"),
    )
    client.post(
        f"/api/v1/projects/{proj_id}/knowledge/rebuild",
        headers=_token("pkx-owner"),
    )
    for method, path in (
        ("get", f"/api/v1/projects/{proj_id}/knowledge/card"),
        ("get", f"/api/v1/projects/{proj_id}/knowledge/search"),
        ("post", f"/api/v1/projects/{proj_id}/knowledge/rebuild"),
    ):
        res = getattr(client, method)(path, headers=_token("pkx-other"))
        assert res.status_code == 404, (method, path, res.status_code)


def test_org_scoped_stamp_prevents_cross_org_reuse(setup_db, flag_on):
    """org 7 建索引；org 9 携带同一 project_id 也读不到（空集 200）。"""
    proj_id = _create_project("pkx-owner")
    client.post(
        f"/api/v1/projects/{proj_id}/datasets",
        json={"name": "landcover", "source_type": "upload",
              "source_ref": "upload_3", "crs": "EPSG:4326"},
        headers=_token("pkx-owner", org_id=7),
    )
    client.post(
        f"/api/v1/projects/{proj_id}/knowledge/rebuild",
        headers=_token("pkx-owner", org_id=7),
    )
    # org 9：owner 相同（pkx-owner 是 owner），但 effective org 不同 ——
    # 知识查询的 org 恒等值过滤保证读不到 org:7 的投影行。
    res = client.get(
        f"/api/v1/projects/{proj_id}/knowledge/search",
        headers=_token("pkx-owner", org_id=9),
    )
    assert res.status_code == 200
    assert res.json()["count"] == 0


def test_unknown_project_404(setup_db, flag_on):
    res = client.get(
        "/api/v1/projects/proj_does_not_exist/knowledge/card",
        headers=_token("pkx-owner"),
    )
    assert res.status_code == 404
