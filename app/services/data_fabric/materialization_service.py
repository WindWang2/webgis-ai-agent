"""
Geospatial Data Fabric: Materialization Service
Pipeline for executing QuerySpec pushdown queries and materializing remote data into local session store emitting ref_id.

Reliability contract (Data Fabric V3):
- A ref exists IFF its payload is retrievable. Store failure (exception OR the
  Redis-unavailability sentinel) is reported as a typed ``MATERIALIZATION_FAILED``
  result with ``ref_id=None`` and ``success=False`` — never a fake ref, never a
  fake success.
- #766: an adapter query failure (in-band marker on the QueryResult) is raised
  as a typed ``DataFabricError`` by ``execute_query`` and reported as a typed
  failure — an unreachable source is never materialized as an empty-but-
  successful dataset.
- ``set_alias`` is best-effort metadata; its failure must NOT invalidate an
  otherwise-valid ref.
"""
import asyncio
import logging
from typing import Dict, Any, Optional
from app.schemas.data_fabric_schema import QuerySpec, QueryResult
from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
from app.services.data_fabric.errors import DataFabricError, MaterializationFailedError, error_from_query_result
from app.services.data_fabric.fingerprint import dataset_fingerprint_service
from app.services.data_fabric.limits import enforce_result_bounds
from app.services.session_data import session_data_manager
from app.services.session_data_protocol import is_unavailable_ref

logger = logging.getLogger(__name__)


def _is_demo_source_type(source_type: Any) -> bool:
    """#767: surface whether a source_type resolves to the explicit demo/sample
    adapter, so synthetic data is never mistaken for a real remote fetch."""
    if not source_type or not isinstance(source_type, str):
        return False
    try:
        from app.services.data_fabric.registry import resolve_adapter_spec

        return bool(resolve_adapter_spec(source_type).is_demo)
    except Exception:
        return False


def _is_demo_adapter_source(query_result: QueryResult) -> bool:
    """#767: True when the QueryResult was produced by the demo adapter."""
    return _is_demo_source_type((query_result.metadata or {}).get("source_type"))


class MaterializationService:
    """
    Materialization pipeline for Data Fabric datasets.
    Handles QuerySpec execution pushdown and local materialization emitting ref_id cursors.
    """

    def execute_query(
        self,
        adapter: GeospatialDataSourceAdapter,
        dataset_id: str,
        query_spec: QuerySpec,
    ) -> QueryResult:
        """
        Execute pushdown query or selective fetch on remote data source adapter.

        #766: an in-band adapter failure marker on the returned QueryResult
        (``schema_info['error']`` / ``metadata['error_type']``) is converted
        into a typed ``DataFabricError`` — "fetch failed" stays distinguishable
        from a genuinely empty dataset.
        """
        logger.info(f"[MaterializationService] Executing query for '{dataset_id}' with spec: {query_spec}")
        try:
            result = adapter.query(dataset_id, query_spec)
        except Exception as e:
            logger.error(f"[MaterializationService] Query pushdown failed for '{dataset_id}': {e}")
            raise
        err = error_from_query_result(result)
        if err is not None:
            logger.error(
                "[MaterializationService] Query failed for '%s': %s", dataset_id, err
            )
            raise err
        return result

    async def materialize(
        self,
        dataset_id: str,
        query_result: QueryResult,
        session_id: str = "default",
        layer_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Materialize query results locally into session_data_manager, emitting ref_id.

        Truthfulness invariant: the returned dict reports ``success``/``status``
        consistent with whether ``ref_id`` is a real, retrievable ref. On any
        store failure the result is ``success=False`` with ``ref_id=None`` and a
        typed ``error_type=MATERIALIZATION_FAILED`` — no fake ref is minted.
        """
        layer_title = layer_name or f"Materialized Layer {dataset_id}"
        is_demo = _is_demo_adapter_source(query_result)
        geojson_payload = {
            "type": "FeatureCollection",
            "features": query_result.features,
            "properties": {
                "dataset_id": dataset_id,
                "layer_name": layer_title,
                "total_count": query_result.total_count or len(query_result.features),
                "schema_info": query_result.schema_info,
            },
        }

        feature_count = len(query_result.features)
        total_count = query_result.total_count or feature_count
        # #728: the fingerprint json.dumps the whole payload — at the 50k
        # feature config bound that is a ~145 ms on-loop stall; store()
        # already offloads its dumps, keep the discipline consistent.
        fingerprint = await asyncio.to_thread(
            dataset_fingerprint_service.calculate_data_fingerprint,
            query_result.features,
        )

        # Resource guard (Section 22): reject an oversized result BEFORE storing
        # it, so a server that ignores `limit` cannot OOM the process. This
        # raises ResultTooLargeError with an actionable hint.
        enforce_result_bounds(query_result.features)

        # Store in session_data_manager and retrieve cursor ref_id.
        # A ref exists iff its payload is retrievable: both an exception and the
        # store-unavailability sentinel are real failures — never fake a ref.
        try:
            ref_id = await session_data_manager.store(session_id, geojson_payload, prefix="data-fabric")
        except Exception as e:
            logger.error(
                "[MaterializationService] store failed for '%s': %s", dataset_id, e
            )
            return self._failure(
                dataset_id, layer_title, feature_count, total_count,
                fingerprint, query_result,
                MaterializationFailedError(f"session store failed: {e}"),
            )

        if is_unavailable_ref(ref_id):
            logger.error(
                "[MaterializationService] store returned unavailable ref for '%s': %s",
                dataset_id, ref_id,
            )
            return self._failure(
                dataset_id, layer_title, feature_count, total_count,
                fingerprint, query_result,
                MaterializationFailedError("session store unavailable"),
            )

        # set_alias is best-effort metadata enrichment; a failure here does not
        # invalidate an otherwise-valid ref, so we keep the ref and proceed.
        try:
            await session_data_manager.set_alias(session_id, ref_id, layer_title)
        except Exception as ae:
            logger.warning(
                "[MaterializationService] set_alias failed (%s); ref '%s' still valid",
                ae, ref_id,
            )

        logger.info(
            "[MaterializationService] Materialized dataset '%s' -> ref_id: %s",
            dataset_id, ref_id,
        )

        return {
            "status": "success",
            "success": True,
            "ref_id": ref_id,
            "dataset_id": dataset_id,
            "layer_name": layer_title,
            "feature_count": feature_count,
            "total_count": total_count,
            "fingerprint": fingerprint,
            # #767: demo/sample adapters serve synthetic features — label the
            # payload so it is never mistaken for real remote data.
            "is_demo": is_demo,
            "schema_info": query_result.schema_info,
            "metadata": query_result.metadata,
        }

    @staticmethod
    def _failure(
        dataset_id: str,
        layer_title: str,
        feature_count: int,
        total_count: int,
        fingerprint: str,
        query_result: QueryResult,
        err: DataFabricError,
    ) -> Dict[str, Any]:
        """Build a truthful materialization-failure result (no ref, no success)."""
        d = err.to_dict()
        return {
            "status": "failed",
            "success": False,
            "ref_id": None,
            "dataset_id": dataset_id,
            "layer_name": layer_title,
            "feature_count": feature_count,
            "total_count": total_count,
            "fingerprint": fingerprint,
            "is_demo": _is_demo_adapter_source(query_result),
            "schema_info": query_result.schema_info,
            "metadata": query_result.metadata,
            "error_type": d["error_type"],
            "error": d["error"],
        }

    async def materialize_dataset(
        self,
        adapter: GeospatialDataSourceAdapter,
        dataset_id: str,
        query_spec: Optional[QuerySpec] = None,
        session_id: str = "default",
        layer_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Unified pushdown query execution and local materialization pipeline.
        Emits ref_id cursor for downstream analysis.

        The blocking remote ``execute_query`` runs off the event loop via
        ``asyncio.to_thread`` so concurrent tool dispatches aren't stalled; an
        ``asyncio.CancelledError`` during the await propagates before store (no
        stale materialization).

        #766: a typed source failure (unreachable / bad response / non-JSON
        WFS body / ...) raised by ``execute_query`` is reported as a typed
        failure result — never materialized as an empty success.
        """
        import asyncio

        spec = query_spec or QuerySpec(limit=100)
        layer_title = layer_name or f"Materialized Layer {dataset_id}"
        is_demo = _is_demo_source_type(
            getattr(getattr(adapter, "profile", None), "source_type", None)
        )
        try:
            query_result = await asyncio.to_thread(self.execute_query, adapter, dataset_id, spec)
        except DataFabricError as e:
            logger.error(
                "[MaterializationService] materialize query failed for '%s': %s",
                dataset_id, e,
            )
            return {
                "status": "failed",
                "success": False,
                "ref_id": None,
                "dataset_id": dataset_id,
                "layer_name": layer_title,
                "feature_count": 0,
                "total_count": 0,
                "fingerprint": None,
                "is_demo": is_demo,
                "schema_info": {},
                "metadata": {},
                "error_type": e.code,
                "error": str(e),
            }
        return await self.materialize(dataset_id, query_result, session_id=session_id, layer_name=layer_name)


# Global singleton instance
materialization_service = MaterializationService()
