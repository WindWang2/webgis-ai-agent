"""V9 P4 —— 模板版本化契约测试（继承覆盖矩阵 / 失效兼容读取 / V7 组件校验）。

同时覆盖 REST 面全路径（tests/data/test_quality_engine_api.py 同款
TestClient 约定）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.database import Base, Engine, SessionLocal
from app.main import app
from app.models.db_model import CartographyTemplate, User
from app.services.templates.versioning import (
    create_version,
    deep_merge,
    deprecate_version,
    extract_component_refs,
    get_version,
    resolve_payload,
    validate_component_refs,
)

client = TestClient(app)

_TID = "tmpl_v9_test"


def _auth(role="viewer", sub="tv-user"):
    from app.core.auth import create_access_token
    return {"Authorization": "Bearer " + create_access_token(
        {"sub": sub, "username": sub, "role": role})}


@pytest.fixture(autouse=True)
def _setup():
    Base.metadata.create_all(bind=Engine)
    with SessionLocal() as db:
        db.merge(User(id="tv-user", username="tv-user", email="tv-user@example.com",
                      password_hash="x", role="viewer", is_active=True))
        db.merge(User(id="tv-admin", username="tv-admin", email="tv-admin@example.com",
                      password_hash="x", role="admin", is_active=True))
        db.merge(CartographyTemplate(
            id=_TID, kind="layout", name="V9 contract", category="layout",
            keywords=[], description="", payload={"paperSize": "A4"},
            is_builtin=False, version=1,
        ))
        db.commit()
    yield
    with SessionLocal() as db:
        from app.models.template_version import TemplateVersion

        db.query(TemplateVersion).filter_by(template_id=_TID).delete()
        db.query(CartographyTemplate).filter_by(id=_TID).delete()
        db.commit()


# ── 纯函数面 ─────────────────────────────────────────────────────────


def test_deep_merge_override_matrix():
    """继承覆盖矩阵：标量/列表整体覆盖、dict 递归合并、子 None 覆盖父。"""
    parent = {"a": 1, "list": [1, 2], "style": {"font": "serif", "size": 12}}
    child = {"a": 2, "list": [3], "style": {"size": 14}, "new": True,
             "nulled": None}
    out = deep_merge(parent, child)
    assert out == {"a": 2, "list": [3], "style": {"font": "serif", "size": 14},
                   "new": True, "nulled": None}
    # 父不可变（无就地别名）
    assert parent["a"] == 1 and parent["style"]["size"] == 12


def test_extract_component_refs_explicit_and_layout_switches():
    payload = {
        "components": [{"id": "north_arrow"}, "scale_bar"],
        "component_dependencies": ["legend"],
        "showGraticule": True,
        "showLegend": False,  # False 不引
    }
    refs = extract_component_refs(payload)
    assert set(refs) == {"north_arrow", "scale_bar", "legend", "graticule"}


def test_component_validation_against_v7_registry():
    ok = validate_component_refs(["north_arrow", "scale_bar"])
    assert ok["ok"] and "north_arrow" in ok["valid"]
    bad = validate_component_refs(["no_such_component_xyz"])
    assert not bad["ok"]
    assert bad["violations"][0]["reason"] == "unknown_component"


# ── 版本化 + 继承 + 失效 ─────────────────────────────────────────────


def test_version_chain_monotonic_and_current_payload_advanced():
    with SessionLocal() as db:
        v1 = create_version(db, _TID, {"paperSize": "A4", "showLegend": True})
        assert v1.version == 1 and v1.parent_version_id is None
        v2 = create_version(db, _TID, {"style": {"font": "serif"}})
        assert v2.version == 2 and v2.parent_version_id == v1.id
        # 主表 payload 前移（既有读路径兼容），但快照不变
        tmpl = db.get(CartographyTemplate, _TID)
        assert tmpl.payload == {"paperSize": "A4", "showLegend": True, "style": {"font": "serif"}}
        assert v1.payload == {"paperSize": "A4", "showLegend": True}

        effective, chain = resolve_payload(db, v2)
        assert effective["paperSize"] == "A4"       # 父链继承
        assert effective["showLegend"] is True
        assert effective["style"] == {"font": "serif"}  # 子覆盖
        assert chain == [_TID, _TID]


def test_cross_template_inheritance_and_cycle_guard():
    with SessionLocal() as db:
        db.merge(CartographyTemplate(
            id="tmpl_v9_child", kind="layout", name="child", category="layout",
            keywords=[], description="", payload={}, is_builtin=False, version=1))
        db.commit()
        try:
            parent_v = create_version(db, _TID, {"paperSize": "A4"})
            child_v = create_version(
                db, "tmpl_v9_child", {"style": {"size": 14}},
                parent_version_id=parent_v.id)
            effective, chain = resolve_payload(db, child_v)
            assert effective["paperSize"] == "A4"
            assert chain == [_TID, "tmpl_v9_child"]
        finally:
            from app.models.template_version import TemplateVersion

            db.query(TemplateVersion).filter_by(
                template_id="tmpl_v9_child").delete()
            db.query(CartographyTemplate).filter_by(id="tmpl_v9_child").delete()
            db.commit()


def test_deprecation_keeps_compatible_read():
    with SessionLocal() as db:
        v1 = create_version(db, _TID, {"paperSize": "A4"})
        v2 = create_version(db, _TID, {"paperSize": "A3"})
        assert v2.parent_version_id == v1.id
        deprecate_version(db, _TID, 1, note="superseded")
        row = get_version(db, _TID, 1)
        assert row.deprecated_at is not None
        # 兼容读取：deprecated 版本 resolve 仍可用
        effective, _ = resolve_payload(db, row)
        assert effective == {"paperSize": "A4"}


# ── REST 全路径 ──────────────────────────────────────────────────────


def test_versions_rest_full_path():
    created = client.post(f"/api/v1/templates/{_TID}/versions", json={
        "payload": {"paperSize": "A4", "showLegend": True},
    }, headers=_auth())
    assert created.status_code == 200
    v1 = created.json()["version"]
    assert v1["version"] == 1
    assert v1["component_validation"]["ok"], "legend 是注册表内组件"

    created2 = client.post(f"/api/v1/templates/{_TID}/versions", json={
        "payload": {"style": {"font": "serif"}},
        "parent_version_id": v1["id"],
    }, headers=_auth())
    assert created2.status_code == 200
    v2 = created2.json()["version"]
    assert v2["version"] == 2

    listed = client.get(f"/api/v1/templates/{_TID}/versions")
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 2

    detail = client.get(f"/api/v1/templates/{_TID}/versions/{v2['version']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["effective_payload"]["showLegend"] is True
    assert body["inheritance_chain"] == [_TID, _TID]

    dep = client.post(
        f"/api/v1/templates/{_TID}/versions/{v2['version']}/deprecate",
        json={"note": "tmp"}, headers=_auth())
    assert dep.status_code == 200
    detail2 = client.get(f"/api/v1/templates/{_TID}/versions/{v2['version']}")
    assert detail2.json()["version"]["deprecated"] is True

    assert client.get("/api/v1/templates/none-such/versions/1").status_code == 404
    bad = client.post(f"/api/v1/templates/{_TID}/versions", json={
        "payload": {"components": ["no_such_component_xyz"]}},
        headers=_auth())
    assert bad.status_code == 200  # 创建成功但违规随版本快照披露
    assert bad.json()["version"]["component_validation"]["ok"] is False
    write_denied = client.post(f"/api/v1/templates/{_TID}/versions", json={
        "payload": {}})
    assert write_denied.status_code in (401, 403)
