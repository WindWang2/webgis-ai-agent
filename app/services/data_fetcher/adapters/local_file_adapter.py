import json
import os
from pathlib import Path
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

        # SEC-12: 之前用 settings.UPLOAD_DIR（不存在，只有 DATA_DIR）+ startswith
        # 做包含校验。startswith 可被路径前缀碰撞绕过（例如 /data_evil/x 或
        # 符号链接）。改为：
        #   1. 用 settings.DATA_DIR 作为根目录；
        #   2. 用 os.path.realpath 解析符号链接 / ../ 拼接；
        #   3. 用 Path.parents 严格包含校验，确保最终路径落在 DATA_DIR 之内。
        base_dir = Path(os.path.realpath(settings.DATA_DIR))
        full_path = Path(os.path.realpath(base_dir / file_path.lstrip("/")))

        # 严格包含校验：full_path 必须等于 base_dir 或位于其下某一层父目录链中。
        if full_path != base_dir and base_dir not in full_path.parents:
            raise ValueError("Invalid file path: access denied")

        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        file_ext = file_path.split('.')[-1].lower()

        # Parse GIS file
        if file_ext == 'geojson':
            with open(full_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        elif file_ext in ['zip', 'shp', 'kml', 'gml']:
            with fiona.open(str(full_path)) as src:
                features = list(src)
                return {"type": "FeatureCollection", "features": features}
        else:
            raise ValueError(f"Unsupported file format: {file_ext}")
