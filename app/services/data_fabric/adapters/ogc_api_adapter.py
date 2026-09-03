"""
OGC API - Features Data Source Adapter
"""
import time
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
from app.services.data_fabric.errors import (
    DataFabricError,
    InvalidQueryError,
    SourceBadResponseError,
)
from app.services.data_fabric.query.capabilities import get_capabilities
from app.services.data_fabric.query.compilers import compile_predicate_cql2
from app.services.data_fabric.query.evidence import build_evidence
from app.services.data_fabric.query.models import CursorPage, OffsetPage
from app.services.data_fabric.query.normalize import normalize_query_spec
from app.services.data_fabric.query.planner import plan_query
from app.services.data_fabric.query.execution import decode_cursor, encode_cursor
from app.services.data_fabric.security import (
    DataFabricSecurity,
    make_safe_session,
    safe_json_get,
)
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
        self.session = make_safe_session(allow_private=self.profile.allow_private)
        if "headers" in self.options:
            self.session.headers.update(self.options["headers"])
        self._conformance_cache: Optional[Tuple[float, List[str]]] = None

    # ── conformance / capability V2 ───────────────────────────────────

    def _get_conformance(self) -> List[str]:
        """/conformance 声明（60s 缓存；失败返回空表=保守）。"""
        if self._conformance_cache and (time.monotonic() - self._conformance_cache[0]) < 60:
            return self._conformance_cache[1]
        classes: List[str] = []
        try:
            data = safe_json_get(
                self.session, self.url.rstrip("/") + "/conformance", timeout=5,
                max_bytes=2 * 1024 * 1024,
            )
            raw = data.get("conformsTo", []) if isinstance(data, dict) else []
            classes = [str(c) for c in raw if isinstance(c, str)]
        except Exception:
            classes = []
        self._conformance_cache = (time.monotonic(), classes)
        return classes

    def _conformance_declares(self, uri_fragment: str) -> bool:
        return any(uri_fragment in c for c in self._get_conformance())

    def _cql2_supported(self) -> bool:
        """仅当服务声明 CQL2（text 或 JSON）时启用 filter 下推。"""
        return self._conformance_declares("cql2-text") or self._conformance_declares(
            "ogcapi-features-2"
        ) or any("cql2" in c.lower() for c in self._get_conformance())

    def _capabilities_v2(self):
        caps = get_capabilities("ogc_api")
        return caps.model_copy(update={"filter_pushdown": self._cql2_supported()})

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

            # #769: srs comes from the collection's declared CRS list; when the
            # collection declares none, srs stays None — never a fabricated
            # EPSG:4326 (the metadata truthfulness contract).
            declared_crs = col_data.get("crs") or []
            srs = declared_crs[0] if declared_crs else None

            return DatasetDescriptor(
                id=dataset_id,
                title=col_data.get("title", dataset_id),
                description=col_data.get("description", f"OGC API Collection {dataset_id}"),
                source_type="ogc_api",
                geometry_type=col_data.get("itemType", "Feature"),
                srs=srs,
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
                srs=None,
                bbox=None,
                feature_count=None,
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
        """V2: normalize → plan → 有界执行（conformance 门控 CQL2，links.next 游标）。"""
        import time as _time

        started = _time.monotonic()
        try:
            v2 = normalize_query_spec(query_spec)
        except DataFabricError:
            raise

        if not self.url:
            raise InvalidQueryError("OGC API adapter unconfigured (missing URL)")

        descriptor = self.describe(dataset_id)
        from app.services.data_fabric.fingerprint import dataset_fingerprint_service

        fp = dataset_fingerprint_service.calculate_descriptor_fingerprint(descriptor)
        caps = self._capabilities_v2()
        plan = plan_query(v2, descriptor, caps, source_id=self.profile.id, dataset_fingerprint=fp)

        items_url = f"{self.url.rstrip('/')}/collections/{dataset_id}/items"
        params: Dict[str, Any] = {"limit": v2.page.limit}

        # offset 非核心参数：仅在 conformance 声明 paging 扩展时发送（否则
        # 走 links.next 游标语义——单页 + has_more）。
        supports_offset = self._conformance_declares(
            "http://www.opengis.net/doc/IS/ogcapi-features-2/1.0"
        ) or any("paging" in c.lower() for c in self._get_conformance())
        offset = getattr(v2.page, "offset", 0)
        if isinstance(v2.page, CursorPage):
            # links.next 是 URL 令牌：cursor 直接作为 next URL 使用
            if v2.page.cursor:
                try:
                    decoded = decode_cursor(v2.page.cursor)
                except DataFabricError:
                    decoded = None
                if isinstance(decoded, list) and decoded and isinstance(decoded[0], str) \
                        and decoded[0].startswith("http"):
                    items_url = decoded[0]
                    params = {"limit": v2.page.limit}
                    plan = plan.model_copy(update={"pagination_note": "links.next token"})
                else:
                    raise InvalidQueryError("malformed OGC cursor (expected links.next token)")
        elif offset and supports_offset:
            params["offset"] = offset

        if v2.spatial is not None:
            if v2.spatial.op != "bbox":
                raise InvalidQueryError(
                    f"OGC API supports bbox pushdown only (got '{v2.spatial.op}')"
                )
            from app.services.data_fabric.query.predicates import bbox_crosses_antimeridian

            if bbox_crosses_antimeridian(v2.spatial.bbox):
                raise InvalidQueryError(
                    "antimeridian-crossing bbox not supported by OGC API bbox param"
                )
            params["bbox"] = ",".join(str(b) for b in v2.spatial.bbox)
            # bbox-crs 显式声明（默认 CRS84）
            params["bbox-crs"] = "<http://www.opengis.net/def/crs/OGC/1.3/CRS84>"
            params["crs"] = "<http://www.opengis.net/def/crs/OGC/1.3/CRS84>"

        if v2.temporal is not None:
            if v2.temporal.op == "during":
                params["datetime"] = f"{v2.temporal.start}/{v2.temporal.end}"
            elif v2.temporal.op == "before":
                params["datetime"] = f"../{v2.temporal.value}"
            else:
                params["datetime"] = f"{v2.temporal.value}/.."

        # CQL2 filter：仅当 conformance 声明且为 AST 时编译（filter-lang 显式）
        if v2.filter is not None:
            if not self._cql2_supported():
                raise InvalidQueryError(
                    "server does not declare CQL2 conformance; attribute filter "
                    "unsupported for this source"
                )
            params["filter"] = compile_predicate_cql2(v2.filter)
            params["filter-lang"] = "cql2-text"

        try:
            geojson = safe_json_get(
                self.session, items_url, params=params,
                timeout=min(v2.execution.deadline_s, 30.0),
            )
        except DataFabricError:
            raise
        except Exception as e:
            raise SourceBadResponseError(f"OGC API Features query error: {e}") from e

        features = geojson.get("features", []) if isinstance(geojson, dict) else []
        if not isinstance(features, list):
            features = []
        matched = geojson.get("numberMatched") if isinstance(geojson, dict) else None
        if isinstance(matched, str) and matched.isdigit():
            matched = int(matched)
        elif not isinstance(matched, int):
            matched = None

        next_url = None
        if isinstance(geojson, dict):
            for link in geojson.get("links", []) or []:
                if isinstance(link, dict) and link.get("rel") == "next" and link.get("href"):
                    next_url = str(link["href"])
                    break

        returned = len(features)
        truncated = returned >= v2.page.limit
        if matched is not None and isinstance(v2.page, OffsetPage):
            truncated = matched > (v2.page.offset + returned)
        next_cursor = encode_cursor([next_url]) if (truncated and next_url) else None

        evidence = build_evidence(
            plan, started_at=started, result_count=returned,
            total_matching=matched, truncated=truncated,
            rows_fetched=returned, rows_returned=returned, http_requests=1,
        )
        return QueryResult(
            dataset_id=dataset_id,
            features=features,
            total_count=returned,
            total_matching=matched,
            returned_count=returned,
            truncated=truncated,
            has_more=truncated,
            next_cursor=next_cursor,
            result_mode="features",
            execution_time_seconds=round(_time.monotonic() - started, 4),
            schema_info={"returned": returned},
            metadata={
                "exec_time_ms": round((_time.monotonic() - started) * 1000, 2),
                "pushdown_bbox": plan.pushed_spatial,
                "pushdown_filter": bool(plan.pushed_filters),
                "cql2_used": bool(v2.filter is not None),
                "query_plan": plan.model_dump(),
                "query_evidence": evidence.model_dump(),
                "is_demo": False,
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
