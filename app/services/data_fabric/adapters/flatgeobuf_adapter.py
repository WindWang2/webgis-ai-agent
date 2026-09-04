"""
FlatGeobuf Data Source Adapter — V2 (ADR-0094 Wave F)

相对 V1 的升级（审计 C2 + Wave F 契约）：
- 真实端点读失败 → typed SourceUnreachableError/SourceBadResponseError（绝不回落
  synthetic fixture 冒充真实数据）；fixture 仅存于无端点的显式 demo 模式
  （is_demo=True / source="synthetic-demo"）。
- bbox + 投影下推：pyogrio 可用时走 ``read_dataframe(bbox=..., columns=[...],
  max_features=window)``（packed RTree + 列裁剪）；否则 gpd.read_file(bbox=...)。
- 诚实 metadata：``spatial_index_used`` 真实路径为 None（文件是否含 RTree
  不可知，不再硬编码 True）；demo fixture 声明 packed_rtree 时才为 True。
- V2：normalize → plan → 执行；QueryResult 附 plan/evidence；SAMPLE +
  STATISTICS（本地有界聚合）模式。
"""
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
from app.services.data_fabric.adapters.postgis_adapter import (
    _filter_features_by_bbox,
)
from app.services.data_fabric.errors import (
    DataFabricError,
    InvalidQueryError,
    SecurityBlockedError,
    SourceBadResponseError,
    SourceUnreachableError,
)
from app.services.data_fabric.query.capabilities import get_capabilities
from app.services.data_fabric.query.evidence import build_evidence
from app.services.data_fabric.query.execution import (
    StreamingBudget,
    compute_aggregates,
    deterministic_sample,
)
from app.services.data_fabric.query.models import OffsetPage, QuerySpecV2, ResultMode
from app.services.data_fabric.query.normalize import normalize_query_spec
from app.services.data_fabric.query.planner import plan_query
from app.services.data_fabric.query.predicates import (
    evaluate_predicate,
    evaluate_temporal,
    iter_fields,
)
from app.services.data_fabric.security import (
    DataFabricSecurity,
    DataFabricSecurityError,
    _local_file_max_bytes_from_settings,
    _local_file_roots_from_settings,
    make_safe_session,
    resolve_safe_local_path,
)
from app.schemas.data_fabric_schema import (
    DatasetDescriptor,
    QuerySpec,
    QueryResult,
    DataFabricHealth,
    ConnectionProfile,
)

logger = logging.getLogger(__name__)

FGB_MAGIC = b"fgb\x03fgb\x00"
MAX_PREVIEW_LIMIT = 50
MAX_QUERY_LIMIT = 5000
_REMOTE_SCHEMES = ("http://", "https://", "s3://", "minio://", "gs://")

SYNTHETIC_FGB_FIXTURES: Dict[str, Dict[str, Any]] = {
    "beijing_subway_stations": {
        "dataset_id": "beijing_subway_stations",
        "title": "Beijing Subway Stations FlatGeobuf",
        "description": "Spatial point index of Beijing metro stations with spatial indexing.",
        "geometry_type": "Point",
        "feature_count": 350,
        "crs": "EPSG:4326",
        "bbox": [116.1, 39.7, 116.7, 40.2],
        "columns": {"station_id": "int", "name_zh": "string", "line": "string", "passenger_flow": "int"},
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [116.4074, 39.9042]},
                "properties": {"station_id": 101, "name_zh": "Tiananmen East", "line": "Line 1", "passenger_flow": 120000},
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [116.3541, 39.9897]},
                "properties": {"station_id": 102, "name_zh": "Xitang", "line": "Line 10", "passenger_flow": 85000},
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [116.4600, 39.9100]},
                "properties": {"station_id": 103, "name_zh": "Guomao", "line": "Line 1", "passenger_flow": 210000},
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [116.3100, 39.9500]},
                "properties": {"station_id": 104, "name_zh": "Zhongguancun", "line": "Line 4", "passenger_flow": 140000},
            },
        ],
    }
}


class FlatGeobufAdapter(GeospatialDataSourceAdapter):
    """
    FlatGeobuf Data Fabric Adapter (V2):
    Optimized binary reader utilizing FlatGeobuf header layout, packed R-tree
    spatial index, and fast selective feature streaming.
    """

    def __init__(self, connection_profile: ConnectionProfile):
        super().__init__(connection_profile)
        self.endpoint = (self.profile.endpoint or "").strip()
        self.allow_private = getattr(self.profile, "allow_private", False)
        # SSRF-safe session: every request (incl. redirects) is revalidated.
        self.session = make_safe_session(allow_private=self.allow_private)

    # ── V2 capability（诚实声明：未实现不下发）──────────────────────────

    def _capabilities_v2(self):
        """flatgeobuf 默认矩阵已诚实（bbox 投影下推、无 filter/agg 下推）。"""
        return get_capabilities("flatgeobuf")

    # ── 源解析（本地守卫 / 远程 URL）────────────────────────────────────

    def _readable_path(self) -> str:
        """返回可读路径/URL；本地路径先过 Section 44 守卫。typed 错误。"""
        if self.endpoint.startswith(_REMOTE_SCHEMES):
            if self.endpoint.startswith(("http://", "https://")):
                return DataFabricSecurity.validate_url(
                    self.endpoint, allow_private=self.allow_private
                )
            # s3:// 需要本 seam 未托管的 GDAL/vsis3 凭据环境 —— 诚实 typed
            # 失败（不做凭据探测，也不回落 fixture）。
            raise SourceUnreachableError(
                "FlatGeobuf s3:// reads require a configured GDAL/vsis3 "
                "credential environment (not managed by this seam)",
                details={
                    "hint": "expose the object over http(s), or read it via the "
                            "GeoParquet adapter (fsspec + s3fs range reads)"
                },
            )
        try:
            resolved = resolve_safe_local_path(
                self.endpoint,
                _local_file_roots_from_settings(),
                _local_file_max_bytes_from_settings(),
            )
        except DataFabricSecurityError as e:
            raise SecurityBlockedError(str(e)) from e
        if not resolved.is_file():
            raise SourceUnreachableError(
                f"FlatGeobuf source configured but not readable: {self.endpoint}"
            )
        return str(resolved)

    def probe(self) -> bool:
        """Probe FlatGeobuf file accessibility and magic signature.

        Truthfulness: no-endpoint = explicit demo mode (reachable); an endpoint
        that IS configured but points at a missing/unreadable source is NOT.
        """
        if not self.endpoint:
            return True  # explicit demo mode
        try:
            if self.endpoint.startswith(("http://", "https://")):
                safe_url = DataFabricSecurity.validate_url(self.endpoint, allow_private=self.allow_private)
                resp = self.session.get(safe_url, headers={"Range": "bytes=0-7"}, timeout=5)
                return resp.status_code in (200, 206) and resp.content.startswith(b"fgb")
            elif os.path.isfile(self.endpoint):
                with open(self.endpoint, "rb") as f:
                    header = f.read(8)
                    return header.startswith(b"fgb")
            return False
        except Exception as e:
            logger.debug(f"FlatGeobuf probe failed for {self.endpoint}: {e}")
            return False

    def capabilities(self) -> List[str]:
        return [
            "pushdown_bbox",
            "spatial_index",
            "vector_features",
            "fast_binary_scan",
            "packed_rtree",
        ]

    def list_datasets(self) -> List[Dict[str, Any]]:
        """List FlatGeobuf datasets."""
        dataset_name = os.path.basename(self.endpoint) if self.endpoint else "beijing_subway_stations"
        if not dataset_name.strip():
            dataset_name = "beijing_subway_stations"

        return [
            {
                "id": dataset_name,
                "title": f"FlatGeobuf ({dataset_name})",
                "source_type": "flatgeobuf",
                "format": "fgb",
            }
        ]

    def describe(self, dataset_id: str) -> DatasetDescriptor:
        """Fetch FlatGeobuf header metadata, geometry type, CRS, and feature count.

        审计 C2：synthetic fixture 仅存于无端点的 demo 模式（标注 is_demo）；
        真实端点读失败 → 诚实 stub（None = 未知），绝不 fixture。
        """
        if not self.endpoint:
            fixture = SYNTHETIC_FGB_FIXTURES.get(dataset_id, SYNTHETIC_FGB_FIXTURES["beijing_subway_stations"])
            fields = [{"name": k, "type": v} for k, v in fixture["columns"].items()]
            return DatasetDescriptor(
                id=dataset_id,
                title=fixture["title"],
                description=fixture["description"],
                source_type="flatgeobuf",
                geometry_type=fixture["geometry_type"],
                srs=fixture["crs"],
                bbox=fixture["bbox"],
                feature_count=fixture["feature_count"],
                fields=fields,
                schema_fields=fixture["columns"],
                metadata={
                    "spatial_index": "packed_rtree",
                    "is_demo": True,
                    "source": "synthetic-demo",
                },
            )

        error_type: Optional[str] = None
        error_message: Optional[str] = None
        file_size: Optional[int] = None
        try:
            path = self._readable_path()
            # 本地文件：魔数校验（fgb magic）
            if not path.startswith(("http://", "https://", "s3://", "minio://", "gs://")):
                with open(path, "rb") as f:
                    magic = f.read(8)
                    if not magic.startswith(b"fgb"):
                        raise SourceBadResponseError(
                            f"Invalid FlatGeobuf magic header: {magic!r}"
                        )
                file_size = os.path.getsize(path)

            # Fast pyogrio metadata inspect if available
            try:
                import pyogrio

                info = pyogrio.read_info(path)
                fields = [{"name": name, "type": str(dtype)} for name, dtype in zip(info["fields"], info["dtypes"])]
                schema_fields = {name: str(dtype) for name, dtype in zip(info["fields"], info["dtypes"])}
                bbox = list(info["total_bounds"]) if info.get("total_bounds") is not None else None
                crs = info.get("crs")
                srs = str(crs) if crs is not None else None

                return DatasetDescriptor(
                    id=dataset_id,
                    title=os.path.basename(self.endpoint),
                    description=f"FlatGeobuf dataset ({info.get('features_count', 0)} records)",
                    source_type="flatgeobuf",
                    geometry_type=info.get("geometry_type") or None,
                    srs=srs,
                    bbox=bbox,
                    feature_count=info.get("features_count"),
                    fields=fields,
                    schema_fields=schema_fields,
                    metadata={
                        "file_size": file_size,
                        "spatial_index": "packed_rtree",
                        "is_demo": False,
                    },
                )
            except DataFabricError:
                raise
            except Exception as pie:
                logger.debug(f"Fast pyogrio read_info failed for '{dataset_id}': {pie}, fallback to geopandas")

            # geopandas fallback（无 pyogrio 时）
            import geopandas as gpd

            gdf = gpd.read_file(path)
            bbox = list(gdf.total_bounds) if not gdf.empty else None
            schema_fields = {col: str(dtype) for col, dtype in gdf.dtypes.items()}
            fields = [{"name": k, "type": str(v)} for k, v in schema_fields.items()]
            geom_type = gdf.geometry.type.iloc[0] if not gdf.empty else None

            return DatasetDescriptor(
                id=dataset_id,
                title=os.path.basename(self.endpoint),
                description=f"FlatGeobuf dataset ({file_size or '?'} bytes)",
                source_type="flatgeobuf",
                geometry_type=geom_type,
                srs=str(gdf.crs) if gdf.crs else None,
                bbox=bbox,
                feature_count=len(gdf),
                fields=fields,
                schema_fields=schema_fields,
                metadata={
                    "file_size": file_size,
                    "spatial_index": "packed_rtree",
                    "is_demo": False,
                },
            )
        except DataFabricError as e:
            error_type, error_message = e.code, str(e)
            logger.warning(f"FlatGeobuf describe error for '{dataset_id}': {e}")
        except Exception as e:
            error_type, error_message = "SOURCE_BAD_RESPONSE", str(e)
            logger.warning(f"FlatGeobuf describe error for '{dataset_id}': {e}")

        # 诚实 stub：None = 未知，绝不 fixture（审计 C2）
        return DatasetDescriptor(
            id=dataset_id,
            title=dataset_id,
            description=f"FlatGeobuf dataset (descriptor unavailable: {error_message})",
            source_type="flatgeobuf",
            geometry_type=None,
            srs=None,
            bbox=None,
            feature_count=None,
            fields=[],
            metadata={"error_type": error_type, "error": error_message, "is_demo": False},
        )

    def preview(self, dataset_id: str, limit: int = 10) -> Dict[str, Any]:
        """Fetch sample features preview."""
        bounded_limit = max(1, min(limit, MAX_PREVIEW_LIMIT))
        q_spec = QuerySpec(limit=bounded_limit)
        q_res = self.query(dataset_id, q_spec)
        return {
            "schema": {"table": dataset_id, "columns": q_res.schema_info.get("columns", [])},
            "properties": q_res.features[0]["properties"] if q_res.features else {},
            "features": q_res.features[:bounded_limit],
            "bbox": q_res.schema_info.get("dataset_bbox", [-180.0, -90.0, 180.0, 90.0]),
        }

    # ── 查询主路径 ─────────────────────────────────────────────────────

    def query(self, dataset_id: str, query_spec: QuerySpec) -> QueryResult:
        """V2: normalize → plan → 有界执行。

        真实端点读失败抛 typed 错误（绝不 synthetic fixture 冒充成功）；
        无端点时进入显式 demo 模式（is_demo=True）。
        """
        started = time.monotonic()
        try:
            v2 = normalize_query_spec(query_spec)  # 失败抛 typed InvalidQueryError
        except DataFabricError:
            raise

        descriptor = self.describe(dataset_id)
        from app.services.data_fabric.fingerprint import dataset_fingerprint_service

        fp = dataset_fingerprint_service.calculate_descriptor_fingerprint(descriptor)
        caps = self._capabilities_v2()
        plan = plan_query(v2, descriptor, caps, source_id=self.profile.id, dataset_fingerprint=fp)

        if not self.endpoint:
            return self._query_demo(dataset_id, v2, plan, started, fp, descriptor)

        if v2.spatial is not None and v2.spatial.op != "bbox":
            raise InvalidQueryError(
                f"FlatGeobuf adapter supports bbox spatial filtering only (got '{v2.spatial.op}')"
            )

        try:
            path = self._readable_path()
        except DataFabricError:
            raise

        if v2.output.mode == ResultMode.DESCRIPTOR:
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
                schema_info={"columns": list(descriptor.schema_fields.keys())},
                metadata=self._metadata(plan, evidence, started, extra={
                    "is_demo": False,
                    "source": "remote",
                    "spatial_index_used": None,
                }),
            )

        try:
            return self._execute_real(dataset_id, v2, plan, started, fp, descriptor, path)
        except DataFabricError:
            raise
        except Exception as e:
            logger.warning(f"FlatGeobuf file query failed for '{dataset_id}': {e}")
            raise SourceBadResponseError(f"FlatGeobuf read failed: {e}") from e

    # ── 真实端点执行（pyogrio 优先 / geopandas 回退）───────────────────

    def _execute_real(
        self,
        dataset_id: str,
        v2: QuerySpecV2,
        plan,
        started: float,
        fp: Optional[str],
        descriptor: DatasetDescriptor,
        path: str,
    ) -> QueryResult:
        mode = v2.output.mode
        page = v2.page
        offset = page.offset if isinstance(page, OffsetPage) else 0
        query_bbox = v2.spatial.bbox if v2.spatial is not None else None
        # pyogrio 可用 → bbox/columns/max_features 下推；spatial_index_used
        # 如实为 None（文件是否含 RTree 未知，不再硬编码 True）。
        engine = "pyogrio"
        try:
            import pyogrio
        except ImportError:
            pyogrio = None
            engine = "geopandas"

        select = v2.select
        needed = self._needed_columns(v2, descriptor)

        window: int
        if mode == ResultMode.SAMPLE and v2.sample is not None:
            window = max(v2.sample.size, page.limit)
        else:
            window = page.limit
        # 读窗口 = offset + limit（+1 哨兵判定 has_more）；STATISTICS 全量有界扫描
        max_features = offset + window + 1 if mode != ResultMode.STATISTICS else v2.execution.max_rows

        if mode == ResultMode.STATISTICS:
            return self._statistics_real(
                dataset_id, v2, plan, started, path, pyogrio, needed, engine,
            )

        read_geometry = mode != ResultMode.STATISTICS
        columns_arg = needed or None
        if pyogrio is not None:
            gdf = pyogrio.read_dataframe(
                path,
                bbox=tuple(query_bbox) if query_bbox else None,
                columns=columns_arg,
                max_features=max_features,
                read_geometry=read_geometry,
            )
        else:
            import geopandas as gpd

            kwargs: Dict[str, Any] = {}
            if columns_arg:
                kwargs["columns"] = columns_arg
            if query_bbox:
                kwargs["bbox"] = tuple(query_bbox)
            # 有界读取（rows slice → GDAL skip/max features）
            kwargs["rows"] = slice(0, max_features)
            try:
                gdf = gpd.read_file(path, **kwargs)
            except TypeError:
                kwargs.pop("rows", None)
                gdf = gpd.read_file(path, **kwargs).iloc[:max_features]

        features, prop_rows = self._gdf_to_features(gdf, select)
        if v2.filter is not None:
            features = [f for f in features if evaluate_predicate(v2.filter, f["properties"])]
        if v2.temporal is not None:
            features = [f for f in features if evaluate_temporal(v2.temporal, f["properties"])]
        # 精确 bbox 兜底（引擎 bbox 过滤即包围盒级；无几何读取时跳过）
        if query_bbox is not None and read_geometry:
            features = _filter_features_by_bbox(features, query_bbox)

        budget = StreamingBudget(
            max_rows=v2.execution.max_rows,
            max_bytes=v2.execution.max_bytes,
            max_vertices=v2.execution.max_vertices,
        )
        for f in features:
            budget.add_feature(f)

        if mode == ResultMode.SAMPLE and v2.sample is not None:
            out = deterministic_sample(features, v2.sample, fp)
            truncated = False
        else:
            out = features[offset: offset + page.limit]
            truncated = len(features) > offset + page.limit

        total_matching: Optional[int] = None if truncated else offset + len(out)
        evidence = build_evidence(
            plan, started_at=started, result_count=len(out),
            total_matching=total_matching, truncated=truncated,
            rows_fetched=len(features), rows_returned=len(out),
        )
        return QueryResult(
            dataset_id=dataset_id,
            features=out,
            data={"type": "FeatureCollection", "features": out},
            total_count=len(out),
            total_matching=total_matching,
            returned_count=len(out),
            truncated=truncated,
            has_more=truncated,
            result_mode=(
                "sample" if mode == ResultMode.SAMPLE
                else ("materialize" if mode == ResultMode.MATERIALIZE else "features")
            ),
            execution_time_seconds=round(time.monotonic() - started, 4),
            schema_info={"columns": list(select) if select else list(descriptor.schema_fields.keys()), "dataset_bbox": descriptor.bbox},
            metadata=self._metadata(plan, evidence, started, extra={
                "is_demo": False,
                "source": "remote",
                "column_projection": bool(select),
                # 诚实：驱动会在文件含 RTree 时使用之，但是否含不可知 → None
                "spatial_index_used": None,
                "engine": engine,
            }),
        )

    def _statistics_real(
        self,
        dataset_id: str,
        v2: QuerySpecV2,
        plan,
        started: float,
        path: str,
        pyogrio: Any,
        needed: List[str],
        engine: str,
    ) -> QueryResult:
        """STATISTICS：本地有界聚合（零 geometry 读取）。"""
        query_bbox = v2.spatial.bbox if v2.spatial is not None else None

        # 无过滤纯 count → read_info features_count（零特征读取）
        pure_count = (
            v2.filter is None and v2.spatial is None and v2.temporal is None
            and not v2.group_by
            and bool(v2.aggregate) and all(a.func == "count" and a.field is None for a in v2.aggregate)
        )
        if pure_count and pyogrio is not None:
            info = pyogrio.read_info(path)
            rows = [{"count": int(info.get("features_count") or 0)}]
            rows_scanned = 0
        else:
            if pyogrio is not None:
                gdf = pyogrio.read_dataframe(
                    path,
                    bbox=tuple(query_bbox) if query_bbox else None,
                    columns=needed or None,
                    max_features=v2.execution.max_rows,
                    read_geometry=False,
                )
                prop_rows = gdf.to_dict(orient="records")
            else:
                import geopandas as gpd

                try:
                    gdf = gpd.read_file(
                        path,
                        bbox=tuple(query_bbox) if query_bbox else None,
                        rows=v2.execution.max_rows,
                    )
                except TypeError:
                    gdf = gpd.read_file(
                        path, bbox=tuple(query_bbox) if query_bbox else None,
                    ).iloc[: v2.execution.max_rows]
                if needed:
                    gdf = gdf[[c for c in needed if c in gdf.columns]]
                prop_rows = gdf.to_dict(orient="records")
            rows_scanned = len(prop_rows)
            if v2.filter is not None:
                prop_rows = [r for r in prop_rows if evaluate_predicate(v2.filter, r)]
            if v2.temporal is not None:
                prop_rows = [r for r in prop_rows if evaluate_temporal(v2.temporal, r)]
            rows = compute_aggregates(prop_rows, v2.aggregate or [], v2.group_by)

        evidence = build_evidence(
            plan, started_at=started, result_count=len(rows),
            rows_fetched=rows_scanned, rows_returned=len(rows),
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
                "is_demo": False,
                "source": "remote",
                "spatial_index_used": None,
                "engine": engine,
            }),
        )

    # ── 辅助 ───────────────────────────────────────────────────────────

    @staticmethod
    def _gdf_to_features(
        gdf: Any, select: Optional[List[str]]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """GeoDataFrame → (features, 属性行)。投影在属性层应用。"""
        if gdf is None or len(gdf) == 0:
            return [], []
        try:
            data = json.loads(gdf.to_json())
        except Exception:
            data = {"type": "FeatureCollection", "features": []}
        raw_features = data.get("features", []) if isinstance(data, dict) else []
        out: List[Dict[str, Any]] = []
        for f in raw_features:
            props = dict(f.get("properties") or {})
            if select is not None:
                props = {k: v for k, v in props.items() if k in select}
            out.append({
                "type": "Feature",
                "geometry": f.get("geometry"),
                "properties": props,
            })
        return out, [f["properties"] for f in out]

    def _needed_columns(self, v2: QuerySpecV2, descriptor: DatasetDescriptor) -> List[str]:
        """投影列 ∪ 谓词/时间/聚合/分组引用列。"""
        known = set(descriptor.schema_fields.keys()) | {f.get("name") for f in descriptor.fields if f.get("name")}
        names = set(v2.select or [])
        if v2.filter is not None:
            names.update(iter_fields(v2.filter))
        if v2.temporal is not None:
            names.add(v2.temporal.field)
        if v2.group_by:
            names.update(v2.group_by)
        for a in v2.aggregate or []:
            if a.field:
                names.add(a.field)
        # describe 失败（schema 未知）→ 只按请求列读取，GDAL 自行校验
        if not known:
            return sorted(names)
        return [n for n in sorted(names) if n in known]

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
        """无端点 → 显式 synthetic demo（is_demo=True；metadata 全程标注）。"""
        fixture = SYNTHETIC_FGB_FIXTURES.get(dataset_id, SYNTHETIC_FGB_FIXTURES["beijing_subway_stations"])
        mode = v2.output.mode
        select = v2.select
        budget = StreamingBudget(
            max_rows=v2.execution.max_rows,
            max_bytes=v2.execution.max_bytes,
            max_vertices=v2.execution.max_vertices,
        )

        kept: List[Dict[str, Any]] = []
        for feat in fixture["features"]:
            if v2.filter is not None and not evaluate_predicate(v2.filter, feat["properties"]):
                continue
            if v2.temporal is not None and not evaluate_temporal(v2.temporal, feat["properties"]):
                continue
            props = dict(feat["properties"])
            if select is not None:
                props = {k: v for k, v in props.items() if k in select}
            feature = {"type": "Feature", "geometry": feat["geometry"], "properties": props}
            budget.add_feature(feature)
            kept.append(feature)

        if v2.spatial is not None:
            kept = _filter_features_by_bbox(kept, v2.spatial.bbox)

        rows = [f["properties"] for f in kept]

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
                is_demo=True,
                execution_time_seconds=round(time.monotonic() - started, 4),
                schema_info={"columns": list(agg_rows[0].keys()) if agg_rows else []},
                metadata=self._metadata(plan, evidence, started, extra={
                    "is_demo": True,
                    "source": "synthetic-demo",
                    # fixture 自身声明 packed_rtree（对 synthetic 数据为真）
                    "spatial_index_used": True,
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
                is_demo=True,
                execution_time_seconds=round(time.monotonic() - started, 4),
                schema_info={"columns": list(fixture["columns"].keys())},
                metadata=self._metadata(plan, evidence, started, extra={
                    "is_demo": True,
                    "source": "synthetic-demo",
                }),
            )

        page = v2.page
        offset = page.offset if isinstance(page, OffsetPage) else 0
        if mode == ResultMode.SAMPLE and v2.sample is not None:
            out = deterministic_sample(kept, v2.sample, fp)
            truncated = False
        else:
            out = kept[offset: offset + page.limit]
            truncated = len(kept) > offset + page.limit

        total_matching: Optional[int] = None if truncated else offset + len(out)
        evidence = build_evidence(
            plan, started_at=started, result_count=len(out),
            total_matching=total_matching, truncated=truncated,
            rows_fetched=len(kept), rows_returned=len(out),
        )
        cols = list(select) if select else list(fixture["columns"].keys())
        return QueryResult(
            dataset_id=dataset_id,
            features=out,
            data={"type": "FeatureCollection", "features": out},
            total_count=len(out),
            total_matching=total_matching,
            returned_count=len(out),
            truncated=truncated,
            has_more=truncated,
            result_mode=(
                "sample" if mode == ResultMode.SAMPLE
                else ("materialize" if mode == ResultMode.MATERIALIZE else "features")
            ),
            is_demo=True,
            execution_time_seconds=round(time.monotonic() - started, 4),
            schema_info={"columns": cols},
            metadata=self._metadata(plan, evidence, started, extra={
                "is_demo": True,
                "source": "synthetic-demo",
                "spatial_index_used": True,  # fixture 声明 packed_rtree
                "column_projection": bool(select),
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
                adapter="flatgeobuf",
                message="FlatGeobuf binary data source verified",
                latency_ms=latency,
                details={"endpoint": self.endpoint or "synthetic_fixture_mode"},
            )
        return DataFabricHealth(
            status="unreachable",
            adapter="flatgeobuf",
            message=f"FlatGeobuf source inaccessible at {self.endpoint}",
            latency_ms=latency,
            details={"endpoint": self.endpoint},
        )
