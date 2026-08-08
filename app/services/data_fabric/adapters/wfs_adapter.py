"""
WFS (Web Feature Service) Data Source Adapter
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


class WFSAdapter(GeospatialDataSourceAdapter):
    """
    Concrete Data Fabric adapter for OGC WFS (Web Feature Service) endpoints.
    Parses GetCapabilities XML safely, extracts feature types, supports bounded GetFeature queries with BBOX.
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
        if "headers" in self.options:
            self.session.headers.update(self.options["headers"])

    def probe(self) -> bool:
        """Lightweight WFS GetCapabilities probe."""
        if not self.url:
            return False
        try:
            params = {
                "SERVICE": "WFS",
                "REQUEST": "GetCapabilities",
                "VERSION": self.options.get("version", "2.0.0"),
            }
            resp = self.session.get(self.url, params=params, timeout=5)
            return resp.status_code in (200, 206)
        except Exception as e:
            logger.debug(f"WFS probe failed for {self.url}: {e}")
            return False

    def capabilities(self) -> List[str]:
        """List WFS adapter capabilities."""
        return [
            "pushdown_bbox",
            "vector_features",
            "wfs",
            "ogc_standard",
        ]

    def list_datasets(self) -> List[Dict[str, Any]]:
        """Discover available FeatureTypes from WFS GetCapabilities."""
        if not self.url:
            return []
        try:
            params = {
                "SERVICE": "WFS",
                "REQUEST": "GetCapabilities",
                "VERSION": self.options.get("version", "2.0.0"),
            }
            resp = self.session.get(self.url, params=params, timeout=10)
            resp.raise_for_status()

            tree = DataFabricSecurity.parse_safe_xml(resp.content)
            datasets = []

            # Traverse FeatureType tags across WFS 1.0, 1.1, 2.0 XML namespaces
            for elem in tree.iter():
                tag_name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                if tag_name == "FeatureType":
                    ft_name = ""
                    ft_title = ""
                    ft_abstract = ""
                    for child in elem:
                        child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                        if child_tag == "Name":
                            ft_name = (child.text or "").strip()
                        elif child_tag == "Title":
                            ft_title = (child.text or "").strip()
                        elif child_tag == "Abstract":
                            ft_abstract = (child.text or "").strip()

                    if ft_name:
                        datasets.append({
                            "id": ft_name,
                            "title": ft_title or ft_name,
                            "description": ft_abstract,
                            "source_type": "wfs",
                        })

            return datasets
        except Exception as e:
            logger.warning(f"WFS list_datasets failed for {self.url}: {e}")
            return []

    def describe(self, dataset_id: str) -> DatasetDescriptor:
        """Fetch DatasetDescriptor for a specific WFS FeatureType."""
        if not self.url:
            raise ValueError("WFS endpoint URL is missing in connection profile")

        return DatasetDescriptor(
            id=dataset_id,
            title=dataset_id,
            description=f"WFS FeatureType {dataset_id}",
            source_type="wfs",
            geometry_type="Feature",
            srs="EPSG:4326",
            bbox=[-180.0, -90.0, 180.0, 90.0],
            feature_count=None,
            fields=[],
            metadata={"endpoint_url": self.url, "feature_type": dataset_id},
        )

    def preview(self, dataset_id: str, limit: int = 10) -> Dict[str, Any]:
        """Fetch sample feature preview from WFS."""
        bounded_limit = max(1, min(limit, MAX_PREVIEW_LIMIT))
        q_spec = QuerySpec(limit=bounded_limit)
        q_res = self.query(dataset_id, q_spec)
        return {
            "schema": {"feature_type": dataset_id},
            "properties": q_res.features[0].get("properties", {}) if q_res.features else {},
            "features": q_res.features,
            "bbox": [-180.0, -90.0, 180.0, 90.0],
        }

    def query(self, dataset_id: str, query_spec: QuerySpec) -> QueryResult:
        """Execute GetFeature query on WFS endpoint."""
        bounded_limit = max(1, min(query_spec.limit or 100, MAX_QUERY_LIMIT))
        start_time = time.time()

        if not self.url:
            return QueryResult(
                dataset_id=dataset_id,
                features=[],
                total_count=0,
                schema_info={"error": "Missing URL"},
                metadata={"error_hint": "WFS adapter unconfigured (missing URL)"},
            )

        params: Dict[str, Any] = {
            "SERVICE": "WFS",
            "REQUEST": "GetFeature",
            "VERSION": self.options.get("version", "2.0.0"),
            "TYPENAME": dataset_id,
            "TYPENAMES": dataset_id,
            "OUTPUTFORMAT": "application/json",
            "COUNT": bounded_limit,
            "MAXFEATURES": bounded_limit,
        }

        if query_spec.bbox and len(query_spec.bbox) == 4:
            minx, miny, maxx, maxy = query_spec.bbox
            params["BBOX"] = f"{minx},{miny},{maxx},{maxy},EPSG:4326"

        try:
            resp = self.session.get(self.url, params=params, timeout=15)
            resp.raise_for_status()

            features = []
            if "json" in resp.headers.get("Content-Type", "").lower() or resp.text.strip().startswith("{"):
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
            logger.warning(f"WFS query error for '{dataset_id}': {e}")
            return QueryResult(
                dataset_id=dataset_id,
                features=[],
                total_count=0,
                schema_info={"error": str(e)},
                metadata={
                    "exec_time_ms": exec_time,
                    "error_hint": f"WFS query error: {e}",
                },
            )

    def health(self) -> DataFabricHealth:
        """Diagnostic health check for WFS endpoint."""
        start_time = time.time()
        if not self.url:
            return DataFabricHealth(
                status="unreachable",
                message="WFS URL missing",
            )
        try:
            ok = self.probe()
            latency = round((time.time() - start_time) * 1000, 2)
            if ok:
                return DataFabricHealth(
                    status="healthy",
                    message="WFS service online and responsive",
                    latency_ms=latency,
                )
            return DataFabricHealth(
                status="unreachable",
                message="WFS probe returned non-200 status",
                latency_ms=latency,
            )
        except Exception as e:
            latency = round((time.time() - start_time) * 1000, 2)
            return DataFabricHealth(
                status="unreachable",
                message=f"WFS health check failed: {e}",
                latency_ms=latency,
            )
