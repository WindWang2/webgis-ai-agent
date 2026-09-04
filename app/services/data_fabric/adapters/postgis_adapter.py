"""PostGIS Relational Geospatial Data Source Adapter — V2 reference (ADR-0094).

V2 语义（相对 V1 的升级，全部由 AdapterContractTest 验证）：
- legacy QuerySpec → QuerySpecV2 归一化 → capability-aware planner → 参数化执行；
  执行计划附在 ``metadata["query_plan"]``，证据附在 ``metadata["query_evidence"]``。
- 谓词经 typed AST 编译：字段白名单来自 describe 缓存 schema，值一律绑定参数。
- 投影下推：显式列清单 + ST_AsGeoJSON(ST_Transform(geom, out_srid))；
  移除 V1 的 ``SELECT *, geom`` 双重几何传输。
- 稳定排序：无显式 order_by 时附加 PK（或 ctid）排序，保证分页确定性。
- keyset/cursor 分页：存在稳定 PK 时优先；QueryResult 返回 next_cursor/has_more。
- 聚合/分组下推；STATISTICS 模式零 geometry 传输。
- statement_timeout 按 ExecutionBudget（SET LOCAL，事务级）。
- describe 探测 geometry 索引 / PK / 行数（无索引 → 性能警告 + 建议 DDL，绝不自动 DDL）。
- server-side MVT（ST_AsMVT）与同源 server-side spatial join 供联邦优先使用。
"""
import json
import logging
from decimal import Decimal
from collections import OrderedDict
import re
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.schemas.data_fabric_schema import (
    ConnectionProfile,
    DataFabricHealth,
    DatasetDescriptor,
    QueryResult,
    QuerySpec,
)
from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
from app.services.data_fabric.errors import DataFabricError, InvalidQueryError
from app.services.data_fabric.query.capabilities import default_capabilities
from app.services.data_fabric.query.compilers import (
    compile_predicate_sql,
    compile_spatial_sql,
    compile_temporal_sql,
    quote_ident,
)
from app.services.data_fabric.query.evidence import build_evidence, evidence_for_descriptor
from app.services.data_fabric.query.execution import decode_cursor, encode_cursor
from app.services.data_fabric.query.models import (
    CursorPage,
    OffsetPage,
    QuerySpecV2,
    ResultMode,
)
from app.services.data_fabric.query.normalize import normalize_query_spec
from app.services.data_fabric.query.planner import plan_query
from app.services.data_fabric.query.predicates import PredicateError

logger = logging.getLogger(__name__)

MAX_PREVIEW_LIMIT = 100
MAX_QUERY_LIMIT = 10_000
MVT_MAX_FEATURES_PER_TILE = 20_000
MVT_MIN_ZOOM = 0
MVT_MAX_ZOOM = 22

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _geom_bounds(geom: Optional[Dict[str, Any]]) -> Optional[List[float]]:
    """GeoJSON geometry 的坐标包围盒（纯 Python，无 shapely 依赖）。"""
    if not isinstance(geom, dict):
        return None
    coords = geom.get("coordinates")
    if coords is None:
        return None
    minx = miny = float("inf")
    maxx = maxy = float("-inf")

    def _walk(c: Any) -> None:
        nonlocal minx, miny, maxx, maxy
        if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
            x, y = float(c[0]), float(c[1])
            minx = min(minx, x)
            miny = min(miny, y)
            maxx = max(maxx, x)
            maxy = max(maxy, y)
        elif isinstance(c, (list, tuple)):
            for item in c:
                _walk(item)

    _walk(coords)
    if minx is float("inf"):
        return None
    return [minx, miny, maxx, maxy]


def _filter_features_by_bbox(
    features: List[Dict[str, Any]], bbox: Sequence[float]
) -> List[Dict[str, Any]]:
    """保留与 bbox 相交（包围盒级判定）的 features。反子午线 bbox
    （minx > maxx，R2-m2）按西环/东环两段 OR 判定。"""
    minx, miny, maxx, maxy = bbox
    antimeridian = minx > maxx
    out: List[Dict[str, Any]] = []
    for f in features:
        b = _geom_bounds(f.get("geometry"))
        if b is None:
            continue  # 无几何 → 空间过滤语义下不保留
        if antimeridian:
            x_hit = b[2] >= minx or b[0] <= maxx  # 西环 [minx,180] 或东环 [-180,maxx]
        else:
            x_hit = b[0] <= maxx and b[2] >= minx
        if x_hit and b[1] <= maxy and b[3] >= miny:
            out.append(f)
    return out

# V1 兼容导出（tests/unit/test_data_fabric_postgis_where_431.py 引用）：
# 受限 where → AST → 参数化 SQL；失败按旧契约抛 ValueError。
def _parse_safe_where(expr: str):  # noqa: F811 (V1 compat shim)
    from app.services.data_fabric.query.compilers import compile_predicate_sql

    try:
        return compile_predicate_sql(parse_legacy_where(expr))
    except DataFabricError as e:
        raise ValueError(str(e)) from e


from app.services.data_fabric.query.normalize import parse_legacy_where  # noqa: E402,F401


# ── 连接池（修复 V1：创建失败不再永久 memoize None；线程安全）──────────────

_POSTGIS_POOLS: Dict[str, Any] = {}
_POOLS_LOCK = threading.Lock()
# 失败退避：同一 key 在窗口内不重试创建（避免每次查询都尝试建池）。
_POOL_RETRY_BACKOFF_S = 30.0
_POOL_FAILURES: Dict[str, float] = {}

# R4-C1/M1（ADR-0094 §10 性能）：表元数据缓存必须**进程级共享**——REST 路径
# 每个 query/materialize/tile 请求都会 build_adapter 新建 adapter 实例，
# 实例级 _meta_cache 恒冷 → 每请求 ~6 次 catalog 查询 + 全表 COUNT(*)。
# 键 (pool_key, dataset_id)；TTL 60s（与 catalog sync 频率匹配）。
_META_CACHE_TTL_S = 60.0
_META_CACHE_LOCK = threading.Lock()
_META_CACHE: "OrderedDict[Tuple[str, str], Tuple[float, Any]]" = OrderedDict()
_META_CACHE_MAX = 2048


def _meta_cache_get(key: Tuple[str, str]) -> Any:
    with _META_CACHE_LOCK:
        entry = _META_CACHE.get(key)
        if entry is None:
            return None
        loaded_at, meta = entry
        if time.monotonic() - loaded_at > _META_CACHE_TTL_S:
            _META_CACHE.pop(key, None)
            return None
        _META_CACHE.move_to_end(key)
        return meta


def reset_postgis_meta_cache() -> None:
    """清空进程级表元数据缓存（测试隔离 / 管理操作）。"""
    with _META_CACHE_LOCK:
        _META_CACHE.clear()


def _meta_cache_put(key: Tuple[str, str], meta: Any) -> None:
    with _META_CACHE_LOCK:
        _META_CACHE[key] = (time.monotonic(), meta)
        _META_CACHE.move_to_end(key)
        while len(_META_CACHE) > _META_CACHE_MAX:
            _META_CACHE.popitem(last=False)


def _pool_key(host: str, port: int, dbname: str, user: str) -> str:
    return f"{user}@{host}:{port}/{dbname}"


def _get_or_create_postgis_pool(host: str, port: int, dbname: str, user: str, password: str) -> Any:
    key = _pool_key(host, port, dbname, user)
    pool = _POSTGIS_POOLS.get(key)
    if pool is not None:
        return pool
    with _POOLS_LOCK:
        pool = _POSTGIS_POOLS.get(key)
        if pool is not None:
            return pool
        failed_at = _POOL_FAILURES.get(key)
        now = time.monotonic()
        if failed_at is not None and now - failed_at < _POOL_RETRY_BACKOFF_S:
            return None
        try:
            from psycopg2.pool import ThreadedConnectionPool

            new_pool = ThreadedConnectionPool(
                minconn=1,
                maxconn=10,
                host=host,
                port=port,
                dbname=dbname,
                user=user,
                password=password,
                connect_timeout=5,
            )
            _POSTGIS_POOLS[key] = new_pool
            _POOL_FAILURES.pop(key, None)
            return new_pool
        except Exception as e:
            logger.debug("[PostGISAdapter] pool creation failed (will retry in %ss): %s",
                         _POOL_RETRY_BACKOFF_S, e)
            _POOL_FAILURES[key] = now
            return None


class _TableMeta:
    """describe 缓存的表元数据（字段/几何列/SRID/PK/索引/行数/V3 列统计）。"""

    __slots__ = ("schema", "table", "fields", "field_names", "geom_col", "srid",
                 "geom_type", "pk_col", "has_geometry_index", "feature_count", "bbox",
                 "column_statistics")

    def __init__(self):
        self.schema = "public"
        self.table = ""
        self.fields: List[Dict[str, Any]] = []
        self.field_names: List[str] = []
        self.geom_col: Optional[str] = None
        self.srid: Optional[int] = None  # None = 未知（含 srid 0）
        self.geom_type: Optional[str] = None
        self.pk_col: Optional[str] = None
        self.has_geometry_index: Optional[bool] = None
        self.feature_count: Optional[int] = None
        self.bbox: Optional[List[float]] = None
        self.column_statistics: Optional[Dict[str, Any]] = None  # V3: pg_stats 尽力探针


class PostGISAdapter(GeospatialDataSourceAdapter):
    """PostGIS V2 reference adapter。"""

    def __init__(self, connection_profile: ConnectionProfile):
        super().__init__(connection_profile)
        self.host = self.profile.host or "localhost"
        self.port = self.profile.port or 5432
        self.database = self.profile.database or "postgres"
        self.username = self.profile.username or "postgres"
        self.password = self.profile.password or ""
        self.options = self.profile.options or {}
        self._meta_cache: Dict[str, _TableMeta] = {}
        self._caps = default_capabilities("postgis")

    # ── identifier 卫生 ────────────────────────────────────────────────

    def _sanitize_identifier(self, identifier: str) -> Tuple[str, str]:
        if not identifier or not isinstance(identifier, str):
            raise InvalidQueryError(f"Invalid PostGIS dataset identifier: {identifier}")
        parts = identifier.split(".")
        for part in parts:
            if not re.match(r"^[a-zA-Z0-9_]+$", part):
                raise InvalidQueryError(
                    f"Invalid table identifier '{identifier}'. "
                    "Hint: Identifiers must contain only alphanumeric characters and underscores."
                )
        if len(parts) == 2:
            return parts[0], parts[1]
        if len(parts) == 1:
            return "public", parts[0]
        raise InvalidQueryError(f"Invalid dataset identifier structure '{identifier}'")

    @staticmethod
    def _sanitize_geom_col(geom_col: Any) -> Optional[str]:
        """geometry_columns 读取的几何列名同样校验（二阶注入防御，审计 B-13a）。"""
        if not geom_col or not isinstance(geom_col, str):
            return None
        if not _IDENT_RE.match(geom_col):
            logger.warning("[PostGISAdapter] rejecting suspicious geometry column name %r", geom_col)
            return None
        return geom_col

    # ── 连接管理 ───────────────────────────────────────────────────────

    def _get_connection(self):
        pool = _get_or_create_postgis_pool(
            self.host, self.port or 5432, self.database, self.username, self.password
        )
        if pool:
            try:
                conn = pool.getconn()
                setattr(conn, "_is_pooled", True)
                return conn
            except Exception as pe:
                logger.debug("Pool getconn failed: %s, fallback to direct connection", pe)
        try:
            import psycopg2

            return psycopg2.connect(
                host=self.host,
                port=self.port,
                dbname=self.database,
                user=self.username,
                password=self.password,
                connect_timeout=5,
            )
        except ImportError:
            try:
                import psycopg

                # kwargs 形式（修复 V1 conninfo f-string 的密码注入/解析破坏）。
                return psycopg.connect(
                    host=self.host,
                    port=self.port or 5432,
                    dbname=self.database,
                    user=self.username,
                    password=self.password,
                )
            except Exception as e:
                raise RuntimeError(
                    f"PostGIS database driver unavailable: {e}. "
                    "Hint: Install 'psycopg2-binary' or 'psycopg' to connect to PostGIS databases."
                ) from e
        except Exception as e:
            raise RuntimeError(
                f"PostGIS connection failed to {self.host}:{self.port}/{self.database}: {e}. "
                "Hint: Verify host reachability, database name, user credentials, and security group settings."
            ) from e

    def _release_connection(self, conn: Any) -> None:
        """归还连接（DATA-04 语义保留：putconn 失败 → putconn(close=True) → close）。"""
        if not conn:
            return
        if getattr(conn, "_is_pooled", False):
            pool = _get_or_create_postgis_pool(
                self.host, self.port or 5432, self.database, self.username, self.password
            )
            if pool:
                try:
                    pool.putconn(conn)
                    return
                except Exception as pe:
                    logger.warning("[PostGISAdapter] pool.putconn failed (%s); reclaiming slot", pe)
                    try:
                        pool.putconn(conn, close=True)
                        return
                    except Exception:
                        try:
                            conn.close()
                        except Exception:
                            pass
                        return
        try:
            conn.close()
        except Exception:
            pass

    @contextmanager
    def _connection_context(self):
        """连接上下文：进入/退出时 rollback 清理事务状态，保证池化连接干净。"""
        conn = None
        try:
            conn = self._get_connection()
            try:
                conn.rollback()  # 清理池化连接可能残留的事务状态
            except Exception:
                pass
            yield conn
            try:
                conn.rollback()  # 只读查询：结束事务归还干净连接
            except Exception:
                pass
        finally:
            if conn:
                self._release_connection(conn)

    def _apply_statement_timeout(self, conn: Any, timeout_s: Optional[float]) -> None:
        """按 ExecutionBudget 设置事务级 statement_timeout（参数化，失败不致命）。"""
        if not timeout_s or timeout_s <= 0:
            return
        try:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = %s", (int(timeout_s * 1000),))
        except Exception as e:
            logger.debug("[PostGISAdapter] statement_timeout apply failed: %s", e)
            try:
                conn.rollback()
            except Exception:
                pass

    # ── 基础契约 ───────────────────────────────────────────────────────

    def probe(self) -> bool:
        try:
            with self._connection_context() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    res = cur.fetchone()
                    return res is not None and res[0] == 1
        except Exception as e:
            logger.debug("PostGIS probe failed: %s", e)
            return False

    def capabilities(self) -> List[str]:
        return [
            "pushdown_bbox",
            "pushdown_filter",
            "projection_pushdown",
            "sort_pushdown",
            "cursor_pagination",
            "aggregation",
            "vector_features",
            "schema_discovery",
            "server_mvt",
        ]

    def capabilities_v2(self):
        return self._caps

    def list_datasets(self) -> List[Dict[str, Any]]:
        try:
            with self._connection_context() as conn:
                with conn.cursor() as cur:
                    try:
                        cur.execute(
                            "SELECT f_table_schema, f_table_name, f_geometry_column, srid, type "
                            "FROM geometry_columns;"
                        )
                        rows = cur.fetchall()
                        return [
                            {
                                "id": f"{r[0]}.{r[1]}",
                                "title": r[1],
                                "schema": r[0],
                                "geometry_column": r[2],
                                "srid": r[3],
                                "geometry_type": r[4],
                                "source_type": "postgis",
                            }
                            for r in rows
                        ]
                    except Exception:
                        conn.rollback()
                        cur.execute(
                            "SELECT table_schema, table_name FROM information_schema.tables "
                            "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
                            "AND table_type = 'BASE TABLE';"
                        )
                        rows = cur.fetchall()
                        return [
                            {
                                "id": f"{r[0]}.{r[1]}",
                                "title": r[1],
                                "schema": r[0],
                                "geometry_type": "Unknown",
                                "source_type": "postgis",
                            }
                            for r in rows
                        ]
        except Exception as e:
            logger.warning("PostGIS list_datasets failed: %s", e)
            return []

    # ── describe（带索引/PK 探测）────────────────────────────────────

    def _load_table_meta(
        self, dataset_id: str, conn: Any, *, lightweight: bool = False
    ) -> _TableMeta:
        """加载表元数据（进程级共享缓存，TTL 60s）。

        ``lightweight=True``（MVT 瓦片热路径）：跳过全表 COUNT(*) 与
        ST_EstimatedExtent 探测——瓦片路径不使用行数/范围，二者在大表上
        各是秒级查询（R4-C1）。
        """
        pool_key = _pool_key(
            getattr(self, "host", "localhost"),
            getattr(self, "port", None) or 5432,
            getattr(self, "database", "postgres"),
            getattr(self, "username", "postgres"),
        )
        shared_key = (pool_key, dataset_id)
        cached = _meta_cache_get(shared_key)
        if cached is not None:
            return cached
        schema_name, table_name = self._sanitize_identifier(dataset_id)
        meta = _TableMeta()
        meta.schema, meta.table = schema_name, table_name
        cur = conn.cursor()

        cur.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position;",
            (schema_name, table_name),
        )
        meta.fields = [{"name": r[0], "type": r[1]} for r in cur.fetchall()]
        meta.field_names = [f["name"] for f in meta.fields]

        try:
            cur.execute(
                "SELECT f_geometry_column, srid, type FROM geometry_columns "
                "WHERE f_table_schema = %s AND f_table_name = %s LIMIT 1;",
                (schema_name, table_name),
            )
            g_row = cur.fetchone()
        except Exception:
            conn.rollback()
            cur = conn.cursor()
            g_row = None
        if g_row:
            meta.geom_col = self._sanitize_geom_col(g_row[0])
            raw_srid = g_row[1]
            meta.geom_type = g_row[2] or "Geometry"
            try:
                srid_val = int(raw_srid) if raw_srid is not None else 0
            except (TypeError, ValueError):
                srid_val = 0
            meta.srid = srid_val if srid_val > 0 else None  # 0/未知 → None（不再摇摆 4326/-1）
        # PK（单列 PK 才可作为 keyset 游标）
        try:
            cur.execute(
                "SELECT a.attname FROM pg_index i "
                "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
                "WHERE i.indrelid = %s::regclass AND i.indisprimary;",
                (f'"{schema_name}"."{table_name}"',),
            )
            pk_rows = [r[0] for r in cur.fetchall()]
            if len(pk_rows) == 1 and _IDENT_RE.match(pk_rows[0]):
                meta.pk_col = pk_rows[0]
        except Exception:
            conn.rollback()
            cur = conn.cursor()
        # geometry 索引探测（绝不自动 DDL）
        if meta.geom_col:
            try:
                cur.execute(
                    "SELECT 1 FROM pg_indexes WHERE schemaname = %s AND tablename = %s "
                    "AND indexdef ILIKE '%%using gist%%' AND indexdef ILIKE %s LIMIT 1;",
                    (schema_name, table_name, f"%({meta.geom_col})%"),
                )
                meta.has_geometry_index = cur.fetchone() is not None
            except Exception:
                conn.rollback()
                cur = conn.cursor()
        # 行数 + extent（lightweight 跳过：瓦片路径不需要）
        if not lightweight:
            try:
                # 与 PK 探测同款参数化：标识符经 %s::regclass 绑定，不做字符串拼接
                cur.execute(
                    "SELECT COUNT(*) FROM %s::regclass;",
                    (f'"{schema_name}"."{table_name}"',),
                )
                row = cur.fetchone()
                meta.feature_count = int(row[0]) if row and row[0] is not None else None
            except Exception:
                conn.rollback()
                cur = conn.cursor()
        if not lightweight and meta.geom_col and meta.srid:
            try:
                cur.execute(
                    'SELECT ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e) FROM '
                    '(SELECT ST_Transform(ST_EstimatedExtent(%s, %s, %s), 4326) AS e) s;',
                    (schema_name, table_name, meta.geom_col),
                )
                row = cur.fetchone()
                if row and all(v is not None for v in row):
                    meta.bbox = [float(v) for v in row]
            except Exception:
                conn.rollback()
                cur = conn.cursor()
        # V3 列统计（pg_stats 尽力探针；失败静默 None —— 统计绝不阻断查询）
        if not lightweight:
            try:
                from app.services.data_fabric.query.statistics import (
                    collect_postgis_statistics,
                )

                def _fetch_all(sql: str, params: tuple):
                    cur.execute(sql, params)
                    return cur.fetchall()

                stats = collect_postgis_statistics(_fetch_all, schema_name, table_name)
                meta.column_statistics = stats or None
            except Exception:
                conn.rollback()
                cur = conn.cursor()
                meta.column_statistics = None
        _meta_cache_put(shared_key, meta)
        return meta

    def describe(self, dataset_id: str) -> DatasetDescriptor:
        schema_name, table_name = self._sanitize_identifier(dataset_id)
        try:
            with self._connection_context() as conn:
                meta = self._load_table_meta(dataset_id, conn)
                srs = f"EPSG:{meta.srid}" if meta.srid else None
                md: Dict[str, Any] = {
                    "schema": schema_name,
                    "table": table_name,
                    "primary_key": meta.pk_col,
                    "has_geometry_index": meta.has_geometry_index,
                }
                # V3：列统计进 descriptor.metadata，statistics_from_descriptor 统一收割
                if meta.column_statistics:
                    md["column_statistics"] = [
                        {"name": name, **info}
                        for name, info in meta.column_statistics.items()
                    ]
                if meta.has_geometry_index is False and meta.geom_col:
                    md["index_suggestion"] = (
                        f'CREATE INDEX ON "{schema_name}"."{table_name}" '
                        f'USING GIST ("{meta.geom_col}");'
                    )
                return DatasetDescriptor(
                    id=dataset_id,
                    title=table_name,
                    description=f"PostGIS spatial table {schema_name}.{table_name}",
                    source_type="postgis",
                    geometry_type=meta.geom_type or "Geometry",
                    srs=srs,
                    crs=srs,
                    bbox=meta.bbox,
                    feature_count=meta.feature_count,
                    fields=meta.fields,
                    metadata=md,
                )
        except DataFabricError:
            raise
        except Exception as e:
            # 失败 → typed error（不再返回伪造 descriptor；审计 C2）。
            # 连接类故障 → SOURCE_UNREACHABLE（可重试/触发熔断）；其余 → INVALID_QUERY。
            msg = str(e).lower()
            if any(k in msg for k in ("connection", "timeout", "reach", "refused", "dns", "authenticate", "password")):
                from app.services.data_fabric.errors import SourceUnreachableError

                raise SourceUnreachableError(f"PostGIS describe failed for '{dataset_id}': {e}") from e
            raise InvalidQueryError(f"PostGIS describe failed for '{dataset_id}': {e}") from e

    def preview(self, dataset_id: str, limit: int = 10) -> Dict[str, Any]:
        bounded = max(1, min(limit, MAX_PREVIEW_LIMIT))
        try:
            spec = QuerySpec(limit=bounded)
            result = self.query(dataset_id, spec)
            return {
                "schema": {"table": dataset_id, "columns": result.schema_info.get("columns", [])},
                "properties": (result.features[0].get("properties") if result.features else {}) or {},
                "features": result.features,
                "bbox": None,
                "is_demo": False,
            }
        except Exception as e:
            logger.warning("PostGIS preview error for '%s': %s", dataset_id, e)
            return {
                "schema": {"table": dataset_id, "error": str(e)},
                "properties": {},
                "features": [],
                "bbox": None,
            }

    # ── 查询主路径 ─────────────────────────────────────────────────────

    def query(self, dataset_id: str, query_spec: QuerySpec) -> QueryResult:
        started = time.monotonic()
        try:
            v2 = normalize_query_spec(query_spec)
        except DataFabricError:
            raise
        except Exception as e:
            raise InvalidQueryError(f"query normalization failed: {e}") from e

        try:
            return self._execute_v2(dataset_id, v2, started)
        except DataFabricError:
            raise
        except PredicateError as e:
            raise InvalidQueryError(f"invalid predicate: {e}") from e
        except Exception as e:
            # 保留 V1 语义：连接/驱动故障以 RuntimeError 描述；调用方
            # （manager breaker 包装）决定重试。禁止伪造数据。
            logger.warning("PostGIS query failed for '%s': %s", dataset_id, e)
            raise RuntimeError(f"PostGIS query error: {e}") from e

    def _execute_v2(self, dataset_id: str, v2: QuerySpecV2, started: float) -> QueryResult:
        timeout_s = min(v2.execution.deadline_s, 300.0)
        with self._connection_context() as conn:
            self._apply_statement_timeout(conn, timeout_s)
            meta = self._load_table_meta(dataset_id, conn)
            if not meta.field_names:
                raise InvalidQueryError(
                    f"dataset '{dataset_id}' not found or has no readable columns"
                )
            descriptor = self._descriptor_from_meta(dataset_id, meta)
            from app.services.data_fabric.fingerprint import dataset_fingerprint_service

            fp = dataset_fingerprint_service.calculate_descriptor_fingerprint(descriptor)
            from app.services.data_fabric.query.statistics import (
                statistics_for_request,
            )

            plan = plan_query(
                v2, descriptor, self._caps,
                source_id=self.profile.id, dataset_fingerprint=fp,
                stats=statistics_for_request(descriptor, fp),
            )
            budget = v2.execution

            where_sql, params, spatial_pushed = self._compile_where(v2, meta, descriptor)
            if not spatial_pushed and v2.spatial is not None:
                # plan 如实降级：spatial 未下推 → 本地过滤（hybrid）
                plan = plan.model_copy(update={
                    "pushed_spatial": False,
                    "local_filters": plan.local_filters + [f"spatial:{v2.spatial.op}(local)"],
                    "execution_mode": "hybrid" if plan.pushed_filters else "local_fallback",
                    "warnings": plan.warnings + [
                        "dataset SRID unknown; bbox filtered locally over a bounded fetch "
                        "(matches beyond the fetch limit are not returned)"
                    ],
                })

            order_cols = self._order_columns(v2, meta)
            mode = plan.result_mode

            # ---- STATISTICS / aggregation ----
            if mode == ResultMode.STATISTICS or v2.aggregate:
                rows, db_queries = self._execute_aggregation(conn, meta, v2, where_sql, params, order_cols)
                evidence = build_evidence(
                    plan, started_at=started, result_count=len(rows),
                    total_matching=None, rows_fetched=len(rows), rows_returned=len(rows),
                    db_queries=db_queries,
                    dataset_version=evidence_for_descriptor(descriptor, fp),
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
                    metadata={
                        "query_plan": plan.model_dump(),
                        "query_evidence": evidence.model_dump(),
                        "is_demo": False,
                    },
                )

            # ---- DESCRIPTOR mode（零数据传输）----
            if mode == ResultMode.DESCRIPTOR:
                evidence = build_evidence(
                    plan, started_at=started, result_count=0,
                    dataset_version=evidence_for_descriptor(descriptor, fp),
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
                    schema_info={"columns": meta.field_names},
                    metadata={
                        "query_plan": plan.model_dump(),
                        "query_evidence": evidence.model_dump(),
                        "is_demo": False,
                    },
                )

            # ---- VECTOR_TILE mode：返回 tile 描述（实际 tile 由 Wave I 路由服务）----
            if mode == ResultMode.VECTOR_TILE:
                evidence = build_evidence(
                    plan, started_at=started, dataset_version=evidence_for_descriptor(descriptor, fp),
                )
                return QueryResult(
                    dataset_id=dataset_id,
                    features=[],
                    data={"tile_strategy": "server_mvt", "tilejson": self._tile_strategy(dataset_id, meta)},
                    total_count=meta.feature_count or 0,
                    returned_count=0,
                    payload_type="vector_tile",
                    result_mode="vector_tile",
                    execution_time_seconds=round(time.monotonic() - started, 4),
                    schema_info={"columns": meta.field_names},
                    metadata={
                        "query_plan": plan.model_dump(),
                        "query_evidence": evidence.model_dump(),
                        "is_demo": False,
                    },
                )

            # ---- FEATURES / SAMPLE / MATERIALIZE ----
            select_list = self._select_columns(v2, meta)

            page = v2.page
            order_cols = self._order_columns(v2, meta)
            # R2-C1/M8 修复：
            # 1) 排除谓词用裸列名（不带 ASC/DESC——方向入行比较是语法错误）；
            # 2) 比较方向跟随排序（全 ASC → `>`，全 DESC → `<`；混合方向不支持
            #    keyset，降级 offset 并在 plan 注明——绝不静默错页）；
            # 3) 游标键列泛化：排序键未投影时以 _cursor_key_N 附加列取值
            #    （此前仅 PK 排序可用游标，显式 order_by + cursor 静默停摆）。
            directions = [o.direction for o in v2.order_by]
            uniform = "asc" if all(d == "asc" for d in directions) else (
                "desc" if all(d == "desc" for d in directions) else None
            )
            keyset_ok = bool(order_cols) and (uniform is not None)
            cmp_op = ">" if uniform == "asc" else "<"
            if isinstance(page, CursorPage) and page.cursor and not keyset_ok:
                raise InvalidQueryError(
                    "keyset cursor requires a uniform (all-ASC or all-DESC) order key; "
                    "use offset pagination for mixed-direction ordering"
                )

            # 游标键取值：排序字段若不在投影中，附加别名化列
            cursor_key_aliases: List[Tuple[str, str]] = []  # (alias, field)
            if keyset_ok:
                projected = set(v2.select or meta.field_names)
                for _e, fname in order_cols:
                    if fname in projected:
                        cursor_key_aliases.append((fname, fname))
                    else:
                        alias = f"_cursor_key_{len(cursor_key_aliases)}"
                        select_list.append(f"{quote_ident(fname)} AS {alias}")
                        cursor_key_aliases.append((alias, fname))

            where_fragments: List[str] = []
            if where_sql:
                where_fragments.append(where_sql)
            base_params = list(params)  # where 参数（count 复用）
            if isinstance(page, CursorPage) and page.cursor and keyset_ok:
                cursor_vals = decode_cursor(page.cursor)
                if len(cursor_vals) != len(order_cols):
                    raise InvalidQueryError("cursor arity mismatch with order key")
                where_fragments.append(
                    "(" + ", ".join(quote_ident(f) for _e, f in order_cols) + ") "
                    + cmp_op + " (" + ", ".join(["%s"] * len(order_cols)) + ")"
                )
                base_params = base_params + list(cursor_vals)

            if mode == ResultMode.SAMPLE and v2.sample:
                limit = v2.sample.size
                sample_sql = self._sample_sql(meta, v2.sample, fp)
            else:
                limit = page.limit
                sample_sql = ""

            sql = (
                f"SELECT {', '.join(select_list)} FROM "
                f'"{meta.schema}"."{meta.table}"'
                f"{sample_sql}"
                f"{(' WHERE ' + ' AND '.join(where_fragments)) if where_fragments else ''}"
            )
            if order_cols:
                sql += " ORDER BY " + ", ".join(e for e, _f in order_cols)
            sql += " LIMIT %s"
            sql_params = base_params + [limit]
            page_offset = page.offset if isinstance(page, OffsetPage) else 0
            if page_offset > 0:
                sql += " OFFSET %s"
                sql_params.append(page_offset)

            cur = conn.cursor()
            cur.execute(sql, tuple(sql_params))
            columns = [d[0] for d in cur.description]
            rows = cur.fetchall()
            db_queries = 2  # meta + main

            features = []
            for r in rows:
                row_dict = dict(zip(columns, r))
                raw_geo = row_dict.pop("_geojson", None)
                # 游标键别名列不出现在属性中（_cursor_key*）
                for k in [k for k in row_dict if k.startswith("_cursor_key")]:
                    row_dict.pop(k)
                geom = json.loads(raw_geo) if isinstance(raw_geo, str) else raw_geo
                features.append({
                    "type": "Feature",
                    "geometry": geom,
                    "properties": row_dict,
                })

            # 本地 bbox 过滤（仅当 spatial 未下推，如未知 SRID 表）：
            # 在有界取回的页内精确过滤，保证返回子集语义正确。
            local_bbox = None
            if not spatial_pushed and v2.spatial is not None and v2.spatial.op == "bbox":
                local_bbox = v2.spatial.bbox
                features = _filter_features_by_bbox(features, local_bbox)
                # 本地过滤后 has_more/next_cursor 基于过滤前 fetch（保守）
                next_cursor = None

            # total_matching：仅第一页计算（count 复用同一 WHERE）
            total_matching: Optional[int] = None
            first_page = (isinstance(page, OffsetPage) and page.offset == 0) or (
                isinstance(page, CursorPage) and not page.cursor
            )
            if first_page:
                count_sql = (
                    f'SELECT COUNT(*) FROM "{meta.schema}"."{meta.table}"'
                    f"{(' WHERE ' + where_sql) if where_sql else ''}"
                )
                cur.execute(count_sql, tuple(params))
                row = cur.fetchone()
                total_matching = int(row[0]) if row and row[0] is not None else None
                db_queries += 1

            non_geom_cols = [c for c in columns if c not in ("_geojson", "_cursor_key")]

            fetched = len(features)
            if total_matching is not None:
                position = page.offset if isinstance(page, OffsetPage) else 0
                has_more = total_matching > position + fetched
            else:
                has_more = fetched >= limit
            next_cursor: Optional[str] = None
            if has_more and rows and keyset_ok and cursor_key_aliases:
                last_row = dict(zip(columns, rows[-1]))
                next_cursor = encode_cursor([last_row.get(alias) for alias, _f in cursor_key_aliases])
            truncated = has_more
            evidence = build_evidence(
                plan, started_at=started, result_count=len(features),
                total_matching=total_matching, truncated=truncated,
                rows_fetched=len(features), rows_returned=len(features),
                db_queries=db_queries,
                dataset_version=evidence_for_descriptor(descriptor, fp),
            )
            return QueryResult(
                dataset_id=dataset_id,
                features=features,
                total_count=len(features),
                total_matching=total_matching,
                returned_count=len(features),
                truncated=truncated,
                has_more=has_more,
                next_cursor=next_cursor,
                result_mode=("sample" if mode == ResultMode.SAMPLE else "features"),
                execution_time_seconds=round(time.monotonic() - started, 4),
                schema_info={"columns": non_geom_cols},
                metadata={
                    "query_plan": plan.model_dump(),
                    "query_evidence": evidence.model_dump(),
                    "exec_time_ms": round((time.monotonic() - started) * 1000, 2),
                    "pushdown_bbox": plan.pushed_spatial,
                    "pushdown_filter": bool(plan.pushed_filters),
                    "pushdown_projection": plan.pushed_projection,
                    "is_demo": False,
                    "budget": {"deadline_s": budget.deadline_s},
                },
            )

    # ── SQL 组装辅助 ───────────────────────────────────────────────────

    def _descriptor_from_meta(self, dataset_id: str, meta: _TableMeta) -> DatasetDescriptor:
        srs = f"EPSG:{meta.srid}" if meta.srid else None
        return DatasetDescriptor(
            id=dataset_id,
            source_type="postgis",
            geometry_type=meta.geom_type or "Geometry",
            srs=srs,
            crs=srs,
            bbox=meta.bbox,
            feature_count=meta.feature_count,
            fields=meta.fields,
            metadata={
                "primary_key": meta.pk_col,
                "has_geometry_index": meta.has_geometry_index,
            },
        )

    def _compile_where(self, v2: QuerySpecV2, meta: _TableMeta, descriptor: DatasetDescriptor):
        """谓词 → WHERE。返回 (where_sql, params, spatial_pushed)。

        P2-1 语义保留：dataset SRID 未知（geometry_columns srid=0/NULL）时，
        空间谓词不能下推（没有源 CRS 可表达 envelope）——返回
        ``spatial_pushed=False``，调用方对有界结果做本地 bbox 过滤并如实记录。
        """
        clauses: List[str] = []
        params: List[Any] = []
        spatial_pushed = False
        if v2.spatial is not None:
            op = v2.spatial.op
            if meta.srid is None:
                if op != "bbox":
                    raise InvalidQueryError(
                        "dataset SRID is unknown (0); spatial predicate unsupported. "
                        "Hint: set a SRID constraint on the geometry column or use bbox."
                    )
                # bbox 由调用方本地过滤（bounded fetch + exact local filter）
            else:
                geom_field = v2.spatial.field or meta.geom_col
                if not geom_field:
                    raise InvalidQueryError(
                        "dataset has no geometry column; spatial predicate unsupported"
                    )
                from app.services.data_fabric.query.planner import parse_epsg

                query_srid = parse_epsg(getattr(v2.spatial, "crs", "EPSG:4326")) or 4326
                col_srid = meta.srid
                sql_s, p = compile_spatial_sql(
                    v2.spatial, geom_field=geom_field, col_srid=col_srid, bbox_crs_srid=query_srid
                )
                clauses.append(sql_s)
                params.extend(p)
                spatial_pushed = True
        if v2.filter is not None:
            sql_f, p = compile_predicate_sql(v2.filter, allowed_fields=meta.field_names)
            clauses.append(sql_f)
            params.extend(p)
        if v2.temporal is not None:
            if v2.temporal.field not in meta.field_names:
                raise InvalidQueryError(
                    f"temporal field '{v2.temporal.field}' not in table schema"
                )
            sql_t, p = compile_temporal_sql(v2.temporal, allowed_fields=meta.field_names)
            clauses.append(sql_t)
            params.extend(p)
        return (" AND ".join(clauses) if clauses else ""), params, spatial_pushed

    def _select_columns(self, v2: QuerySpecV2, meta: _TableMeta) -> List[str]:
        cols: List[str] = []
        if v2.select is not None:
            allowed = set(meta.field_names)
            for f in v2.select:
                if f not in allowed:
                    raise InvalidQueryError(f"field '{f}' not in table schema")
                if meta.geom_col and f == meta.geom_col:
                    continue
                cols.append(quote_ident(f))
            if not cols:
                cols.append("1 AS _empty_projection")
        else:
            cols = [quote_ident(f) for f in meta.field_names if not (meta.geom_col and f == meta.geom_col)]
        if meta.geom_col:
            geo = self._geojson_expr(meta, v2)
            cols.append(f"{geo} AS _geojson")
        return cols

    def _geojson_expr(self, meta: _TableMeta, v2: QuerySpecV2) -> str:
        if not meta.geom_col:
            return "NULL"
        gcol = quote_ident(meta.geom_col)
        from app.services.data_fabric.query.planner import parse_epsg

        out_srid = parse_epsg(v2.output.crs) or 4326
        if meta.srid and meta.srid != out_srid:
            return f"ST_AsGeoJSON(ST_Transform({gcol}, {out_srid}), 7)"
        return f"ST_AsGeoJSON({gcol}, 7)"

    def _order_columns(self, v2: QuerySpecV2, meta: _TableMeta) -> List[Tuple[str, str]]:
        """返回排序键 [(sql_expr, field_name), ...]：显式 order_by；缺省附加 PK。"""
        cols: List[Tuple[str, str]] = []
        for o in v2.order_by:
            if o.field not in meta.field_names:
                raise InvalidQueryError(f"order_by field '{o.field}' not in table schema")
            cols.append((f'{quote_ident(o.field)} {o.direction.upper()}', o.field))
        if not cols and meta.pk_col:
            cols.append((quote_ident(meta.pk_col), meta.pk_col))
        return cols

    def _sample_sql(self, meta: _TableMeta, sample, dataset_fp: Optional[str]) -> str:
        """服务端确定性采样子句：``TABLESAMPLE SYSTEM (p) REPEATABLE (seed)``。

        百分比与 seed 都是服务端计算的数值（非用户输入），语法位置不允许
        参数绑定，故以数值字面量渲染（repr(float) / int，无注入面）。
        位置在 FROM 表名之后、WHERE 之前。
        """
        from app.services.data_fabric.query.execution import sample_seed

        total = meta.feature_count or 0
        if total <= 0:
            return ""
        pct = min(100.0, max(0.001, (sample.size / total) * 100.0 * 2.0))
        seed = sample_seed(dataset_fp, sample) % 2_147_483_647
        return f" TABLESAMPLE SYSTEM ({pct!r}) REPEATABLE ({seed})"

    def _execute_aggregation(
        self, conn: Any, meta: _TableMeta, v2: QuerySpecV2,
        where_sql: str, params: List[Any], order_cols: List[str],
    ) -> Tuple[List[Dict[str, Any]], int]:
        cur = conn.cursor()
        select_parts: List[str] = []
        if v2.group_by:
            for g in v2.group_by:
                if g not in meta.field_names:
                    raise InvalidQueryError(f"group_by field '{g}' not in table schema")
                select_parts.append(quote_ident(g))
        for a in v2.aggregate or []:
            if a.func == "count" and a.field is None:
                select_parts.append("COUNT(*)")
                continue
            if a.field not in meta.field_names:
                raise InvalidQueryError(f"aggregate field '{a.field}' not in table schema")
            qf = quote_ident(a.field)
            name = f"{a.func}_{a.field}"
            fn = {
                "count": f"COUNT({qf})",
                "sum": f"SUM({qf})",
                "avg": f"AVG({qf})",
                "min": f"MIN({qf})",
                "max": f"MAX({qf})",
                "stddev": f"STDDEV({qf})",  # Postgres STDDEV = sample stddev
                "distinct_count": f"COUNT(DISTINCT {qf})",
            }[a.func]
            select_parts.append(f"{fn} AS {quote_ident(name)}")
        sql = (
            f"SELECT {', '.join(select_parts)} FROM "
            f'"{meta.schema}"."{meta.table}"'
            f"{(' WHERE ' + where_sql) if where_sql else ''}"
        )
        if v2.group_by:
            sql += " GROUP BY " + ", ".join(quote_ident(g) for g in v2.group_by)
        # R2-m8：ORDER BY 列必须在 GROUP BY 内（否则 PG 报错）；无分组时用
        # 第一排序键保证聚合页序稳定。
        if v2.group_by and order_cols:
            grouped_first = next((e for e, f in order_cols if f in v2.group_by), None)
            if grouped_first:
                sql += " ORDER BY " + grouped_first
        elif order_cols and not v2.group_by:
            sql += " ORDER BY " + order_cols[0][0]
        sql += " LIMIT %s"
        page = v2.page
        if isinstance(page, OffsetPage) and page.offset:
            sql += " OFFSET %s"
            cur.execute(sql, tuple(params + [page.limit, page.offset]))
        else:
            cur.execute(sql, tuple(params + [page.limit]))
        columns = [d[0] for d in cur.description]
        rows = cur.fetchall()
        out = []
        for r in rows:
            d: Dict[str, Any] = {}
            for c, v in zip(columns, r):
                if hasattr(v, "isoformat"):
                    d[c] = v.isoformat()
                elif isinstance(v, (bytes, bytearray)):
                    continue
                elif isinstance(v, float):
                    d[c] = round(v, 6)
                elif isinstance(v, Decimal):
                    d[c] = float(round(v, 6))  # R2-m8：Decimal 不得进入 JSON 面
                else:
                    d[c] = v
            out.append(d)
        return out, 2

    def _tile_strategy(self, dataset_id: str, meta: _TableMeta) -> Dict[str, Any]:
        return {
            "type": "server_mvt",
            "dataset": dataset_id,
            "format": "pbf",
            "max_zoom": MVT_MAX_ZOOM,
            "bounds": meta.bbox or [-180, -90, 180, 90],
            "attribution": "PostGIS Data Fabric",
        }

    # ── server-side MVT（Wave I 路由消费）─────────────────────────────

    def serve_mvt_tile(
        self,
        dataset_id: str,
        z: int,
        x: int,
        y: int,
        *,
        select_fields: Optional[Sequence[str]] = None,
        where_v2: Optional[QuerySpecV2] = None,
        timeout_s: float = 30.0,
    ) -> Optional[bytes]:
        """ST_AsMVT 动态瓦片（参数化 / 有界 / revision 由路由层缓存键处理）。

        返回 None = 空瓦片（无相交特征）。
        """
        if not (MVT_MIN_ZOOM <= z <= MVT_MAX_ZOOM):
            raise InvalidQueryError(f"zoom {z} out of range [{MVT_MIN_ZOOM},{MVT_MAX_ZOOM}]")
        n = 1 << z
        if not (0 <= x < n and 0 <= y < n):
            raise InvalidQueryError(f"tile {z}/{x}/{y} out of range")
        with self._connection_context() as conn:
            self._apply_statement_timeout(conn, timeout_s)
            meta = self._load_table_meta(dataset_id, conn, lightweight=True)
            if not meta.geom_col:
                raise InvalidQueryError("dataset has no geometry column; tiles unsupported")
            gcol = quote_ident(meta.geom_col)
            # R2-M3 关联：未知 SRID 的瓦片路径禁止静默假定 4326（错瓦片）
            if meta.srid is None:
                raise InvalidQueryError(
                    "dataset SRID is unknown (0); server-side tiles require a "
                    "SRID-constrained geometry column"
                )
            col_srid = meta.srid

            props = []
            allowed = set(meta.field_names)
            for f in (select_fields or (meta.field_names[:8])):
                if f in allowed and f != meta.geom_col:
                    props.append(quote_ident(f))
            prop_select = (", " + ", ".join(props)) if props else ""

            # zoom 感泛化：低 zoom 提高容差（Web Mercator 米）
            tolerance = max(0.0, 40_075_016.6 / (2 ** z) / 256.0 * 4.0) if z < 13 else 0.0

            # 额外属性过滤（typed AST 编译）
            extra_where = ""
            extra_params: List[Any] = []
            if where_v2 is not None and where_v2.filter is not None:
                sql_f, p = compile_predicate_sql(where_v2.filter, allowed_fields=meta.field_names)
                extra_where = " AND " + sql_f
                extra_params = list(p)

            simplify = (
                f"ST_SimplifyPreserveTopology(ST_Transform({gcol}, 3857), {tolerance!r})"
                if tolerance > 0
                else f"ST_Transform({gcol}, 3857)"
            )
            sql = f"""
            SELECT ST_AsMVT(tile, 'default', 4096, 'geom') FROM (
                SELECT ST_AsMVTGeom(
                    {simplify},
                    ST_TileEnvelope(%s, %s, %s),
                    4096, 64, true
                ) AS geom{prop_select}
                FROM "{meta.schema}"."{meta.table}"
                WHERE {gcol} && ST_Transform(ST_TileEnvelope(%s, %s, %s), {col_srid})
                  AND ST_Intersects({gcol}, ST_Transform(ST_TileEnvelope(%s, %s, %s), {col_srid}))
                  {extra_where}
                LIMIT %s
            ) AS tile WHERE tile.geom IS NOT NULL;
            """
            params = [z, x, y, z, x, y, z, x, y] + extra_params + [MVT_MAX_FEATURES_PER_TILE]
            cur = conn.cursor()
            cur.execute(sql, tuple(params))
            row = cur.fetchone()
            if row and row[0]:
                return bytes(row[0])
            return None

    # ── 同源 server-side spatial join（联邦优先路径）──────────────────

    def server_spatial_join(
        self,
        dataset_points: str,
        dataset_polygons: str,
        *,
        join_op: str = "within",
        point_filter: Optional[QuerySpecV2] = None,
        polygon_filter: Optional[QuerySpecV2] = None,
        group_by_polygon_field: Optional[str] = None,
        limit: int = 10_000,
    ) -> List[Dict[str, Any]]:
        """同源 server-side 点面 join（GROUP BY 在数据库执行，只传聚合结果）。"""
        with self._connection_context() as conn:
            self._apply_statement_timeout(conn, 60.0)
            pm = self._load_table_meta(dataset_points, conn)
            gm = self._load_table_meta(dataset_polygons, conn)
            if not pm.geom_col or not gm.geom_col:
                raise InvalidQueryError("both datasets need geometry columns for spatial join")
            p_gcol, g_gcol = quote_ident(pm.geom_col), quote_ident(gm.geom_col)
            op_sql = {"within": "ST_Within", "intersects": "ST_Intersects"}[join_op]
            g_srid = gm.srid or 4326
            p_geom = (
                f"ST_Transform({p_gcol}, {g_srid})" if (pm.srid and pm.srid != g_srid) else p_gcol
            )
            clauses = [f"{op_sql}({p_geom}, {g_gcol})"]
            params: List[Any] = []
            if polygon_filter is not None and polygon_filter.filter is not None:
                sql_f, p = compile_predicate_sql(polygon_filter.filter, allowed_fields=gm.field_names)
                clauses.append(sql_f)
                params.extend(p)
            if point_filter is not None and point_filter.filter is not None:
                sql_f, p = compile_predicate_sql(point_filter.filter, allowed_fields=pm.field_names)
                clauses.append(sql_f)
                params.extend(p)
            group_field = group_by_polygon_field or (gm.pk_col or gm.field_names[0])
            if group_field not in gm.field_names:
                raise InvalidQueryError(f"group field '{group_field}' not in schema")
            q_group = quote_ident(group_field)
            sql = (
                f'SELECT g.{q_group} AS group_key, COUNT(*) AS cnt '
                f'FROM "{pm.schema}"."{pm.table}" AS p, "{gm.schema}"."{gm.table}" AS g '
                f"WHERE {' AND '.join(clauses)} "
                f"GROUP BY g.{q_group} LIMIT %s;"
            )
            params.append(limit)
            cur = conn.cursor()
            cur.execute(sql, tuple(params))
            return [
                {"group_key": (r[0].isoformat() if hasattr(r[0], "isoformat") else r[0]), "count": int(r[1])}
                for r in cur.fetchall()
            ]

    def count_rows(self, dataset_id: str, v2: Optional[QuerySpecV2] = None) -> int:
        """count-only（STATISTICS 语义；零 geometry 传输）。"""
        with self._connection_context() as conn:
            meta = self._load_table_meta(dataset_id, conn)
            where_sql, params = "", []
            if v2 is not None:
                descriptor = self._descriptor_from_meta(dataset_id, meta)
                where_sql, params, _ = self._compile_where(v2, meta, descriptor)
            sql = (
                f'SELECT COUNT(*) FROM "{meta.schema}"."{meta.table}"'
                f"{(' WHERE ' + where_sql) if where_sql else ''}"
            )
            cur = conn.cursor()
            cur.execute(sql, tuple(params))
            row = cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0

    def health(self) -> DataFabricHealth:
        start_time = time.time()
        try:
            with self._connection_context() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT PostGIS_Full_Version();")
                    ver_row = cur.fetchone()
                    latency = round((time.time() - start_time) * 1000, 2)
                    return DataFabricHealth(
                        status="healthy",
                        message="PostGIS database responsive and PostGIS extension enabled",
                        details={
                            "version": ver_row[0] if ver_row else "Unknown",
                            "host": self.host,
                            "database": self.database,
                        },
                        latency_ms=latency,
                    )
        except Exception:
            latency = round((time.time() - start_time) * 1000, 2)
            return DataFabricHealth(
                status="unreachable",
                message="PostGIS health check failed",
                details={
                    "host": self.host,
                    "database": self.database,
                    "hint": "Check PostgreSQL service status, network firewall, port 5432 access, and credentials.",
                },
                latency_ms=latency,
            )
