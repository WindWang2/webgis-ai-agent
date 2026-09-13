"""ads-v1 offline fixture layer for data-source adapters (DS0 / gap A11, ADR-0170).

``FakeSourceServer`` gives every new adapter / retrieval / fallback test the
same offline seam:

- **HTTP protocols** (OGC API Features / WFS / STAC / ArcGIS REST): canned
  responses served by a ``FakeFabricAdapter`` mounted on a real
  ``requests.Session`` — no network, no sockets, per-hop SSRF validation
  still enforced (reuses ``tests/fixtures/data_fabric/fake_server.py``).
  Install with the ``patched_safe_sessions`` fixture, which swaps
  ``make_safe_session`` in every fabric adapter module for a session backed
  by this server's routes.
- **PostGIS**: canned DB-API pool at the adapter's own seam
  (``postgis_adapter._POSTGIS_POOLS``), supporting the statements the adapter
  actually issues (``SELECT 1`` probe, ``SET LOCAL …``, canned result rows).

Canned payloads are *minimal true responses*: the fixture test round-trips
each protocol through the real adapter (probe → list_datasets → describe /
query) and asserts the canned data comes back — a payload shape that drifts
from the protocol turns the fixture suite red.

Nothing here fabricates *data*: features served are the ones the test
explicitly declared, labelled with the fake host ``ads-fixture.invalid``.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytest
import requests

from tests.fixtures.data_fabric.fake_server import make_response

FAKE_HOST = "ads-fixture.invalid"  # RFC 2606 reserved TLD — never resolvable


# ── Canned protocol payloads (minimal true responses) ───────────────────────


def _feature(id_: str, x: float = 116.4, y: float = 39.9, props: str = "name") -> Dict[str, Any]:
    return {
        "type": "Feature",
        "id": id_,
        "geometry": {"type": "Point", "coordinates": [x, y]},
        "properties": {props: f"feat-{id_}", "value": int(id_)},
    }


def geojson_feature_collection(features: Sequence[Dict[str, Any]], matched: Optional[int] = None) -> Dict[str, Any]:
    body: Dict[str, Any] = {"type": "FeatureCollection", "features": list(features)}
    if matched is not None:
        body["numberMatched"] = matched
        body["numberReturned"] = len(features)
    return body


def ogc_collections_doc(collection_ids: Sequence[str]) -> Dict[str, Any]:
    return {
        "links": [{"href": f"https://{FAKE_HOST}/ogc/collections", "rel": "self"}],
        "collections": [
            {
                "id": cid,
                "title": f"{cid} collection",
                "description": f"ads fixture collection {cid}",
                "extent": {
                    "spatial": {"bbox": [[100.0, 20.0, 130.0, 50.0]]},
                    "temporal": {"interval": [["2015-01-01T00:00:00Z", "2024-12-31T00:00:00Z"]]},
                },
            }
            for cid in collection_ids
        ],
    }


def stac_search_doc(item_ids: Sequence[str]) -> Dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "stac_version": "1.0.0",
        "context": {"returned": len(item_ids), "limit": 10, "matched": len(item_ids)},
        "features": [
            {
                "type": "Feature",
                "stac_version": "1.0.0",
                "id": iid,
                "geometry": {"type": "Polygon", "coordinates": [[[100.0, 20.0], [130.0, 20.0], [130.0, 50.0], [100.0, 50.0], [100.0, 20.0]]]},
                "properties": {"datetime": "2024-06-01T00:00:00Z", "platform": "ads-fixture"},
                "assets": {"data": {"href": f"https://{FAKE_HOST}/assets/{iid}.tif", "roles": ["data"]}},
                "links": [],
                "bbox": [100.0, 20.0, 130.0, 50.0],
            }
            for iid in item_ids
        ],
        "links": [],
    }


WFS_CAPABILITIES_XML = """<?xml version="1.0" encoding="UTF-8"?>
<wfs:WFS_Capabilities xmlns:wfs="http://www.opengis.net/wfs/2.0"
                      xmlns:ows="http://www.opengis.net/ows/1.1" version="2.0.0">
  <ows:ServiceIdentification><ows:Title>ads-fixture WFS</ows:Title></ows:ServiceIdentification>
  <wfs:FeatureTypeList>
    <wfs:FeatureType>
      <wfs:Name>ads:parcels</wfs:Name>
      <ows:Title>parcels</ows:Title>
      <ows:WGS84BoundingBox><ows:LowerCorner>100 20</ows:LowerCorner><ows:UpperCorner>130 50</ows:UpperCorner></ows:WGS84BoundingBox>
    </wfs:FeatureType>
  </wfs:FeatureTypeList>
</wfs:WFS_Capabilities>
"""

ARCGIS_LAYER_META = {
    "id": 0,
    "name": "parcels",
    "type": "Feature Layer",
    "geometryType": "esriGeometryPoint",
    "extent": {"xmin": 100.0, "ymin": 20.0, "xmax": 130.0, "ymax": 50.0, "spatialReference": {"wkid": 4326}},
    "fields": [
        {"name": "name", "type": "esriFieldTypeString", "alias": "name"},
        {"name": "value", "type": "esriFieldTypeInteger", "alias": "value"},
    ],
}

ARCGIS_SERVER_INFO = {
    "currentVersion": 11.1,
    "serviceDescription": "ads-fixture ArcGIS FeatureServer",
    "layers": [{"id": 0, "name": "parcels", "parentLayerId": -1, "subLayerIds": None}],
}


def arcgis_geojson_response(features: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """esri features (attributes + {x,y} geometry) → GeoJSON FeatureCollection."""
    geo: List[Dict[str, Any]] = []
    for f in features:
        attrs = f.get("attributes", {})
        geom = f.get("geometry") or {}
        if "coordinates" in geom:
            gj_geom = geom
        else:
            gj_geom = {"type": "Point", "coordinates": [geom.get("x"), geom.get("y")]}
        geo.append({
            "type": "Feature",
            "id": attrs.get("OBJECTID"),
            "geometry": gj_geom,
            "properties": attrs,
        })
    return {
        "type": "FeatureCollection",
        "features": geo,
        "properties": {"exceededTransferLimit": False},
    }


# ── PostGIS canned DB-API pool ───────────────────────────────────────────────


class FakePostgisCursor:
    """Minimal DB-API cursor: canned rows keyed by SQL substring match."""

    def __init__(self, canned: Sequence[Tuple[str, List[tuple]]]):
        self.canned = list(canned)
        self._pending: List[tuple] = []
        self.executed: List[str] = []

    def _rows_for(self, sql: str) -> List[tuple]:
        for needle, rows in self.canned:
            if needle in sql:
                return rows
        return []

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append(sql)
        self._pending = list(self._rows_for(sql))

    def fetchone(self) -> Optional[tuple]:
        return self._pending.pop(0) if self._pending else None

    def fetchall(self) -> List[tuple]:
        rows, self._pending = self._pending, []
        return rows

    def close(self) -> None:
        self._pending = []

    def __enter__(self) -> "FakePostgisCursor":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class FakePostgisConnection:
    def __init__(self, canned: Sequence[Tuple[str, List[tuple]]]):
        self.canned = canned
        self.closed = False
        self.committed = False

    def cursor(self) -> FakePostgisCursor:
        return FakePostgisCursor(self.canned)

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class FakePostgisPool:
    def __init__(self, canned: Sequence[Tuple[str, List[tuple]]]):
        self._canned = canned
        self.getconn_calls = 0

    def getconn(self) -> FakePostgisConnection:
        self.getconn_calls += 1
        return FakePostgisConnection(self._canned)

    def putconn(self, conn) -> None:  # noqa: ANN001 — matches real pool signature
        conn.close()


# ── FakeSourceServer ─────────────────────────────────────────────────────────


class FakeSourceServer:
    """Accumulates routes for every protocol a test needs, then serves them.

    Usage::

        server = FakeSourceServer()
        server.add_ogc_api(features=[_feature("1")])
        server.add_wfs()
        server.add_stac()
        server.add_arcgis(features=[_feature("2")])
        # HTTP adapters now build sessions backed by these routes:
        patched_safe_sessions(monkeypatch, server)
        server.add_postgis(monkeypatch, canned=[("SELECT 1", [(1,)])])
    """

    def __init__(self) -> None:
        self.routes: List[Tuple[str, Any]] = []
        self.requested_urls: List[str] = []

    # -- route plumbing ------------------------------------------------------
    def _add(self, prefix: str, handler) -> str:  # noqa: ANN001
        self.routes.append((prefix, handler))
        return prefix

    def _record(self, request: requests.PreparedRequest) -> None:
        self.requested_urls.append(request.url)

    @property
    def base_url(self) -> str:
        return f"https://{FAKE_HOST}"

    # -- protocol installers --------------------------------------------------
    def add_ogc_api(
        self,
        *,
        collection_ids: Sequence[str] = ("lake_depth",),
        features: Sequence[Dict[str, Any]] = (),
        prefix: str = "/ogc",
    ) -> str:
        """OGC API — Features: landing/collections/items (GET)."""

        def handler(request: requests.PreparedRequest) -> requests.Response:
            self._record(request)
            path = request.url
            if "/items" in path:
                return make_response(request.url, json_body=geojson_feature_collection(features, matched=len(features)))
            if re.search(r"/collections/[^/]+$", path):
                cid = path.rstrip("/").rsplit("/", 1)[-1]
                return make_response(request.url, json_body=ogc_collections_doc([cid])["collections"][0])
            return make_response(request.url, json_body=ogc_collections_doc(collection_ids))

        self._add(prefix, handler)
        return self.base_url + prefix

    def add_wfs(self, *, features: Sequence[Dict[str, Any]] = (), prefix: str = "/wfs") -> str:
        """WFS 2.0: GetCapabilities (XML) + GetFeature (GeoJSON output)."""

        def handler(request: requests.PreparedRequest) -> requests.Response:
            self._record(request)
            req = request.url.split("?")[-1].upper()
            body = request.body or b""
            if isinstance(body, bytes):
                body = body.decode("utf-8", errors="ignore")
            if "REQUEST=GETFEATURE" in req or "REQUEST=GETFEATURE" in body.upper():
                return make_response(request.url, json_body=geojson_feature_collection(features, matched=len(features)))
            return make_response(
                request.url,
                text=WFS_CAPABILITIES_XML,
                headers={"Content-Type": "text/xml; charset=utf-8"},
            )

        self._add(prefix, handler)
        return self.base_url + prefix

    def add_stac(self, *, item_ids: Sequence[str] = ("scene-001",), prefix: str = "/stac") -> str:
        """STAC API: POST /search (and GET landing)."""

        def handler(request: requests.PreparedRequest) -> requests.Response:
            self._record(request)
            if request.method == "POST":
                return make_response(request.url, json_body=stac_search_doc(item_ids))
            return make_response(
                request.url,
                json_body={"id": "ads-fixture", "stac_version": "1.0.0", "links": [{"href": f"https://{FAKE_HOST}/stac/search", "rel": "search", "method": "POST"}]},
            )

        self._add(prefix, handler)
        return self.base_url + prefix

    def add_arcgis(
        self,
        *,
        features: Sequence[Dict[str, Any]] = (),
        prefix: str = "/arcgis/rest/services/ads/FeatureServer",
    ) -> str:
        """ArcGIS REST: server info (?f=json) + layer metadata + query."""

        def esri_features() -> List[Dict[str, Any]]:
            return [
                {
                    "attributes": {"OBJECTID": int(f["id"]), "name": f["properties"]["name"], "value": f["properties"]["value"]},
                    "geometry": {"x": f["geometry"]["coordinates"][0], "y": f["geometry"]["coordinates"][1]},
                }
                for f in features
            ]

        def handler(request: requests.PreparedRequest) -> requests.Response:
            self._record(request)
            from urllib.parse import urlparse

            path = urlparse(request.url).path
            if "/query" in path:
                return make_response(request.url, json_body=arcgis_geojson_response(esri_features()))
            if re.search(r"/FeatureServer/?$", path):
                return make_response(request.url, json_body=ARCGIS_SERVER_INFO)
            return make_response(request.url, json_body=ARCGIS_LAYER_META)

        self._add(prefix, handler)
        return self.base_url + prefix

    def add_postgis(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        canned: Sequence[Tuple[str, List[tuple]]] = (("SELECT 1", [(1,)]),),
        host: str = "postgis-fake",
        database: str = "gisdb",
        username: str = "ads",
    ) -> str:
        """PostGIS: canned DB-API pool installed at the adapter's own seam."""
        from app.services.data_fabric.adapters import postgis_adapter as pga_mod

        pool = FakePostgisPool(canned)
        # Same key layout the adapter uses: user@host:port/db (default port 5432).
        key = f"{username}@{host}:5432/{database}"
        monkeypatch.setitem(pga_mod._POSTGIS_POOLS, key, pool)
        return f"postgresql://{username}:secret@{host}:5432/{database}"


@pytest.fixture
def fake_source_server() -> FakeSourceServer:
    """A fresh FakeSourceServer per test."""
    return FakeSourceServer()


@pytest.fixture
def patched_safe_sessions(monkeypatch: pytest.MonkeyPatch):
    """Factory: point every fabric adapter's ``make_safe_session`` at routes.

    Returns ``install(server)`` which (re)mounts the adapter session factory.
    Call it after the server's routes are registered.
    """

    def install(server: FakeSourceServer) -> None:
        from tests.fixtures.data_fabric.fake_server import session_with_fake

        session = session_with_fake(tuple(server.routes))

        import app.services.data_fabric.adapters as adapter_pkg
        import importlib
        import pkgutil

        for mod_info in pkgutil.iter_modules(adapter_pkg.__path__):
            mod = importlib.import_module(f"app.services.data_fabric.adapters.{mod_info.name}")
            if hasattr(mod, "make_safe_session"):
                monkeypatch.setattr(mod, "make_safe_session", lambda *a, **k: session)
        # connection_manager hosts GenericDataSourceAdapter's session factory too.
        from app.services.data_fabric import connection_manager as cm

        if hasattr(cm, "make_safe_session"):
            monkeypatch.setattr(cm, "make_safe_session", lambda *a, **k: session)

    return install
