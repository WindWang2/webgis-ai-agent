"""Local File Data Source Adapter (ads-v1 DS1, ADR-0171) — declared local assets.

Handles the ``local_file`` protocol: declared local data files under explicit
roots (yearbook sqlite, local GeoJSON exports). Honest semantics:

- missing file → typed ``SourceUnreachableError`` (unavailable, never empty);
- supported formats: ``.geojson``/``.json`` (GeoJSON), ``.sqlite``/``.db``
  (relational tables → geometry-less features), others → typed
  ``InvalidQueryError`` (no guessing, no fabrication);
- path access via ``resolve_safe_local_path`` (declared roots only).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.schemas.data_fabric_schema import DataFabricHealth, QueryResult
from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
from app.services.data_fabric.errors import (
    InvalidQueryError,
    SecurityBlockedError,
    SourceBadResponseError,
    SourceUnreachableError,
)
from app.services.data_fabric.security import (
    DataFabricSecurityError,
    _local_file_max_bytes_from_settings,
    _local_file_roots_from_settings,
    resolve_safe_local_path,
)

logger = logging.getLogger(__name__)

_GEOJSON_SUFFIXES = {".geojson", ".json"}
_SQLITE_SUFFIXES = {".sqlite", ".db"}
_MAX_QUERY_FEATURES = 50_000


def _resolve_file(endpoint: str, options: Dict[str, Any]) -> Path:
    raw = (endpoint or "").strip() or str(options.get("base_dir") or "").strip()
    if not raw or raw.startswith("${"):
        raise SourceUnreachableError(
            "local_file source path is unset (env ref unresolved)",
            details={"hint": "ingest local data first; unset local asset is 'unavailable'"},
        )
    path = Path(raw).expanduser()
    theme_root = str(options.get("theme_root") or "").strip()
    if theme_root and path.suffix.lower() not in _GEOJSON_SUFFIXES | _SQLITE_SUFFIXES:
        path = path / theme_root
    try:
        return resolve_safe_local_path(
            str(path),
            _local_file_roots_from_settings(),
            _local_file_max_bytes_from_settings(),
        )
    except DataFabricSecurityError as e:
        raise SecurityBlockedError(str(e)) from e


class LocalFileAdapter(GeospatialDataSourceAdapter):
    """Declared local file asset adapter (GeoJSON or sqlite)."""

    def __init__(self, connection_profile):
        super().__init__(connection_profile)
        self._path: Optional[Path] = None

    # -- internals ---------------------------------------------------------------
    def _file_path(self) -> Path:
        if self._path is None:
            endpoint = self.profile.endpoint_url or self.profile.url or ""
            self._path = _resolve_file(endpoint, dict(self.profile.options or {}))
        return self._path

    def _require_existing(self) -> Path:
        path = self._file_path()
        if not path.exists():
            raise SourceUnreachableError(
                f"local file missing: {path}",
                details={"hint": "ingest local data first"},
            )
        return path

    def _kind(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in _GEOJSON_SUFFIXES:
            return "geojson"
        if suffix in _SQLITE_SUFFIXES:
            return "sqlite"
        raise InvalidQueryError(
            f"unsupported local file format '{suffix}'",
            details={"supported": sorted(_GEOJSON_SUFFIXES | _SQLITE_SUFFIXES)},
        )

    def _load_geojson(self) -> Dict[str, Any]:
        path = self._require_existing()
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise SourceBadResponseError(f"cannot parse local GeoJSON {path}: {e}") from e
        if not isinstance(doc, dict) or doc.get("type") != "FeatureCollection":
            raise SourceBadResponseError(
                f"{path.name} is not a GeoJSON FeatureCollection",
                details={"hint": "declare real GeoJSON files only"},
            )
        return doc

    def _sqlite_connect(self) -> sqlite3.Connection:
        path = self._require_existing()
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        except sqlite3.Error as e:
            raise SourceUnreachableError(f"cannot open sqlite {path}: {e}") from e
        return conn

    # -- adapter contract ----------------------------------------------------------
    def probe(self) -> bool:
        try:
            return self._file_path().exists()
        except Exception:  # noqa: BLE001 — probe never raises
            return False

    def capabilities(self) -> List[str]:
        kind = self._kind(self._file_path())
        if kind == "geojson":
            return ["bbox", "projection", "limit"]
        return ["projection", "limit", "aggregation"]

    def list_datasets(self) -> List[Dict[str, Any]]:
        path = self._require_existing()
        kind = self._kind(path)
        if kind == "geojson":
            return [{"id": path.stem, "name": path.stem, "data_type": "vector", "path": str(path)}]
        with self._sqlite_connect() as conn:
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            ]
        return [
            {"id": f"{path.stem}:{t}", "name": t, "data_type": "table", "path": str(path)}
            for t in tables
        ]

    def describe(self, dataset_id: str):
        from app.schemas.data_fabric_schema import DatasetDescriptor

        path = self._require_existing()
        kind = self._kind(path)
        if kind == "geojson":
            doc = self._load_geojson()
            features = doc.get("features") or []
            props = (features[0].get("properties") or {}) if features else {}
            fields = [{"name": k, "type": type(v).__name__} for k, v in props.items()]
            return DatasetDescriptor(
                id=dataset_id,
                source_type="local_file",
                source_id=self.profile.id,
                name=path.stem,
                data_type="vector",
                feature_count=len(features),
                geometry_type="Unknown",
                fields=fields,
                query_capabilities=self.capabilities(),
                metadata={"path": str(path), "adapter": "ads.local_file"},
            )
        table = self._sqlite_table(dataset_id, path)
        with self._sqlite_connect() as conn:
            cols = conn.execute(
                "PRAGMA table_info(" + _quote_ident(table) + ")"  # nosec B608 — identifier quoted & from discovered list
            ).fetchall()
            count = conn.execute(
                "SELECT COUNT(*) FROM " + _quote_ident(table)  # nosec B608 — ditto
            ).fetchone()[0]
        fields = [{"name": c[1], "type": c[2]} for c in cols]
        return DatasetDescriptor(
            id=dataset_id,
            source_type="local_file",
            source_id=self.profile.id,
            name=table,
            data_type="table",
            feature_count=int(count),
            geometry_type="None",
            fields=fields,
            query_capabilities=self.capabilities(),
            metadata={"path": str(path), "table": table, "adapter": "ads.local_file"},
        )

    def preview(self, dataset_id: str, limit: int = 10) -> Dict[str, Any]:
        result = self.query(dataset_id, _BoundedSpec(limit=limit))
        return {
            "dataset_id": dataset_id,
            "columns": [f["name"] for f in (result.metadata or {}).get("fields", [])],
            "features": result.features,
            "is_demo": False,
        }

    def query(self, dataset_id: str, query_spec) -> QueryResult:
        path = self._require_existing()
        kind = self._kind(path)
        limit = max(0, min(int(query_spec.limit or 0), _MAX_QUERY_FEATURES))
        offset = max(0, int(query_spec.offset or 0))
        if kind == "geojson":
            doc = self._load_geojson()
            features = doc.get("features") or []
            bbox = query_spec.bbox
            if bbox:
                features = [f for f in features if _feature_in_bbox(f, bbox)]
            total = len(features)
            features = features[offset: offset + limit]
            return QueryResult(
                dataset_id=dataset_id,
                query_spec=query_spec,
                features=features,
                metadata={"adapter": "ads.local_file", "source": "local_file",
                          "is_demo": False, "count": len(features), "total_matched": total},
            )
        table = self._sqlite_table(dataset_id, path)
        columns = list(query_spec.columns or [])
        col_sql = ", ".join(_quote_ident(c) for c in columns) if columns else "*"
        with self._sqlite_connect() as conn:
            rows = conn.execute(
                "SELECT " + col_sql + " FROM " + _quote_ident(table) + " LIMIT ? OFFSET ?",  # nosec B608 — identifiers quoted & from discovered list; values bound
                (limit, offset),
            ).fetchall()
            cols = [d[0] for d in conn.execute(
                "SELECT " + col_sql + " FROM " + _quote_ident(table) + " LIMIT 0"  # nosec B608 — ditto
            ).description]
        features = [dict(zip(cols, row)) for row in rows]
        return QueryResult(
            dataset_id=dataset_id,
            query_spec=query_spec,
            features=features,
            metadata={"adapter": "ads.local_file", "source": "local_sqlite",
                      "is_demo": False, "count": len(features), "fields": [{"name": c, "type": "text"} for c in cols]},
        )

    def health(self) -> DataFabricHealth:
        reachable = self.probe()
        return DataFabricHealth(
            source_type="local_file",
            reachable=reachable,
            latency_ms=0.0,
            details={"path": str(self._path) if self._path else None},
        )

    # -- helpers -------------------------------------------------------------------
    def _sqlite_table(self, dataset_id: str, path: Path) -> str:
        known = {d["id"]: d["name"] for d in self.list_datasets()}
        if dataset_id in known:
            return str(known[dataset_id])
        raise InvalidQueryError(
            f"unknown table '{dataset_id}'",
            details={"known": sorted(known)},
        )


def _quote_ident(name: str) -> str:
    """SQLite 标识符引用：双写内嵌引号（表/列名来自自省，非用户自由文本；
    值一律走绑定参数 —— security net 禁止 f-string 直接进 execute()）。"""
    return '"' + name.replace('"', '""') + '"'


class _BoundedSpec:
    """Minimal QuerySpec stand-in for preview()."""

    def __init__(self, limit: int = 10):
        self.limit = limit
        self.offset = 0
        self.bbox = None
        self.columns = None


def _feature_in_bbox(
    feature: Dict[str, Any], bbox: Tuple[float, float, float, float]
) -> bool:
    """Pure-math bbox test over a GeoJSON feature's coordinate bounds."""
    minx, miny, maxx, maxy = bbox
    coords: List[Tuple[float, float]] = []
    _collect_coords(feature.get("geometry"), coords)
    if not coords:
        return False
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    return not (max(xs) < minx or min(xs) > maxx or max(ys) < miny or min(ys) > maxy)


def _collect_coords(node: Any, out: List[Tuple[float, float]]) -> None:
    if node is None:
        return
    if isinstance(node, dict):
        for child in node.values():
            _collect_coords(child, out)
        return
    if isinstance(node, (list, tuple)):
        if node and isinstance(node[0], (int, float)) and len(node) >= 2:
            out.append((float(node[0]), float(node[1])))
            return
        for child in node:
            _collect_coords(child, out)
