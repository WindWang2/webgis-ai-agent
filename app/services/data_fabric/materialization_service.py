"""
Geospatial Data Fabric: Materialization Service
Pipeline for executing QuerySpec pushdown queries and materializing remote data into local session store emitting ref_id.
"""
import logging
from typing import Dict, Any, Optional, List
from app.schemas.data_fabric_schema import QuerySpec, QueryResult, DatasetDescriptor
from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
from app.services.data_fabric.fingerprint import dataset_fingerprint_service
from app.services.session_data import session_data_manager

logger = logging.getLogger(__name__)


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
        """
        logger.info(f"[MaterializationService] Executing query for '{dataset_id}' with spec: {query_spec}")
        try:
            return adapter.query(dataset_id, query_spec)
        except Exception as e:
            logger.error(f"[MaterializationService] Query pushdown failed for '{dataset_id}': {e}")
            raise

    async def materialize(
        self,
        dataset_id: str,
        query_result: QueryResult,
        session_id: str = "default",
        layer_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Materialize query results locally into session_data_manager, emitting ref_id.
        """
        layer_title = layer_name or f"Materialized Layer {dataset_id}"
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

        # Store in session_data_manager and retrieve cursor ref_id
        import uuid
        try:
            ref_id = await session_data_manager.store(session_id, geojson_payload, prefix="data-fabric")
            try:
                await session_data_manager.set_alias(session_id, ref_id, layer_title)
            except Exception as ae:
                logger.warning(f"[MaterializationService] set_alias failed ({ae}); proceeding with ref_id '{ref_id}'")
        except Exception as e:
            logger.warning(f"[MaterializationService] session_data_manager store failed ({e}); using fallback ref_id")
            ref_id = f"ref:data-fabric-{uuid.uuid4().hex[:16]}"

        # Compute fingerprint hash for data payload

        fingerprint = dataset_fingerprint_service.calculate_data_fingerprint(query_result.features)

        logger.info(f"[MaterializationService] Materialized dataset '{dataset_id}' -> ref_id: {ref_id}")

        return {
            "status": "success",
            "ref_id": ref_id,
            "dataset_id": dataset_id,
            "layer_name": layer_title,
            "feature_count": len(query_result.features),
            "total_count": query_result.total_count or len(query_result.features),
            "fingerprint": fingerprint,
            "schema_info": query_result.schema_info,
            "metadata": query_result.metadata,
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
        """
        spec = query_spec or QuerySpec(limit=100)
        query_result = self.execute_query(adapter, dataset_id, spec)
        return await self.materialize(dataset_id, query_result, session_id=session_id, layer_name=layer_name)


# Global singleton instance
materialization_service = MaterializationService()
