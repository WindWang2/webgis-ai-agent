from typing import Any, Dict, Optional
from .models import DataQuery, StandardGISData, DataSourceType, GISDataType
from .adapters import (
    PostGISAdapter,
    OSSAdapter,
    ThirdPartyAPIAdapter,
    LocalFileAdapter
)
from .cache import CacheManager
from .permissions import PermissionFilter
import logging

logger = logging.getLogger(__name__)

class DataFetcherService:
    _instance = None
    _adapter_map: Dict[DataSourceType, Any] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            # Initialize adapters
            cls._instance._adapter_map = {
                DataSourceType.POSTGIS: PostGISAdapter(),
                DataSourceType.OSS: OSSAdapter(),
                DataSourceType.THIRD_PARTY_API: ThirdPartyAPIAdapter(),
                DataSourceType.LOCAL_FILE: LocalFileAdapter()
            }
            cls._instance._cache_manager = CacheManager()
        return cls._instance

    def query(self, query: DataQuery) -> StandardGISData:
        """
        Main query entry point:
        1. Check cache if not skipped
        2. Get appropriate adapter
        3. Fetch data from source
        4. Apply permission filtering
        5. Convert to standard format
        6. Cache the result
        7. Return standardized data
        """
        try:
            # Step 1: Check cache first
            if not query.skip_cache:
                cached_data = self._cache_manager.get(query)
                if cached_data:
                    logger.info(f"Cache hit for query: {query.data_source}")
                    return StandardGISData(**cached_data)

            # Step 2: Get adapter for data source
            adapter = self._adapter_map.get(query.data_source)
            if not adapter:
                raise ValueError(f"Unsupported data source: {query.data_source}")

            # Step 3: Fetch data from source
            raw_data = adapter.query(query.query_params)

            # Step 4: Apply permission filtering
            filtered_data = PermissionFilter.filter(raw_data, query.user_role)

            # Step 5: Convert to standard format
            standard_data = self._convert_to_standard_format(filtered_data, query.data_type, query.data_source)

            # Step 6: Cache the result
            if not query.skip_cache:
                self._cache_manager.set(query, standard_data.dict(), query.cache_ttl)

            return standard_data

        except Exception as e:
            logger.error(f"Data query failed: {str(e)}", exc_info=True)
            # Try to fall back to cached data
            if not query.skip_cache:
                cached_data = self._cache_manager.get(query)
                if cached_data:
                    logger.info("Falling back to cached data after source failure")
                    # 移除缓存中的 metadata 避免重复键冲突
                    cached_clean = {k: v for k, v in cached_data.items() if k != "metadata"}
                    return StandardGISData(**cached_clean, metadata={"is_fallback": True, "fallback_reason": str(e)})
            # Return error response if no cache fallback
            return StandardGISData(
                success=False,
                data_type=query.data_type or GISDataType.VECTOR,
                error_message=f"Query failed: {str(e)}",
                metadata={"is_error": True}
            )

    def _convert_to_standard_format(self, raw_data: Any, data_type: Optional[GISDataType], source: DataSourceType) -> StandardGISData:
        """Convert raw data from different sources to standard GIS data format"""
        # Auto-detect data type if not provided
        if not data_type:
            if isinstance(raw_data, dict) and raw_data.get("type") == "FeatureCollection":
                data_type = GISDataType.VECTOR
            else:
                data_type = GISDataType.ATTRIBUTE

        if data_type == GISDataType.VECTOR:
            features = raw_data.get("features", []) if isinstance(raw_data, dict) else []
            return StandardGISData(
                data_type=data_type,
                features=features,
                metadata={
                    "source": source,
                    "feature_count": len(features)
                }
            )
        elif data_type == GISDataType.RASTER:
            return StandardGISData(
                data_type=data_type,
                raster_data=raw_data,
                metadata={"source": source}
            )
        else: # ATTRIBUTE
            return StandardGISData(
                data_type=data_type,
                features=[{"properties": raw_data}] if isinstance(raw_data, dict) else [],
                metadata={"source": source}
            )
