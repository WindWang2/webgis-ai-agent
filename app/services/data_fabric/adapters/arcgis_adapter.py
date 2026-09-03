"""ArcGIS REST Feature/Map Service Data Source Adapter — V2 (ADR-0094).

相对 V1 的升级（修复审计 F-9/M-2 + Wave E 契约）：
- where 由 typed AST 编译（单引号 doubling 转义）——raw where 透传通道移除
  （此前 LLM 自由文本直达远端 SQL 面）。
- outFields 投影下推（此前硬编码 "*"）。
- orderByFields 排序下推；跨页稳定排序要求 objectId 排序兜底。
- maxRecordCount/exceededTransferLimit 诚实处理：探测服务上限，页大小钳制；
  exceededTransferLimit=True → truncated/has_more 如实（此前 2000=全部的
  静默截断）。
- returnCountOnly=true 的 count-only（STATISTICS 零几何）。
- describe 读取真实 extent/spatialReference（此前硬编码 4326+全球框）。
- options.headers 应用到 session（token 认证可用，对齐 OGC/WFS）。
- dataset_id URL 路径段校验（防 ../ 遍历远端 URL 空间）。
- 全部 GET 经 safe_json_get（Content-Length + 解压后字节上限）。
- V2：normalize → plan → 执行；QueryResult 附 plan/evidence。
"""
import re
import time
import logging
from typing import Any, Dict, List, Optional

from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
from app.services.data_fabric.errors import (
    DataFabricError,
    InvalidQueryError,
    SourceBadResponseError,
)
from app.services.data_fabric.query.capabilities import get_capabilities
from app.services.data_fabric.query.compilers import compile_predicate_arcgis
from app.services.data_fabric.query.evidence import build_evidence
from app.services.data_fabric.query.models import OffsetPage, ResultMode
from app.services.data_fabric.query.normalize import normalize_query_spec
from app.services.data_fabric.query.planner import plan_query
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
MAX_QUERY_LIMIT = 10_000
# 未探测到 maxRecordCount 时的保守页上限（ArcGIS 默认 2000）
_FALLBACK_MAX_RECORD_COUNT = 2_000

_LAYER_ID_RE = re.compile(r"^[0-9]+$|^_?[A-Za-z0-9_]{1,60}$")


def _safe_layer_id(dataset_id: str) -> str:
    """图层 ID 只允许数字或简单标识符（防 URL 路径遍历）。"""
    if not dataset_id or not _LAYER_ID_RE.match(dataset_id):
        raise InvalidQueryError(f"invalid ArcGIS layer id: {dataset_id!r}")
    return dataset_id


class ArcGISAdapter(GeospatialDataSourceAdapter):
    """ArcGIS REST FeatureServer/MapServer V2 adapter。"""

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
        # token 认证支持（对齐 OGC/WFS 行为）
        if "headers" in self.options:
            self.session.headers.update(self.options["headers"])
        token = self.options.get("token")
        if isinstance(token, str) and token:
            # 仅放入请求参数；绝不回显到 describe/结果 metadata
            self._token = token
        else:
            self._token = None
        self._layer_meta_cache: Dict[str, Dict[str, Any]] = {}

    # ── 基础契约 ───────────────────────────────────────────────────────

    def probe(self) -> bool:
        if not self.url:
            return False
        try:
            data = safe_json_get(self.session, self.url, params={"f": "json"},
                                 timeout=5, max_bytes=2 * 1024 * 1024)
            return isinstance(data, dict) and "currentVersion" in data
        except Exception as e:
            logger.debug("ArcGIS probe failed: %s", e)
            return False

    def capabilities(self) -> List[str]:
        return [
            "pushdown_bbox",
            "pushdown_filter",
            "projection_pushdown",
            "sort_pushdown",
            "resultOffset_pagination",
            "count_only",
            "vector_features",
            "arcgis_rest",
        ]

    def list_datasets(self) -> List[Dict[str, Any]]:
        if not self.url:
            return []
        try:
            data = safe_json_get(self.session, self.url, params={"f": "json"},
                                 timeout=10, max_bytes=8 * 1024 * 1024)
            layers = data.get("layers", []) or []
            tables = data.get("tables", []) or []
            out = []
            for lyr in layers + tables:
                lyr_id = str(lyr.get("id", ""))
                if lyr_id:
                    out.append({
                        "id": lyr_id,
                        "title": lyr.get("name", lyr_id),
                        "geometry_type": lyr.get("geometryType", "Table"),
                        "source_type": "arcgis",
                    })
            return out
        except Exception as e:
            logger.warning("ArcGIS list_datasets error: %s", e)
            return []

    def _layer_meta(self, dataset_id: str) -> Dict[str, Any]:
        """图层元数据（maxRecordCount/extent/spatialReference/fields，带缓存）。"""
        cached = self._layer_meta_cache.get(dataset_id)
        if cached is not None:
            return cached
        layer_id = _safe_layer_id(dataset_id)
        layer_url = f"{self.url.rstrip('/')}/{layer_id}"
        data = safe_json_get(self.session, layer_url, params={"f": "json"},
                             timeout=10, max_bytes=8 * 1024 * 1024)
        if not isinstance(data, dict):
            raise SourceBadResponseError(f"ArcGIS layer metadata invalid for '{dataset_id}'")
        if "error" in data:
            raise SourceBadResponseError(
                f"ArcGIS layer error for '{dataset_id}': {data['error'].get('message', 'unknown')}"
            )
        self._layer_meta_cache[dataset_id] = data
        return data

    def describe(self, dataset_id: str) -> DatasetDescriptor:
        if not self.url:
            raise InvalidQueryError("ArcGIS REST endpoint URL missing")
        layer_id = _safe_layer_id(dataset_id)
        try:
            data = self._layer_meta(dataset_id)
        except DataFabricError:
            raise
        except Exception as e:
            raise SourceUnreachableError_arcgis(e) from e

        fields = [
            {"name": f.get("name"), "type": f.get("type"), "alias": f.get("alias")}
            for f in data.get("fields", []) or []
            if isinstance(f, dict) and f.get("name")
        ]

        # 真实 extent + spatialReference（不再硬编码 4326/全球）
        bbox: Optional[List[float]] = None
        extent = data.get("extent") or {}
        if isinstance(extent, dict):
            try:
                xmin, ymin = float(extent["xmin"]), float(extent["ymin"])
                xmax, ymax = float(extent["xmax"]), float(extent["ymax"])
                spatial_ref = extent.get("spatialReference") or {}
                wkid = spatial_ref.get("latestWkid") or spatial_ref.get("wkid")
                if wkid == 4326 or wkid is None:
                    bbox = [xmin, ymin, xmax, ymax]  # 4326 extent 已是 lon/lat
                # 非 4326 extent：不伪造换算（planner 无法安全变换）→ None
            except (KeyError, TypeError, ValueError):
                bbox = None
        srs: Optional[str] = None
        sr = data.get("spatialReference") or (extent.get("spatialReference") if isinstance(extent, dict) else None)
        if isinstance(sr, dict):
            wkid = sr.get("latestWkid") or sr.get("wkid")
            if isinstance(wkid, int):
                srs = f"EPSG:{wkid}"

        oid_field = data.get("objectIdField") or "OBJECTID"
        max_record_count = data.get("maxRecordCount") or _FALLBACK_MAX_RECORD_COUNT
        supports_pagination = bool(data.get("supportsPagination"))
        geom_type_map = {
            "esriGeometryPoint": "Point",
            "esriGeometryPolyline": "LineString",
            "esriGeometryPolygon": "Polygon",
            "esriGeometryMultipoint": "MultiPoint",
        }
        return DatasetDescriptor(
            id=dataset_id,
            title=data.get("name", dataset_id),
            description=data.get("description") or f"ArcGIS REST Layer {dataset_id}",
            source_type="arcgis",
            geometry_type=geom_type_map.get(data.get("geometryType"), "Unknown"),
            srs=srs,
            bbox=bbox,
            feature_count=None,
            fields=fields,
            metadata={
                # redact（审计 F-4）
                "layer_url": DataFabricSecurity.redact_url(f"{self.url.rstrip('/')}/{layer_id}"),
                "object_id_field": oid_field,
                "max_record_count": int(max_record_count) if max_record_count else _FALLBACK_MAX_RECORD_COUNT,
                "supports_pagination": supports_pagination,
            },
        )

    def preview(self, dataset_id: str, limit: int = 10) -> Dict[str, Any]:
        bounded_limit = max(1, min(limit, MAX_PREVIEW_LIMIT))
        q_res = self.query(dataset_id, QuerySpec(limit=bounded_limit))
        return {
            "schema": {"layer": dataset_id},
            "properties": q_res.features[0].get("properties", {}) if q_res.features else {},
            "features": q_res.features,
            "bbox": None,
        }

    # ── 查询 ───────────────────────────────────────────────────────────

    def query(self, dataset_id: str, query_spec: QuerySpec) -> QueryResult:
        started = time.monotonic()
        v2 = normalize_query_spec(query_spec)
        try:
            return self._execute_v2(dataset_id, v2, started)
        except DataFabricError:
            raise
        except Exception as e:
            logger.warning("ArcGIS query error for '%s': %s", dataset_id, e)
            raise SourceBadResponseError(f"ArcGIS query error: {e}") from e

    def _execute_v2(self, dataset_id: str, v2, started: float) -> QueryResult:
        if not self.url:
            raise InvalidQueryError("ArcGIS REST adapter unconfigured (missing URL)")
        layer_id = _safe_layer_id(dataset_id)

        descriptor = self.describe(dataset_id)
        from app.services.data_fabric.fingerprint import dataset_fingerprint_service

        fp = dataset_fingerprint_service.calculate_descriptor_fingerprint(descriptor)
        meta = descriptor.metadata
        max_record_count = int(meta.get("max_record_count", _FALLBACK_MAX_RECORD_COUNT))
        supports_pagination = bool(meta.get("supports_pagination"))
        oid_field = str(meta.get("object_id_field", "OBJECTID"))

        caps = get_capabilities("arcgis").model_copy(update={
            "offset_pagination": supports_pagination,
            "max_page_size": min(max_record_count, MAX_QUERY_LIMIT),
        })
        plan = plan_query(v2, descriptor, caps, source_id=self.profile.id, dataset_fingerprint=fp)

        # STATISTICS：count-only（returnCountOnly，零几何传输）
        if plan.result_mode == ResultMode.STATISTICS:
            if v2.aggregate and len(v2.aggregate) == 1 and v2.aggregate[0].func == "count" and not v2.group_by:
                params = self._base_params(v2)
                params["returnCountOnly"] = "true"
                params.pop("resultRecordCount", None)
                params.pop("resultOffset", None)
                query_url = f"{self.url.rstrip('/')}/{layer_id}/query"
                data = safe_json_get(self.session, query_url, params=params,
                                     timeout=min(v2.execution.deadline_s, 30.0))
                count = data.get("count") if isinstance(data, dict) else None
                if not isinstance(count, int):
                    raise SourceBadResponseError("ArcGIS returnCountOnly returned no count")
                evidence = build_evidence(plan, started_at=started, result_count=1,
                                          total_matching=count, rows_fetched=0,
                                          rows_returned=1, http_requests=1)
                return QueryResult(
                    dataset_id=dataset_id,
                    features=[],
                    data=[{"count": count}],
                    total_count=1,
                    total_matching=count,
                    returned_count=1,
                    payload_type="aggregation",
                    result_mode="statistics",
                    execution_time_seconds=round(time.monotonic() - started, 4),
                    metadata={
                        "query_plan": plan.model_dump(),
                        "query_evidence": evidence.model_dump(),
                        "is_demo": False,
                    },
                )
            raise InvalidQueryError(
                "ArcGIS supports count-only statistics; use aggregate=[count] without group_by "
                "or switch to a PostGIS source"
            )

        page = v2.page
        limit = min(page.limit, max_record_count)  # 页大小钳制到服务上限
        offset = page.offset if isinstance(page, OffsetPage) else 0

        params = self._base_params(v2)
        params["resultRecordCount"] = limit
        if offset and supports_pagination:
            params["resultOffset"] = offset
        elif offset:
            plan = plan.model_copy(update={
                "warnings": plan.warnings + ["layer lacks supportsPagination; offset not pushed"],
            })

        # where：AST 编译（单引号 doubling）
        where_sql = "1=1"
        if v2.filter is not None:
            allowed = [f["name"] for f in descriptor.fields if f.get("name")]
            from app.services.data_fabric.query.predicates import validate_predicate_fields

            validate_predicate_fields(v2.filter, allowed)
            where_sql = compile_predicate_arcgis(v2.filter)

        # temporal → where 片段（AND 连接；值经 _arcgis_quote 转义）
        if v2.temporal is not None:
            from app.services.data_fabric.query.compilers import _arcgis_quote

            def _iso(s: str) -> str:
                return s.replace("Z", "")

            t = v2.temporal
            if t.op == "during":
                frag = (f"{t.field} >= TIMESTAMP {_arcgis_quote(_iso(t.start))} AND "
                        f"{t.field} <= TIMESTAMP {_arcgis_quote(_iso(t.end))}")
            elif t.op == "before":
                frag = f"{t.field} < TIMESTAMP {_arcgis_quote(_iso(t.value))}"
            else:
                frag = f"{t.field} > TIMESTAMP {_arcgis_quote(_iso(t.value))}"
            where_sql = f"({where_sql}) AND ({frag})"

        params["where"] = where_sql

        # outFields 投影
        if v2.select is not None:
            allowed = set(f["name"] for f in descriptor.fields if f.get("name"))
            proj = [f for f in v2.select if f in allowed]
            params["outFields"] = ",".join(proj + [oid_field]) if proj else "*"
        else:
            params["outFields"] = "*"

        # orderByFields（分页稳定排序：无显式排序时按 objectId）
        order_parts = []
        for o in v2.order_by:
            order_parts.append(f"{o.field} {o.direction.upper()}")
        if not order_parts:
            order_parts.append(f"{oid_field} ASC")
        params["orderByFields"] = ",".join(order_parts)

        # bbox envelope
        if v2.spatial is not None:
            if v2.spatial.op != "bbox":
                raise InvalidQueryError(
                    f"ArcGIS adapter supports bbox envelope pushdown only "
                    f"(got '{v2.spatial.op}'); use bbox or a PostGIS source"
                )
            from app.services.data_fabric.query.predicates import bbox_crosses_antimeridian

            if bbox_crosses_antimeridian(v2.spatial.bbox):
                raise InvalidQueryError("antimeridian-crossing bbox not supported by ArcGIS envelope")
            minx, miny, maxx, maxy = v2.spatial.bbox
            params["geometry"] = f"{minx},{miny},{maxx},{maxy}"
            params["geometryType"] = "esriGeometryEnvelope"
            params["inSR"] = "4326"
            params["spatialRel"] = "esriSpatialRelIntersects"

        query_url = f"{self.url.rstrip('/')}/{layer_id}/query"
        try:
            data = safe_json_get(self.session, query_url, params=params,
                                 timeout=min(v2.execution.deadline_s, 30.0))
        except DataFabricError:
            raise
        except Exception as e:
            raise SourceBadResponseError(f"ArcGIS query request failed: {e}") from e

        if isinstance(data, dict) and "error" in data:
            err = data["error"]
            raise SourceBadResponseError(
                f"ArcGIS query error: {err.get('message', 'unknown') if isinstance(err, dict) else err}"
            )

        features = data.get("features", []) or []
        if not isinstance(features, list):
            features = []

        # 诚实截断语义（审计 M-2）：exceededTransferLimit 说明还有数据
        exceeded = bool(data.get("exceededTransferLimit")) if isinstance(data, dict) else False
        returned = len(features)
        truncated = exceeded or returned >= limit
        # 偏移累计（本页起点 + 本页大小）
        if supports_pagination:
            has_more = truncated
        else:
            has_more = exceeded  # 不支持分页的服务：只有 exceeded 标志可信

        evidence = build_evidence(
            plan, started_at=started, result_count=returned,
            total_matching=None, truncated=truncated,
            rows_fetched=returned, rows_returned=returned, http_requests=1,
        )
        return QueryResult(
            dataset_id=dataset_id,
            features=features,
            total_count=returned,
            total_matching=None,
            returned_count=returned,
            truncated=truncated,
            has_more=has_more,
            result_mode="features",
            execution_time_seconds=round(time.monotonic() - started, 4),
            schema_info={"returned": returned, "max_record_count": max_record_count},
            metadata={
                "exec_time_ms": round((time.monotonic() - started) * 1000, 2),
                "pushdown_bbox": plan.pushed_spatial,
                "pushdown_filter": bool(plan.pushed_filters),
                "pushdown_projection": v2.select is not None,
                "pushdown_sort": True,
                "exceeded_transfer_limit": exceeded,
                "max_record_count": max_record_count,
                "query_plan": plan.model_dump(),
                "query_evidence": evidence.model_dump(),
                "is_demo": False,
            },
        )

    def _base_params(self, v2) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "f": "geojson",
            "outSR": "4326",
        }
        if self._token:
            params["token"] = self._token
        return params

    def health(self) -> DataFabricHealth:
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
        except Exception:
            latency = round((time.time() - start_time) * 1000, 2)
            return DataFabricHealth(
                status="unreachable",
                message="ArcGIS health check failed",
                latency_ms=latency,
            )


def SourceUnreachableError_arcgis(e: Exception):
    from app.services.data_fabric.errors import SourceUnreachableError

    return SourceUnreachableError(f"ArcGIS layer metadata fetch failed: {e}")
