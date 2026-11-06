"""Behavioral evidence for #1860 S1-023/024/033 and #1862 S3b-016..020."""
from types import SimpleNamespace
import math

import pytest


def point(x, y):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [x, y]}, "properties": {}}


def test_local_radius_and_polygon_exclude_bbox_only_candidates(monkeypatch):
    from app.services import local_first
    from shapely.geometry import Polygon
    monkeypatch.setattr(local_first, "_local_poi_chain", lambda *args, **kwargs: {
        "features": [point(110, 30), point(110.009, 30.009)], "count": 2, "source": "local_osm"})
    result = local_first._spatial_poi_query([109, 29, 111, 31], center=[110, 30], radius_m=1000)
    assert result["count"] == 1 and result["features"][0]["geometry"]["coordinates"] == [110, 30]
    from pyproj import Geod
    x, y, _ = Geod(ellps="WGS84").fwd(110, 30, 0, 1000)
    west, south, east, north = local_first._radius_bbox(110, 30, 1000)
    assert west <= x <= east and south <= y <= north
    polygon = Polygon([(110, 30), (111, 30), (110, 31), (110, 30)])
    monkeypatch.setattr(local_first, "_local_poi_chain", lambda *args, **kwargs: {
        "features": [point(110.1, 30.1), point(110.9, 30.9)], "count": 2, "source": "local_osm"})
    result = local_first._spatial_poi_query([110, 30, 111, 31], polygon=polygon)
    assert result["count"] == 1
    assert result["spatial_filter_applied"] == "polygon"
    from app.services.local_poi import _coerce_polygon
    assert _coerce_polygon([[110, 30], [111, 30], [110, 31], [110, 30]]).area == pytest.approx(0.5)
    assert "total_matched" not in result
    assert local_first._spatial_poi_query([110, 30, 111, 31]) is None


def test_osm_template_refresh_and_per_open_config_without_environment_mutation(tmp_path, monkeypatch):
    from app.services import local_osm
    import pyogrio
    monkeypatch.setattr(local_osm, "osm_gpkg_dir", lambda: tmp_path / "output")
    monkeypatch.setattr(local_osm, "_available_memory_gb", lambda: 100)
    conf = local_osm._ensure_osmconf()
    conf.write_text("obsolete-template")
    assert local_osm._ensure_osmconf().read_text() == local_osm._OSMCONF_INI
    monkeypatch.setenv("OSM_CONFIG_FILE", "unrelated-existing-config")
    source = tmp_path / "small.osm"
    source.write_text('<osm version="0.6"><node id="1" lat="30" lon="110"><tag k="name" v="School"/><tag k="amenity" v="school"/></node></osm>')
    read = pyogrio.read_dataframe
    calls = []
    def scoped_read(*args, **kwargs):
        calls.append(kwargs.get("CONFIG_FILE"))
        return read(*args, **kwargs)
    monkeypatch.setattr(pyogrio, "read_dataframe", scoped_read)
    result = local_osm.ingest_pbf(source, ["pois"], force=True)
    assert result["themes"]["pois"]["rows"] == 1
    assert calls == [str(conf)]
    import os
    assert os.environ["OSM_CONFIG_FILE"] == "unrelated-existing-config"


def test_poi_long_rows_and_nan_township_centers_do_not_abort_ingest(tmp_path):
    from app.services import local_poi
    header = ["id", "name", "type", "typecode", "location", "adcode", "adname", "cityname", "pname", "address", "tel"]
    good = ["1", "town", "乡镇级地名", "190000", "110,30", "123456", "county", "city", "province", "", "", "extra"]
    bad = ["2", "bad", "乡镇级地名", "190000", "nan,nan", "123456"]
    count, centers = local_poi._flush_chunk([good, bad], header, tmp_path / "poi.gpkg", append=False)
    assert count == 1 and len(centers) == 1
    assert all(math.isfinite(value) for value in centers[0][2:4])


def test_district_cache_has_lru_bound_and_independent_values(monkeypatch):
    from app.services import local_poi
    local_poi._district_codes_cache.clear()
    monkeypatch.setattr(local_poi, "_DISTRICT_CACHE_MAX", 3)
    for code in ["111111", "222222", "333333", "444444"]:
        local_poi._resolve_district_codes(code)
    assert len(local_poi._district_codes_cache) == 3 and "111111" not in local_poi._district_codes_cache
    value = local_poi._resolve_district_codes("444444")
    value.append("forged")
    assert local_poi._resolve_district_codes("444444") == ["444444"]
    assert local_poi._resolve_district_codes("x" * 1000) == []
    local_poi._district_codes_cache.clear()


def test_panel_missing_year_is_skipped_and_like_wildcards_are_literal(tmp_path, monkeypatch):
    from app.services import local_yearbook
    import pandas as pd
    path = tmp_path / "panel.xlsx"
    path.touch()
    monkeypatch.setattr(local_yearbook, "yearbook_db_path", lambda: tmp_path / "yearbook.sqlite")
    frame = pd.DataFrame([
        {"年份": None, "区县代码": "123450", "区县": "bad"},
        {"年份": 2020, "区县代码": "123451", "区县": "10%_县"},
        {"年份": 2020, "区县代码": "123452", "区县": "10abc县"},
    ])
    monkeypatch.setattr(local_yearbook, "_excel_sheets", lambda path: SimpleNamespace(sheet_names=["原始数据"], parse=lambda sheet: frame))
    result = local_yearbook.ingest_county_panel(path)
    assert result["rows"] == 2
    result = local_yearbook.query_county_panel(name="%_")
    assert result["count"] == 1 and result["rows"][0]["county"] == "10%_县"


def test_stream_admission_enforces_user_source_global_lifetimes_and_rate(monkeypatch):
    from app.services.data_fabric import stream_limits as limits
    limits.reset_for_tests()
    leases = []
    try:
        first, second = limits.StreamLease("same"), limits.StreamLease("same")
        leases += [first, second]
        with pytest.raises(limits.StreamLimitExceeded, match="concurrency"):
            limits.StreamLease("same")
        first.bind_source("source")
        second.bind_source("source")
        for user in ["other1", "other2"]:
            lease = limits.StreamLease(user)
            leases.append(lease)
            lease.bind_source("source")
        next_lease = limits.StreamLease("other3")
        leases.append(next_lease)
        with pytest.raises(limits.StreamLimitExceeded, match="concurrency"):
            next_lease.bind_source("source")
        first.close()
        recovered = limits.StreamLease("same")
        leases.append(recovered)
        recovered.bind_source("source")
    finally:
        for lease in leases:
            lease.close()
    assert not limits._ACTIVE
    limits.reset_for_tests()
    monkeypatch.setattr(limits, "USER_RPM", 2)
    limits.StreamLease("rate").close()
    limits.StreamLease("rate").close()
    with pytest.raises(limits.StreamLimitExceeded, match="rate"):
        limits.StreamLease("rate")
    limits.reset_for_tests()


@pytest.mark.asyncio
async def test_catalog_literal_search_exact_geometry_and_input_limits(tmp_path, monkeypatch):
    from app.api.routes import data_fabric as routes
    from app.models.data_fabric import DataSourceModel, CatalogItemModel
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    DataSourceModel.__table__.create(engine)
    CatalogItemModel.__table__.create(engine)
    sessions = sessionmaker(engine)
    with sessions() as db:
        db.add(DataSourceModel(id="s", name="public", source_type="local_file", endpoint_url="x"))
        db.add_all([CatalogItemModel(id="one", source_id="s", name="literal%_item", geometry_type="Point"),
                    CatalogItemModel(id="two", source_id="s", name="literalXXitem", geometry_type="MultiPoint")])
        db.commit()
    monkeypatch.setattr(routes, "SessionLocal", sessions)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.get_current_user_optional] = lambda: None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.get("/data-fabric/catalog", params={"q": "%_"})
        assert result.status_code == 200 and result.json()["total"] == 1
        result = await client.get("/data-fabric/catalog", params={"geometry_type": "point"})
        assert result.status_code == 200 and result.json()["total"] == 1
        for params in [{"q": "x" * 129}, {"geometry_type": "%Point%"}]:
            assert (await client.get("/data-fabric/catalog", params=params)).status_code == 422
    engine.dispose()


@pytest.mark.asyncio
async def test_publish_rejects_absent_actor_before_session_or_project_io(monkeypatch):
    from app.services.lakehouse.project_publish import publish_to_project, revoke_project_objects, PublishError
    from unittest.mock import AsyncMock
    guard = AsyncMock()
    monkeypatch.setattr("app.services.auth_history_bridge.verify_session_owner", guard)
    with pytest.raises(PublishError, match="project not found"):
        await publish_to_project(None, session_id="s", project_id="p", object_ids=["object"], actor_id=None)
    with pytest.raises(PublishError, match="project not found"):
        await revoke_project_objects(None, project_id="p", object_ids=["object"], actor_id=None)
    guard.assert_not_called()


def test_publish_sync_uses_live_owner_editor_authority(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models.db_model import Organization, User
    from app.models.project import Project
    from app.services.lakehouse.project_publish import _publish_sync, PublishError
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for model in (Organization, User, Project):
        model.__table__.create(engine)
    sessions = sessionmaker(engine)
    monkeypatch.setattr("app.core.database.SessionLocal", sessions)
    with sessions() as db:
        db.add(Organization(id=1, name="one", slug="one"))
        db.add_all([User(id=uid, username=uid, email=f"{uid}@example.com", password_hash="x", org_id=1, role=role, is_active=True)
                    for uid, role in [("owner", "viewer"), ("editor", "editor"), ("viewer", "viewer")]])
        db.add(Project(id="project", owner_id="owner", org_id=1, name="project", status="active"))
        db.commit()
    args = dict(session_id="s", project_id="project", object_ids=[], tags=None, store=None)
    assert _publish_sync(**args, actor_id="editor")["published"] == []
    with pytest.raises(PublishError, match="project not found"):
        _publish_sync(**args, actor_id="viewer")
    with sessions() as db:
        db.get(User, "owner").is_active = False
        db.commit()
    with pytest.raises(PublishError, match="project not found"):
        _publish_sync(**args, actor_id="owner")
    engine.dispose()


@pytest.mark.asyncio
async def test_stream_source_denial_precedes_adapter_and_success_releases_lease(monkeypatch):
    from app.api.routes import data_fabric as routes
    from app.services.data_fabric import stream_limits as limits
    from app.extensions_platform import fabric_bridge
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    limits.reset_for_tests()
    holders = []
    for index in range(limits.SOURCE_CONCURRENCY):
        lease = limits.StreamLease(f"holder-{index}")
        lease.bind_source("source")
        holders.append(lease)
    monkeypatch.setattr(routes, "_authorize_catalog_item", lambda *args: SimpleNamespace(source_id="source"))
    async def prep(fn):
        return await fn(None)
    monkeypatch.setattr(routes, "_run_async_manager", prep)
    calls = []
    def resolve(*args):
        calls.append(True)
        return None, "dataset"
    monkeypatch.setattr(type(routes.data_fabric_manager), "resolve_catalog_stream", classmethod(resolve))
    async def pump(*args, **kwargs):
        yield point(110, 30)
    monkeypatch.setattr(fabric_bridge, "stream_features_from_adapter", pump)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.get_current_user] = lambda: {"user_id": "new-user"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.get("/data-fabric/catalog/item/features/stream")
        assert denied.status_code == 429 and not calls
        for lease in holders:
            lease.close()
        success = await client.get("/data-fabric/catalog/item/features/stream")
        assert success.status_code == 200 and '"_eof":true' in success.text
        assert not limits._ACTIVE
        assert (await client.get("/data-fabric/catalog/item/features/stream", params={"limit": 50001})).status_code == 422
    limits.reset_for_tests()


def test_stream_global_limit_cannot_be_bypassed_with_distinct_users(monkeypatch):
    from app.services.data_fabric import stream_limits as limits
    limits.reset_for_tests()
    monkeypatch.setattr(limits, "GLOBAL_CONCURRENCY", 1)
    first = limits.StreamLease("one")
    try:
        with pytest.raises(limits.StreamLimitExceeded, match="global"):
            limits.StreamLease("two")
    finally:
        first.close()
    limits.StreamLease("two").close()
    limits.reset_for_tests()
