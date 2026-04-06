from typing import Any, Dict
from sqlalchemy import text
from app.core.database import SessionLocal
from .base import DataSourceAdapter

class PostGISAdapter(DataSourceAdapter):
    def query(self, query_params: Dict[str, Any]) -> Any:
        """
        Query PostGIS database:
        Supported params: table, bbox, geometry_column, properties, filter
        Returns GeoJSON FeatureCollection
        """
        table = query_params.get("table")
        bbox = query_params.get("bbox")
        geom_col = query_params.get("geometry_column", "geom")
        properties = query_params.get("properties", "*")
        filter_condition = query_params.get("filter", "1=1")

        if not table:
            raise ValueError("Table name is required for PostGIS query")

        db = SessionLocal()
        try:
            # Build GeoJSON query
            if bbox and len(bbox) == 4:
                minx, miny, maxx, maxy = bbox
                bbox_filter = f"ST_Intersects({geom_col}, ST_MakeEnvelope({minx}, {miny}, {maxx}, {maxy}, 4326))"
                filter_condition = f"({filter_condition}) AND {bbox_filter}"

            sql = text(f"""
                SELECT json_build_object(
                    'type', 'FeatureCollection',
                    'features', json_agg(
                        json_build_object(
                            'type', 'Feature',
                            'geometry', ST_AsGeoJSON({geom_col})::json,
                            'properties', json_build_object({','.join([f"'{p}', {p}" for p in properties.split(',')]) if properties != '*' else "'*', to_jsonb(t) - 'geom'"})
                        )
                    )
                ) as geojson
                FROM {table} t
                WHERE {filter_condition}
            """)

            result = db.execute(sql)
            geojson = result.scalar_one_or_none()
            return geojson or {"type": "FeatureCollection", "features": []}
        finally:
            db.close()
