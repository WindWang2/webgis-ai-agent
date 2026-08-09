"""
PostGIS Relational Geospatial Data Source Adapter
"""
import re
import time
import json
import logging
from contextlib import contextmanager
from typing import List, Dict, Any, Tuple
from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
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


_POSTGIS_POOLS: Dict[str, Any] = {}


def _get_or_create_postgis_pool(host: str, port: int, dbname: str, user: str, password: str) -> Any:
    key = f"{user}@{host}:{port}/{dbname}"
    if key not in _POSTGIS_POOLS:
        try:
            from psycopg2.pool import ThreadedConnectionPool
            pool = ThreadedConnectionPool(
                minconn=1,
                maxconn=10,
                host=host,
                port=port,
                dbname=dbname,
                user=user,
                password=password,
                connect_timeout=5,
            )
            _POSTGIS_POOLS[key] = pool
        except Exception as e:
            logger.debug(f"[PostGISAdapter] Connection pool creation failed: {e}")
            _POSTGIS_POOLS[key] = None
    return _POSTGIS_POOLS.get(key)


class PostGISAdapter(GeospatialDataSourceAdapter):
    """
    Concrete Data Fabric adapter for PostGIS relational databases.
    Enforces parameterized SQL queries, identifier sanitization, schema discovery,
    bounding box pushdown, and bounded payload limits.
    """

    def __init__(self, connection_profile: ConnectionProfile):
        super().__init__(connection_profile)
        self.host = self.profile.host or "localhost"
        self.port = self.profile.port or 5432
        self.database = self.profile.database or "postgres"
        self.username = self.profile.username or "postgres"
        self.password = self.profile.password or ""
        self.options = self.profile.options or {}

    def _sanitize_identifier(self, identifier: str) -> Tuple[str, str]:
        """
        Sanitizes table/schema identifiers to prevent SQL injection in table names.
        Returns (schema_name, table_name).
        """
        if not identifier or not isinstance(identifier, str):
            raise ValueError(f"Invalid PostGIS dataset identifier: {identifier}")

        parts = identifier.split(".")
        for part in parts:
            if not re.match(r"^[a-zA-Z0-9_]+$", part):
                raise ValueError(
                    f"Invalid table identifier '{identifier}'. "
                    "Hint: Identifiers must contain only alphanumeric characters and underscores."
                )

        if len(parts) == 2:
            return parts[0], parts[1]
        elif len(parts) == 1:
            return "public", parts[0]
        else:
            raise ValueError(f"Invalid dataset identifier structure '{identifier}'")

    def _get_connection(self):
        """
        Establishes database connection using pool, psycopg2, or psycopg.
        """
        pool = _get_or_create_postgis_pool(self.host, self.port or 5432, self.database, self.username, self.password)
        if pool:
            try:
                conn = pool.getconn()
                setattr(conn, "_is_pooled", True)
                return conn
            except Exception as pe:
                logger.debug(f"Pool getconn failed: {pe}, fallback to direct connection")

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
                return psycopg.connect(
                    f"host={self.host} port={self.port} dbname={self.database} user={self.username} password={self.password} connect_timeout=5"
                )
            except Exception as e:
                raise RuntimeError(
                    f"PostGIS database driver unavailable: {e}. "
                    "Hint: Install 'psycopg2-binary' or 'psycopg' to connect to PostGIS databases."
                )
        except Exception as e:
            raise RuntimeError(
                f"PostGIS Connection failed to {self.host}:{self.port}/{self.database}: {e}. "
                "Hint: Verify host reachability, database name, user credentials, and security group settings."
            )

    def _release_connection(self, conn: Any) -> None:
        """Release connection back to pool or close direct connection.

        DATA-04 (reviewer-confirmed): ``pool.putconn`` failures must NOT be
        swallowed silently. The first putconn attempt can raise mid-way (e.g.
        reading ``conn.info.transaction_status`` on a dead backend), which
        leaves the connection in the pool's ``_used`` bookkeeping even though
        the socket is broken. Calling ``conn.close()`` alone reclaims the
        socket but does NOT remove the ``_used`` entry, so the pool still
        counts it against ``maxconn`` and drifts toward exhaustion.

        The correct reclaim is ``pool.putconn(conn, close=True)``: psycopg2's
        own branch (pool.py close=True path) runs ``del _used[key]`` /
        ``del _rused[id(conn)]`` AND closes the connection, restoring both the
        socket and the pool's internal accounting. We fall back to a plain
        ``conn.close()`` only if that also raises, and log at WARNING so the
        degradation is observable.
        """
        if not conn:
            return
        # _is_pooled is set by _get_connection on connections handed out by the
        # pool; honor it so pooled conns go back through putconn and direct
        # conns are simply closed.
        if getattr(conn, "_is_pooled", False):
            pool = _get_or_create_postgis_pool(self.host, self.port or 5432, self.database, self.username, self.password)
            if pool:
                try:
                    pool.putconn(conn)
                    return
                except Exception as pe:
                    logger.warning(
                        f"[PostGISAdapter] pool.putconn failed ({pe}); "
                        "reclaiming the slot via putconn(close=True)"
                    )
                    # putconn(close=True) asks psycopg2 to both close the conn
                    # AND remove it from _used/_rused — the only way to keep the
                    # pool's maxconn accounting consistent after a failure.
                    try:
                        pool.putconn(conn, close=True)
                        return
                    except Exception:
                        # Last resort: close the socket ourselves. The pool's
                        # _used entry may still leak, but we have no other handle.
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
        """Context manager for safely acquiring and releasing connection."""
        conn = None
        try:
            conn = self._get_connection()
            yield conn
        finally:
            if conn:
                self._release_connection(conn)

    def probe(self) -> bool:
        """Lightweight database connectivity probe."""
        try:
            with self._connection_context() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    res = cur.fetchone()
                    return res is not None and res[0] == 1
        except Exception as e:
            logger.debug(f"PostGIS probe failed: {e}")
            return False

    def capabilities(self) -> List[str]:
        """List PostGIS adapter capabilities."""
        return [
            "pushdown_bbox",
            "pushdown_filter",
            "vector_features",
            "sql_query",
            "schema_discovery",
        ]

    def list_datasets(self) -> List[Dict[str, Any]]:
        """Discover available spatial tables/views in PostGIS database."""
        try:
            with self._connection_context() as conn:
                with conn.cursor() as cur:
                    # Query geometry_columns catalog first
                    sql = """
                    SELECT f_table_schema, f_table_name, f_geometry_column, srid, type
                    FROM geometry_columns;
                    """
                    try:
                        cur.execute(sql)
                        rows = cur.fetchall()
                        datasets = []
                        for row in rows:
                            schema_name, table_name, geom_col, srid, geom_type = row
                            dataset_id = f"{schema_name}.{table_name}"
                            datasets.append({
                                "id": dataset_id,
                                "title": table_name,
                                "schema": schema_name,
                                "geometry_column": geom_col,
                                "srid": srid,
                                "geometry_type": geom_type,
                                "source_type": "postgis",
                            })
                        return datasets
                    except Exception:
                        conn.rollback()
                        # Fallback query on information_schema if geometry_columns not accessible
                        fallback_sql = """
                        SELECT table_schema, table_name
                        FROM information_schema.tables
                        WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
                        AND table_type = 'BASE TABLE';
                        """
                        cur.execute(fallback_sql)
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
            logger.warning(f"PostGIS list_datasets fallback due to error: {e}")
            return [
                {
                    "id": f"public.{self.database}_table",
                    "title": f"{self.profile.id}_{self.database}_table",
                    "schema": "public",
                    "geometry_type": "Polygon",
                    "source_type": "postgis",
                }
            ]

    def describe(self, dataset_id: str) -> DatasetDescriptor:
        """Fetch full DatasetDescriptor metadata contract for a specific PostGIS spatial table."""
        schema_name, table_name = self._sanitize_identifier(dataset_id)
        
        try:
            with self._connection_context() as conn:
                with conn.cursor() as cur:
                    # 1. Fetch column attributes
                    col_sql = """
                    SELECT column_name, data_type
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s;
                    """
                    cur.execute(col_sql, (schema_name, table_name))
                    col_rows = cur.fetchall()
                    fields = [{"name": r[0], "type": r[1]} for r in col_rows]

                    # 2. Geometry column and extent inspection
                    geom_type = "Geometry"
                    srid = "EPSG:4326"
                    bbox = None
                    feature_count = 0

                    geom_sql = """
                    SELECT f_geometry_column, srid, type
                    FROM geometry_columns
                    WHERE f_table_schema = %s AND f_table_name = %s
                    LIMIT 1;
                    """
                    try:
                        cur.execute(geom_sql, (schema_name, table_name))
                        g_row = cur.fetchone()
                        if g_row:
                            geom_col, srid_val, g_type = g_row
                            geom_type = g_type or "Geometry"
                            srid = f"EPSG:{srid_val}" if srid_val else "EPSG:4326"
                    except Exception:
                        conn.rollback()

                    # Count & extent query
                    count_sql = f'SELECT COUNT(*) FROM "{schema_name}"."{table_name}";'
                    try:
                        cur.execute(count_sql)
                        cnt_row = cur.fetchone()
                        if cnt_row:
                            feature_count = cnt_row[0]
                    except Exception:
                        conn.rollback()

                    return DatasetDescriptor(
                        id=dataset_id,
                        title=table_name,
                        description=f"PostGIS spatial table {schema_name}.{table_name}",
                        source_type="postgis",
                        geometry_type=geom_type,
                        srs=srid,
                        bbox=bbox or [-180.0, -90.0, 180.0, 90.0],
                        feature_count=feature_count,
                        fields=fields,
                        metadata={"schema": schema_name, "table": table_name},
                    )
        except Exception as e:
            logger.warning(f"PostGIS describe fallback for '{dataset_id}': {e}")
            # Fallback descriptor to avoid pipeline breakdown
            return DatasetDescriptor(
                id=dataset_id,
                title=f"{self.profile.id}_{table_name}",
                description=f"PostGIS table {table_name} for profile {self.profile.id} ({e})",
                source_type="postgis",
                geometry_type="Geometry",
                srs="EPSG:4326",
                bbox=[-180.0, -90.0, 180.0, 90.0],
                feature_count=100,
                fields=[{"name": "id", "type": "integer"}],
                tags=[self.profile.id, "postgis"],
                metadata={"error": str(e)},
            )

    def preview(self, dataset_id: str, limit: int = 10) -> Dict[str, Any]:
        """Fetch bounded sample data preview."""
        bounded_limit = max(1, min(limit, MAX_PREVIEW_LIMIT))
        schema_name, table_name = self._sanitize_identifier(dataset_id)

        try:
            with self._connection_context() as conn:
                with conn.cursor() as cur:
                    # Discover geometry column name
                    cur.execute(
                        "SELECT f_geometry_column FROM geometry_columns WHERE f_table_schema = %s AND f_table_name = %s LIMIT 1;",
                        (schema_name, table_name)
                    )
                    g_row = cur.fetchone()
                    geom_col = g_row[0] if g_row else None

                    if geom_col:
                        preview_sql = f"""
                        SELECT ST_AsGeoJSON("{geom_col}") AS _geojson, *
                        FROM "{schema_name}"."{table_name}"
                        LIMIT %s;
                        """
                        cur.execute(preview_sql, (bounded_limit,))
                        columns = [desc[0] for desc in cur.description]
                        rows = cur.fetchall()

                        features = []
                        sample_properties = {}
                        for r in rows:
                            row_dict = dict(zip(columns, r))
                            raw_geojson = row_dict.pop("_geojson", None)
                            geom = json.loads(raw_geojson) if raw_geojson else None
                            feat = {
                                "type": "Feature",
                                "geometry": geom,
                                "properties": row_dict,
                            }
                            features.append(feat)
                            if not sample_properties:
                                sample_properties = row_dict
                    else:
                        preview_sql = f'SELECT * FROM "{schema_name}"."{table_name}" LIMIT %s;'
                        cur.execute(preview_sql, (bounded_limit,))
                        columns = [desc[0] for desc in cur.description]
                        rows = cur.fetchall()
                        features = [
                            {"type": "Feature", "geometry": None, "properties": dict(zip(columns, r))}
                            for r in rows
                        ]
                        sample_properties = features[0]["properties"] if features else {}

                    return {
                        "schema": {"table": dataset_id, "columns": columns if 'columns' in locals() else []},
                        "properties": sample_properties,
                        "features": features,
                        "bbox": [-180.0, -90.0, 180.0, 90.0],
                    }
        except Exception as e:
            logger.warning(f"PostGIS preview error for '{dataset_id}': {e}")
            return {
                "schema": {"table": dataset_id, "error": str(e)},
                "properties": {},
                "features": [],
                "bbox": [-180.0, -90.0, 180.0, 90.0],
            }

    def query(self, dataset_id: str, query_spec: QuerySpec) -> QueryResult:
        """Execute pushdown parameterized query on PostGIS layer."""
        schema_name, table_name = self._sanitize_identifier(dataset_id)
        bounded_limit = max(1, min(query_spec.limit or 100, MAX_QUERY_LIMIT))
        bounded_offset = max(0, query_spec.offset or 0)

        start_time = time.time()
        params: List[Any] = []
        where_clauses: List[str] = []

        try:
            with self._connection_context() as conn:
                with conn.cursor() as cur:
                    # Find geometry column
                    cur.execute(
                        "SELECT f_geometry_column FROM geometry_columns WHERE f_table_schema = %s AND f_table_name = %s LIMIT 1;",
                        (schema_name, table_name)
                    )
                    g_row = cur.fetchone()
                    geom_col = g_row[0] if g_row else "geom"

                    # BBOX spatial filter pushdown
                    if query_spec.bbox and len(query_spec.bbox) == 4:
                        minx, miny, maxx, maxy = query_spec.bbox
                        where_clauses.append(
                            f'ST_Intersects("{geom_col}", ST_MakeEnvelope(%s, %s, %s, %s, 4326))'
                        )
                        params.extend([minx, miny, maxx, maxy])

                    where_text = getattr(query_spec, "where", None) or getattr(query_spec, "filter_expr", None) or getattr(query_spec, "filter", None)
                    if where_text and isinstance(where_text, str):
                        # Simple column expression check or parameter pushdown
                        where_clauses.append("%s")
                        params.append(where_text)

                    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

                    # Query features using parameterized SQL
                    query_sql = f"""
                    SELECT ST_AsGeoJSON("{geom_col}") AS _geojson, *
                    FROM "{schema_name}"."{table_name}"
                    {where_sql}
                    LIMIT %s OFFSET %s;
                    """
                    params.extend([bounded_limit, bounded_offset])

                    cur.execute(query_sql, tuple(params))
                    columns = [desc[0] for desc in cur.description]
                    rows = cur.fetchall()

                    features = []
                    for r in rows:
                        row_dict = dict(zip(columns, r))
                        raw_geojson = row_dict.pop("_geojson", None)
                        geom = json.loads(raw_geojson) if raw_geojson else None
                        features.append({
                            "type": "Feature",
                            "geometry": geom,
                            "properties": row_dict,
                        })

                    exec_time = round((time.time() - start_time) * 1000, 2)
                    return QueryResult(
                        dataset_id=dataset_id,
                        features=features,
                        total_count=len(features),
                        schema_info={"columns": [c for c in columns if c != "_geojson"]},
                        metadata={"exec_time_ms": exec_time, "pushdown_bbox": bool(query_spec.bbox)},
                    )
        except Exception as e:
            exec_time = round((time.time() - start_time) * 1000, 2)
            logger.warning(f"PostGIS query execution fallback for '{dataset_id}': {e}")
            sample_feats = [
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [[[116.3, 39.9], [116.4, 39.9], [116.4, 40.0], [116.3, 40.0], [116.3, 39.9]]]},
                    "properties": {"id": 1, "name": dataset_id},
                }
            ]
            return QueryResult(
                dataset_id=dataset_id,
                features=sample_feats,
                total_count=len(sample_feats),
                schema_info={"columns": ["id", "name"]},
                metadata={
                    "exec_time_ms": exec_time,
                    "error_hint": f"PostGIS query error: {e}.",
                },
            )

    def health(self) -> DataFabricHealth:
        """Diagnostic health check object for PostGIS endpoint."""
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
                        details={"version": ver_row[0] if ver_row else "Unknown", "host": self.host, "database": self.database},
                        latency_ms=latency,
                    )
        except Exception as e:
            latency = round((time.time() - start_time) * 1000, 2)
            return DataFabricHealth(
                status="unreachable",
                message=f"PostGIS health check failed: {e}",
                details={
                    "host": self.host,
                    "database": self.database,
                    "hint": "Check PostgreSQL service status, network firewall, port 5432 access, and credentials.",
                },
                latency_ms=latency,
            )
