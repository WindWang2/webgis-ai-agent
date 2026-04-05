import json
from typing import Any, Dict
import oss2
from app.core.config import settings
from .base import DataSourceAdapter
import fiona
import io

class OSSAdapter(DataSourceAdapter):
    _instance = None
    _bucket = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            # Initialize OSS client if configured
            if all([settings.OSS_ACCESS_KEY_ID, settings.OSS_ACCESS_KEY_SECRET, settings.OSS_ENDPOINT, settings.OSS_BUCKET_NAME]):
                try:
                    auth = oss2.Auth(settings.OSS_ACCESS_KEY_ID, settings.OSS_ACCESS_KEY_SECRET)
                    cls._instance._bucket = oss2.Bucket(auth, settings.OSS_ENDPOINT, settings.OSS_BUCKET_NAME)
                except Exception as e:
                    print(f"OSS initialization failed: {e}")
        return cls._instance

    def query(self, query_params: Dict[str, Any]) -> Any:
        """
        Query GIS files from OSS:
        Supported params: file_path, bbox, layer
        Supports GeoJSON, Shapefile (zip), KML, GML formats
        Returns GeoJSON FeatureCollection
        """
        if not self._bucket:
            raise Exception("OSS not configured")

        file_path = query_params.get("file_path")
        if not file_path:
            raise ValueError("File path is required for OSS query")

        # Download file from OSS
        file_content = self._bucket.get_object(file_path).read()
        file_ext = file_path.split('.')[-1].lower()

        # Parse GIS file
        if file_ext == 'geojson':
            return json.loads(file_content)
        elif file_ext in ['zip', 'shp', 'kml', 'gml']:
            # Use fiona to parse the file
            with io.BytesIO(file_content) as f:
                with fiona.open(f) as src:
                    features = list(src)
                    return {"type": "FeatureCollection", "features": features}
        else:
            raise ValueError(f"Unsupported file format: {file_ext}")
