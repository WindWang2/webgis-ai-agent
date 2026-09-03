"""
STAC (SpatioTemporal Asset Catalog) Data Source Adapter — V2 (ADR-0094 Wave F)

相对 V1 的升级（审计 C2/#430 + Wave F 契约）：
- V2：normalize → plan → POST /search 执行；QueryResult 附 plan/evidence；
  result modes（descriptor/statistics/sample/features）；links.next 游标
  （token / next URL，同 ogc_api_adapter 的不透明 cursor 模式）。
- demo 标注：describe() demo metadata 含 is_demo=True；query fixture →
  QueryResult.is_demo=True（#430 的 source="synthetic-demo" 标签保留）。
- 全部 GET 经 safe_json_get（Content-Length + 解压后字节上限；#766 语义：
  非 200/坏 JSON 为 typed 失败，绝不静默空成功）。
- POST /search 保持原状（payload/bbox/datetime 不变）；timeout 取自
  ExecutionBudget.deadline。
"""
import logging
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests

from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
from app.services.data_fabric.adapters.postgis_adapter import (
    _filter_features_by_bbox,
)
from app.services.data_fabric.errors import (
    SOURCE_UNREACHABLE,
    DataFabricError,
    InvalidQueryError,
    SourceAuthFailedError,
    SourceBadResponseError,
    SourceRateLimitedError,
    SourceUnreachableError,
    classify_http_status,
)
from app.services.data_fabric.query.capabilities import get_capabilities
from app.services.data_fabric.query.evidence import build_evidence
from app.services.data_fabric.query.execution import (
    compute_aggregates,
    decode_cursor,
    deterministic_sample,
    encode_cursor,
)
from app.services.data_fabric.query.models import (
    CursorPage,
    OffsetPage,
    QuerySpecV2,
    ResultMode,
)
from app.services.data_fabric.query.normalize import normalize_query_spec
from app.services.data_fabric.query.planner import plan_query
from app.services.data_fabric.query.predicates import (
    evaluate_predicate,
    evaluate_temporal,
)
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
    STAC Data Fabric Adapter (V2):
    Generalizes STAC API / Catalog data access beyond Sentinel to any STAC 1.0.0 compliance source.
    Implements STAC search, pushdown bbox/datetime filtering, and labeled demo fallback fixtures.
    """

    def __init__(self, connection_profile: ConnectionProfile):
        super().__init__(connection_profile)
        self.endpoint = (self.profile.endpoint or "").strip()
        self.allow_private = getattr(self.profile, "allow_private", False)
        # SSRF-safe session: every request (incl. redirects) is revalidated.
        self.session = make_safe_session(allow_private=self.allow_private)

    def probe(self) -> bool:
        """Reachability probe for STAC endpoint (bounded GET via safe_json_get)."""
        if not self.endpoint:
            return True  # Fallback synthetic mode is reachable
        try:
            safe_url = DataFabricSecurity.validate_url(self.endpoint, allow_private=self.allow_private)
            data = safe_json_get(
                self.session, safe_url, timeout=5,
                headers={"Accept": "application/json"},
            )
            return isinstance(data, dict) and (
                "stac_version" in data or "collections" in data or "links" in data
            )
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
            try:
                body = safe_json_get(self.session, collections_url, timeout=8)
            except requests.exceptions.HTTPError:
                body = None  # /collections 非 200 → 尝试根 catalog links
            if isinstance(body, dict):
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
            root_json = safe_json_get(self.session, safe_url, timeout=8)
            if isinstance(root_json, dict):
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
                    "is_demo": True,
                },
            )

        error_type: Optional[str] = None
        error_message: Optional[str] = None
        try:
            safe_url = DataFabricSecurity.validate_url(self.endpoint, allow_private=self.allow_private)
            coll_url = urljoin(safe_url + "/", f"collections/{dataset_id}")
            try:
                c = safe_json_get(self.session, coll_url, timeout=8)
            except requests.exceptions.HTTPError as he:
                status = getattr(getattr(he, "response", None), "status_code", 0) or 0
                error_type = classify_http_status(status)
                error_message = f"STAC collection fetch returned HTTP {status}"
                logger.warning(f"STAC describe for '{dataset_id}' failed: {error_message}")
                c = None
            if c is not None:
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
                    metadata={
                        "extent": c.get("extent"),
                        "license": c.get("license"),
                        "is_demo": False,
                    },
                )
        except DataFabricError as e:
            error_type, error_message = e.code, str(e)
            logger.warning(f"STAC describe error for '{dataset_id}': {e}")
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
            "properties": q_res.features[0].get("properties", {}) if q_res.features else {},
            "features": q_res.features[:bounded_limit],
            "bbox": [-180.0, -90.0, 180.0, 90.0],
        }

    # ── 查询主路径（V2）────────────────────────────────────────────────

    def query(self, dataset_id: str, query_spec: QuerySpec) -> QueryResult:
        """V2: normalize → plan → POST /search（bbox/datetime 下推）。

        真实端点失败抛 typed 错误（#766/#430：绝不以 synthetic fixture 或
        in-band 空结果冒充成功）；无端点时进入显式 demo 模式（is_demo=True）。
        """
        started = time.monotonic()
        try:
            v2 = normalize_query_spec(query_spec)  # 失败抛 typed InvalidQueryError
        except DataFabricError:
            raise

        descriptor = self.describe(dataset_id)
        from app.services.data_fabric.fingerprint import dataset_fingerprint_service

        fp = dataset_fingerprint_service.calculate_descriptor_fingerprint(descriptor)
        caps = get_capabilities("stac")
        plan = plan_query(v2, descriptor, caps, source_id=self.profile.id, dataset_fingerprint=fp)

        if not self.endpoint:
            return self._query_demo(dataset_id, v2, plan, started, fp, descriptor)

        if v2.spatial is not None and v2.spatial.op != "bbox":
            raise InvalidQueryError(
                f"STAC /search supports bbox spatial pushdown only (got '{v2.spatial.op}')"
            )

        try:
            return self._search_remote(dataset_id, v2, plan, started)
        except DataFabricError:
            raise
        except Exception as e:
            logger.warning(f"STAC remote query failed for '{dataset_id}': {e}")
            raise SourceBadResponseError(f"STAC search failed: {e}") from e

    def _search_remote(self, dataset_id: str, v2: QuerySpecV2, plan, started: float) -> QueryResult:
        safe_url = DataFabricSecurity.validate_url(self.endpoint, allow_private=self.allow_private)
        search_url = urljoin(safe_url + "/", "search")
        page = v2.page
        limit = page.limit
        offset = page.offset if isinstance(page, OffsetPage) else 0

        payload: Dict[str, Any] = {
            "collections": [dataset_id],
            "limit": limit,
        }
        # 游标（links.next token 或 next URL；与写入侧 encode_cursor 成对）
        if isinstance(page, CursorPage) and page.cursor:
            decoded = self._decode_next_token(page.cursor)
            if decoded is None:
                raise InvalidQueryError("malformed STAC cursor (expected links.next token)")
            next_url, token = decoded
            if token:
                payload["token"] = token
                payload["next"] = token
            if next_url and next_url.startswith("http"):
                search_url = next_url
        elif offset:
            payload["page"] = offset // max(limit, 1) + 1

        if v2.spatial is not None:
            payload["bbox"] = list(v2.spatial.bbox)
        if v2.temporal is not None:
            if v2.temporal.op == "during":
                payload["datetime"] = f"{v2.temporal.start}/{v2.temporal.end}"
            elif v2.temporal.op == "before":
                payload["datetime"] = f"../{v2.temporal.value}"
            else:
                payload["datetime"] = f"{v2.temporal.value}/.."

        # POST /search 保持原状（不引入新参数）；timeout 取自 ExecutionBudget
        resp = self.session.post(
            search_url, json=payload, timeout=min(v2.execution.deadline_s, 30.0),
        )
        if resp.status_code != 200:
            # 真实源失败 → typed raise（绝不 fixture 冒充、绝不 in-band 空成功）
            raise self._http_error(resp.status_code)

        try:
            data = resp.json()
        except ValueError as e:
            raise SourceBadResponseError(f"STAC search returned non-JSON body: {e}") from e
        features = data.get("features", []) if isinstance(data, dict) else []
        if not isinstance(features, list):
            features = []

        matched = data.get("numberMatched") if isinstance(data, dict) else None
        if isinstance(matched, str) and matched.isdigit():
            matched = int(matched)
        elif not isinstance(matched, int):
            matched = None

        # links.next → 不透明游标（token 优先，退化为 next URL）
        next_url, next_token = self._extract_next_link(data)
        returned = len(features)
        truncated = returned >= limit
        if matched is not None:
            truncated = matched > offset + returned
        next_cursor = encode_cursor([next_token or next_url]) if (truncated and (next_token or next_url)) else None

        # 属性谓词本地求值（caps.filter_pushdown=False；页内有界）
        if v2.filter is not None:
            features = [f for f in features if evaluate_predicate(v2.filter, f.get("properties") or {})]
            returned = len(features)
        if v2.temporal is not None:
            features = [f for f in features if self._temporal_matches(v2.temporal, f.get("properties") or {})]
            returned = len(features)
        # 投影（select）应用于 properties
        if v2.select is not None:
            features = [self._project_item(f, v2.select) for f in features]
            returned = len(features)

        if v2.output.mode == ResultMode.STATISTICS and v2.aggregate:
            rows = compute_aggregates(
                [f.get("properties") or {} for f in features], v2.aggregate, v2.group_by,
            )
            evidence = build_evidence(
                plan, started_at=started, result_count=len(rows),
                rows_fetched=returned, rows_returned=len(rows), http_requests=1,
            )
            return QueryResult(
                dataset_id=dataset_id,
                features=[],
                data=rows,
                total_count=len(rows),
                returned_count=len(rows),
                payload_type="aggregation",
                result_mode="statistics",
                execution_time_seconds=round(time.monotonic() - started, 4),
                schema_info={"columns": list(rows[0].keys()) if rows else []},
                metadata=self._metadata(plan, evidence, started, extra={
                    "is_demo": False, "source": "remote",
                }),
            )

        if v2.output.mode == ResultMode.SAMPLE and v2.sample is not None:
            features = deterministic_sample(features, v2.sample, None)
            returned = len(features)
            truncated = False

        evidence = build_evidence(
            plan, started_at=started, result_count=returned,
            total_matching=matched, truncated=truncated,
            rows_fetched=returned, rows_returned=returned, http_requests=1,
        )
        return QueryResult(
            dataset_id=dataset_id,
            features=features,
            data={"type": "FeatureCollection", "features": features},
            total_count=returned,
            total_matching=matched,
            returned_count=returned,
            truncated=truncated,
            has_more=truncated,
            next_cursor=next_cursor,
            result_mode=("sample" if v2.output.mode == ResultMode.SAMPLE else "features"),
            execution_time_seconds=round(time.monotonic() - started, 4),
            schema_info={"returned": returned},
            metadata=self._metadata(plan, evidence, started, extra={
                "is_demo": False,
                "source": "remote",
            }),
        )

    # ── 游标 / HTTP 错误辅助 ───────────────────────────────────────────

    @staticmethod
    def _temporal_matches(node: Any, props: Dict[str, Any]) -> bool:
        """时间谓词本地求值（STAC 字段解析）。

        legacy ``datetime_range`` 归一化默认字段是 ``time``；STAC 规范字段
        是 ``datetime`` —— 条目属性缺失声明的字段时回退到 ``datetime``
        （不静默丢弃条目，也不放宽谓词本身）。
        """
        if node.field in props or "datetime" not in props:
            return evaluate_temporal(node, props)
        resolved = node.model_copy(update={"field": "datetime"})
        return evaluate_temporal(resolved, props)


    @staticmethod
    def _decode_next_token(cursor: str) -> Optional[Tuple[Optional[str], Optional[str]]]:
        """cursor → (next_url, token)。格式非法返回 None（typed error 由调用方抛）。"""
        try:
            decoded = decode_cursor(cursor)
        except DataFabricError:
            return None
        if not isinstance(decoded, list) or not decoded:
            return None
        value = decoded[0]
        if not isinstance(value, str) or not value:
            return None
        if value.startswith("http"):
            return value, None
        return None, value

    @staticmethod
    def _extract_next_link(data: Any) -> Tuple[Optional[str], Optional[str]]:
        """links[rel=next] → (href, token)。POST next 的 token 在 link.body 内。"""
        if not isinstance(data, dict):
            return None, None
        for link in data.get("links", []) or []:
            if isinstance(link, dict) and link.get("rel") == "next":
                href = link.get("href")
                body = link.get("body") if isinstance(link.get("body"), dict) else {}
                token = body.get("token") or body.get("next")
                return (
                    str(href) if isinstance(href, str) and href else None,
                    str(token) if token else None,
                )
        return None, None

    @staticmethod
    def _http_error(status_code: int) -> DataFabricError:
        if status_code in (401, 403):
            return SourceAuthFailedError(f"STAC search returned HTTP {status_code}")
        if status_code == 404:
            return SourceUnreachableError(f"STAC search returned HTTP {status_code}")
        if status_code == 429:
            return SourceRateLimitedError(f"STAC search returned HTTP {status_code}")
        return SourceBadResponseError(f"STAC search returned HTTP {status_code}")

    @staticmethod
    def _project_item(item: Dict[str, Any], select: List[str]) -> Dict[str, Any]:
        props = item.get("properties") or {}
        projected = {k: v for k, v in props.items() if k in select}
        out = dict(item)
        out["properties"] = projected
        return out

    # ── demo 模式（无端点；显式标注）───────────────────────────────────

    def _query_demo(
        self,
        dataset_id: str,
        v2: QuerySpecV2,
        plan,
        started: float,
        fp: Optional[str],
        descriptor: DatasetDescriptor,
    ) -> QueryResult:
        """无端点 → 显式 synthetic demo（is_demo=True；#430 标签保留）。"""
        fixture = SYNTHETIC_STAC_FIXTURES.get(dataset_id, SYNTHETIC_STAC_FIXTURES["landsat-8-c2-l2"])
        items = list(fixture.get("items", []))
        mode = v2.output.mode

        # 空间过滤（bbox 相交）
        if v2.spatial is not None:
            items = _filter_features_by_bbox(items, v2.spatial.bbox)
        # 属性/时间谓词本地求值
        if v2.filter is not None:
            items = [i for i in items if evaluate_predicate(v2.filter, i.get("properties") or {})]
        if v2.temporal is not None:
            items = [i for i in items if self._temporal_matches(v2.temporal, i.get("properties") or {})]

        rows = [i.get("properties") or {} for i in items]
        if mode == ResultMode.STATISTICS:
            agg_rows = compute_aggregates(rows, v2.aggregate or [], v2.group_by)
            evidence = build_evidence(
                plan, started_at=started, result_count=len(agg_rows),
                rows_fetched=len(rows), rows_returned=len(agg_rows),
            )
            return QueryResult(
                dataset_id=dataset_id,
                features=[],
                data=agg_rows,
                total_count=len(agg_rows),
                returned_count=len(agg_rows),
                payload_type="aggregation",
                result_mode="statistics",
                execution_time_seconds=round(time.monotonic() - started, 4),
                schema_info={"columns": list(agg_rows[0].keys()) if agg_rows else []},
                metadata=self._metadata(plan, evidence, started, extra={
                    "is_demo": True, "source": "synthetic-demo",
                }),
            )

        if mode == ResultMode.DESCRIPTOR:
            evidence = build_evidence(plan, started_at=started, result_count=0)
            return QueryResult(
                dataset_id=dataset_id,
                features=[],
                data=descriptor.model_dump(),
                total_count=0,
                returned_count=0,
                payload_type="descriptor",
                result_mode="descriptor",
                execution_time_seconds=round(time.monotonic() - started, 4),
                schema_info={"columns": [f["name"] for f in descriptor.fields]},
                metadata=self._metadata(plan, evidence, started, extra={
                    "is_demo": True, "source": "synthetic-demo",
                }),
            )

        page = v2.page
        offset = page.offset if isinstance(page, OffsetPage) else 0
        if mode == ResultMode.SAMPLE and v2.sample is not None:
            sliced = deterministic_sample(items, v2.sample, fp)
            truncated = False
        else:
            sliced = items[offset: offset + page.limit]
            truncated = len(items) > offset + page.limit

        if v2.select is not None:
            sliced = [self._project_item(i, v2.select) for i in sliced]

        total_matching: Optional[int] = None if truncated else offset + len(sliced)
        evidence = build_evidence(
            plan, started_at=started, result_count=len(sliced),
            total_matching=total_matching, truncated=truncated,
            rows_fetched=len(items), rows_returned=len(sliced),
        )
        return QueryResult(
            dataset_id=dataset_id,
            features=sliced,
            data={"type": "FeatureCollection", "features": sliced},
            total_count=len(sliced),
            total_matching=total_matching,
            returned_count=len(sliced),
            truncated=truncated,
            has_more=truncated,
            result_mode=("sample" if mode == ResultMode.SAMPLE else "features"),
            is_demo=True,
            execution_time_seconds=round(time.monotonic() - started, 4),
            schema_info={"returned": len(sliced)},
            metadata=self._metadata(plan, evidence, started, extra={
                "is_demo": True,
                # Explicit label so callers never mistake demo data for remote data.
                "source": "synthetic-demo",
            }),
        )

    # ── 公共 metadata 组装 ─────────────────────────────────────────────

    @staticmethod
    def _metadata(plan, evidence, started: float, extra: Dict[str, Any]) -> Dict[str, Any]:
        md: Dict[str, Any] = {
            "exec_time_ms": round((time.monotonic() - started) * 1000, 2),
            "pushdown_bbox": plan.pushed_spatial,
            "pushdown_filter": bool(plan.pushed_filters),
            "pushdown_projection": plan.pushed_projection,
            "query_plan": plan.model_dump(),
            "query_evidence": evidence.model_dump(),
        }
        md.update(extra)
        return md

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
