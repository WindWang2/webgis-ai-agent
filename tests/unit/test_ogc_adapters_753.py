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


class FakeResponse:
    def __init__(self, *, json_data=None, content=b"", text="", status_code=200,
                 headers=None):
        self._json = json_data
        self.content = content if content else text.encode()
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


class FakeSession:
    """Records requests; routes by substring on the URL."""

    def __init__(self):
        self.calls = []
        self.headers = {}
        self.routes = {}

    def get(self, url, params=None, timeout=None):
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

    def route(url, params=None, timeout=None):
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

    def route(url, params=None, timeout=None):
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
    assert call["params"]["BBOX"] == "116.0,39.5,117.0,40.5,EPSG:4326"
    # absurd limit clamps to MAX_QUERY_LIMIT
    wfs.query("roads", QuerySpec(limit=999999))
    call2 = wfs.session.calls[-1]
    assert int(call2["params"]["COUNT"]) == 10000


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
