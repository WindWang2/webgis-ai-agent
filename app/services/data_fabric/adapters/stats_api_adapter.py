"""Public Statistics API Data Source Adapter (ads-v1 DS1, ADR-0171).

Generic tabular/statistics API adapter (``stats_api`` protocol) for declared
public sources (World Bank, GBIF, Overpass …). Response mapping is declared
per dataset in the registry (``options.datasets``), never guessed:

- ``path``: API path under the source endpoint;
- ``items_path``: dotted path to the rows array in the JSON body;
- ``lat_field``/``lon_field``: optional coordinate fields (Point geometry);
- ``page_param``/``page_size_param``: pagination mapping (bounded pages).

Honest semantics: HTTP failures → typed errors; rows are the API's own
values (no synthesis); unreachable offline = typed failure, never fake data.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import requests

from app.schemas.data_fabric_schema import DataFabricHealth, QueryResult
from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
from app.services.data_fabric.errors import (
    InvalidQueryError,
    SourceBadResponseError,
    SourceUnreachableError,
)

logger = logging.getLogger(__name__)

_MAX_PAGES = 10
_MAX_ROWS = 20_000


def _dotted(body: Any, dotted: str) -> Any:
    cur = body
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


class StatsApiAdapter(GeospatialDataSourceAdapter):
    """Declared public statistics-API adapter (data_type=table/point)."""

    def __init__(self, connection_profile):
        super().__init__(connection_profile)
        self._session = None

    def _http_session(self):
        """SSRF-safe session (same guard as every other fabric adapter)."""
        if self._session is None:
            from app.services.data_fabric.security import make_safe_session

            self._session = make_safe_session(allow_private=self.profile.allow_private)
            self._session.headers.update({"User-Agent": "ads-v1-stats-api-adapter"})
        return self._session

    # -- internals ---------------------------------------------------------------
    def _base_url(self) -> str:
        url = (self.profile.endpoint_url or self.profile.url or "").strip()
        if not url:
            raise SourceUnreachableError(
                "stats_api source has no endpoint",
                details={"hint": "declare endpoint in config/sources/*.yaml"},
            )
        return url.rstrip("/")

    def _datasets_decl(self) -> List[Dict[str, Any]]:
        return list((self.profile.options or {}).get("datasets") or [])

    def _decl(self, dataset_id: str) -> Dict[str, Any]:
        for d in self._datasets_decl():
            if str(d.get("dataset_id")) == dataset_id:
                return d
        raise InvalidQueryError(
            f"undeclared dataset '{dataset_id}' for stats_api source",
            details={"declared": [d.get("dataset_id") for d in self._datasets_decl()],
                    "hint": "declare datasets in config/sources/*.yaml (no guessing)"},
        )

    def _fetch_json(self, url: str, params: Optional[Dict[str, Any]] = None) -> Any:
        session = self._http_session()
        try:
            resp = session.get(url, params=params, timeout=30)
        except requests.RequestException as e:
            raise SourceUnreachableError(f"stats_api unreachable: {e}") from e
        if resp.status_code != 200:
            raise SourceBadResponseError(
                f"stats_api returned {resp.status_code}",
                details={"url": url},
            )
        try:
            return resp.json()
        except ValueError as e:
            raise SourceBadResponseError(f"stats_api returned non-JSON body: {e}") from e

    @staticmethod
    def _rows_to_features(rows: List[Any], decl: Dict[str, Any]) -> List[Dict[str, Any]]:
        lat_f, lon_f = decl.get("lat_field"), decl.get("lon_field")
        features: List[Dict[str, Any]] = []
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            geometry = None
            if lat_f and lon_f and row.get(lat_f) is not None and row.get(lon_f) is not None:
                try:
                    geometry = {"type": "Point", "coordinates": [float(row[lon_f]), float(row[lat_f])]}
                except (TypeError, ValueError):
                    geometry = None
            features.append(
                {"type": "Feature", "id": row.get("id") or f"row-{i}", "geometry": geometry, "properties": row}
            )
        return features

    # -- adapter contract ----------------------------------------------------------
    def probe(self) -> bool:
        try:
            self._fetch_json(self._base_url(), params=None)
            return True
        except Exception:  # noqa: BLE001 — probe never raises
            return False

    def capabilities(self) -> List[str]:
        return ["pagination", "projection"]

    def list_datasets(self) -> List[Dict[str, Any]]:
        decls = self._datasets_decl()
        if not decls:
            raise SourceUnreachableError(
                "stats_api source declares no datasets",
                details={"hint": "declare options.datasets in the source registry"},
            )
        return [
            {
                "id": str(d.get("dataset_id")),
                "name": str(d.get("title") or d.get("dataset_id")),
                "data_type": "point" if d.get("lat_field") else "table",
            }
            for d in decls
        ]

    def describe(self, dataset_id: str):
        from app.schemas.data_fabric_schema import DatasetDescriptor

        decl = self._decl(dataset_id)
        return DatasetDescriptor(
            id=dataset_id,
            source_type="stats_api",
            source_id=self.profile.id,
            name=str(decl.get("title") or dataset_id),
            data_type="point" if decl.get("lat_field") else "table",
            geometry_type="Point" if decl.get("lat_field") else "None",
            fields=[{"name": k, "type": "declared"} for k in (decl.get("fields") or [])],
            query_capabilities=self.capabilities(),
            metadata={
                "adapter": "ads.stats_api",
                "path": decl.get("path"),
                "items_path": decl.get("items_path"),
                "verified": bool((self.profile.options or {}).get("verified", False)),
            },
        )

    def preview(self, dataset_id: str, limit: int = 10) -> Dict[str, Any]:
        result = self.query(dataset_id, _SimpleSpec(limit=limit))
        return {"dataset_id": dataset_id, "features": result.features, "is_demo": False}

    def query(self, dataset_id: str, query_spec) -> QueryResult:
        decl = self._decl(dataset_id)
        rel_path = str(decl.get("path") or "").strip("/")
        if not rel_path:
            raise InvalidQueryError(
                f"dataset '{dataset_id}' declares no request path",
                details={"hint": "declare path in options.datasets"},
            )
        url = f"{self._base_url()}/{rel_path}"
        page_param = decl.get("page_param")
        page_size_param = decl.get("page_size_param")
        page_size = min(int(query_spec.limit or decl.get("page_size") or 100), _MAX_ROWS)
        items_path = str(decl.get("items_path") or "")

        features: List[Dict[str, Any]] = []
        total_matched: Optional[int] = None
        page = 1
        hit_row_cap = False
        hit_page_cap = False
        while True:
            params: Dict[str, Any] = {}
            if page_param:
                params[page_param] = page
                if page_size_param:
                    params[page_size_param] = page_size
            payload = self._fetch_json(url, params=params or None)
            rows = _dotted(payload, items_path) if items_path else payload
            if rows is None:
                raise SourceBadResponseError(
                    f"items_path '{items_path}' not found in response",
                    details={"dataset": dataset_id},
                )
            if isinstance(rows, dict):
                # {results:[…], total:n} shaped bodies
                total_matched = rows.get("total", total_matched)
                rows = rows.get("results") or rows.get("data") or []
            if not isinstance(rows, list):
                raise SourceBadResponseError(
                    "items_path did not resolve to a list",
                    details={"dataset": dataset_id},
                )
            features.extend(self._rows_to_features(rows, decl))
            got_full_page = len(rows) >= page_size
            if len(features) >= _MAX_ROWS:
                hit_row_cap = True
                break
            if not page_param or not got_full_page:
                break
            if page >= _MAX_PAGES:
                hit_page_cap = True
                break
            page += 1

        if len(features) > _MAX_ROWS:
            hit_row_cap = True
        features = features[: _MAX_ROWS]
        truncated = bool(
            hit_row_cap
            or hit_page_cap
            or (total_matched is not None and total_matched > len(features))
        )
        return QueryResult(
            dataset_id=dataset_id,
            query_spec=query_spec,
            features=features,
            total_count=len(features),
            total_matching=total_matched,
            returned_count=len(features),
            truncated=truncated,
            has_more=truncated,
            metadata={
                "adapter": "ads.stats_api",
                "is_demo": False,
                "count": len(features),
                "total_matched": total_matched,
                "pages_fetched": page,
            },
        )

    def health(self) -> DataFabricHealth:
        reachable = self.probe()
        return DataFabricHealth(
            source_type="stats_api",
            reachable=reachable,
            latency_ms=0.0,
            details={"endpoint": self._base_url()},
        )


class _SimpleSpec:
    """Minimal QuerySpec stand-in for preview()."""

    def __init__(self, limit: int = 10):
        self.limit = limit
        self.offset = 0
        self.bbox = None
        self.columns = None
        self.filter_expr = None
