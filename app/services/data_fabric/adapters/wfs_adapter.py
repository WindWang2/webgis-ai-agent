"""
WFS (Web Feature Service) Data Source Adapter
"""
import time
import logging
from typing import Any, Dict, List, Optional
from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
from app.services.data_fabric.errors import SOURCE_BAD_RESPONSE
from app.services.data_fabric.metadata import normalize_crs
from app.services.data_fabric.security import DataFabricSecurity, make_safe_session
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
        self.session = make_safe_session(allow_private=self.profile.allow_private)
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

    def _get_capabilities_entry(self, dataset_id: str) -> Optional[Dict[str, str]]:
        """Parse GetCapabilities for one FeatureType; return its SRS/bbox info.

        #769: WFS DefaultSRS/SRS (and OtherSRS fallback) plus the
        WGS84BoundingBox corners. Returns None when capabilities is
        unreachable/unparseable — callers must then leave srs/bbox unset
        instead of fabricating EPSG:4326 / a worldwide extent.
        """
        params = {
            "SERVICE": "WFS",
            "REQUEST": "GetCapabilities",
            "VERSION": self.options.get("version", "2.0.0"),
        }
        resp = self.session.get(self.url, params=params, timeout=10)
        resp.raise_for_status()
        tree = DataFabricSecurity.parse_safe_xml(resp.content)

        for elem in tree.iter():
            tag_name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag_name != "FeatureType":
                continue
            info: Dict[str, str] = {}
            for child in elem:
                child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                if child_tag in ("DefaultSRS", "SRS", "OtherSRS") and "srs" not in info:
                    info["srs"] = (child.text or "").strip()
                elif child_tag == "Name" and (child.text or "").strip() == dataset_id:
                    info["name"] = dataset_id
                elif child_tag == "WGS84BoundingBox":
                    corners = [
                        (c.text or "").strip()
                        for c in child
                        if c.tag.split("}")[-1] in ("LowerCorner", "UpperCorner")
                    ]
                    if len(corners) == 2:
                        info["lower"] = corners[0]
                        info["upper"] = corners[1]
            if info.get("name") == dataset_id:
                return info
        return None

    def describe(self, dataset_id: str) -> DatasetDescriptor:
        """Fetch DatasetDescriptor for a specific WFS FeatureType.

        #769 truthfulness: SRS and bbox come from GetCapabilities
        (DefaultSRS/SRS + WGS84BoundingBox). When capabilities is unreachable
        or does not declare them, ``srs``/``bbox`` stay ``None`` — EPSG:4326 /
        a worldwide extent are never fabricated.
        """
        if not self.url:
            raise ValueError("WFS endpoint URL is missing in connection profile")

        srs: Optional[str] = None
        bbox = None
        describe_error: Optional[str] = None
        try:
            entry = self._get_capabilities_entry(dataset_id)
        except Exception as e:
            entry = None
            describe_error = str(e)
            logger.warning(f"WFS describe GetCapabilities failed for '{dataset_id}': {e}")

        if entry is not None:
            raw_srs = entry.get("srs")
            if raw_srs:
                srs = normalize_crs(raw_srs)
            lower = entry.get("lower", "")
            upper = entry.get("upper", "")
            try:
                if lower and upper:
                    lx, ly = (float(v) for v in lower.split()[:2])
                    ux, uy = (float(v) for v in upper.split()[:2])
                    bbox = [lx, ly, ux, uy]
            except ValueError:
                bbox = None

        metadata: Dict[str, Any] = {"endpoint_url": self.url, "feature_type": dataset_id}
        if describe_error:
            metadata["describe_error"] = describe_error
        elif entry is None:
            metadata["describe_error"] = (
                f"FeatureType '{dataset_id}' not found in GetCapabilities"
            )

        return DatasetDescriptor(
            id=dataset_id,
            title=dataset_id,
            description=f"WFS FeatureType {dataset_id}",
            source_type="wfs",
            geometry_type="Feature",
            srs=srs,
            bbox=bbox,
            feature_count=None,
            fields=[],
            metadata=metadata,
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
            # #769: explicitly negotiate WGS84 output. Without srsName many
            # WFS 1.1/2.0 servers answer in their native (projected) SRS and
            # the coordinates would flow on as degrees.
            "SRSNAME": "EPSG:4326",
            "srsName": "EPSG:4326",
        }

        if query_spec.bbox and len(query_spec.bbox) == 4:
            minx, miny, maxx, maxy = query_spec.bbox
            params["BBOX"] = f"{minx},{miny},{maxx},{maxy},EPSG:4326"

        try:
            resp = self.session.get(self.url, params=params, timeout=15)
            resp.raise_for_status()

            content_type = resp.headers.get("Content-Type", "").lower()
            body_is_json = "json" in content_type or resp.text.strip().startswith("{")
            if not body_is_json:
                # #766: a 200 body that is not JSON (common: a WFS 2.0 server
                # ignoring OUTPUTFORMAT and returning GML) is a FETCH FAILURE —
                # never a silently empty "successful" dataset.
                exec_time = round((time.time() - start_time) * 1000, 2)
                return QueryResult(
                    dataset_id=dataset_id,
                    features=[],
                    total_count=0,
                    schema_info={
                        "error": (
                            f"WFS returned non-JSON content (Content-Type="
                            f"{content_type or 'unknown'}); JSON output not supported"
                        )
                    },
                    metadata={
                        "exec_time_ms": exec_time,
                        "error_type": SOURCE_BAD_RESPONSE,
                        "error": "WFS GetFeature returned a non-JSON 200 body",
                    },
                )

            data = resp.json()
            features = data.get("features", [])

            # #769: if the response FeatureCollection declares a non-WGS84 CRS,
            # the coordinates are NOT lon/lat degrees — refuse them with a
            # typed error instead of feeding projected coordinates into the
            # pipeline as EPSG:4326 (reprojection is out of scope here).
            declared_crs = data.get("crs") if isinstance(data, dict) else None
            if declared_crs:
                normalized = normalize_crs(str(declared_crs))
                if normalized not in ("EPSG:4326", "CRS84"):
                    exec_time = round((time.time() - start_time) * 1000, 2)
                    return QueryResult(
                        dataset_id=dataset_id,
                        features=[],
                        total_count=0,
                        schema_info={
                            "error": (
                                f"WFS returned features in {declared_crs} "
                                f"(normalized {normalized}); only EPSG:4326/CRS84 "
                                f"is supported"
                            )
                        },
                        metadata={
                            "exec_time_ms": exec_time,
                            "error_type": SOURCE_BAD_RESPONSE,
                            "error": f"unsupported CRS in response: {declared_crs}",
                            "crs": str(declared_crs),
                        },
                    )

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
