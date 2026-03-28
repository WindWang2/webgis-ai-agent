import json
import os
from typing import Any, Dict
import fiona
from .base import DataSourceAdapter
from app.core.config import settings

class LocalFileAdapter(DataSourceAdapter):
    def query(self, query_params: Dict[str, Any]) -> Any:
        """
        Query locally uploaded GIS files:
        Supported params: file_path, bbox, layer
        Supports GeoJSON, Shapefile (zip), KML, GML formats
        Returns GeoJSON FeatureCollection
        """
        file_path = query_params.get("file_path")
        if not file_path:
            raise ValueError("File path is required for local file query")

        # Make sure file is within allowed upload directory
        full_path = os.path.join(settings.UPLOAD_DIR, file_path.lstrip("/"))
        if not full_path.startswith(os.path.abspath(settings.UPLOAD_DIR)):
            raise ValueError("Invalid file path: access denied")

        if not os.path.exists(full_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        file_ext = file_path.split('.')[-1].lower()

        # Parse GIS file
        if file_ext == 'geojson':
            with open(full_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        elif file_ext in ['zip', 'shp', 'kml', 'gml']:
            with fiona.open(full_path) as src:
                features = list(src)
                return {"type": "FeatureCollection", "features": features}
        else:
            raise ValueError(f"Unsupported file format: {file_ext}")
