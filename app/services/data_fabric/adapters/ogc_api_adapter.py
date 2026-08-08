"""
OGC API - Features Data Source Adapter
"""
import time
import logging
import requests
from typing import List, Dict, Any, Optional
from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
from app.services.data_fabric.security import DataFabricSecurity
from app.schemas.data_fabric_schema import (
    DatasetDescriptor,
    QuerySpec,
    QueryResult,
    DataFabricHealth,
    ConnectionProfile,
)

logger = logging.getLogger(__name__)

MAX_PREVIEW_LIMIT = 100
MAX_QUERY_LIMIT = 10000


class OGCAPIAdapter(GeospatialDataSourceAdapter):
    """
    Concrete Data Fabric adapter for OGC API - Features endpoints.
    Provides collection discovery, GeoJSON item querying with BBOX pushdown, pagination,
    bounded payload enforcement, and SSRF security validation.
    """

    def __init__(self, connection_profile: ConnectionProfile):
        super().__init__(connection_profile)
        self.raw_url = self.profile.url or ""
        self.url = DataFabricSecurity.validate_url(self.raw_url, allow_private=self.profile.allow_private) if self.raw_url else ""
        self.options = self.profile.options or {}
        self.session = requests.Session()
        if "headers" in self.options:
            self.session.headers.update(self.options["headers"])

    def probe(self) -> bool:
        """Lightweight reachability probe for OGC API landing page or collections."""
        if not self.url:
            return False
        try:
            target_url = self.url.rstrip("/") + "/collections"
            resp = self.session.get(target_url, timeout=5)
            return resp.status_code in (200, 206)
        except Exception as e:
            logger.debug(f"OGC API probe failed for {self.url}: {e}")
            return False

    def capabilities(self) -> List[str]:
        """List OGC API adapter capability flags."""
        return [
            "pushdown_bbox",
            "pushdown_filter",
            "vector_features",
            "ogc_api_features",
            "pagination",
        ]

    def list_datasets(self) -> List[Dict[str, Any]]:
        """Discover available collections in OGC API Features endpoint."""
        if not self.url:
            return []
        try:
            target_url = self.url.rstrip("/") + "/collections"
            resp = self.session.get(target_url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            collections = data.get("collections", [])

            datasets = []
            for col in collections:
                col_id = col.get("id", "")
                title = col.get("title", col_id)
                datasets.append({
                    "id": col_id,
                    "title": title,
                    "description": col.get("description", ""),
                    "item_type": col.get("itemType", "feature"),
                    "source_type": "ogc_api",
                    "extent": col.get("extent", {}),
                })
            return datasets
        except Exception as e:
            logger.warning(f"OGC API list_datasets failed for {self.url}: {e}")
            return []

    def describe(self, dataset_id: str) -> DatasetDescriptor:
        """Fetch full DatasetDescriptor for a specific OGC API collection."""
        if not self.url:
            raise ValueError("OGC API endpoint URL is missing in connection profile")

        base = self.url.rstrip("/")
        col_url = f"{base}/collections/{dataset_id}"
        
        try:
            resp = self.session.get(col_url, timeout=10)
            resp.raise_for_status()
            col_data = resp.json()

            # Parse bounding box if present
            spatial_bbox = None
            try:
                spatial_bbox = col_data.get("extent", {}).get("spatial", {}).get("bbox", [None])[0]
            except Exception:
                pass

            # Query queryables for field definitions if supported
            fields = []
            try:
                q_url = f"{col_url}/queryables"
                q_resp = self.session.get(q_url, timeout=5)
                if q_resp.status_code == 200:
                    q_data = q_resp.json()
                    props = q_data.get("properties", {})
                    for field_name, field_def in props.items():
                        fields.append({
                            "name": field_name,
                            "type": field_def.get("type", "string"),
                            "description": field_def.get("title", ""),
                        })
            except Exception:
                pass

            return DatasetDescriptor(
                id=dataset_id,
                title=col_data.get("title", dataset_id),
                description=col_data.get("description", f"OGC API Collection {dataset_id}"),
                source_type="ogc_api",
                geometry_type=col_data.get("itemType", "Feature"),
                srs=col_data.get("crs", ["EPSG:4326"])[0] if col_data.get("crs") else "EPSG:4326",
                bbox=spatial_bbox or [-180.0, -90.0, 180.0, 90.0],
                feature_count=None,
                fields=fields,
                metadata={"collection_url": col_url, "links": col_data.get("links", [])},
            )
        except Exception as e:
            logger.warning(f"OGC API describe failed for collection '{dataset_id}': {e}")
            return DatasetDescriptor(
                id=dataset_id,
                title=dataset_id,
                description=f"OGC API Collection ({e})",
                source_type="ogc_api",
                geometry_type="Feature",
                srs="EPSG:4326",
                bbox=[-180.0, -90.0, 180.0, 90.0],
                feature_count=0,
                fields=[],
                metadata={"error": str(e)},
            )

    def preview(self, dataset_id: str, limit: int = 10) -> Dict[str, Any]:
        """Fetch bounded sample GeoJSON preview from OGC API items."""
        bounded_limit = max(1, min(limit, MAX_PREVIEW_LIMIT))
        if not self.url:
            return {"schema": {}, "properties": {}, "features": [], "bbox": [-180.0, -90.0, 180.0, 90.0]}

        items_url = f"{self.url.rstrip('/')}/collections/{dataset_id}/items"
        params = {"limit": bounded_limit}

        try:
            resp = self.session.get(items_url, params=params, timeout=10)
            resp.raise_for_status()
            geojson = resp.json()
            features = geojson.get("features", [])

            sample_props = features[0].get("properties", {}) if features else {}
            fields = [{"name": k, "type": type(v).__name__} for k, v in sample_props.items()]

            return {
                "schema": {"collection": dataset_id, "fields": fields},
                "properties": sample_props,
                "features": features,
                "bbox": geojson.get("bbox") or [-180.0, -90.0, 180.0, 90.0],
            }
        except Exception as e:
            logger.warning(f"OGC API preview error for '{dataset_id}': {e}")
            return {
                "schema": {"collection": dataset_id, "error": str(e)},
                "properties": {},
                "features": [],
                "bbox": [-180.0, -90.0, 180.0, 90.0],
            }

    def query(self, dataset_id: str, query_spec: QuerySpec) -> QueryResult:
        """Execute pushdown query against OGC API items endpoint."""
        bounded_limit = max(1, min(query_spec.limit or 100, MAX_QUERY_LIMIT))
        bounded_offset = max(0, query_spec.offset or 0)
        start_time = time.time()

        if not self.url:
            return QueryResult(
                dataset_id=dataset_id,
                features=[],
                total_count=0,
                schema_info={"error": "Missing URL"},
                metadata={"error_hint": "OGC API adapter unconfigured (missing URL)"},
            )

        items_url = f"{self.url.rstrip('/')}/collections/{dataset_id}/items"
        params: Dict[str, Any] = {
            "limit": bounded_limit,
            "offset": bounded_offset,
        }

        if query_spec.bbox and len(query_spec.bbox) == 4:
            params["bbox"] = ",".join(str(b) for b in query_spec.bbox)

        where_text = getattr(query_spec, "where", None) or getattr(query_spec, "filter_expr", None) or getattr(query_spec, "filter", None)
        if where_text:
            params["filter"] = where_text

        try:
            resp = self.session.get(items_url, params=params, timeout=15)
            resp.raise_for_status()
            geojson = resp.json()

            features = geojson.get("features", [])
            matched = geojson.get("numberMatched") or len(features)
            exec_time = round((time.time() - start_time) * 1000, 2)

            return QueryResult(
                dataset_id=dataset_id,
                features=features,
                total_count=matched,
                schema_info={"returned": len(features)},
                metadata={
                    "exec_time_ms": exec_time,
                    "pushdown_bbox": bool(query_spec.bbox),
                    "url": resp.url,
                },
            )
        except Exception as e:
            exec_time = round((time.time() - start_time) * 1000, 2)
            logger.warning(f"OGC API query error for '{dataset_id}': {e}")
            return QueryResult(
                dataset_id=dataset_id,
                features=[],
                total_count=0,
                schema_info={"error": str(e)},
                metadata={
                    "exec_time_ms": exec_time,
                    "error_hint": (
                        f"OGC API Features query error: {e}. "
                        "Hint: Check if collection exists, endpoint complies with OGC API Features standard, and network is accessible."
                    ),
                },
            )

    def health(self) -> DataFabricHealth:
        """Diagnostic health check for OGC API endpoint."""
        start_time = time.time()
        if not self.url:
            return DataFabricHealth(
                status="unreachable",
                message="OGC API URL missing",
                details={"hint": "Specify valid HTTP/HTTPS URL in connection profile."},
            )

        try:
            target_url = self.url.rstrip("/") + "/conformance"
            resp = self.session.get(target_url, timeout=5)
            latency = round((time.time() - start_time) * 1000, 2)
            if resp.status_code == 200:
                conforms = resp.json().get("conformsTo", [])
                return DataFabricHealth(
                    status="healthy",
                    message="OGC API Features service online and responsive",
                    details={"conformsTo": conforms[:5]},
                    latency_ms=latency,
                )
            else:
                return DataFabricHealth(
                    status="degraded",
                    message=f"OGC API responded with HTTP status {resp.status_code}",
                    details={"status_code": resp.status_code},
                    latency_ms=latency,
                )
        except Exception as e:
            latency = round((time.time() - start_time) * 1000, 2)
            return DataFabricHealth(
                status="unreachable",
                message=f"OGC API health check failed: {e}",
                details={"hint": f"Unable to reach {self.url}. Verify host status and firewall rules."},
                latency_ms=latency,
            )
