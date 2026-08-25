"""
STAC (SpatioTemporal Asset Catalog) Data Source Adapter
Generalized STAC adapter supporting multi-collection discovery, item search,
spatial/temporal pushdown filtering, and lazy streaming.
"""
import time
import logging
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin
from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
from app.services.data_fabric.errors import SOURCE_UNREACHABLE, classify_http_status
from app.services.data_fabric.security import DataFabricSecurity, make_safe_session
from app.schemas.data_fabric_schema import (
    DatasetDescriptor,
    QuerySpec,
    QueryResult,
    DataFabricHealth,
    ConnectionProfile,
)

logger = logging.getLogger(__name__)

MAX_PREVIEW_LIMIT = 50
MAX_QUERY_LIMIT = 1000

SYNTHETIC_STAC_FIXTURES: Dict[str, Dict[str, Any]] = {
    "landsat-8-c2-l2": {
        "id": "landsat-8-c2-l2",
        "title": "Landsat 8 Collection 2 Level-2 Surface Reflectance",
        "description": "Global multispectral optical satellite imagery from USGS Landsat 8 OLI/TIRS.",
        "license": "proprietary",
        "extent": {
            "spatial": {"bbox": [[-180.0, -90.0, 180.0, 90.0]]},
            "temporal": {"interval": [["2013-02-11T00:00:00Z", None]]},
        },
        "item_count": 125000,
        "assets": ["SR_B1", "SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6", "SR_B7", "ST_B10"],
        "items": [
            {
                "type": "Feature",
                "stac_version": "1.0.0",
                "id": "LC08_L2SP_123032_20230515_20230522_02_T1",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[116.2, 39.8], [116.8, 39.8], [116.8, 40.3], [116.2, 40.3], [116.2, 39.8]]],
                },
                "bbox": [116.2, 39.8, 116.8, 40.3],
                "properties": {
                    "datetime": "2023-05-15T02:45:00Z",
                    "platform": "landsat-8",
                    "instruments": ["oli", "tirs"],
                    "eo:cloud_cover": 3.42,
                    "gsd": 30,
                },
                "assets": {
                    "SR_B4": {"href": "https://example-stac.org/landsat/SR_B4.tif", "type": "image/tiff; application=geotiff"},
                    "SR_B5": {"href": "https://example-stac.org/landsat/SR_B5.tif", "type": "image/tiff; application=geotiff"},
                },
                "collection": "landsat-8-c2-l2",
            },
            {
                "type": "Feature",
                "stac_version": "1.0.0",
                "id": "LC08_L2SP_015033_20230610_20230618_02_T1",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-74.2, 40.5], [-73.7, 40.5], [-73.7, 40.9], [-74.2, 40.9], [-74.2, 40.5]]],
                },
                "bbox": [-74.2, 40.5, -73.7, 40.9],
                "properties": {
                    "datetime": "2023-06-10T15:30:00Z",
                    "platform": "landsat-8",
                    "instruments": ["oli", "tirs"],
                    "eo:cloud_cover": 8.12,
                    "gsd": 30,
                },
                "assets": {
                    "SR_B4": {"href": "https://example-stac.org/landsat/SR_B4_ny.tif", "type": "image/tiff; application=geotiff"},
                },
                "collection": "landsat-8-c2-l2",
            },
        ],
    },
    "cop-dem-30m": {
        "id": "cop-dem-30m",
        "title": "Copernicus DEM GLO-30 Digital Elevation Model",
        "description": "Global 30-meter Digital Elevation Model (DEM) derived from TanDEM-X.",
        "license": "free-to-use",
        "extent": {
            "spatial": {"bbox": [[-180.0, -84.0, 180.0, 84.0]]},
            "temporal": {"interval": [["2011-01-01T00:00:00Z", "2015-12-31T23:59:59Z"]]},
        },
        "item_count": 26000,
        "assets": ["data", "coverage", "xml"],
        "items": [
            {
                "type": "Feature",
                "stac_version": "1.0.0",
                "id": "Copernicus_DSM_COG_10_N39_00_E116_00_DEM",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[116.0, 39.0], [117.0, 39.0], [117.0, 40.0], [116.0, 40.0], [116.0, 39.0]]],
                },
                "bbox": [116.0, 39.0, 117.0, 40.0],
                "properties": {
                    "datetime": "2021-04-22T00:00:00Z",
                    "platform": "tandem-x",
                    "gsd": 30,
                },
                "assets": {
                    "data": {"href": "https://example-stac.org/dem/N39E116_DEM.tif", "type": "image/tiff; application=geotiff; profile=cloud-optimized"},
                },
                "collection": "cop-dem-30m",
            }
        ],
    },
}


class STACAdapter(GeospatialDataSourceAdapter):
    """
    STAC Data Fabric Adapter:
    Generalizes STAC API / Catalog data access beyond Sentinel to any STAC 1.0.0 compliance source.
    Implements STAC search, pushdown bbox/datetime filtering, and fallback fixtures.
    """

    def __init__(self, connection_profile: ConnectionProfile):
        super().__init__(connection_profile)
        self.endpoint = (self.profile.endpoint or "").strip()
        self.allow_private = getattr(self.profile, "allow_private", False)
        # SSRF-safe session: every request (incl. redirects) is revalidated.
        self.session = make_safe_session(allow_private=self.allow_private)

    def probe(self) -> bool:
        """Reachability probe for STAC endpoint."""
        if not self.endpoint:
            return True  # Fallback synthetic mode is reachable
        try:
            safe_url = DataFabricSecurity.validate_url(self.endpoint, allow_private=self.allow_private)
            resp = self.session.get(safe_url, timeout=5, headers={"Accept": "application/json"})
            if resp.status_code == 200:
                data = resp.json()
                return "stac_version" in data or "collections" in data or "links" in data
            return False
        except Exception as e:
            logger.debug(f"STAC probe failed for {self.endpoint}: {e}")
            return False

    def capabilities(self) -> List[str]:
        return [
            "pushdown_bbox",
            "pushdown_datetime",
            "stac_search",
            "raster_assets",
            "vector_features",
            "collections",
            "collection_discovery",
        ]

    def list_datasets(self) -> List[Dict[str, Any]]:
        """List available STAC collections.

        Truthfulness contract (#430): when a real endpoint is configured, any
        discovery failure — unreachable, non-200, unparseable body, or a root
        catalog without child links — returns an EMPTY list (mirroring the
        ogc/wfs/arcgis adapters and this adapter's own query() path). The
        synthetic fixtures are served ONLY on the explicit no-endpoint demo
        path, and every entry is labeled ``source="synthetic-demo"`` so no
        caller can mistake demo data for the remote's real datasets.
        """
        if not self.endpoint:
            return self._list_synthetic_collections()

        try:
            safe_url = DataFabricSecurity.validate_url(self.endpoint, allow_private=self.allow_private)
            collections_url = urljoin(safe_url + "/", "collections")
            resp = self.session.get(collections_url, timeout=8)
            if resp.status_code == 200:
                body = resp.json()
                colls = body.get("collections", [])
                result = []
                for c in colls:
                    cid = c.get("id")
                    if cid:
                        result.append({
                            "id": cid,
                            "title": c.get("title", cid),
                            "description": c.get("description", ""),
                            "license": c.get("license", "unknown"),
                            "source_type": "stac",
                        })
                if result:
                    return result

            # Try parsing root catalog links
            root_resp = self.session.get(safe_url, timeout=8)
            if root_resp.status_code == 200:
                root_json = root_resp.json()
                links = root_json.get("links", [])
                child_ids = [
                    link["href"].split("/")[-1] for link in links if link.get("rel") in ("child", "collection")
                ]
                if child_ids:
                    return [
                        {"id": cid, "title": cid, "source_type": "stac"}
                        for cid in child_ids
                    ]

            # Real endpoint configured but discovery yielded nothing
            # truthful — empty list, NO synthetic fallback (#430).
            logger.warning(
                f"STAC list_datasets for '{self.endpoint}' returned no collections; "
                f"registering an empty catalog"
            )
            return []
        except Exception as e:
            logger.warning(f"STAC list_datasets failed for '{self.endpoint}': {e}")
            return []

    def _list_synthetic_collections(self) -> List[Dict[str, Any]]:
        """Explicit no-endpoint DEMO mode — labeled as such on every entry."""
        return [
            {
                "id": coll_id,
                "title": fixture["title"],
                "description": fixture["description"],
                "license": fixture["license"],
                "source_type": "stac",
                # Explicit label so callers never mistake demo data for
                # remote data (same contract as query()'s demo path).
                "source": "synthetic-demo",
            }
            for coll_id, fixture in SYNTHETIC_STAC_FIXTURES.items()
        ]

    def describe(self, dataset_id: str) -> DatasetDescriptor:
        """Fetch STAC collection descriptor metadata.

        Truthfulness contract (#430): the synthetic fixtures are used ONLY in
        explicit no-endpoint demo mode (and then labeled in metadata). With a
        real endpoint configured, a failed descriptor fetch returns an honest
        stub carrying a typed error and NO fabricated feature count — never
        fixture metadata presented as the remote's data.
        """
        if not self.endpoint:
            fixture = SYNTHETIC_STAC_FIXTURES.get(dataset_id, SYNTHETIC_STAC_FIXTURES["landsat-8-c2-l2"])
            spatial_bbox = fixture["extent"]["spatial"]["bbox"][0]
            return DatasetDescriptor(
                id=dataset_id,
                title=fixture["title"],
                description=fixture["description"],
                source_type="stac",
                geometry_type="Polygon",
                srs="EPSG:4326",
                bbox=spatial_bbox,
                feature_count=fixture.get("item_count", len(fixture.get("items", []))),
                fields=[{"name": a, "type": "asset"} for a in fixture.get("assets", [])],
                metadata={
                    "extent": fixture["extent"],
                    "license": fixture["license"],
                    "item_count": fixture.get("item_count"),
                    # Explicit label: this is demo data, not remote data.
                    "source": "synthetic-demo",
                },
            )

        error_type: Optional[str] = None
        error_message: Optional[str] = None
        try:
            safe_url = DataFabricSecurity.validate_url(self.endpoint, allow_private=self.allow_private)
            coll_url = urljoin(safe_url + "/", f"collections/{dataset_id}")
            resp = self.session.get(coll_url, timeout=8)
            if resp.status_code == 200:
                c = resp.json()
                bbox = c.get("extent", {}).get("spatial", {}).get("bbox", [[-180.0, -90.0, 180.0, 90.0]])[0]
                summaries = c.get("summaries", {})
                fields = [{"name": k, "type": "property"} for k in summaries.keys()]
                # Feature count: only what the source actually reports
                # (`item_count`, used by several STAC implementations).
                # Never fabricate a count the endpoint did not provide.
                raw_count = c.get("item_count")
                feature_count = int(raw_count) if isinstance(raw_count, (int, float)) else None
                return DatasetDescriptor(
                    id=dataset_id,
                    title=c.get("title", dataset_id),
                    description=c.get("description", f"STAC Collection {dataset_id}"),
                    source_type="stac",
                    geometry_type="Polygon",
                    srs="EPSG:4326",
                    bbox=bbox,
                    feature_count=feature_count,
                    fields=fields,
                    metadata={"extent": c.get("extent"), "license": c.get("license")},
                )
            error_type = classify_http_status(resp.status_code)
            error_message = f"STAC collection fetch returned HTTP {resp.status_code}"
            logger.warning(f"STAC describe for '{dataset_id}' failed: {error_message}")
        except Exception as e:
            error_type = SOURCE_UNREACHABLE
            error_message = f"STAC collection fetch failed: {e}"
            logger.warning(f"STAC describe error for '{dataset_id}': {e}")

        # Honest failure stub: no fixture fallback, no fabricated counts
        # (#430). The typed error lets callers distinguish an unreachable
        # source from an empty one.
        return DatasetDescriptor(
            id=dataset_id,
            title=dataset_id,
            description=f"STAC Collection {dataset_id} (descriptor unavailable)",
            source_type="stac",
            feature_count=None,
            fields=[],
            metadata={"error_type": error_type, "error": error_message},
        )

    def preview(self, dataset_id: str, limit: int = 10) -> Dict[str, Any]:
        """Fetch sample item GeoJSON features for preview."""
        bounded_limit = max(1, min(limit, MAX_PREVIEW_LIMIT))
        q_spec = QuerySpec(limit=bounded_limit)
        q_res = self.query(dataset_id, q_spec)
        return {
            "schema": {"collection": dataset_id, "format": "stac_item_geojson"},
            "properties": q_res.features[0]["properties"] if q_res.features else {},
            "features": q_res.features[:bounded_limit],
            "bbox": [-180.0, -90.0, 180.0, 90.0],
        }

    def query(self, dataset_id: str, query_spec: QuerySpec) -> QueryResult:
        """Execute STAC search with pushdown bbox and temporal filtering.

        Truthfulness contract: when a real endpoint is configured, a query
        failure (non-200 or exception) returns a structured ``QueryResult`` with
        ``features=[]`` and a typed ``error_type`` in metadata — it does NOT fall
        back to synthetic fixtures as if they were the remote's real data. The
        synthetic fixtures are used only in explicit no-endpoint demo mode, and
        are labeled ``source="synthetic-demo"`` in metadata.
        """
        start_time = time.time()
        bounded_limit = max(1, min(query_spec.limit or 50, MAX_QUERY_LIMIT))

        if self.endpoint:
            try:
                safe_url = DataFabricSecurity.validate_url(self.endpoint, allow_private=self.allow_private)
                search_url = urljoin(safe_url + "/", "search")
                payload: Dict[str, Any] = {
                    "collections": [dataset_id],
                    "limit": bounded_limit,
                }
                if query_spec.bbox:
                    payload["bbox"] = query_spec.bbox
                if query_spec.datetime_range and len(query_spec.datetime_range) == 2:
                    payload["datetime"] = f"{query_spec.datetime_range[0]}/{query_spec.datetime_range[1]}"

                resp = self.session.post(search_url, json=payload, timeout=10)
                if resp.status_code != 200:
                    # Real source responded with an error — surface it, do not
                    # masquerade synthetic fixtures as the remote's data.
                    err_type = classify_http_status(resp.status_code)
                    exec_time = round((time.time() - start_time) * 1000, 2)
                    return QueryResult(
                        dataset_id=dataset_id,
                        features=[],
                        total_count=0,
                        returned_count=0,
                        metadata={
                            "exec_time_ms": exec_time,
                            "source": "remote",
                            "error_type": err_type,
                            "error": f"STAC search returned HTTP {resp.status_code}",
                            "http_status": resp.status_code,
                        },
                    )
                data = resp.json()
                features = data.get("features", [])
                exec_time = round((time.time() - start_time) * 1000, 2)
                return QueryResult(
                    dataset_id=dataset_id,
                    features=features[:bounded_limit],
                    data={"type": "FeatureCollection", "features": features[:bounded_limit]},
                    total_count=data.get("numberMatched", len(features)),
                    returned_count=len(features[:bounded_limit]),
                    metadata={"exec_time_ms": exec_time, "pushdown_bbox": bool(query_spec.bbox), "source": "remote"},
                )
            except Exception as e:
                # Endpoint configured but unreachable / bad response — fail
                # truthfully with empty features + typed error. No synthetic data.
                logger.warning(f"STAC remote query failed for '{dataset_id}': {e}")
                exec_time = round((time.time() - start_time) * 1000, 2)
                return QueryResult(
                    dataset_id=dataset_id,
                    features=[],
                    total_count=0,
                    returned_count=0,
                    metadata={
                        "exec_time_ms": exec_time,
                        "source": "remote",
                        "error_type": "SOURCE_UNREACHABLE",
                        "error": f"STAC search failed: {e}",
                    },
                )

        # No endpoint configured → explicit synthetic DEMO mode (labeled).
        # Synthetic fallback filtering
        fixture = SYNTHETIC_STAC_FIXTURES.get(dataset_id, SYNTHETIC_STAC_FIXTURES["landsat-8-c2-l2"])
        items = list(fixture.get("items", []))

        # Spatial filter pushdown (bbox intersection)
        if query_spec.bbox and len(query_spec.bbox) == 4:
            q_minx, q_miny, q_maxx, q_maxy = query_spec.bbox
            filtered_items = []
            for item in items:
                ibox = item.get("bbox")
                if ibox and len(ibox) == 4:
                    i_minx, i_miny, i_maxx, i_maxy = ibox
                    # Check box overlap
                    if not (i_maxx < q_minx or i_minx > q_maxx or i_maxy < q_miny or i_miny > q_maxy):
                        filtered_items.append(item)
                else:
                    filtered_items.append(item)
            items = filtered_items

        sliced = items[:bounded_limit]
        exec_time = round((time.time() - start_time) * 1000, 2)
        return QueryResult(
            dataset_id=dataset_id,
            features=sliced,
            data={"type": "FeatureCollection", "features": sliced},
            total_count=len(items),
            returned_count=len(sliced),
            metadata={
                "exec_time_ms": exec_time,
                "pushdown_bbox": bool(query_spec.bbox),
                # Explicit label so callers never mistake demo data for remote data.
                "source": "synthetic-demo",
            },
        )

    def health(self) -> DataFabricHealth:
        start_time = time.time()
        is_ok = self.probe()
        latency = round((time.time() - start_time) * 1000, 2)
        if is_ok:
            return DataFabricHealth(
                status="healthy",
                adapter="stac",
                message="STAC catalog/endpoint responsive",
                latency_ms=latency,
                details={"endpoint": self.endpoint or "synthetic_fixture_mode"},
            )
        return DataFabricHealth(
            status="unreachable",
            adapter="stac",
            message=f"Unable to reach STAC endpoint: {self.endpoint}",
            latency_ms=latency,
            details={"endpoint": self.endpoint},
        )
