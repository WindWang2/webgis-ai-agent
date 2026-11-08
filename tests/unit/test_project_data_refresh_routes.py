"""G10 REST 路由集成测试（真实 HTTP + 真实认证/权限门 + 真实编排）。

模式：DI（真实后端服务调用）。payload 与 MapSpec 端口用本地合成数据
（契约允许），但鉴权、请求校验、proposal 持久化、漂移检查、CAS 采纳
全部走生产代码。浏览器证据另见 E2E journey，不在此冒充。
"""
from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from app.api.routes import project_data_refresh as routes_mod
from app.main import app
from app.services.project_runtime.refresh.execution import InMemoryMapSpecPort
from app.services.project_runtime.refresh.service import ProjectDataRefreshService
from app.services.project_runtime.refresh.sources import StaticPayloadProvider
from app.services.project_runtime.refresh.store import (
    RefreshStore,
    reset_refresh_store,
)

client = TestClient(app)

PID = "proj-g10-route"
SID = "sess-g10-route"
OWNER = "g10-owner"
INTRUDER = "g10-intruder"
DSID = "ds-g10-route"
REF = "ref-g10-route"


def _token(sub: str) -> dict:
    from app.core.auth import create_access_token

    return {"Authorization": "Bearer " + create_access_token(
        {"sub": sub, "username": sub, "role": "viewer"})}


def _feature(fid: str, n: int) -> dict:
    return {"type": "Feature", "id": fid,
            "properties": {"name": fid, "students": n},
            "geometry": "POINT(1 1)"}


def _payload(version: int, feats: list) -> dict:
    return {
        "descriptor": {
            "content_hash": f"h-v{version}", "content_revision": version,
            "crs": "EPSG:4326", "bbox": [103.9, 30.5, 104.2, 30.8],
            "field_schema": {"name": {"type": "str"},
                             "students": {"type": "int", "unit": "person"}},
            "field_schema_complete": True, "estimated_bytes": 1024,
            "feature_count": len(feats),
        },
        "payload": {"type": "FeatureCollection", "features": feats},
    }


def _mapspec() -> dict:
    return {"title": "学校分布", "legend": {"position": "bottom-left"},
            "layers": [{"id": "L1", "source": "ref://schools", "type": "circle",
                        "paint": {"circle-color": "#3388ff"}, "visible": True}],
            "components": [], "workbench": {"lockedLayerIds": [],
                                             "lockedComponentIds": []}}


FEATS_V1 = [_feature("s1", 100), _feature("s2", 200), _feature("s3", 300)]


@pytest.fixture(autouse=True)
def _setup(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.core.database import Base, Engine, SessionLocal

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    reset_refresh_store()
    Base.metadata.create_all(bind=Engine)
    with SessionLocal() as s:
        from app.models.db_model import Conversation, User
        from app.models.project import Project, ProjectDataset

        for uid in (OWNER, INTRUDER):
            s.merge(User(id=uid, username=uid, email=f"{uid}@example.com",
                         password_hash="x", role="viewer", is_active=True))
        s.merge(Project(id=PID, name="g10", owner_id=OWNER, status="active"))
        s.merge(ProjectDataset(id=DSID, project_id=PID, name="schools",
                               source_type="upload", source_ref=REF,
                               version_fingerprint="fp-v1", crs="EPSG:4326"))
        s.merge(Conversation(id=SID, user_id=OWNER, title="seed"))
        s.commit()

    store = RefreshStore()
    svc = ProjectDataRefreshService(
        store=store,
        port=InMemoryMapSpecPort(copy.deepcopy(_mapspec()), revision=3),
        payload_provider=StaticPayloadProvider({REF: _payload(1, FEATS_V1)}),
    )
    monkeypatch.setattr(routes_mod, "_svc", lambda: svc)
    _setup.svc = svc
    yield
    reset_refresh_store()


def _base() -> str:
    return f"/api/v1/projects/{PID}/data-refresh"


# ── 权限门（O01） ───────────────────────────────────────────────────

def test_o01_unauthenticated_request_is_rejected():
    r = client.post(f"{_base()}/check", json={"session_id": SID})
    assert r.status_code in (401, 403)


def test_o01_cross_owner_is_404_without_leaking_diffs():
    r = client.post(f"{_base()}/check", json={"session_id": SID},
                    headers=_token(INTRUDER))
    assert r.status_code == 404
    assert "content_hash" not in r.text
    assert "schools" not in r.text


def test_o05_invalid_dag_and_non_finite_numbers_are_rejected():
    r = client.post(f"{_base()}/preview",
                    json={"session_id": SID,
                          "dag": {"nodes": [{"node_id": "a"}] * 100,
                                  "edges": []}},
                    headers=_token(OWNER))
    assert r.status_code == 422


# ── 全链（O10 后端侧） ─────────────────────────────────────────────

def test_o10_full_http_journey_check_preview_execute_compare_adopt_rollback():
    h = _token(OWNER)
    svc: ProjectDataRefreshService = _setup.svc

    # 1) 首次检查更新 → 建立基线，不声称变化
    r = client.post(f"{_base()}/check", json={"session_id": SID}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == 0
    assert any("baseline" in w.lower() for w in r.json()["warnings"])

    # 2) 来源变化 → 检查到 stale
    feats_v2 = FEATS_V1[:-1] + [_feature("s4", 400), _feature("s5", 500)]
    svc.payload_provider = StaticPayloadProvider({REF: _payload(2, feats_v2)})
    r = client.post(f"{_base()}/check",
                    json={"session_id": SID, "establish_baseline": False}, headers=h)
    assert r.status_code == 200
    assert r.json()["changed"] == 1 and r.json()["stale"] is True

    # 3) 来源清单
    r = client.get(f"{_base()}/sources?session_id={SID}", headers=h)
    assert r.status_code == 200
    assert r.json()["items"][0]["logical_id"] == f"dataset:{DSID}"

    # 4) 预览 → 精确差异 + 最小重算子图（显式传真实分析图）
    dag = {"nodes": [{"node_id": "data:schools", "parameters": []},
                     {"node_id": "data:population", "parameters": []},
                     {"node_id": "analysis:heat", "parameters": []},
                     {"node_id": "analysis:normalized", "parameters": []},
                     {"node_id": "output:map", "parameters": []}],
           "edges": [{"from": "data:schools", "to": "analysis:heat"},
                     {"from": "data:population", "to": "analysis:normalized"},
                     {"from": "analysis:heat", "to": "output:map"},
                     {"from": "analysis:normalized", "to": "output:map"}]}
    r = client.post(f"{_base()}/preview",
                    json={"session_id": SID, "dag": dag}, headers=h)
    assert r.status_code == 200, r.text
    proposal = r.json()
    pid = proposal["proposal_id"]
    assert proposal["diffs"][0]["exact_counts"]["added"] == 2
    assert proposal["diffs"][0]["exact_counts"]["removed"] == 1
    assert "data:schools" in proposal["plan"]["recompute"]
    assert "analysis:normalized" not in proposal["plan"]["recompute"]

    # 5) 提案清单 / 状态 / 解释（刷新后恢复）
    assert client.get(f"{_base()}/proposals", headers=h).status_code == 200
    st = client.get(f"{_base()}/proposals/{pid}/status", headers=h)
    assert st.status_code == 200 and st.json()["recoverable"] is True
    ex = client.get(f"{_base()}/proposals/{pid}/explain", headers=h)
    assert ex.status_code == 200 and ex.json()["stale"] is True

    # 6) 执行隔离候选
    r = client.post(f"{_base()}/proposals/{pid}/execute", json={}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["receipt"]["version_adoption"] == "unchanged"
    assert r.json()["receipt"]["map_observation"] == "unverified"

    # 7) 候选对比
    cmp_ = client.get(f"{_base()}/proposals/{pid}/compare", headers=h)
    assert cmp_.status_code == 200 and cmp_.json()["candidate_available"] is True

    # 8) 原子采纳（revision 3 → 4）
    r = client.post(f"{_base()}/proposals/{pid}/adopt",
                    json={"expected_revision": 3,
                          "proposal_fingerprint": proposal["proposal_fingerprint"]},
                    headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "adopted"
    assert r.json()["revision_after"] == 4
    assert r.json()["map_observation"] == "unverified"

    # 9) 回退（新 revision 4 → 5，不倒写）
    r = client.post(f"{_base()}/proposals/{pid}/rollback", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["revision_after"] == 5


def test_o05_client_fingerprint_mismatch_is_refused():
    h = _token(OWNER)
    client.post(f"{_base()}/check", json={"session_id": SID}, headers=h)
    r = client.post(f"{_base()}/preview", json={"session_id": SID}, headers=h)
    assert r.status_code == 200, r.text
    pid = r.json()["proposal_id"]
    r = client.post(f"{_base()}/proposals/{pid}/adopt",
                    json={"proposal_fingerprint": "g10:forged"}, headers=h)
    assert r.status_code == 409
    # ADR-0138 统一错误信封：结构化 detail 在 data（legacy 信封在 detail）
    body = r.json()
    payload = body.get("data") or body.get("detail") or {}
    assert payload.get("code") == "proposal_fingerprint_mismatch"
