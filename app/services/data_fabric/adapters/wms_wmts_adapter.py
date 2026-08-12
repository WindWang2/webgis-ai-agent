"""
WMS & WMTS Raster Data Source Adapter
"""
import time
import logging
from typing import List, Dict, Any
from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
from app.services.data_fabric.security import DataFabricSecurity, make_safe_session
from app.schemas.data_fabric_schema import (
    DatasetDescriptor,
    QuerySpec,
    QueryResult,
    DataFabricHealth,
    ConnectionProfile,
)

logger = logging.getLogger(__name__)


class WMSWMTSAdapter(GeospatialDataSourceAdapter):
    """
    Concrete Data Fabric adapter for OGC WMS (Web Map Service) and WMTS (Web Map Tile Service).
    Parses GetCapabilities XML safely, provides raster tile source descriptors and metadata.
    """

    def __init__(self, connection_profile: ConnectionProfile):
        super().__init__(connection_profile)
        self.raw_url = self.profile.url or ""
        self.url = (
            DataFabricSecurity.validate_url(self.raw_url, allow_private=self.profile.allow_private)
            if self.raw_url
            else ""
        )
        self.service_type = self.profile.source_type.lower()
        self.options = self.profile.options or {}
        self.session = make_safe_session(allow_private=self.profile.allow_private)

    def probe(self) -> bool:
        """Lightweight WMS/WMTS GetCapabilities probe."""
        if not self.url:
            return False
        try:
            service = "WMTS" if "wmts" in self.service_type else "WMS"
            params = {
                "SERVICE": service,
                "REQUEST": "GetCapabilities",
                "VERSION": "1.3.0" if service == "WMS" else "1.0.0",
            }
            resp = self.session.get(self.url, params=params, timeout=5)
            return resp.status_code in (200, 206)
        except Exception as e:
            logger.debug(f"WMS/WMTS probe failed for {self.url}: {e}")
            return False

    def capabilities(self) -> List[str]:
        """List WMS/WMTS adapter capabilities."""
        return [
            "raster_tile",
            "wms",
            "wmts",
            "ogc_standard",
        ]

    def list_datasets(self) -> List[Dict[str, Any]]:
        """Discover available layers from WMS/WMTS GetCapabilities."""
        if not self.url:
            return []
        try:
            service = "WMTS" if "wmts" in self.service_type else "WMS"
            params = {
                "SERVICE": service,
                "REQUEST": "GetCapabilities",
            }
            resp = self.session.get(self.url, params=params, timeout=10)
            resp.raise_for_status()

            tree = DataFabricSecurity.parse_safe_xml(resp.content)
            datasets = []

            for elem in tree.iter():
                tag_name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                if tag_name == "Layer":
                    layer_name = ""
                    layer_title = ""
                    for child in elem:
                        child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                        if child_tag == "Name" or child_tag == "Identifier":
                            layer_name = (child.text or "").strip()
                        elif child_tag == "Title":
                            layer_title = (child.text or "").strip()

                    if layer_name:
                        datasets.append({
                            "id": layer_name,
                            "title": layer_title or layer_name,
                            "source_type": self.service_type,
                            "geometry_type": "Raster",
                        })

            return datasets
        except Exception as e:
            logger.warning(f"WMS/WMTS list_datasets failed for {self.url}: {e}")
            return []

    def describe(self, dataset_id: str) -> DatasetDescriptor:
        """Fetch DatasetDescriptor for a WMS/WMTS raster layer."""
        return DatasetDescriptor(
            id=dataset_id,
            title=dataset_id,
            description=f"{self.service_type.upper()} Raster Layer {dataset_id}",
            source_type=self.service_type,
            geometry_type="Raster",
            srs="EPSG:3857",
            bbox=[-180.0, -90.0, 180.0, 90.0],
            feature_count=None,
            fields=[],
            metadata={
                "endpoint_url": self.url,
                "layer": dataset_id,
                "tile_url_template": f"{self.url}?SERVICE={self.service_type.upper()}&REQUEST=GetMap&LAYERS={dataset_id}&FORMAT=image/png",
            },
        )

    def preview(self, dataset_id: str, limit: int = 10) -> Dict[str, Any]:
        """Fetch bounded raster metadata preview."""
        return {
            "schema": {"layer": dataset_id, "type": "Raster"},
            "properties": {"layer_name": dataset_id, "endpoint": self.url},
            "features": [],
            "bbox": [-180.0, -90.0, 180.0, 90.0],
        }

    def query(self, dataset_id: str, query_spec: QuerySpec) -> QueryResult:
        """WMS/WMTS does not support vector feature queries; returns raster metadata descriptor."""
        return QueryResult(
            dataset_id=dataset_id,
            features=[],
            total_count=0,
            schema_info={"geometry_type": "Raster", "layer": dataset_id},
            metadata={
                "getmap_url": f"{self.url}?SERVICE=WMS&REQUEST=GetMap&LAYERS={dataset_id}&STYLES=&CRS=EPSG:3857&WIDTH=256&HEIGHT=256&FORMAT=image/png",
                "tile_url": f"{self.url}?SERVICE=WMS&REQUEST=GetMap&LAYERS={dataset_id}&STYLES=&CRS=EPSG:3857&WIDTH=256&HEIGHT=256&FORMAT=image/png",
                "pushdown_bbox": bool(query_spec.bbox),
            },
        )

    def health(self) -> DataFabricHealth:
        """Diagnostic health check for WMS/WMTS endpoint."""
        start_time = time.time()
        try:
            ok = self.probe()
            latency = round((time.time() - start_time) * 1000, 2)
            if ok:
                return DataFabricHealth(
                    status="healthy",
                    message=f"{self.service_type.upper()} raster service responsive",
                    latency_ms=latency,
                )
            return DataFabricHealth(
                status="unreachable",
                message=f"{self.service_type.upper()} probe failed",
                latency_ms=latency,
            )
        except Exception as e:
            latency = round((time.time() - start_time) * 1000, 2)
            return DataFabricHealth(
                status="unreachable",
                message=f"{self.service_type.upper()} health check error: {e}",
                latency_ms=latency,
            )
