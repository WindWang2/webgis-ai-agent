"""WP3 REST 面测试 —— 全路径 + 授权（SEC-02）+ 传播/一致性/冻结计划。

TestClient 约定同 tests/unit/design_specs/test_design_spec_api.py（记得造
User 行 —— auth 校验用户存在）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.database import Base, Engine, SessionLocal
from app.main import app
from app.models.design_spec import CartographyDesignSpec, CartographyDesignSpecVersion
from app.models.map_series import CartographyMapSeries, CartographyMapSeriesVersion
from app.services.design_specs.versioning import create_spec as create_design_spec
from tests.unit.map_series.helpers import series_payload

client = TestClient(app)


def _auth(role="viewer", sub="ms-user"):
    from app.core.auth import create_access_token
    return {"Authorization": "Bearer " + create_access_token(
        {"sub": sub, "username": sub, "role": role})}


@pytest.fixture(autouse=True)
def _setup():
    Base.metadata.create_all(bind=Engine)
    with SessionLocal() as db:
        from app.models.db_model import User

        for uid in ("ms-user", "ms-owner", "ms-other", "ms-admin", "ms-peer"):
            role = "admin" if uid == "ms-admin" else "viewer"
            org = 77 if uid in ("ms-owner", "ms-peer", "ms-admin") else None
            db.merge(User(id=uid, username=uid, email=f"{uid}@example.com",
                          password_hash="x", role=role, org_id=org, is_active=True))
        spec = create_design_spec(
            db, {"spec_version": "design_spec.v1", "name": "API宿主规范"},
            creator_id="ms-owner",
        )
        db.merge(User(id="ms-owner", username="ms-owner", email="ms-owner@example.com",
                      password_hash="x", role="viewer", is_active=True))
        db.commit()
        db._host_spec_id = spec.id  # noqa: SLF001
    yield
    with SessionLocal() as db:
        db.query(CartographyMapSeriesVersion).delete()
        db.query(CartographyMapSeries).delete()
        db.query(CartographyDesignSpecVersion).delete()
        db.query(CartographyDesignSpec).delete()
        db.query(User).filter(User.id.in_(
            ("ms-user", "ms-owner", "ms-other", "ms-admin", "ms-peer"))).delete(
            synchronize_session=False)
        db.commit()


def _payload(db, name="API系列计划", **kw) -> dict:
    spec_id = getattr(db, "_host_spec_id", None)
    if spec_id is None:
        with SessionLocal() as s:
            row = s.query(CartographyDesignSpec).filter_by(name="API宿主规范").first()
            spec_id = row.id
    return series_payload(name=name, design_spec_id=spec_id, **kw)


def _create_series(name="API系列计划", sub="ms-owner", **kw):
    with SessionLocal() as db:
        payload = _payload(db, name=name, **kw)
    r = client.post("/api/v1/map-series", json={"payload": payload}, headers=_auth(sub=sub))
    assert r.status_code == 200, r.text
    return r.json()["series"]


class TestRestLifecycle:
    def test_create_list_get_roundtrip(self):
        series = _create_series()
        assert series["current_version"] == 1
        assert series["structural_digest"].startswith("mseries-sha256:")
        assert series["spec_version"] >= 1  # 缺省 pin 解析时点记录

        r2 = client.get("/api/v1/map-series", headers=_auth(sub="ms-owner"))
        names = {item["name"] for item in r2.json()["items"]}
        assert "API系列计划" in names

        r3 = client.get(f"/api/v1/map-series/{series['id']}", headers=_auth(sub="ms-owner"))
        assert r3.status_code == 200
        assert r3.json()["payload"]["name"] == "API系列计划"
        assert len(r3.json()["payload"]["members"]) == 2

    def test_create_requires_auth(self):
        with SessionLocal() as db:
            payload = _payload(db, name="匿名系列")
        r = client.post("/api/v1/map-series", json={"payload": payload})
        assert r.status_code in (401, 403)

    def test_create_unknown_spec_pin_400(self):
        payload = series_payload(name="坏pin系列", design_spec_id="no-such-spec")
        r = client.post("/api/v1/map-series", json={"payload": payload}, headers=_auth())
        assert r.status_code == 400
        assert r.json()["data"]["code"] == "MAP_SERIES_INVALID"

    def test_name_conflict_maps_409(self):
        _create_series(name="冲突系列")
        r = client.post("/api/v1/map-series",
                        json={"payload": _payload_from_db("冲突系列")},
                        headers=_auth(sub="ms-owner"))
        assert r.status_code == 409
        assert r.json()["data"]["code"] == "MAP_SERIES_NAME_CONFLICT"

    def test_cross_tenant_reads_404_not_403(self):
        series = _create_series(name="他人系列")
        r = client.get(f"/api/v1/map-series/{series['id']}", headers=_auth(sub="ms-other"))
        assert r.status_code == 404  # 不可见 = 404 不泄露存在性（SEC-02）

    def test_member_upsert_and_remove(self):
        series = _create_series(name="成员系列")
        sid = series["id"]
        r = client.post(f"/api/v1/map-series/{sid}/members", json={
            "op": "upsert",
            "member": {"member_id": "m3", "title": "细节图",
                       "session_id": "sess-api-m3", "role": "detail"},
        }, headers=_auth(sub="ms-owner"))
        assert r.status_code == 200, r.text
        assert r.json()["member_count"] == 3

        r2 = client.post(f"/api/v1/map-series/{sid}/members", json={
            "op": "remove", "member_id": "m3",
        }, headers=_auth(sub="ms-owner"))
        assert r2.status_code == 200
        assert r2.json()["op"] == "remove"

    def test_member_write_requires_can_write(self):
        series = _create_series(name="只读成员系列")
        r = client.post(f"/api/v1/map-series/{series['id']}/members", json={
            "op": "upsert",
            "member": {"member_id": "mx", "title": "x", "session_id": "s"},
        }, headers=_auth(sub="ms-peer"))  # 同组织但非创建者且非 admin
        assert r.status_code == 403

    def test_version_create_and_inheritance(self):
        series = _create_series(name="版本系列")
        sid = series["id"]
        client.get(f"/api/v1/map-series/{sid}", headers=_auth(sub="ms-owner"))
        r = client.post(f"/api/v1/map-series/{sid}/versions", json={
            "payload": _payload_from_db("版本系列", description="子版"),
        }, headers=_auth(sub="ms-owner"))
        assert r.status_code == 200, r.text
        assert r.json()["version"]["version"] == 2
        r2 = client.get(f"/api/v1/map-series/{sid}", headers=_auth(sub="ms-owner"))
        assert r2.json()["series"]["current_version"] == 2
        assert r2.json()["payload"]["description"] == "子版"

    def test_cross_series_parent_rejected_400(self):
        s1 = _create_series(name="父系列")
        s2 = _create_series(name="子系列")
        with SessionLocal() as db:
            v1 = db.query(CartographyMapSeriesVersion).filter_by(series_id=s1["id"]).first()
            parent_id = v1.id
        r = client.post(f"/api/v1/map-series/{s2['id']}/versions", json={
            "payload": _payload_from_db("子系列"),
            "parent_version_id": parent_id,  # 跨系列 parent
        }, headers=_auth(sub="ms-owner"))
        assert r.status_code == 400


def _payload_from_db(name, **kw):
    with SessionLocal() as db:
        row = db.query(CartographyDesignSpec).filter_by(name="API宿主规范").first()
        return series_payload(name=name, design_spec_id=row.id, **kw)


class TestRestConsistencyPropagationFreeze:
    def test_consistency_reports_findings(self):
        series = _create_series(name="REST一致性系列")
        r = client.get(
            f"/api/v1/map-series/{series['id']}/consistency", headers=_auth(sub="ms-owner")
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is True
        assert isinstance(body["findings"], list)
        assert body["ok"] is True  # SESSION_UNAVAILABLE 是 warning，不阻断
        codes = {f["code"] for f in body["findings"]}
        assert "SESSION_UNAVAILABLE" in codes

    def test_propagation_preview_readonly(self):
        series = _create_series(name="REST预览系列")
        r = client.post(
            f"/api/v1/map-series/{series['id']}/propagation/preview",
            json={"intents": {"palette": {"target_layer_id": "districts", "color": "#123456"}}},
            headers=_auth(sub="ms-owner"),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is True
        assert body["unresolvable_member_ids"] == ["m1", "m2"]  # 会话不存在如实披露

    def test_propagation_apply_requires_write(self):
        series = _create_series(name="REST应用授权系列")
        r = client.post(
            f"/api/v1/map-series/{series['id']}/propagation/apply",
            json={"intents": {"palette": {"target_layer_id": "districts", "color": "#123456"}}},
            headers=_auth(sub="ms-peer"),  # 同组织可见但不可写
        )
        assert r.status_code == 403

    def test_propagation_apply_returns_receipts(self):
        series = _create_series(name="REST应用系列")
        r = client.post(
            f"/api/v1/map-series/{series['id']}/propagation/apply",
            json={"intents": {"palette": {"target_layer_id": "districts", "color": "#123456"}}},
            headers=_auth(sub="ms-owner"),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is True
        statuses = {r_["status"] for r_ in body["receipts"]}
        assert statuses == {"failed"}  # 会话缺失如实 failed，不假造 committed
        assert "batch is not atomic" in body["note"]

    def test_propagation_preview_invalid_intents_400(self):
        series = _create_series(name="REST非法意图系列")
        r = client.post(
            f"/api/v1/map-series/{series['id']}/propagation/preview",
            json={"intents": {"palette": {"target_layer_id": "d", "color": "red"}}},
            headers=_auth(sub="ms-owner"),
        )
        assert r.status_code == 400

    def test_freeze_plan_returns_json(self):
        series = _create_series(name="REST冻结系列")
        r = client.post(
            f"/api/v1/map-series/{series['id']}/freeze-plan", json={},
            headers=_auth(sub="ms-owner"),
        )
        assert r.status_code == 200, r.text
        plan = r.json()["freeze_plan"]
        assert plan["plan_version"] == "map_series_freeze.v1"
        assert plan["unresolvable_member_ids"] == ["m1", "m2"]


class TestTools:
    def _registry(self):
        from app.tools.map_series_tools import register_map_series_tools
        from app.tools.registry import ToolRegistry

        registry = ToolRegistry()
        register_map_series_tools(registry)
        return registry

    def test_tools_registered(self):
        registry = self._registry()
        for name in (
            "webgis_map_series_list",
            "webgis_map_series_inspect",
            "webgis_map_series_create",
            "webgis_map_series_add_member",
            "webgis_map_series_consistency",
            "webgis_map_series_propagation_preview",
            "webgis_map_series_propagation_apply",
        ):
            assert name in registry.tool_names(), name

    def test_list_tool_schema_not_degraded_by_guard(self):
        # _guard 的 functools.wraps 必须保留 —— @tool 依赖签名内省
        registry = self._registry()
        schemas = {
            s.get("function", {}).get("name"): s
            for s in registry.get_schemas()
        }
        schema = schemas["webgis_map_series_consistency"]
        assert "series_id" in str(schema)

    async def test_tool_create_list_inspect_consistency_roundtrip(self):
        from app.services.jobs.context import JobOrigin, use_origin

        registry = self._registry()
        with SessionLocal() as db:
            from app.models.db_model import User

            db.merge(User(id="ms-owner", username="ms-owner",
                          email="ms-owner@example.com", password_hash="x",
                          role="viewer", is_active=True))
            spec = create_design_spec(
                db, {"spec_version": "design_spec.v1", "name": "工具宿主规范"},
                creator_id="ms-owner",
            )
            db.commit()
            payload = series_payload(
                name="工具系列", design_spec_id=spec.id,
                members=[{"member_id": "m1", "title": "图1",
                          "session_id": "sess-tool-m1"}],
            )
        with use_origin(JobOrigin(session_id="sess-tool", owner_id="ms-owner")):
            created = await registry.dispatch(
                "webgis_map_series_create", {"payload": payload}
            )
            assert created["success"], created
            series_id = created["series"]["id"]

            listing = await registry.dispatch("webgis_map_series_list", {"limit": 5})
            assert listing["success"]
            assert any(i["name"] == "工具系列" for i in listing["items"])

            inspected = await registry.dispatch(
                "webgis_map_series_inspect", {"series_id": series_id}
            )
            assert inspected["success"]
            assert inspected["payload"]["name"] == "工具系列"

            consistency = await registry.dispatch(
                "webgis_map_series_consistency", {"series_id": series_id}
            )
            assert consistency["success"]
            assert any(
                f["code"] == "SESSION_UNAVAILABLE" for f in consistency["findings"]
            )

            preview = await registry.dispatch(
                "webgis_map_series_propagation_preview",
                {"series_id": series_id,
                 "intents": {"palette": {"target_layer_id": "districts",
                                         "color": "#123456"}}},
            )
            assert preview["success"]
            assert preview["unresolvable_member_ids"] == ["m1"]

    async def test_tool_anonymous_create_rejected(self):
        registry = self._registry()
        result = await registry.dispatch(
            "webgis_map_series_create",
            {"payload": series_payload(name="工具匿名系列",
                                       design_spec_id="whatever")},
        )
        assert not result["success"]
        assert result["code"] == "MAP_SERIES_FORBIDDEN"
