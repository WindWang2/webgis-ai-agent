"""
GeoParquet Data Source Adapter — V2 (ADR-0094 Wave F)

相对 V1 的升级（审计 C2 + Wave F 契约）：
- 真实端点读失败 → typed SourceUnreachableError/SourceBadResponseError（绝不回落
  SYNTHETIC fixture 冒充真实数据，审计 C2）；synthetic fixture 仅存于无端点的
  显式 demo 模式，describe()/query() 均标注 is_demo=True / source="synthetic-demo"。
- pyarrow footer 优先：num_rows / num_row_groups / schema / ``geo`` 元数据
  （主几何列、CRS、列级 bbox）。
- 列投影 + 有界流式：``ParquetFile.iter_batches(batch_size=1024, columns=[...],
  row_groups=[...])``，绝不整表物化 GeoDataFrame。
- 行组剪枝：``geo`` covering / 几何列 chunk statistics 提供逐行组 bbox 时跳过
  不相交行组；否则读取全部行组并逐批精确 bbox 过滤（复用 postgis_adapter 的
  ``_filter_features_by_bbox``）。
- 属性/时间谓词本地求值（evaluate_predicate / evaluate_temporal，安全基线）。
- page limit/offset；SAMPLE（deterministic_sample）；STATISTICS（compute_aggregates；
  无过滤纯 count 直接取 footer num_rows，零扫描）。
- 远程端点（http/https/s3）：fsspec.open range read（SSRF 校验先行）；缺
  fsspec → typed SourceUnreachableError + hint（诚实失败，无 fixture）。
- V2：normalize → plan → 执行；QueryResult 附 plan/evidence。

capability 诚实声明（V1 教训 "GeoParquet lazy_batching"）：本 wave 属性谓词为
本地求值、统计不做 parquet statistics 下推、空间仅 bbox → 相应覆盖为 False /
["bbox"]，未实现的能力绝不声明。
"""
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.services.data_fabric import vector_carrier
from app.services.data_fabric.arrow_ops import (
    ArrowPredicateUnsupported,
    arrow_aggregate_batches,
    bbox_filter_expression,
    build_predicate_expression,
    covering_bbox_columns,
)
from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
from app.services.data_fabric.adapters.postgis_adapter import (
    _filter_features_by_bbox,
    _geom_bounds,
)
from app.services.data_fabric.errors import (
    DataFabricError,
    InvalidQueryError,
    QueryBudgetExceededError,
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

MAX_PREVIEW_LIMIT = 50
MAX_QUERY_LIMIT = 5000
BATCH_SIZE = 1024
_REMOTE_SCHEMES = ("http://", "https://", "s3://", "minio://", "gs://")

SYNTHETIC_GEOPARQUET_FIXTURES: Dict[str, Dict[str, Any]] = {
    "us_states_geoparquet": {
        "dataset_id": "us_states_geoparquet",
        "title": "US State Boundaries GeoParquet",
        "description": "Synthetic GeoParquet table containing state boundary polygons and demographic attributes.",
        "feature_count": 50,
        "bbox": [-125.0, 24.5, -66.9, 49.3],
        "columns": {
            "state_code": "string",
            "state_name": "string",
            "population": "int64",
            "area_sqkm": "float64",
            "geometry": "geometry",
        },
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-124.4, 42.0], [-116.5, 42.0], [-116.5, 46.3], [-124.4, 46.3], [-124.4, 42.0]]],
                },
                "properties": {
                    "state_code": "OR",
                    "state_name": "Oregon",
                    "population": 4240000,
                    "area_sqkm": 254799.0,
                },
            },
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-124.5, 32.5], [-114.1, 32.5], [-114.1, 42.0], [-124.5, 42.0], [-124.5, 32.5]]],
                },
                "properties": {
                    "state_code": "CA",
                    "state_name": "California",
                    "population": 39000000,
                    "area_sqkm": 423970.0,
                },
            },
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-74.3, 40.5], [-73.7, 40.5], [-73.7, 45.0], [-74.3, 45.0], [-74.3, 40.5]]],
                },
                "properties": {
                    "state_code": "NY",
                    "state_name": "New York",
                    "population": 19600000,
                    "area_sqkm": 141297.0,
                },
            },
        ],
    }
}


def _wkb_to_geojson(raw: Any) -> Optional[Dict[str, Any]]:
    """GeoParquet geometry（WKB bytes / shapely 对象）→ GeoJSON dict。

    shapely 不可用或值不可解码时返回 None（诚实缺省，不伪造几何）。
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    geo_iface = getattr(raw, "__geo_interface__", None)
    if geo_iface is not None:
        try:
            return dict(geo_iface)
        except Exception:
            return None
    if isinstance(raw, (bytes, bytearray, memoryview)):
        try:
            from shapely import wkb as _shapely_wkb

            geom = _shapely_wkb.loads(bytes(raw))
            return dict(geom.__geo_interface__)
        except Exception:
            return None
    return None


class GeoParquetAdapter(GeospatialDataSourceAdapter):
    """
    GeoParquet Data Fabric Adapter (V2):
    High-performance Parquet adapter featuring column projection, row-group
    pruning, bbox selective pushdown, and bounded lazy record batching.
    """

    def __init__(self, connection_profile: ConnectionProfile):
        super().__init__(connection_profile)
        self.endpoint = (self.profile.endpoint or "").strip()
        self.allow_private = getattr(self.profile, "allow_private", False)
        # SSRF-safe session: every request (incl. redirects) is revalidated.
        self.session = make_safe_session(allow_private=self.allow_private)

    # ── V2 capability（诚实声明：未实现不下发）──────────────────────────

    def _capabilities_v2(self):
        """默认矩阵收紧：属性谓词/统计为本地实现、空间仅 bbox（Wave F）。"""
        return get_capabilities("geoparquet", overrides={
            "filter_pushdown": False,        # 本地逐行求值（安全基线），非 parquet 下推
            "statistics": False,             # 统计在本地 batch 流上计算
            "spatial_predicates": ["bbox"],  # 仅 bbox 包围盒级剪枝/过滤
        })

    # ── 源打开（本地守卫 / 远程 fsspec）─────────────────────────────────

    def _open_source(self) -> Tuple[Any, Any]:
        """返回 (pq.ParquetFile 可用源, 需关闭的 handle 或 None)。

        本地路径先过 Section 44 守卫（traversal / symlink escape / 敏感目录 /
        超限），守卫拒绝 → SecurityBlockedError；文件缺失 →
        SourceUnreachableError；远程 http/https 经 SSRF 校验后走 fsspec range
        read，fsspec 缺失 → SourceUnreachableError + 安装提示。
        """
        if self.endpoint.startswith(_REMOTE_SCHEMES):
            url = self.endpoint
            if url.startswith(("http://", "https://")):
                url = DataFabricSecurity.validate_url(url, allow_private=self.allow_private)
            try:
                import fsspec
            except ImportError as e:
                raise SourceUnreachableError(
                    "remote GeoParquet read requires fsspec",
                    details={"hint": "install fsspec for remote GeoParquet"},
                ) from e
            if url.startswith(("s3://", "minio://")):
                # fsspec 的 s3 后端由 s3fs 提供；缺失时立即诚实失败
                #（避免 fsspec 内部错误类型差异 / 网络尝试）。
                try:
                    import s3fs  # noqa: F401
                except ImportError as e:
                    raise SourceUnreachableError(
                        "s3:// GeoParquet read requires fsspec + s3fs",
                        details={"hint": "install fsspec and s3fs for remote GeoParquet"},
                    ) from e
            try:
                of = fsspec.open(url, "rb")
                fobj = of.open()
                return fobj, fobj
            except DataFabricError:
                raise
            except Exception as e:
                raise SourceUnreachableError(
                    f"GeoParquet remote source not readable: {e}",
                    details={"hint": "install fsspec for remote GeoParquet"},
                ) from e
        # 本地路径：Section 44 守卫
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
                f"GeoParquet source configured but not readable: {self.endpoint}"
            )
        return str(resolved), None

    def probe(self) -> bool:
        """Probe GeoParquet file accessibility and magic header.

        Truthfulness: no-endpoint = explicit demo mode (reachable). An endpoint
        that IS configured but points at a missing/unreadable source is NOT
        reachable — previously the combined condition returned True for that case.
        """
        if not self.endpoint:
            return True  # explicit demo mode
        try:
            if self.endpoint.startswith(("http://", "https://")):
                safe_url = DataFabricSecurity.validate_url(self.endpoint, allow_private=self.allow_private)
                resp = self.session.head(safe_url, timeout=5)
                return resp.status_code in (200, 206)
            elif os.path.isfile(self.endpoint):
                with open(self.endpoint, "rb") as f:
                    magic = f.read(4)
                    return magic == b"PAR1"
            # Endpoint configured but neither a real local file nor http URL.
            return False
        except Exception as e:
            logger.debug(f"GeoParquet probe failed for {self.endpoint}: {e}")
            return False

    def capabilities(self) -> List[str]:
        return [
            "pushdown_bbox",
            "column_projection",
            "vector_features",
            "parquet_metadata",
            "lazy_batching",
        ]

    def list_datasets(self) -> List[Dict[str, Any]]:
        """Discover available GeoParquet datasets."""
        dataset_name = os.path.basename(self.endpoint) if self.endpoint else "us_states_geoparquet"
        if not dataset_name.strip():
            dataset_name = "us_states_geoparquet"

        return [
            {
                "id": dataset_name,
                "title": f"GeoParquet Data ({dataset_name})",
                "source_type": "geoparquet",
                "format": "parquet",
            }
        ]

    # ── describe ───────────────────────────────────────────────────────

    def describe(self, dataset_id: str) -> DatasetDescriptor:
        """Fetch Parquet footer metadata, columns, and geo metadata.

        审计 C2：synthetic fixture 仅存于无端点的 demo 模式（标注 is_demo）；
        真实端点读失败 → 诚实 stub（srs/bbox/feature_count=None = 未知），
        绝不把 fixture 元数据伪装成远端真实数据。
        """
        if not self.endpoint:
            fixture = SYNTHETIC_GEOPARQUET_FIXTURES.get(dataset_id, SYNTHETIC_GEOPARQUET_FIXTURES["us_states_geoparquet"])
            fields = [{"name": k, "type": v} for k, v in fixture["columns"].items()]
            return DatasetDescriptor(
                id=dataset_id,
                title=fixture["title"],
                description=fixture["description"],
                source_type="geoparquet",
                geometry_type="MultiPolygon",
                srs="EPSG:4326",
                bbox=fixture["bbox"],
                feature_count=fixture["feature_count"],
                fields=fields,
                schema_fields=fixture["columns"],
                metadata={
                    "geo": {"version": "1.0.0", "primary_column": "geometry"},
                    "is_demo": True,
                    "source": "synthetic-demo",
                },
            )

        try:
            src, closer = self._open_source()
            try:
                return self._describe_pyarrow(dataset_id, src)
            finally:
                if closer is not None:
                    try:
                        closer.close()
                    except Exception:
                        pass
        except DataFabricError as e:
            logger.warning(f"GeoParquet describe error for '{dataset_id}': {e}")
            return self._honest_stub(dataset_id, e.code, str(e))
        except Exception as e:
            logger.warning(f"GeoParquet describe error for '{dataset_id}': {e}")
            return self._honest_stub(dataset_id, "SOURCE_BAD_RESPONSE", str(e))

    def _honest_stub(self, dataset_id: str, error_type: str, message: str) -> DatasetDescriptor:
        """真实端点 describe 失败的诚实 stub：None = 未知，绝不 fixture。"""
        return DatasetDescriptor(
            id=dataset_id,
            title=dataset_id,
            description=f"GeoParquet dataset (descriptor unavailable: {message})",
            source_type="geoparquet",
            geometry_type=None,
            srs=None,
            bbox=None,
            feature_count=None,
            fields=[],
            metadata={"error_type": error_type, "error": message, "is_demo": False},
        )

    def _describe_pyarrow(self, dataset_id: str, src: Any) -> DatasetDescriptor:
        """Footer-only 元数据（不扫描数据页）。"""
        import pyarrow.parquet as pq

        pf = pq.ParquetFile(src)
        num_rows = pf.metadata.num_rows
        schema_fields = {field.name: str(field.type) for field in pf.schema_arrow}
        fields = [{"name": field.name, "type": str(field.type)} for field in pf.schema_arrow]

        geo_meta = self._read_geo_meta(pf)
        primary_geom = (geo_meta.get("primary_column") or "geometry") if geo_meta else "geometry"
        col_meta = (geo_meta.get("columns") or {}).get(primary_geom, {}) if geo_meta else {}
        # 诚实默认：geo 元数据未声明 → None（未知），绝不伪造全球 bbox/EPSG:4326
        bbox = col_meta.get("bbox") or None
        crs_info = col_meta.get("crs")
        crs_str = crs_info.get("name") if isinstance(crs_info, dict) else None
        geom_types = col_meta.get("geometry_types") or []
        geom_type = next((g for g in geom_types if g), None)

        return DatasetDescriptor(
            id=dataset_id,
            title=os.path.basename(self.endpoint),
            description=f"GeoParquet table with {num_rows} records",
            source_type="geoparquet",
            geometry_type=geom_type,
            srs=crs_str,
            bbox=bbox,
            feature_count=num_rows,
            fields=fields,
            schema_fields=schema_fields,
            metadata={
                "geo": geo_meta,
                "primary_geometry_column": primary_geom if primary_geom in schema_fields else None,
                "num_row_groups": pf.metadata.num_row_groups,
                # ADR-0101 D6：footer 事实进入统计管道 —— collector 显式标注
                # （V3 声明了 geoparquet_footer 收集器但没有生产者）；列只证明
                # 存在（null/NDV 未知 = assumption）；footer 是文件内容元数据，
                # 内容变 → 指纹变 → strong。
                "stats_collector": "geoparquet_footer",
                "revision_strength": "strong",
                "row_count": int(num_rows),
                "column_statistics": [
                    {"name": f.get("name"), "confidence": "assumption"}
                    for f in fields if isinstance(f.get("name"), str)
                ][:128],
                "is_demo": False,
            },
        )

    @staticmethod
    def _read_geo_meta(pf: Any) -> Dict[str, Any]:
        md = pf.metadata.metadata or {}
        raw = md.get(b"geo")
        if not raw:
            return {}
        try:
            parsed = json.loads(raw.decode("utf-8"))
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _result_crs(geo_meta: Dict[str, Any], primary_geom: str) -> Optional[str]:
        """geo 元数据 → 结果面 CRS 字符串（round-1 review MAJOR）。

        materialize（geoparquet）分支从 ``schema_info["crs"]`` 取值编码进
        Arrow/GeoParquet schema —— 此前没有任何 adapter 写这个键，投影 CRS
        在物化时静默丢失。同时接受 PROJJSON（取 ``name``）与字符串两种
        携带形态（与 _describe_pyarrow 同源 geo_meta，但字符串形态不再被
        丢弃）。未知 → None（诚实，绝不伪造 EPSG:4326）。
        """
        try:
            col_meta = (geo_meta.get("columns") or {}).get(primary_geom) or {}
            crs_info = col_meta.get("crs")
            if isinstance(crs_info, dict):
                # schema_info["crs"] 契约是 str：优先 "AUTHORITY:code"（机器
                # 可解释，PROJ 可解析），其次 PROJJSON name（叙述性兜底）。
                # round-2 review：折叠为纯 name 对严格 GeoParquet 读者不可解释。
                crs_id = crs_info.get("id")
                if isinstance(crs_id, dict) and crs_id.get("code") is not None:
                    authority = crs_id.get("authority") or "EPSG"
                    return f"{authority}:{crs_id['code']}"
                return crs_info.get("name") or None
            return str(crs_info) if crs_info else None
        except Exception:
            return None

    def preview(self, dataset_id: str, limit: int = 10) -> Dict[str, Any]:
        """Fetch sample records with bounded limit."""
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

    def query(
        self,
        dataset_id: str,
        query_spec: QuerySpec,
        *,
        cancel_token: Optional[Any] = None,
    ) -> QueryResult:
        """V2: normalize → plan → 有界流式执行。

        真实端点读失败抛 typed SourceUnreachableError/SourceBadResponseError
        （审计 C2：绝不以 synthetic fixture 冒充成功）；无端点时进入显式
        demo 模式（is_demo=True）。
        ``cancel_token``（可选，``app.lib.cancellation.CancellationToken``）
        在 Arrow fast lane 的每个批次边界协作检查（Wave 5 批边界取消）。
        """
        started = time.monotonic()
        try:
            v2 = normalize_query_spec(query_spec)  # 失败抛 typed InvalidQueryError
        except DataFabricError:
            raise

        if not self.endpoint:
            return self._query_demo(dataset_id, v2, started)

        descriptor = self.describe(dataset_id)
        from app.services.data_fabric.fingerprint import dataset_fingerprint_service

        fp = dataset_fingerprint_service.calculate_descriptor_fingerprint(descriptor)
        caps = self._capabilities_v2()
        from app.services.data_fabric.query.statistics import statistics_for_request

        plan = plan_query(
            v2, descriptor, caps, source_id=self.profile.id, dataset_fingerprint=fp,
            stats=statistics_for_request(descriptor, fp),
        )

        if v2.output.mode == ResultMode.DESCRIPTOR:
            evidence = build_evidence(
                plan, started_at=started, result_count=0, dataset_version=None,
            )
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
                metadata=self._metadata(plan, evidence, started, extra={"is_demo": False}),
            )

        from app.lib.cancellation import OperationCancelled

        on_batch = None
        if cancel_token is not None:
            from app.services.data_fabric.streaming import batch_checkpoint_hook

            on_batch = batch_checkpoint_hook(cancel_token)

        src, closer = self._open_source()  # typed 错误（安全/可达性）
        try:
            try:
                import pyarrow.parquet as pq  # noqa: F401
                pf = pq.ParquetFile(src)
                return self._execute_pyarrow(
                    dataset_id, v2, plan, pf, started, fp, descriptor, on_batch=on_batch)
            except ImportError:
                # pyarrow 不可用 → geopandas 高层 API（其自身依赖 pyarrow，
                # 仍不可用则如实失败，绝不回落 fixture）。
                return self._execute_geopandas(dataset_id, v2, plan, started, fp, descriptor, src)
        except DataFabricError:
            raise
        except OperationCancelled:
            # 协作式取消是调用方意志，不是源错误 —— 绝不二次包装。
            raise
        except Exception as e:
            logger.warning(f"GeoParquet file query failed for '{dataset_id}': {e}")
            raise SourceBadResponseError(f"GeoParquet read failed: {e}") from e
        finally:
            if closer is not None:
                try:
                    closer.close()
                except Exception:
                    pass

    # ── pyarrow 有界流式执行 ───────────────────────────────────────────

    def _execute_pyarrow(
        self,
        dataset_id: str,
        v2: QuerySpecV2,
        plan,
        pf: Any,
        started: float,
        fp: Optional[str],
        descriptor: DatasetDescriptor,
        *,
        on_batch: Optional[Any] = None,
    ) -> QueryResult:
        meta = pf.metadata
        schema_names = list(pf.schema_arrow.names)
        geo_meta = self._read_geo_meta(pf)
        primary_geom = (geo_meta.get("primary_column") or "geometry") if geo_meta else "geometry"
        has_geom = primary_geom in schema_names

        query_bbox = self._require_bbox(v2)
        mode = v2.output.mode

        # ---- 行组剪枝（geo covering / 几何列 statistics；无统计则全读）----
        row_groups, pruned = self._row_group_plan(pf, geo_meta, primary_geom, query_bbox)

        # ---- 需要读取的列（投影 ∪ 谓词/聚合/分组引用字段）----
        emit_select = v2.select
        needed = self._needed_columns(v2, schema_names)

        def _columns_arg(include_geometry: bool) -> Optional[List[str]]:
            # 无投影（select=None）：FEATURES/SAMPLE 读全部列（谓词字段自然包含）；
            # STATISTICS 仍按需裁剪（零 geometry 传输语义）。
            if v2.select is None and mode != ResultMode.STATISTICS:
                return None
            cols = list(needed)
            if include_geometry and has_geom and primary_geom not in cols:
                cols.append(primary_geom)
            # 全列读取时传 None（pyarrow 语义：全部列）
            if set(cols) >= set(schema_names):
                return None
            return cols or None

        read_geometry = has_geom and (
            mode in (ResultMode.FEATURES, ResultMode.SAMPLE, ResultMode.MATERIALIZE)
            or query_bbox is not None
        )
        columns_arg = _columns_arg(include_geometry=read_geometry)

        budget = StreamingBudget(
            max_rows=v2.execution.max_rows,
            max_bytes=v2.execution.max_bytes,
            max_vertices=v2.execution.max_vertices,
        )

        # ---- Wave 5：Arrow fast lane 资格判定（属性-only 下推）----
        fast_lane = self._fast_lane_plan(v2, pf, schema_names, geo_meta, query_bbox)

        # ---- STATISTICS：无过滤纯 count → footer num_rows（零扫描）----
        if mode == ResultMode.STATISTICS:
            pure_count = (
                v2.filter is None and v2.spatial is None and v2.temporal is None
                and not v2.group_by
                and bool(v2.aggregate) and all(a.func == "count" and a.field is None for a in v2.aggregate)
            )
            if pure_count:
                # footer 零扫描短路优先于任何 lane（fast lane 绝不接管它）。
                return self._statistics_from_stream(
                    dataset_id, v2, plan, pf, started, fp, descriptor,
                    columns_arg=_columns_arg(include_geometry=query_bbox is not None),
                    row_groups=row_groups, pruned=pruned, budget=budget,
                    footer_num_rows=meta.num_rows,
                )
            if fast_lane is not None:
                attr_expr, bbox_expr, covering_cols = fast_lane
                fast_columns = _columns_arg(include_geometry=False)
                fast_columns = self._with_covering_columns(
                    fast_columns, covering_cols, schema_names)
                return self._statistics_arrow_lane(
                    dataset_id, v2, plan, pf, started, fp, descriptor, meta,
                    row_groups, pruned, budget, attr_expr, bbox_expr,
                    fast_columns, on_batch)
            return self._statistics_from_stream(
                dataset_id, v2, plan, pf, started, fp, descriptor,
                columns_arg=_columns_arg(include_geometry=query_bbox is not None),
                row_groups=row_groups, pruned=pruned, budget=budget,
                footer_num_rows=meta.num_rows,
            )

        if fast_lane is not None:
            attr_expr, bbox_expr, covering_cols = fast_lane
            # 快车道 bbox 走 covering 列（pa.compute），几何列仅为输出而读。
            fast_columns = _columns_arg(
                include_geometry=has_geom and mode in (
                    ResultMode.FEATURES, ResultMode.SAMPLE, ResultMode.MATERIALIZE))
            fast_columns = self._with_covering_columns(
                fast_columns, covering_cols, schema_names)
            return self._features_arrow_lane(
                dataset_id, v2, plan, pf, started, fp, descriptor, meta,
                primary_geom, schema_names, row_groups, pruned, budget,
                attr_expr, bbox_expr, fast_columns, emit_select, mode,
                offset=(v2.page.offset if isinstance(v2.page, OffsetPage) else 0),
                window=(v2.sample.size if (mode == ResultMode.SAMPLE and v2.sample)
                        else v2.page.limit),
                on_batch=on_batch,
            )

        # ---- SAMPLE / FEATURES / MATERIALIZE（dict lane，原样保留）----
        page = v2.page
        offset = page.offset if isinstance(page, OffsetPage) else 0
        window = v2.sample.size if (mode == ResultMode.SAMPLE and v2.sample) else page.limit
        collected: List[Dict[str, Any]] = []
        # FEATURES：读到 offset+limit+1（哨兵判定 has_more）即停；
        # SAMPLE：reservoir 需要尽量多的匹配行 → 受执行预算约束。
        scan_cap = (
            max(window, v2.execution.max_rows)
            if mode == ResultMode.SAMPLE
            else offset + window + 1
        )

        batches = pf.iter_batches(batch_size=BATCH_SIZE, columns=columns_arg, row_groups=row_groups or None)
        for batch in batches:
            for row in batch.to_pylist():
                props, geometry = self._split_row(row, primary_geom)
                if not self._row_passes(v2, props, geometry, query_bbox):
                    continue
                feature = {
                    "type": "Feature",
                    "geometry": geometry,
                    "properties": self._emit_properties(props, emit_select, primary_geom),
                }
                budget.add_feature(feature)
                collected.append(feature)
                if len(collected) >= scan_cap:
                    break
            if len(collected) >= scan_cap:
                break

        if mode == ResultMode.SAMPLE and v2.sample is not None:
            collected = deterministic_sample(collected, v2.sample, fp)

        out = collected[offset: offset + window] if mode != ResultMode.SAMPLE else collected
        truncated = len(collected) > (offset + window) if mode != ResultMode.SAMPLE else False
        total_matching: Optional[int] = None
        if not truncated:
            total_matching = offset + len(out)

        evidence = build_evidence(
            plan, started_at=started, result_count=len(out),
            total_matching=total_matching, truncated=truncated,
            rows_fetched=len(collected), rows_returned=len(out),
        )
        non_geom_cols = [c for c in (emit_select or [c for c in schema_names if c != primary_geom])]
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
            schema_info={
                "columns": non_geom_cols,
                "dataset_bbox": descriptor.bbox,
                # round-1 review MAJOR：CRS 进结果面 —— materialize(geoparquet)
                # 由此把源 CRS 编码进 Arrow/GeoParquet schema（不再静默丢失）。
                "crs": self._result_crs(geo_meta, primary_geom),
            },
            metadata=self._metadata(plan, evidence, started, extra={
                "is_demo": False,
                "source": "remote",
                "column_projection": bool(v2.select),
                "num_rows": meta.num_rows,
                "num_row_groups": meta.num_row_groups,
                "row_groups_read": len(row_groups),
                "row_groups_pruned": pruned,
                "execution_lane": "dict",
            }),
        )

    # ── Wave 5：Arrow fast lane（属性-only 下推）───────────────────────

    def _fast_lane_plan(
        self,
        v2: QuerySpecV2,
        pf: Any,
        schema_names: List[str],
        geo_meta: Dict[str, Any],
        query_bbox: Optional[List[float]],
    ) -> Optional[Tuple[Any, Any, List[str]]]:
        """Arrow fast lane 资格判定 → (attr_expr, bbox_expr, covering 列) 或 None。

        属性-only 快车道：过滤谓词全部可在 pa.compute 下**等价**执行
        （无时间谓词）、空间仅 bbox 且文件带逐行 bbox covering 列、输出
        为列选择/标量聚合（本 adapter 本就无几何变换算子）。任何不支持的
        形状 → None（诚实回落 dict lane，绝不做近似下推）。
        """
        if not vector_carrier.arrow_available():
            return None
        if v2.temporal is not None:
            return None  # 时间谓词不在 Arrow 等价集内
        attr_expr = None
        if v2.filter is not None:
            try:
                attr_expr = build_predicate_expression(v2.filter, pf.schema_arrow)
            except ArrowPredicateUnsupported:
                return None
            except Exception:  # noqa: BLE001 - 谓词构建意外失败 → 诚实回落
                return None
        bbox_expr = None
        covering_cols: List[str] = []
        if query_bbox is not None:
            covering = covering_bbox_columns(geo_meta)
            if covering is None:
                return None
            schema_set = set(schema_names)
            if not all(covering[k] in schema_set for k in ("xmin", "ymin", "xmax", "ymax")):
                return None
            try:
                bbox_expr = bbox_filter_expression(covering, query_bbox)
            except ArrowPredicateUnsupported:
                return None
            except ValueError:
                return None
            covering_cols = [covering[k] for k in ("xmin", "ymin", "xmax", "ymax")]
        return (attr_expr, bbox_expr, covering_cols)

    @staticmethod
    def _with_covering_columns(
        columns_arg: Optional[List[str]],
        covering_cols: List[str],
        schema_names: List[str],
    ) -> Optional[List[str]]:
        """covering 列并入读取集（bbox pa.compute 过滤需要它们；None = 全列）。"""
        if not covering_cols:
            return columns_arg
        if columns_arg is None:
            return None  # 全列读取已包含 covering
        cols = list(columns_arg)
        for c in covering_cols:
            if c in schema_names and c not in cols:
                cols.append(c)
        if set(cols) >= set(schema_names):
            return None
        return cols

    @staticmethod
    def _combine_expressions(attr_expr: Any, bbox_expr: Any) -> Any:
        import pyarrow.compute as pc

        if attr_expr is None:
            return bbox_expr
        if bbox_expr is None:
            return attr_expr
        # Kleene AND：bbox covering 为 null（缺几何）→ 行丢弃，与 dict lane
        # 的 `_row_passes`（无几何行不保留）一致。
        return pc.and_kleene(attr_expr, bbox_expr)

    @staticmethod
    def _table_to_features(
        table: Any,
        primary_geom: Optional[str],
        emit_select: Optional[Sequence[str]],
    ) -> List[Dict[str, Any]]:
        """批边界一次性 Arrow → feature dict 转换（列式 to_pylist 一次，
        谓词/剪枝已在 pa.compute 完成 —— 流中绝不逐行 to_pylist）。"""
        names = [c for c in table.column_names if c != primary_geom]
        num_rows = table.num_rows
        if primary_geom in table.column_names:
            geom_col = table.column(primary_geom).to_pylist()
        else:
            geom_col = [None] * num_rows
        pycols = {n: table.column(n).to_pylist() for n in names}
        out: List[Dict[str, Any]] = []
        for i in range(num_rows):
            props = {n: pycols[n][i] for n in names}
            out.append({
                "type": "Feature",
                "geometry": _wkb_to_geojson(geom_col[i]),
                "properties": GeoParquetAdapter._emit_properties(props, emit_select, primary_geom),
            })
        return out

    def _features_arrow_lane(
        self,
        dataset_id: str,
        v2: QuerySpecV2,
        plan,
        pf: Any,
        started: float,
        fp: Optional[str],
        descriptor: DatasetDescriptor,
        meta: Any,
        primary_geom: str,
        schema_names: List[str],
        row_groups: List[int],
        pruned: int,
        budget: StreamingBudget,
        attr_expr: Any,
        bbox_expr: Any,
        columns_arg: Optional[List[str]],
        emit_select: Optional[Sequence[str]],
        mode: ResultMode,
        *,
        offset: int,
        window: int,
        on_batch: Optional[Any],
    ) -> QueryResult:
        """FEATURES/MATERIALIZE/SAMPLE 快车道：谓词在 pa.compute 上执行，
        feature dict 只在批边界一次性转换（Wave 5）。"""
        import pyarrow as pa

        expr = self._combine_expressions(attr_expr, bbox_expr)
        collected: List[Dict[str, Any]] = []
        # FEATURES：读到 offset+limit+1（哨兵判定 has_more）即停；
        # SAMPLE：reservoir 需要尽量多的匹配行 → 受执行预算约束。
        scan_cap = (
            max(window, v2.execution.max_rows)
            if mode == ResultMode.SAMPLE
            else offset + window + 1
        )

        batches = pf.iter_batches(batch_size=BATCH_SIZE, columns=columns_arg, row_groups=row_groups or None)
        for idx, batch in enumerate(batches, start=1):
            if on_batch is not None:
                on_batch(idx)  # 批边界取消/deadline 协作点
            if expr is not None:
                batch = batch.filter(expr)
            if batch.num_rows == 0:
                continue
            table = pa.Table.from_batches([batch])
            for feature in self._table_to_features(table, primary_geom, emit_select):
                budget.add_feature(feature)
                collected.append(feature)
                if len(collected) >= scan_cap:
                    break
            if len(collected) >= scan_cap:
                break

        if mode == ResultMode.SAMPLE and v2.sample is not None:
            collected = deterministic_sample(collected, v2.sample, fp)

        out = collected[offset: offset + window] if mode != ResultMode.SAMPLE else collected
        truncated = len(collected) > (offset + window) if mode != ResultMode.SAMPLE else False
        total_matching: Optional[int] = None
        if not truncated:
            total_matching = offset + len(out)

        evidence = build_evidence(
            plan, started_at=started, result_count=len(out),
            total_matching=total_matching, truncated=truncated,
            rows_fetched=len(collected), rows_returned=len(out),
        )
        non_geom_cols = [c for c in (emit_select or [c for c in schema_names if c != primary_geom])]
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
            schema_info={
                "columns": non_geom_cols,
                "dataset_bbox": descriptor.bbox,
                # 与 dict lane 同款：CRS 进结果面（materialize 源）。
                "crs": self._result_crs(self._read_geo_meta(pf), primary_geom),
            },
            metadata=self._metadata(plan, evidence, started, extra={
                "is_demo": False,
                "source": "remote",
                "column_projection": bool(v2.select),
                "num_rows": meta.num_rows,
                "num_row_groups": meta.num_row_groups,
                "row_groups_read": len(row_groups),
                "row_groups_pruned": pruned,
                "execution_lane": "arrow",
            }),
        )

    def _statistics_arrow_lane(
        self,
        dataset_id: str,
        v2: QuerySpecV2,
        plan,
        pf: Any,
        started: float,
        fp: Optional[str],
        descriptor: DatasetDescriptor,
        meta: Any,
        row_groups: List[int],
        pruned: int,
        budget: StreamingBudget,
        attr_expr: Any,
        bbox_expr: Any,
        columns_arg: Optional[List[str]],
        on_batch: Optional[Any],
    ) -> QueryResult:
        """STATISTICS 快车道：过滤 + 聚合全程 Arrow 列式，聚合语义走统一
        累加器（``arrow_ops.arrow_aggregate_batches`` —— 与 dict lane 的
        compute_aggregates 同一真相）。"""
        expr = self._combine_expressions(attr_expr, bbox_expr)
        scanned = {"rows": 0}

        def _filtered_batches():
            for idx, batch in enumerate(
                pf.iter_batches(batch_size=BATCH_SIZE, columns=columns_arg,
                                row_groups=row_groups or None),
                start=1,
            ):
                if on_batch is not None:
                    on_batch(idx)
                if expr is not None:
                    batch = batch.filter(expr)
                if batch.num_rows == 0:
                    continue
                scanned["rows"] += batch.num_rows
                budget.rows = scanned["rows"]
                budget.bytes += batch.nbytes
                # 行/字节预算守卫与 dict lane 同阈值（批粒度检查）。
                if budget.rows > budget.max_rows:
                    raise QueryBudgetExceededError(
                        f"row budget exceeded ({budget.max_rows})",
                        details={"hint": "reduce limit, add filters/bbox, or use aggregation"},
                    )
                if budget.bytes > budget.max_bytes:
                    raise QueryBudgetExceededError(
                        f"byte budget exceeded ({budget.max_bytes})",
                        details={"hint": "project fewer fields or narrow the spatial filter"},
                    )
                yield batch

        rows = arrow_aggregate_batches(_filtered_batches(), v2.aggregate or [], v2.group_by)

        evidence = build_evidence(
            plan, started_at=started, result_count=len(rows),
            total_matching=None,
            rows_fetched=scanned["rows"], rows_returned=len(rows),
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
                "footer_count_used": False,
                "row_groups_pruned": pruned,
                "execution_lane": "arrow",
            }),
        )

    def _statistics_from_stream(
        self,
        dataset_id: str,
        v2: QuerySpecV2,
        plan,
        pf: Any,
        started: float,
        fp: Optional[str],
        descriptor: DatasetDescriptor,
        *,
        columns_arg: Optional[List[str]],
        row_groups: List[int],
        pruned: int,
        budget: StreamingBudget,
        footer_num_rows: int,
    ) -> QueryResult:
        """STATISTICS：聚合在投影后的属性行流上本地计算（零 geometry 传输）。"""
        pure_count = (
            v2.filter is None and v2.spatial is None and v2.temporal is None
            and not v2.group_by
            and bool(v2.aggregate) and all(a.func == "count" and a.field is None for a in v2.aggregate)
        )
        if pure_count:
            rows = [{"count": footer_num_rows}]
            rows_scanned = 0
        else:
            rows: List[Dict[str, Any]] = []
            query_bbox = self._require_bbox(v2)
            primary_geom = (self._read_geo_meta(pf).get("primary_column") or "geometry")
            schema_names = list(pf.schema_arrow.names)
            for batch in pf.iter_batches(batch_size=BATCH_SIZE, columns=columns_arg, row_groups=row_groups or None):
                for row in batch.to_pylist():
                    props, geometry = self._split_row(row, primary_geom if primary_geom in schema_names else None)
                    if not self._row_passes(v2, props, geometry, query_bbox):
                        continue
                    budget.add_feature({"type": "Feature", "geometry": geometry, "properties": props})
                    rows.append(props)
            rows_scanned = len(rows)
            rows = compute_aggregates(rows, v2.aggregate or [], v2.group_by)

        evidence = build_evidence(
            plan, started_at=started, result_count=len(rows),
            total_matching=footer_num_rows if pure_count else None,
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
                "footer_count_used": pure_count,
                "row_groups_pruned": pruned,
                "execution_lane": "dict",
            }),
        )

    # ── 行过滤 / 投影辅助 ──────────────────────────────────────────────

    def iter_query_arrow_batches(self, dataset_id: str, query_spec: QuerySpec):
        """V7 联邦 Arrow 通道（ADR-0119 W6）：RecordBatch 级有界流式扫描。

        与 dict lane（``query``）的语义边界（诚实声明）：
        - 行组剪枝（bbox covering/statistics）+ 列投影在**本通道**生效；
        - 属性谓词/offset/limit **不在此编译** —— 由 fabric arrow_lane 用
          同一谓词 AST 在解码行上求值（与 dict lane 同一 Kleene 语义）；
        - pyarrow 缺失 → typed ``VectorCarrierUnavailable``（调用方回落
          dict lane，绝不假装）；
        - 生成器逐批 yield，调用方侧取消/预算检查；读异常包装为 typed
          SourceBadResponseError。
        """
        from app.services.data_fabric.errors import SourceBadResponseError
        from app.services.data_fabric.vector_carrier import VectorCarrierUnavailable

        try:
            import pyarrow.parquet as pq
        except ImportError:
            raise VectorCarrierUnavailable(
                "iter_query_arrow_batches requires the optional 'pyarrow' "
                "dependency; fall back to the dict-lane query()"
            )

        v2 = normalize_query_spec(query_spec)
        if not self.endpoint:
            raise VectorCarrierUnavailable(
                "demo-mode GeoParquet sources do not expose an arrow lane"
            )
        query_bbox = self._require_bbox(v2)
        src, closer = self._open_source()
        try:
            pf = pq.ParquetFile(src)
            schema_names = list(pf.schema_arrow.names)
            geo_meta = self._read_geo_meta(pf)
            primary_geom = (
                (geo_meta.get("primary_column") or "geometry") if geo_meta else "geometry"
            )
            row_groups, _pruned = self._row_group_plan(pf, geo_meta, primary_geom, query_bbox)
            needed = self._needed_columns(v2, schema_names)
            has_geom = primary_geom in schema_names
            columns: Optional[List[str]] = None
            if needed:
                # 有投影/谓词引用列才裁剪；无投影 = 全列读（谓词字段自然包含）。
                columns = list(needed)
                if has_geom and primary_geom not in columns:
                    columns.append(primary_geom)
            for batch in pf.iter_batches(
                batch_size=BATCH_SIZE, columns=columns, row_groups=row_groups or None
            ):
                yield batch
        except (VectorCarrierUnavailable, DataFabricError):
            raise
        except Exception as e:
            raise SourceBadResponseError(f"GeoParquet arrow lane read failed: {e}") from e
        finally:
            if closer is not None:
                try:
                    closer.close()
                except Exception:
                    pass

    @staticmethod
    def _require_bbox(v2: QuerySpecV2) -> Optional[List[float]]:
        """空间谓词 → 查询 bbox；本 wave 仅支持 bbox（capably 诚实声明）。"""
        if v2.spatial is None:
            return None
        if v2.spatial.op != "bbox":
            raise InvalidQueryError(
                f"GeoParquet adapter supports bbox spatial filtering only (got '{v2.spatial.op}')"
            )
        return v2.spatial.bbox

    def _row_passes(
        self,
        v2: QuerySpecV2,
        props: Dict[str, Any],
        geometry: Optional[Dict[str, Any]],
        query_bbox: Optional[List[float]] = None,
    ) -> bool:
        """逐行本地过滤：精确 bbox 相交 + 属性谓词 + 时间谓词。

        行组剪枝只是性能优化，正确性由这里（以及与 postgis 本地过滤一致的
        包围盒级相交判定）保证；无几何行在空间过滤语义下不保留。
        """
        if query_bbox is not None:
            if geometry is None:
                return False
            b = _geom_bounds(geometry)
            if b is None:
                return False
            minx, miny, maxx, maxy = query_bbox
            if not (b[0] <= maxx and b[2] >= minx and b[1] <= maxy and b[3] >= miny):
                return False
        if v2.filter is not None and not evaluate_predicate(v2.filter, props):
            return False
        if v2.temporal is not None and not evaluate_temporal(v2.temporal, props):
            return False
        return True

    @staticmethod
    def _split_row(row: Dict[str, Any], primary_geom: Optional[str]) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        if primary_geom is None:
            return dict(row), None
        raw_geom = row.pop(primary_geom, None)
        return row, _wkb_to_geojson(raw_geom)

    @staticmethod
    def _emit_properties(props: Dict[str, Any], select: Optional[Sequence[str]], primary_geom: Optional[str]) -> Dict[str, Any]:
        if select is None:
            return {k: v for k, v in props.items() if k != primary_geom}
        return {k: props[k] for k in select if k in props}

    def _needed_columns(self, v2: QuerySpecV2, schema_names: List[str]) -> List[str]:
        """投影列 ∪ 谓词/时间/聚合/分组引用列（过滤需要读、不必然输出）。"""
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
        return [n for n in schema_names if n in names]

    def _row_group_plan(
        self, pf: Any, geo_meta: Dict[str, Any], primary_geom: str, query_bbox: Optional[List[float]]
    ) -> Tuple[List[int], int]:
        """返回 (要读取的行组序号, 被剪枝行组数)。

        行组级 bbox 可得（GeoParquet 1.1 covering 的四个统计列，或几何列
        chunk statistics）且与查询 bbox 不相交 → 跳过；任何不确定 → 全读
        （读后仍有逐批精确 bbox 过滤兜底，剪枝只是性能优化）。
        """
        meta = pf.metadata
        total = meta.num_row_groups
        if query_bbox is None:
            return list(range(total)), 0

        col_meta = (geo_meta.get("columns") or {}).get(primary_geom, {})
        covering = (col_meta.get("covering") or {}).get("bbox") or {}
        q_minx, q_miny, q_maxx, q_maxy = query_bbox

        def _chunk_stats(rg: Any, path: str):
            for j in range(rg.num_columns):
                chunk = rg.column(j)
                if chunk.path_in_schema == path:
                    st = chunk.statistics
                    if st is not None and st.has_min_max and st.min is not None and st.max is not None:
                        return float(st.min), float(st.max)
                    return None
            return None

        keep: List[int] = []
        pruned = 0
        try:
            if all(isinstance(covering.get(k), str) for k in ("xmin", "ymin", "xmax", "ymax")):
                for i in range(total):
                    rg = meta.row_group(i)
                    parts = {}
                    for k in ("xmin", "ymin", "xmax", "ymax"):
                        st = _chunk_stats(rg, covering[k])
                        if st is None:
                            parts = None
                            break
                        parts[k] = st
                    if parts is None:
                        keep.append(i)
                        continue
                    # 行组包围盒 = xmin 列的最小值 / xmax 列的最大值（y 同理）
                    g_minx, g_maxx = parts["xmin"][0], parts["xmax"][1]
                    g_miny, g_maxy = parts["ymin"][0], parts["ymax"][1]
                    if g_maxx < q_minx or g_minx > q_maxx or g_maxy < q_miny or g_miny > q_maxy:
                        pruned += 1
                    else:
                        keep.append(i)
                return keep, pruned
        except Exception as e:  # 剪枝失败不影响正确性：退回全读
            logger.debug(f"GeoParquet row-group pruning skipped: {e}")
        return list(range(total)), 0

    # ── geopandas 回退执行（列投影 + bbox kwargs，仍受 limit 约束）────

    def _execute_geopandas(
        self,
        dataset_id: str,
        v2: QuerySpecV2,
        plan,
        started: float,
        fp: Optional[str],
        descriptor: DatasetDescriptor,
        src: Any,
    ) -> QueryResult:
        """pyarrow 流式路径不可用时的回退：gpd.read_parquet(columns/bbox)。

        仍有界（limit+offset 截断）；真实端点失败 → typed 错误，绝不 fixture。
        """
        try:
            import geopandas as gpd
        except ImportError as e:
            raise SourceBadResponseError(
                "GeoParquet read requires pyarrow (preferred) or geopandas",
                details={"hint": "install pyarrow"},
            ) from e

        query_bbox = self._require_bbox(v2)
        page = v2.page
        offset = page.offset if isinstance(page, OffsetPage) else 0
        limit = page.limit

        read_cols = None
        if v2.select is not None:
            read_cols = list(v2.select)

        kwargs: Dict[str, Any] = {}
        if read_cols:
            kwargs["columns"] = read_cols
        if query_bbox:
            kwargs["bbox"] = tuple(query_bbox)
        try:
            gdf = gpd.read_parquet(src, **kwargs)
        except TypeError:
            # 老版本引擎不支持 bbox kwarg → 读后本地过滤
            gdf = gpd.read_parquet(src, columns=read_cols)
            if query_bbox and not gdf.empty:
                minx, miny, maxx, maxy = query_bbox
                gdf = gdf.cx[minx:maxx, miny:maxy]

        if v2.filter is not None:
            keep = gdf.apply(lambda r: evaluate_predicate(v2.filter, dict(r.drop(labels=gdf.geometry.name, errors="ignore"))), axis=1)
            gdf = gdf[keep]

        sliced = gdf.iloc[offset: offset + limit]
        data = json.loads(sliced.to_json())
        features = data.get("features", [])
        has_more = len(gdf) > offset + limit

        evidence = build_evidence(
            plan, started_at=started, result_count=len(features),
            total_matching=int(len(gdf)) if not has_more else None,
            truncated=has_more, rows_fetched=len(gdf), rows_returned=len(features),
        )
        return QueryResult(
            dataset_id=dataset_id,
            features=features,
            data=data,
            total_count=len(features),
            total_matching=None if has_more else int(len(gdf)),
            returned_count=len(features),
            truncated=has_more,
            has_more=has_more,
            result_mode="features",
            execution_time_seconds=round(time.monotonic() - started, 4),
            schema_info={"columns": list(sliced.columns), "dataset_bbox": descriptor.bbox},
            metadata=self._metadata(plan, evidence, started, extra={
                "is_demo": False,
                "source": "remote",
                "column_projection": bool(v2.select),
                "engine": "geopandas-fallback",
                "execution_lane": "dict",
            }),
        )

    # ── demo 模式（无端点；显式标注）───────────────────────────────────

    def _query_demo(self, dataset_id: str, v2: QuerySpecV2, started: float) -> QueryResult:
        """无端点 → 显式 synthetic demo（is_demo=True；metadata 全程标注）。"""
        descriptor = self.describe(dataset_id)
        from app.services.data_fabric.fingerprint import dataset_fingerprint_service

        fp = dataset_fingerprint_service.calculate_descriptor_fingerprint(descriptor)
        plan = plan_query(v2, descriptor, self._capabilities_v2(), source_id=self.profile.id, dataset_fingerprint=fp)

        fixture = SYNTHETIC_GEOPARQUET_FIXTURES.get(dataset_id, SYNTHETIC_GEOPARQUET_FIXTURES["us_states_geoparquet"])
        mode = v2.output.mode
        budget = StreamingBudget(
            max_rows=v2.execution.max_rows,
            max_bytes=v2.execution.max_bytes,
            max_vertices=v2.execution.max_vertices,
        )
        select = v2.select

        # ---- 谓词过滤 + 投影（bbox 精确过滤与真实路径同一语义）----
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

        # ---- STATISTICS ----
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
                "column_projection": bool(select),
            }),
        )

        # ---- DESCRIPTOR ----
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

        # ---- SAMPLE / FEATURES ----
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
        cols = list(select) if select else [c for c in fixture["columns"] if c != "geometry"]
        return QueryResult(
            dataset_id=dataset_id,
            features=out,
            data={"type": "FeatureCollection", "features": out},
            total_count=len(out),
            total_matching=total_matching,
            returned_count=len(out),
            truncated=truncated,
            has_more=truncated,
            result_mode=("sample" if mode == ResultMode.SAMPLE else "features"),
            is_demo=True,
            execution_time_seconds=round(time.monotonic() - started, 4),
            schema_info={"columns": cols},
            metadata=self._metadata(plan, evidence, started, extra={
                "is_demo": True,
                "source": "synthetic-demo",
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
                adapter="geoparquet",
                message="GeoParquet source verified",
                latency_ms=latency,
                details={"endpoint": self.endpoint or "synthetic_fixture_mode"},
            )
        return DataFabricHealth(
            status="unreachable",
            adapter="geoparquet",
            message=f"GeoParquet source inaccessible at {self.endpoint}",
            latency_ms=latency,
            details={"endpoint": self.endpoint},
        )
