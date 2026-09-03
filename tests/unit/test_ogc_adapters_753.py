"""#753: the four production-registered OGC network adapters had ZERO tests —
they are the remote-fetch surface (URL construction, pagination, response
parsing) where reliability bugs historically land. Contract tests via the
shared verify_adapter_contract + targeted behavior assertions, all offline
(fake HTTP session)."""
import pytest

from app.schemas.data_fabric_schema import ConnectionProfile, QuerySpec
from app.services.data_fabric.adapters import (
    ArcGISAdapter,
    OGCAPIAdapter,
    WFSAdapter,
    WMSWMTSAdapter,
)
from tests.unit.test_data_fabric_contract import verify_adapter_contract
from app.services.data_fabric.errors import SourceBadResponseError


class FakeResponse:
    def __init__(self, *, json_data=None, content=b"", text="", status_code=200,
                 headers=None):
        import json as _json
        self._json = json_data
        # bounded_get 走 iter_content：json fixture 需要落到 content
        if content:
            self.content = content
        elif json_data is not None:
            self.content = _json.dumps(json_data).encode()
        else:
            self.content = text.encode()
        self.text = text or (content.decode() if content else "")
        self.status_code = status_code
        self.headers = headers or {"Content-Type": "application/json"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def iter_content(self, chunk_size=65536):
        body = self.content
        for i in range(0, len(body), chunk_size):
            yield body[i:i + chunk_size]

    def close(self):
        pass


class FakeSession:
    """Records requests; routes by substring on the URL."""

    def __init__(self):
        self.calls = []
        self.headers = {}
        self.routes = {}

    def get(self, url, params=None, timeout=None, **kwargs):
        p = dict(params or {})
        self.calls.append({"url": url, "params": p})
        # route on URL + serialized params: OGC endpoints carry REQUEST in
        # query params, not the path.
        key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted(p.items()))
        for needle, resp in self.routes.items():
            if needle in url or needle in key:
                return resp
        return FakeResponse(json_data={})

    def post(self, url, **kw):  # pragma: no cover - unused by these adapters
        raise NotImplementedError


WFS_CAPABILITIES = b"""<?xml version="1.0"?>
<wfs:WFS_Capabilities xmlns:wfs="http://www.opengis.net/wfs/2.0">
  <wfs:FeatureTypeList>
    <wfs:FeatureType>
      <wfs:Name>roads</wfs:Name><wfs:Title>Roads</wfs:Title>
      <wfs:Abstract>Road network</wfs:Abstract>
    </wfs:FeatureType>
  </wfs:FeatureTypeList>
</wfs:WFS_Capabilities>"""

WFS_FEATURES = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {"name": "ring"}, "geometry": {"type": "Point", "coordinates": [116.4, 39.9]}}
    ],
}

OGC_COLLECTIONS = {
    "collections": [{"id": "parcels", "title": "Parcels", "itemType": "feature"}],
}
OGC_PARCELS = {
    "id": "parcels", "title": "Parcels", "itemType": "feature",
    "extent": {"spatial": {"bbox": [[100.0, 20.0, 120.0, 40.0]]}},
    "crs": ["EPSG:4326"],
}
OGC_QUERYABLES = {"properties": {"owner": {"type": "string", "title": "Owner"}}}
OGC_ITEMS = {
    "type": "FeatureCollection",
    "bbox": [100.0, 20.0, 120.0, 40.0],
    "features": [{"type": "Feature", "properties": {"owner": "a"}, "geometry": None}],
}

ARCGIS_SERVICE = {
    "layers": [{"id": 0, "name": "pipelines"}],
}
ARCGIS_LAYER = {
    "id": 0, "name": "pipelines", "description": "Pipes",
    "extent": {"xmin": 100, "ymin": 20, "xmax": 120, "ymax": 40},
    "geometryType": "esriGeometryPolyline",
    "fields": [{"name": "pid", "type": "esriFieldTypeInteger"}],
}
ARCGIS_FEATURES = {
    "features": [{"attributes": {"pid": 7}, "geometry": {"paths": [[[100, 20], [101, 21]]]}}],
    "exceededTransferLimit": False,
}

WMS_CAPABILITIES = b"""<?xml version="1.0"?>
<Capabilities xmlns="http://www.opengis.net/wmts/1.0">
  <Layer>
    <ows:Title xmlns:ows="http://www.opengis.net/ows/1.1">ortho</ows:Title>
    <ows:Identifier xmlns:ows="http://www.opengis.net/ows/1.1">ortho</ows:Identifier>
  </Layer>
</Capabilities>"""


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


@pytest.fixture
def ogc():
    profile = ConnectionProfile(provider_type="ogc_api", endpoint="")
    profile.url = "https://example.com/ogc"
    adapter = OGCAPIAdapter(profile)
    s = FakeSession()

    def route(url, params=None, timeout=None, **kwargs):
        s.calls.append({"url": url, "params": dict(params or {})})
        if url.endswith("/collections") and params is None:
            return FakeResponse(json_data=OGC_COLLECTIONS)
        if url.endswith("/collections/parcels/queryables"):
            return FakeResponse(json_data=OGC_QUERYABLES)
        if url.endswith("/collections/parcels"):
            return FakeResponse(json_data=OGC_PARCELS)
        if url.endswith("/items"):
            return FakeResponse(json_data=OGC_ITEMS)
        return FakeResponse(json_data={})

    s.get = route
    adapter.session = s
    return adapter


@pytest.fixture
def arcgis():
    profile = ConnectionProfile(provider_type="arcgis", endpoint="")
    profile.url = "https://example.com/arcgis/rest/services/Hosted/FS/FeatureServer"
    adapter = ArcGISAdapter(profile)

    def route(url, params=None, timeout=None, **kwargs):
        adapter.session.calls.append({"url": url, "params": dict(params or {})})
        if url.rstrip("/").endswith("FeatureServer"):
            return FakeResponse(json_data=ARCGIS_SERVICE)
        if url.rstrip("/").endswith("/0"):
            return FakeResponse(json_data=ARCGIS_LAYER)
        if url.rstrip("/").endswith("/query"):
            return FakeResponse(json_data=ARCGIS_FEATURES)
        return FakeResponse(json_data={})

    s = FakeSession()
    s.get = route
    adapter.session = s
    return adapter


@pytest.fixture
def wms():
    profile = ConnectionProfile(provider_type="wms_wmts", endpoint="")
    profile.url = "https://example.com/wms"
    adapter = WMSWMTSAdapter(profile)
    adapter.session = FakeSession()
    adapter.session.routes = {"GetCapabilities": FakeResponse(content=WMS_CAPABILITIES)}
    return adapter


def test_wfs_adapter_contract(wfs):
    verify_adapter_contract(wfs, "roads")
    ids = [d["id"] for d in wfs.list_datasets()]
    assert "roads" in ids


def test_wfs_query_bbox_pushdown_and_limit_bound(wfs):
    res = wfs.query("roads", QuerySpec(limit=5, bbox=[116.0, 39.5, 117.0, 40.5]))
    assert res.features and res.metadata["pushdown_bbox"] is True
    call = next(c for c in wfs.session.calls if c["params"].get("REQUEST") == "GetFeature")
    # V2（审计 M3）：WFS 2.0 srsName/BBOX 走 URN CRS84（显式 lon/lat 轴序）
    assert call["params"]["BBOX"] == "116.0,39.5,117.0,40.5,urn:ogc:def:crs:OGC:1.3:CRS84"
    # absurd limit clamps to MAX_QUERY_LIMIT
    wfs.query("roads", QuerySpec(limit=999999))
    call2 = wfs.session.calls[-1]
    assert int(call2["params"]["COUNT"]) == 10000


# ── #766 / #769: WFS fetch failures are typed errors, CRS never fabricated ──


def test_wfs_query_non_json_200_is_typed_error_766(wfs):
    """#766: a 200 GetFeature body that is not JSON (e.g. GML from a WFS 2.0
    server ignoring OUTPUTFORMAT) must be an in-band typed error — never a
    silently empty "successful" dataset."""
    wfs.session.routes = {
        "/wfs": FakeResponse(
            text="<wfs:FeatureCollection/>", headers={"Content-Type": "text/xml"}
        ),
    }
    with pytest.raises(SourceBadResponseError, match="non-JSON|JSON"):
        wfs.query("roads", QuerySpec(limit=5))


def test_wfs_query_declared_non_4326_crs_is_typed_error_769(wfs):
    """#769: a FeatureCollection declaring a projected CRS must be refused with
    a typed error — projected coordinates must not flow on as WGS84 degrees."""
    wfs.session.routes = {
        "/wfs": FakeResponse(json_data={
            "type": "FeatureCollection",
            "crs": "urn:ogc:def:crs:EPSG::28992",
            "features": [{"type": "Feature",
                          "geometry": {"type": "Point", "coordinates": [148000.0, 410000.0]},
                          "properties": {}}],
        }),
    }
    with pytest.raises(SourceBadResponseError, match="28992"):
        wfs.query("roads", QuerySpec(limit=5))


def test_wfs_query_sends_srsname_4326_769(wfs):
    """#769: GetFeature must negotiate WGS84 output via SRSNAME/srsName."""
    wfs.query("roads", QuerySpec(limit=5))
    call = next(c for c in wfs.session.calls if c["params"].get("REQUEST") == "GetFeature")
    # WFS 2.0 → URN CRS84；WFS 1.0 → EPSG 短形式（轴序语义各自正确）
    assert call["params"]["SRSNAME"] == "urn:ogc:def:crs:OGC:1.3:CRS84"
    wfs.version = "1.0.0"
    wfs.query("roads", QuerySpec(limit=5))
    gf_calls = [c for c in wfs.session.calls if c["params"].get("REQUEST") == "GetFeature"]
    assert gf_calls[-1]["params"]["SRSNAME"] == "EPSG:4326"


WFS_CAPABILITIES_SRS = b"""<?xml version="1.0"?>
<wfs:WFS_Capabilities xmlns:wfs="http://www.opengis.net/wfs/2.0">
  <wfs:FeatureTypeList>
    <wfs:FeatureType>
      <wfs:Name>bag:pand</wfs:Name><wfs:Title>BAG</wfs:Title>
      <wfs:DefaultSRS>urn:ogc:def:crs:EPSG::28992</wfs:DefaultSRS>
      <wfs:WGS84BoundingBox>
        <ows:LowerCorner xmlns:ows="http://www.opengis.net/ows/1.1">3.0 50.7</ows:LowerCorner>
        <ows:UpperCorner xmlns:ows="http://www.opengis.net/ows/1.1">7.2 53.6</ows:UpperCorner>
      </wfs:WGS84BoundingBox>
    </wfs:FeatureType>
    <wfs:FeatureType>
      <wfs:Name>roads</wfs:Name><wfs:Title>Roads</wfs:Title>
    </wfs:FeatureType>
  </wfs:FeatureTypeList>
</wfs:WFS_Capabilities>"""


def test_wfs_describe_parses_default_srs_769(wfs):
    """#769: describe() reads DefaultSRS + WGS84BoundingBox from
    GetCapabilities instead of fabricating EPSG:4326 / a worldwide bbox."""
    wfs._caps_cache = None
    wfs.session.routes = {"GetCapabilities": FakeResponse(content=WFS_CAPABILITIES_SRS)}
    desc = wfs.describe("bag:pand")
    assert desc.srs == "EPSG:28992"
    assert desc.bbox == [3.0, 50.7, 7.2, 53.6]


def test_wfs_describe_no_srs_declared_is_none_769(wfs):
    """#769: a FeatureType with no DefaultSRS gets srs=None (never a fabricated
    EPSG:4326), and an unreachable capabilities document likewise."""
    wfs._caps_cache = None
    wfs.session.routes = {"GetCapabilities": FakeResponse(content=WFS_CAPABILITIES)}
    desc = wfs.describe("roads")  # present in capabilities but no SRS declared
    assert desc.srs is None
    assert desc.bbox is None

    # Capabilities unreachable → still no fabricated SRS/bbox.
    def boom(url, params=None, timeout=None):
        raise RuntimeError("unreachable")

    wfs.session.get = boom
    desc2 = wfs.describe("roads")
    assert desc2.srs is None
    assert desc2.bbox is None


def test_ogc_describe_without_declared_crs_is_none_769(ogc):
    """#769: an OGC-API collection that declares no crs gets srs=None —
    previously hardcoded to EPSG:4326 (fabricated)."""
    col = dict(OGC_PARCELS)
    col.pop("crs")
    ogc.session.routes  # noqa: B018 - routes replaced below
    original = ogc.session.get

    def route(url, params=None, timeout=None):
        if url.endswith("/collections/parcels") and params is None:
            return FakeResponse(json_data=col)
        return original(url, params=params, timeout=timeout)

    ogc.session.get = route
    desc = ogc.describe("parcels")
    assert desc.srs is None


def test_ogc_api_adapter_contract(wogc=None, ogc=None):
    pass


def test_ogc_api_full_surface(ogc):
    verify_adapter_contract(ogc, "parcels")
    desc = ogc.describe("parcels")
    assert desc.title == "Parcels"
    assert desc.bbox == [100.0, 20.0, 120.0, 40.0]
    assert any(f["name"] == "owner" for f in desc.fields)
    res = ogc.query("parcels", QuerySpec(limit=2))
    assert res.dataset_id == "parcels"


def test_arcgis_adapter_contract(arcgis):
    verify_adapter_contract(arcgis, "0")
    desc = arcgis.describe("0")
    assert desc.title == "pipelines"
    res = arcgis.query("0", QuerySpec(limit=3))
    assert len(res.features) == 1
    qcall = next(c for c in arcgis.session.calls if c["url"].rstrip("/").endswith("/query"))
    assert qcall["params"].get("f") in ("json", "geojson")


def test_wms_adapter_contract(wms):
    verify_adapter_contract(wms, "ortho")
    res = wms.query("ortho", QuerySpec(limit=1))
    meta = res.metadata or {}
    assert "getmap_url" in meta and "LAYERS=ortho" in meta["getmap_url"]
