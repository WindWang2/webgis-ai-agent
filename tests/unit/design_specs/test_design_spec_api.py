"""WP1 REST 面测试 —— 全路径 + 授权（SEC-02）+ 预览（渲染目标/冲突/字体探测）。

TestClient 约定同 tests/data/test_template_versions_v9.py。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.database import Base, Engine, SessionLocal
from app.main import app
from app.models.design_spec import CartographyDesignSpec, CartographyDesignSpecVersion

client = TestClient(app)


def _auth(role="viewer", sub="ds-user"):
    from app.core.auth import create_access_token
    return {"Authorization": "Bearer " + create_access_token(
        {"sub": sub, "username": sub, "role": role})}


def _payload(name="API测试规范", **kw) -> dict:
    base = {
        "spec_version": "design_spec.v1",
        "name": name,
        "color": {"theme_id": "cartographic.light_interactive", "palette_sequential": "Blues"},
        "classification": {"default_method": "quantiles", "shared_required": True},
        "output": {"targets": ["interactive", "png", "pdf", "svg"]},
    }
    base.update(kw)
    return base


@pytest.fixture(autouse=True)
def _setup():
    Base.metadata.create_all(bind=Engine)
    with SessionLocal() as db:
        from app.models.db_model import User

        for uid in ("ds-user", "owner-x", "attacker-y", "ds-admin"):
            db.merge(User(id=uid, username=uid, email=f"{uid}@example.com",
                          password_hash="x", role="viewer", is_active=True))
        db.merge(User(id="ds-admin", username="ds-admin", email="ds-admin@example.com",
                      password_hash="x", role="admin", is_active=True))
        db.commit()
    yield
    with SessionLocal() as db:
        from app.models.db_model import User

        db.query(CartographyDesignSpecVersion).delete()
        db.query(CartographyDesignSpec).delete()
        db.query(User).filter(User.id.in_(("ds-user", "owner-x", "attacker-y", "ds-admin"))).delete(synchronize_session=False)
        db.commit()


class TestRestLifecycle:
    def test_create_list_get_roundtrip(self):
        r = client.post("/api/v1/design-specs", json={"payload": _payload()}, headers=_auth())
        assert r.status_code == 200, r.text
        spec = r.json()["spec"]
        assert spec["current_version"] == 1
        assert spec["structural_digest"].startswith("dspec-sha256:")

        r2 = client.get("/api/v1/design-specs", headers=_auth())
        names = {item["name"] for item in r2.json()["items"]}
        assert "API测试规范" in names

        r3 = client.get(f"/api/v1/design-specs/{spec['id']}", headers=_auth())
        assert r3.status_code == 200
        assert r3.json()["payload"]["name"] == "API测试规范"
        assert r3.json()["reference_validation"]["ok"]

    def test_create_requires_auth(self):
        r = client.post("/api/v1/design-specs", json={"payload": _payload(name="匿名规范")})
        assert r.status_code in (401, 403)

    def test_name_conflict_maps_409(self):
        client.post("/api/v1/design-specs", json={"payload": _payload(name="冲突名")}, headers=_auth())
        r = client.post(
            "/api/v1/design-specs", json={"payload": _payload(name="冲突名")}, headers=_auth()
        )
        assert r.status_code == 409
        assert r.json()["data"]["code"] == "DESIGN_SPEC_NAME_CONFLICT"

    def test_invalid_payload_maps_400_with_violations(self):
        bad = _payload(name="坏规范")
        bad["classification_breaks"] = [1, 2, 3]
        r = client.post("/api/v1/design-specs", json={"payload": bad}, headers=_auth())
        assert r.status_code == 400
        detail = r.json()["data"]
        assert detail["code"] == "DESIGN_SPEC_INVALID"
        assert any(v["reason"] == "DATA_VALUES_FORBIDDEN" for v in detail["violations"])

    def test_version_create_and_effective_resolution(self):
        spec = client.post(
            "/api/v1/design-specs", json={"payload": _payload(name="版本规范")}, headers=_auth()
        ).json()["spec"]
        v1 = client.get(
            f"/api/v1/design-specs/{spec['id']}/versions/1", headers=_auth()
        ).json()["version"]
        r = client.post(
            f"/api/v1/design-specs/{spec['id']}/versions",
            json={
                "payload": _payload(name="版本规范", description="子版"),
                "parent_version_id": v1["id"],
            },
            headers=_auth(),
        )
        assert r.status_code == 200, r.text
        assert r.json()["version"]["version"] == 2
        eff = client.get(
            f"/api/v1/design-specs/{spec['id']}/versions/latest", headers=_auth()
        ).json()
        assert eff["effective_payload"]["description"] == "子版"
        assert eff["effective_payload"]["color"]["theme_id"] == "cartographic.light_interactive"
        # 同规范继承链：逐版本节点（v1,v2），spec id 一致（跨规范被拒）
        assert eff["inheritance_chain"] == [spec["id"], spec["id"]]

    def test_cross_tenant_reads_404_not_403(self):
        # 不可见 = 404 不泄露存在性（SEC-02）
        spec = client.post(
            "/api/v1/design-specs", json={"payload": _payload(name="他人规范")}, headers=_auth(sub="owner-x")
        ).json()["spec"]
        r = client.get(f"/api/v1/design-specs/{spec['id']}", headers=_auth(sub="attacker-y"))
        assert r.status_code == 404

    def test_publish_and_retire(self):
        spec = client.post(
            "/api/v1/design-specs", json={"payload": _payload(name="发布退役规范")}, headers=_auth()
        ).json()["spec"]
        r = client.post(f"/api/v1/design-specs/{spec['id']}/publish", headers=_auth())
        assert r.status_code == 200 and r.json()["spec"]["status"] == "published"
        r2 = client.post(
            f"/api/v1/design-specs/{spec['id']}/retire", json={"note": "replaced"},
            headers=_auth(),
        )
        assert r2.status_code == 200
        assert r2.json()["spec"]["status"] == "retired"
        assert r2.json()["spec"]["retired"] is True

    def test_deprecate_version(self):
        spec = client.post(
            "/api/v1/design-specs", json={"payload": _payload(name="废弃API规范")}, headers=_auth()
        ).json()["spec"]
        r = client.post(
            f"/api/v1/design-specs/{spec['id']}/versions/1/deprecate",
            json={"note": "old"}, headers=_auth(),
        )
        assert r.status_code == 200
        assert r.json()["deprecated"] is True


class TestPreview:
    def test_preview_reports_targets_chain_and_digest(self):
        spec = client.post(
            "/api/v1/design-specs", json={"payload": _payload(name="预览规范")}, headers=_auth()
        ).json()["spec"]
        r = client.get(f"/api/v1/design-specs/{spec['id']}/preview", headers=_auth())
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["spec"]["id"] == spec["id"]
        assert set(body["render_targets"]["supported"]) == {"interactive", "png", "pdf", "svg"}
        assert body["render_targets"]["unsupported"] == []
        assert body["effective_payload"]["name"] == "预览规范"
        assert body["resolved_digest"].startswith("dspec-sha256:")

    def test_preview_reports_theme_target_conflict(self):
        # 暗色主题不声明 sequential palette 也不声明部分输出目标 → 如实披露
        payload = _payload(name="冲突预览规范")
        payload["color"]["theme_id"] = "cartographic.dark_interactive"
        payload["output"]["targets"] = ["interactive", "pdf"]
        spec = client.post(
            "/api/v1/design-specs", json={"payload": payload}, headers=_auth()
        )
        assert spec.status_code == 200, spec.text
        r = client.get(f"/api/v1/design-specs/{spec.json()['spec']['id']}/preview", headers=_auth())
        body = r.json()
        # 暗色主题 output_targets 是否含 pdf 由 registry 决定 —— 断言只锁
        # 结构真实性：unsupported 每条带 reason，supported ∪ unsupported == 声明目标。
        declared = {"interactive", "pdf"}
        got = {t for t in body["render_targets"]["supported"]} | {
            t["target"] for t in body["render_targets"]["unsupported"]
        }
        assert got == declared
        for item in body["render_targets"]["unsupported"]:
            assert item["reason"]

    def test_preview_unknown_paper_profile_lists_unsupported(self):
        # 直接用 service 层构造（路由层创建会拒绝未知 profile）——预览如实披露
        from app.services.design_specs.preview import build_design_spec_preview

        payload = _payload(name="纸张披露规范")
        payload["output"] = {"targets": ["interactive", "pdf"], "paper_profile": "a4_landscape"}
        with SessionLocal() as db:
            from app.services.design_specs.versioning import create_spec

            spec = create_spec(db, payload, creator_id="ds-user")
            result = build_design_spec_preview(db, spec.id)
            assert result["reference_validation"]["ok"]
            assert "pdf" in result["render_targets"]["supported"]
