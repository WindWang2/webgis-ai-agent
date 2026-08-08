"""
ArcGIS REST Feature/Map Service Data Source Adapter
"""
import time
import logging
import requests
from typing import List, Dict, Any
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


class ArcGISAdapter(GeospatialDataSourceAdapter):
    """
    Concrete Data Fabric adapter for ArcGIS REST Services (FeatureServer / MapServer).
    Queries layer metadata, supports GeoJSON feature querying with BBOX pushdown and pagination.
    """

    def __init__(self, connection_profile: ConnectionProfile):
        super().__init__(connection_profile)
        self.raw_url = self.profile.url or ""
        self.url = (
            DataFabricSecurity.validate_url(self.raw_url, allow_private=self.profile.allow_private)
            if self.raw_url
            else ""
        )
        self.options = self.profile.options or {}
        self.session = requests.Session()

    def probe(self) -> bool:
        """Lightweight reachability probe for ArcGIS REST endpoint."""
        if not self.url:
            return False
        try:
            resp = self.session.get(self.url, params={"f": "json"}, timeout=5)
            return resp.status_code == 200 and "currentVersion" in resp.json()
        except Exception as e:
            logger.debug(f"ArcGIS probe failed for {self.url}: {e}")
            return False

    def capabilities(self) -> List[str]:
        """List ArcGIS REST adapter capabilities."""
        return [
            "pushdown_bbox",
            "pushdown_filter",
            "vector_features",
            "arcgis_rest",
        ]

    def list_datasets(self) -> List[Dict[str, Any]]:
        """Discover available layers in ArcGIS FeatureServer/MapServer."""
        if not self.url:
            return []
        try:
            resp = self.session.get(self.url, params={"f": "json"}, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            layers = data.get("layers", [])
            datasets = []
            for lyr in layers:
                lyr_id = str(lyr.get("id", ""))
                datasets.append({
                    "id": lyr_id,
                    "title": lyr.get("name", lyr_id),
                    "geometry_type": lyr.get("geometryType", "esriGeometryPolygon"),
                    "source_type": "arcgis",
                })
            return datasets
        except Exception as e:
            logger.warning(f"ArcGIS list_datasets error for {self.url}: {e}")
            return []

    def describe(self, dataset_id: str) -> DatasetDescriptor:
        """Fetch DatasetDescriptor for an ArcGIS layer."""
        if not self.url:
            raise ValueError("ArcGIS REST endpoint URL missing")

        layer_url = f"{self.url.rstrip('/')}/{dataset_id}" if dataset_id else self.url
        try:
            resp = self.session.get(layer_url, params={"f": "json"}, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            fields = [
                {"name": f.get("name"), "type": f.get("type"), "alias": f.get("alias")}
                for f in data.get("fields", [])
            ]

            return DatasetDescriptor(
                id=dataset_id,
                title=data.get("name", dataset_id),
                description=data.get("description", f"ArcGIS REST Layer {dataset_id}"),
                source_type="arcgis",
                geometry_type=data.get("geometryType", "Polygon"),
                srs="EPSG:4326",
                bbox=[-180.0, -90.0, 180.0, 90.0],
                feature_count=None,
                fields=fields,
                metadata={"layer_url": layer_url},
            )
        except Exception as e:
            logger.warning(f"ArcGIS describe error for '{dataset_id}': {e}")
            return DatasetDescriptor(
                id=dataset_id,
                title=dataset_id,
                description=f"ArcGIS Layer ({e})",
                source_type="arcgis",
                geometry_type="Feature",
                srs="EPSG:4326",
                bbox=[-180.0, -90.0, 180.0, 90.0],
                feature_count=0,
                fields=[],
                metadata={"error": str(e)},
            )

    def preview(self, dataset_id: str, limit: int = 10) -> Dict[str, Any]:
        """Fetch bounded GeoJSON sample feature preview."""
        bounded_limit = max(1, min(limit, MAX_PREVIEW_LIMIT))
        q_res = self.query(dataset_id, QuerySpec(limit=bounded_limit))
        return {
            "schema": {"layer": dataset_id},
            "properties": q_res.features[0].get("properties", {}) if q_res.features else {},
            "features": q_res.features,
            "bbox": [-180.0, -90.0, 180.0, 90.0],
        }

    def query(self, dataset_id: str, query_spec: QuerySpec) -> QueryResult:
        """Execute pushdown query on ArcGIS REST /query endpoint."""
        bounded_limit = max(1, min(query_spec.limit or 100, MAX_QUERY_LIMIT))
        bounded_offset = max(0, query_spec.offset or 0)
        start_time = time.time()

        if not self.url:
            return QueryResult(
                dataset_id=dataset_id,
                features=[],
                total_count=0,
                schema_info={"error": "Missing URL"},
                metadata={"error_hint": "ArcGIS REST adapter unconfigured (missing URL)"},
            )

        query_url = f"{self.url.rstrip('/')}/{dataset_id}/query" if dataset_id else f"{self.url.rstrip('/')}/query"
        params: Dict[str, Any] = {
            "where": query_spec.where or "1=1",
            "outFields": "*",
            "resultRecordCount": bounded_limit,
            "resultOffset": bounded_offset,
            "f": "geojson",
            "outSR": "4326",
        }

        if query_spec.bbox and len(query_spec.bbox) == 4:
            minx, miny, maxx, maxy = query_spec.bbox
            params["geometry"] = f"{minx},{miny},{maxx},{maxy}"
            params["geometryType"] = "esriGeometryEnvelope"
            params["inSR"] = "4326"
            params["spatialRel"] = "esriSpatialRelIntersects"

        try:
            resp = self.session.get(query_url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            features = data.get("features", [])
            exec_time = round((time.time() - start_time) * 1000, 2)

            return QueryResult(
                dataset_id=dataset_id,
                features=features,
                total_count=len(features),
                schema_info={"returned": len(features)},
                metadata={
                    "exec_time_ms": exec_time,
                    "pushdown_bbox": bool(query_spec.bbox),
                },
            )
        except Exception as e:
            exec_time = round((time.time() - start_time) * 1000, 2)
            logger.warning(f"ArcGIS query error for '{dataset_id}': {e}")
            return QueryResult(
                dataset_id=dataset_id,
                features=[],
                total_count=0,
                schema_info={"error": str(e)},
                metadata={
                    "exec_time_ms": exec_time,
                    "error_hint": f"ArcGIS query error: {e}",
                },
            )

    def health(self) -> DataFabricHealth:
        """Diagnostic health check for ArcGIS REST endpoint."""
        start_time = time.time()
        try:
            ok = self.probe()
            latency = round((time.time() - start_time) * 1000, 2)
            if ok:
                return DataFabricHealth(
                    status="healthy",
                    message="ArcGIS REST service responsive",
                    latency_ms=latency,
                )
            return DataFabricHealth(
                status="unreachable",
                message="ArcGIS probe returned failure",
                latency_ms=latency,
            )
        except Exception as e:
            latency = round((time.time() - start_time) * 1000, 2)
            return DataFabricHealth(
                status="unreachable",
                message=f"ArcGIS health check failed: {e}",
                latency_ms=latency,
            )
