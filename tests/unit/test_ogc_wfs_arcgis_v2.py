"""OGC API / WFS / ArcGIS V2 contract tests (ADR-0094 Wave E).

覆盖审计修复 + V2 语义：
- WFS：GeoJSON CRS dict 接受（C1 回归）、startIndex 下推、FES AST 过滤（POST）、
  propertyName 投影、轴序 URN（M3）
- OGC API：CQL2 conformance 门控（未声明 → typed error；声明 → filter+filter-lang）、
  links.next 游标、datetime 下推、bbox-crs 显式
- ArcGIS：where 由 AST 编译（引号转义/注入面关闭 F-9）、outFields 投影、
  orderByFields 稳定排序、exceededTransferLimit 截断诚实（M-2）、maxRecordCount
  页钳制、returnCountOnly 统计模式
"""

import pytest

from app.schemas.data_fabric_schema import ConnectionProfile, QuerySpec
from app.services.data_fabric.adapters import ArcGISAdapter, OGCAPIAdapter, WFSAdapter
from app.services.data_fabric.errors import InvalidQueryError, SourceBadResponseError

from tests.unit.test_ogc_adapters_753 import (
    ARCGIS_FEATURES,
    ARCGIS_LAYER,
    ARCGIS_SERVICE,
    FakeResponse,
    FakeSession,
    OGC_COLLECTIONS,
    OGC_ITEMS,
    OGC_PARCELS,
    OGC_QUERYABLES,
    WFS_CAPABILITIES,
    WFS_FEATURES,
)


# ── WFS ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def wfs():
    profile = ConnectionProfile(provider_type="wfs", endpoint="")
    profile.url = "https://example.com/wfs"
    adapter = WFSAdapter(profile)
    adapter.session = FakeSession()
    adapter.session.routes = {
        "GetCapabilities": FakeResponse(content=WFS_CAPABILITIES),
        "/wfs": FakeResponse(json_data=WFS_FEATURES),
    }
    return adapter


def test_wfs_crs_dict_form_accepted_c1(wfs):
    """审计 C1 回归：GeoServer dict CRS（CRS84 URN）不再被误判拒绝。"""
    wfs.session.routes = {
        "GetCapabilities": FakeResponse(content=WFS_CAPABILITIES),
        "/wfs": FakeResponse(json_data={
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
            "features": WFS_FEATURES["features"],
        }),
    }
    res = wfs.query("roads", QuerySpec(limit=5))
    assert res.features and res.returned_count == 1


def test_wfs_crs_dict_projected_still_refused(wfs):
    wfs.session.routes = {
        "GetCapabilities": FakeResponse(content=WFS_CAPABILITIES),
        "/wfs": FakeResponse(json_data={
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::28992"}},
            "features": WFS_FEATURES["features"],
        }),
    }
    with pytest.raises(SourceBadResponseError, match="28992"):
        wfs.query("roads", QuerySpec(limit=5))


def test_wfs_startindex_pushed_for_offset(wfs):
    wfs.query("roads", QuerySpec(limit=10, offset=40))
    call = [c for c in wfs.session.calls if c["params"].get("REQUEST") == "GetFeature"][-1]
    assert call["params"]["STARTINDEX"] == 40, "WFS 1.1+/2.0 offset must push STARTINDEX (M4)"


def test_wfs_startindex_absent_for_v1_0(wfs):
    wfs.version = "1.0.0"
    wfs.query("roads", QuerySpec(limit=10, offset=40))
    call = [c for c in wfs.session.calls if c["params"].get("REQUEST") == "GetFeature"][-1]
    assert "STARTINDEX" not in call["params"]


def test_wfs_propertyname_projection(wfs):
    wfs.query("roads", QuerySpec(limit=5, fields=["name", "owner"]))
    call = [c for c in wfs.session.calls if c["params"].get("REQUEST") == "GetFeature"][-1]
    assert call["params"]["PROPERTYNAME"] == "name,owner"


def test_wfs_fes_filter_post_path(wfs):
    """AST 过滤器走 POST FES XML；值经实体转义（无注入面）。"""
    wfs.session.routes = {
        "GetCapabilities": FakeResponse(content=WFS_CAPABILITIES),
        "post": FakeResponse(json_data=WFS_FEATURES),
    }
    captured = {}

    def fake_post(url, data=None, **kw):
        captured["url"] = url
        captured["body"] = data.decode("utf-8") if isinstance(data, bytes) else str(data)
        return FakeResponse(json_data=WFS_FEATURES)

    wfs.session.post = fake_post
    spec = QuerySpec(limit=5, filter_expr={"op": "eq", "field": "name", "value": "a<b&'x'"})
    wfs.query("roads", spec)
    body = captured["body"]
    assert "GetFeature" in body and "ogc:Filter" not in body  # filter 在 Query 内
    assert "PropertyIsEqualTo" in body
    assert "a&lt;b&amp;" in body, "XML 实体转义必须生效"
    assert "a<b" not in body, "未转义的 < 不得出现在 XML 中"


def test_wfs_numbermatched_honest_total(wfs):
    wfs.session.routes = {
        "GetCapabilities": FakeResponse(content=WFS_CAPABILITIES),
        "/wfs": FakeResponse(json_data={
            "type": "FeatureCollection",
            "numberMatched": 987,
            "features": WFS_FEATURES["features"] * 3,
        }),
    }
    res = wfs.query("roads", QuerySpec(limit=5))
    assert res.total_matching == 987
    assert res.truncated is True and res.has_more is True


# ── OGC API ─────────────────────────────────────────────────────────────────


@pytest.fixture
def ogc():
    profile = ConnectionProfile(provider_type="ogc_api", endpoint="")
    profile.url = "https://example.com/ogc"
    adapter = OGCAPIAdapter(profile)
    s = FakeSession()

    def route(url, params=None, timeout=None, **kwargs):
        s.calls.append({"url": url, "params": dict(params or {})})
        if url.endswith("/conformance"):
            return FakeResponse(json_data={"conformsTo": s.conformance})
        if url.endswith("/collections") and params is None:
            return FakeResponse(json_data=OGC_COLLECTIONS)
        if url.endswith("/queryables"):
            return FakeResponse(json_data=OGC_QUERYABLES)
        if url.endswith("/collections/parcels"):
            return FakeResponse(json_data=OGC_PARCELS)
        if "/items" in url:
            return FakeResponse(json_data=s.items_response)
        return FakeResponse(json_data={})

    s.conformance = []
    s.items_response = dict(OGC_ITEMS)
    s.get = route
    adapter.session = s
    return adapter


def test_ogc_filter_without_conformance_is_typed_error(ogc):
    """capability 门控：未声明 CQL2 → filter 是 typed error，不是静默丢弃/透传。"""
    ogc.session.conformance = []
    spec = QuerySpec(limit=5, filter_expr={"op": "eq", "field": "owner", "value": "a"})
    with pytest.raises(InvalidQueryError, match="CQL2"):
        ogc.query("parcels", spec)


def test_ogc_filter_with_conformance_compiled_cql2(ogc):
    ogc.session.conformance = ["http://www.opengis.net/spec/ogcapi-features-2/1.0/conf/cql2-text"]
    spec = QuerySpec(limit=5, filter_expr={"op": "eq", "field": "owner", "value": "O'Brien"})
    ogc.query("parcels", spec)
    call = [c for c in ogc.session.calls if c["url"].endswith("/items")][-1]
    assert call["params"]["filter"] == "owner = 'O''Brien'"
    assert call["params"]["filter-lang"] == "cql2-text"


def test_ogc_links_next_cursor(ogc):
    ogc.session.items_response = {
        "type": "FeatureCollection",
        "numberMatched": 50,
        "features": OGC_ITEMS["features"],
        "links": [{"rel": "next", "href": "https://example.com/ogc/collections/parcels/items?token=abc"}],
    }
    r1 = ogc.query("parcels", QuerySpec(limit=1, page_kind="cursor"))
    assert r1.next_cursor, "links.next must surface as cursor"
    r2 = ogc.query("parcels", QuerySpec(limit=1, page_kind="cursor", cursor=r1.next_cursor))
    assert r2.features  # 第二页使用 next URL


def test_ogc_datetime_pushdown(ogc):
    spec = QuerySpec(limit=5, datetime_range=["2024-01-01", "2024-12-31"])
    ogc.query("parcels", spec)
    call = [c for c in ogc.session.calls if c["url"].endswith("/items")][-1]
    assert call["params"]["datetime"] == "2024-01-01/2024-12-31"


def test_ogc_bbox_crs_explicit(ogc):
    ogc.query("parcels", QuerySpec(limit=5, bbox=[100, 20, 110, 30]))
    call = [c for c in ogc.session.calls if c["url"].endswith("/items")][-1]
    assert call["params"]["bbox"] == "100.0,20.0,110.0,30.0"
    assert "CRS84" in call["params"]["bbox-crs"], "bbox-crs must be explicit (no 4326 assumption)"


# ── ArcGIS ──────────────────────────────────────────────────────────────────


@pytest.fixture
def arcgis():
    profile = ConnectionProfile(provider_type="arcgis", endpoint="")
    profile.url = "https://example.com/arcgis/rest/services/Hosted/FS/FeatureServer"
    adapter = ArcGISAdapter(profile)
    s = FakeSession()

    def route(url, params=None, timeout=None, **kwargs):
        s.calls.append({"url": url, "params": dict(params or {})})
        if url.rstrip("/").endswith("FeatureServer"):
            return FakeResponse(json_data=ARCGIS_SERVICE)
        if url.rstrip("/").endswith("/0"):
            layer = dict(ARCGIS_LAYER)
            layer.update(s.layer_overrides)
            return FakeResponse(json_data=layer)
        if url.rstrip("/").endswith("/query"):
            resp = dict(s.query_response)
            return FakeResponse(json_data=resp)
        return FakeResponse(json_data={})

    s.layer_overrides = {}
    s.query_response = dict(ARCGIS_FEATURES)
    s.get = route
    adapter.session = s
    return adapter


def test_arcgis_where_compiled_from_ast_no_injection(arcgis):
    """F-9 回归：raw where 透传通道已移除；AST 编译 + 引号转义。"""
    spec = QuerySpec(limit=5, filter_expr={"op": "eq", "field": "pid", "value": "7' OR 1=1--"})
    arcgis.query("0", spec)
    call = [c for c in arcgis.session.calls if c["url"].rstrip("/").endswith("/query")][-1]
    where = call["params"]["where"]
    assert where == "pid = '7'' OR 1=1--'", "单引号 doubling 是唯一转义；结构不可注入"
    assert "OR 1=1" not in where.replace("''", "'") or "''" in where


def test_arcgis_outfields_projection(arcgis):
    arcgis.query("0", QuerySpec(limit=5, fields=["pid"]))
    call = [c for c in arcgis.session.calls if c["url"].rstrip("/").endswith("/query")][-1]
    assert call["params"]["outFields"] == "pid,OBJECTID"


def test_arcgis_orderbyfields_stable(arcgis):
    arcgis.query("0", QuerySpec(limit=5))
    call = [c for c in arcgis.session.calls if c["url"].rstrip("/").endswith("/query")][-1]
    assert call["params"]["orderByFields"] == "OBJECTID ASC", "分页稳定排序兜底"


def test_arcgis_exceeded_transfer_limit_honest(arcgis):
    """M-2 回归：2000 上限服务 + exceededTransferLimit → truncated 如实。"""
    arcgis.session.layer_overrides = {"maxRecordCount": 2000, "supportsPagination": True}
    arcgis.session.query_response = {
        "features": [{"attributes": {"pid": i}} for i in range(2000)],
        "exceededTransferLimit": True,
    }
    res = arcgis.query("0", QuerySpec(limit=5000))
    assert res.truncated is True and res.has_more is True
    assert res.metadata["exceeded_transfer_limit"] is True
    assert res.metadata["max_record_count"] == 2000
    call = [c for c in arcgis.session.calls if c["url"].rstrip("/").endswith("/query")][-1]
    assert int(call["params"]["resultRecordCount"]) == 2000, "页大小必须钳制到服务上限"


def test_arcgis_count_only_statistics(arcgis):
    arcgis.session.query_response = {"count": 12345}
    spec = QuerySpec(limit=1, aggregate=[{"func": "count"}])
    res = arcgis.query("0", spec)
    assert res.result_mode == "statistics"
    assert res.data == [{"count": 12345}]
    call = [c for c in arcgis.session.calls if c["url"].rstrip("/").endswith("/query")][-1]
    assert call["params"]["returnCountOnly"] == "true"
    assert "outFields" not in call["params"] or call["params"].get("outFields") is None


def test_arcgis_describe_real_extent_and_srs():
    profile = ConnectionProfile(provider_type="arcgis", endpoint="")
    profile.url = "https://example.com/arcgis/rest/services/Hosted/FS/FeatureServer"
    adapter = ArcGISAdapter(profile)
    s = FakeSession()
    layer = dict(ARCGIS_LAYER)
    layer["extent"] = {
        "xmin": 116.0, "ymin": 39.0, "xmax": 117.0, "ymax": 40.5,
        "spatialReference": {"wkid": 4326},
    }
    layer["spatialReference"] = {"wkid": 4326}
    layer["maxRecordCount"] = 1000

    def route(url, params=None, timeout=None, **kwargs):
        if url.rstrip("/").endswith("/0"):
            return FakeResponse(json_data=layer)
        if url.rstrip("/").endswith("FeatureServer"):
            return FakeResponse(json_data=ARCGIS_SERVICE)
        return FakeResponse(json_data={})

    s.get = route
    adapter.session = s
    desc = adapter.describe("0")
    assert desc.srs == "EPSG:4326"
    assert desc.bbox == [116.0, 39.0, 117.0, 40.5]
    assert desc.metadata["max_record_count"] == 1000


def test_arcgis_layer_id_traversal_rejected():
    profile = ConnectionProfile(provider_type="arcgis", endpoint="")
    profile.url = "https://example.com/arcgis/rest/services/FS/FeatureServer"
    adapter = ArcGISAdapter(profile)
    with pytest.raises(InvalidQueryError):
        adapter.describe("../../admin/service")


def test_arcgis_temporal_compiled_to_where(arcgis):
    arcgis.query("0", QuerySpec(limit=5, datetime_range=["2024-01-01", "2024-12-31"]))
    call = [c for c in arcgis.session.calls if c["url"].rstrip("/").endswith("/query")][-1]
    where = call["params"]["where"]
    assert "time >= TIMESTAMP '2024-01-01'" in where
    assert "time <= TIMESTAMP '2024-12-31'" in where
