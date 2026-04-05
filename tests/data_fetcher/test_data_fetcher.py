import pytest
from unittest.mock import Mock, patch
from app.services.data_fetcher import DataFetcherService
from app.services.data_fetcher.models import DataQuery, DataSourceType, StandardGISData

class TestDataFetcherService:
    def setup_method(self):
        self.service = DataFetcherService()
        self.test_query = DataQuery(
            data_source=DataSourceType.POSTGIS,
            query_params={"table": "poi", "bbox": [116.3, 39.9, 116.5, 40.1]},
            user_role="admin"
        )

    # Test multi-data source support
    @patch("app.services.data_fetcher.adapters.postgis_adapter.PostGISAdapter.query")
    def test_postgis_data_source(self, mock_query):
        mock_query.return_value = {"type": "FeatureCollection", "features": []}
        result = self.service.query(self.test_query)
        assert isinstance(result, StandardGISData)
        mock_query.assert_called_once_with(self.test_query.query_params)

    @patch("app.services.data_fetcher.adapters.oss_adapter.OSSAdapter.query")
    def test_oss_data_source(self, mock_query):
        query = self.test_query.copy(update={"data_source": DataSourceType.OSS})
        mock_query.return_value = {"type": "FeatureCollection", "features": []}
        result = self.service.query(query)
        assert isinstance(result, StandardGISData)
        mock_query.assert_called_once_with(query.query_params)

    @patch("app.services.data_fetcher.adapters.third_party_api_adapter.ThirdPartyAPIAdapter.query")
    def test_third_party_api_data_source(self, mock_query):
        query = self.test_query.copy(update={"data_source": DataSourceType.THIRD_PARTY_API})
        mock_query.return_value = {"type": "FeatureCollection", "features": []}
        result = self.service.query(query)
        assert isinstance(result, StandardGISData)
        mock_query.assert_called_once_with(query.query_params)

    @patch("app.services.data_fetcher.adapters.local_file_adapter.LocalFileAdapter.query")
    def test_local_file_data_source(self, mock_query):
        query = self.test_query.copy(update={"data_source": DataSourceType.LOCAL_FILE})
        mock_query.return_value = {"type": "FeatureCollection", "features": []}
        result = self.service.query(query)
        assert isinstance(result, StandardGISData)
        mock_query.assert_called_once_with(query.query_params)

    # Test unified data model conversion
    def test_unified_data_model_conversion(self):
        raw_vector_data = {"type": "FeatureCollection", "features": [{"properties": {"name": "test"}, "geometry": {"type": "Point", "coordinates": [116.4, 40.0]}}]}
        converted = self.service._convert_to_standard_format(raw_vector_data, data_type="vector")
        assert converted.data_type == "vector"
        assert converted.metadata["source"] == DataSourceType.POSTGIS
        assert len(converted.features) == 1

    # Test cache mechanism
    @patch("app.services.data_fetcher.cache.CacheManager.get")
    @patch("app.services.data_fetcher.adapters.postgis_adapter.PostGISAdapter.query")
    def test_cache_hit_returns_cached_data(self, mock_query, mock_cache_get):
        cached_data = StandardGISData(data_type="vector", features=[], metadata={})
        mock_cache_get.return_value = cached_data
        result = self.service.query(self.test_query)
        assert result == cached_data
        mock_query.assert_not_called()

    @patch("app.services.data_fetcher.cache.CacheManager.get")
    @patch("app.services.data_fetcher.cache.CacheManager.set")
    @patch("app.services.data_fetcher.adapters.postgis_adapter.PostGISAdapter.query")
    def test_cache_miss_fetches_and_caches_data(self, mock_query, mock_cache_set, mock_cache_get):
        mock_cache_get.return_value = None
        raw_data = {"type": "FeatureCollection", "features": []}
        mock_query.return_value = raw_data
        result = self.service.query(self.test_query)
        mock_query.assert_called_once()
        mock_cache_set.assert_called_once()
        assert isinstance(result, StandardGISData)

    # Test permission control
    @patch("app.services.data_fetcher.permissions.PermissionFilter.filter")
    @patch("app.services.data_fetcher.adapters.postgis_adapter.PostGISAdapter.query")
    def test_permission_filter_applied(self, mock_query, mock_filter):
        raw_data = {"type": "FeatureCollection", "features": [{"properties": {"sensitive": True}}, {"properties": {"sensitive": False}}]}
        mock_query.return_value = raw_data
        filtered_data = {"type": "FeatureCollection", "features": [{"properties": {"sensitive": False}}]}
        mock_filter.return_value = filtered_data
        query = self.test_query.copy(update={"user_role": "user"})
        result = self.service.query(query)
        mock_filter.assert_called_once_with(raw_data, "user")
        assert len(result.features) == 1

    # Test exception handling and fallback
    @patch("app.services.data_fetcher.cache.CacheManager.get")
    @patch("app.services.data_fetcher.adapters.postgis_adapter.PostGISAdapter.query")
    def test_data_source_failure_returns_cached_fallback(self, mock_query, mock_cache_get):
        mock_query.side_effect = Exception("Database connection failed")
        cached_data = StandardGISData(data_type="vector", features=[], metadata={"is_fallback": True})
        mock_cache_get.return_value = cached_data
        result = self.service.query(self.test_query)
        assert result == cached_data
        assert result.metadata["is_fallback"] == True

    @patch("app.services.data_fetcher.adapters.postgis_adapter.PostGISAdapter.query")
    def test_data_source_failure_no_cache_returns_error_response(self, mock_query):
        mock_query.side_effect = Exception("Database connection failed")
        result = self.service.query(self.test_query)
        assert result.success == False
        assert "failed" in result.error_message.lower()
