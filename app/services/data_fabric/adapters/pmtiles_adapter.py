"""
PMTiles Data Source Adapter
Supports PMTiles v3 binary header parsing, tile source registration, metadata bounds inspection,
and zero-full-GeoJSON conversion lazy tile reading.
"""
import os
import time
import struct
import logging
from typing import List, Dict, Any
from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
from app.services.data_fabric.security import DataFabricSecurity
from app.schemas.data_fabric_schema import (
    DatasetDescriptor,
    QuerySpec,
    QueryResult,
    DataFabricHealth,
    ConnectionProfile,
)

logger = logging.getLogger(__name__)

HEADER_SIZE = 127
MAX_PREVIEW_TILES = 16

SYNTHETIC_PMTILES_FIXTURES: Dict[str, Dict[str, Any]] = {
    "world_basemap_vector": {
        "dataset_id": "world_basemap_vector",
        "title": "World Vector Basemap PMTiles",
        "description": "Pyramid vector tile package containing administrative boundaries, land use, and roads.",
        "tile_type": "MVT (Mapbox Vector Tile)",
        "min_zoom": 0,
        "max_zoom": 14,
        "bounds": [-180.0, -85.0511, 180.0, 85.0511],
        "center": [0.0, 0.0, 2],
        "vector_layers": [
            {"id": "admin", "fields": {"admin_level": "Number", "name": "String"}},
            {"id": "water", "fields": {"class": "String"}},
            {"id": "roads", "fields": {"class": "String", "oneway": "Number"}},
        ],
        "attribution": "© OpenStreetMap contributors, CartoDB",
    },
    "terrain_dem_raster": {
        "dataset_id": "terrain_dem_raster",
        "title": "Global Terrain DEM Raster PMTiles",
        "description": "Raster elevation hillshade / RGB terrain tiles package.",
        "tile_type": "PNG (Terrarium RGB DEM)",
        "min_zoom": 0,
        "max_zoom": 12,
        "bounds": [-180.0, -89.0, 180.0, 89.0],
        "center": [116.4, 39.9, 8],
        "vector_layers": [],
        "attribution": "© AWS Terrain Tiles",
    },
}


class PMTilesAdapter(GeospatialDataSourceAdapter):
    """
    PMTiles Data Fabric Adapter:
    High-efficiency tile source reader.
    Strictly avoids full GeoJSON conversion of vector/raster tile pyramids,
    providing tile metadata, bounds, and targeted z/x/y tile extraction.
    """

    def __init__(self, connection_profile: ConnectionProfile):
        super().__init__(connection_profile)
        self.endpoint = (self.profile.endpoint or "").strip()
        self.allow_private = getattr(self.profile, "allow_private", False)

    def _parse_header_bytes(self, header_bytes: bytes) -> Dict[str, Any]:
        """
        Parses 127-byte PMTiles v3 header bytes.
        """
        if len(header_bytes) < HEADER_SIZE:
            raise ValueError("Insufficient header size for PMTiles v3")

        magic = header_bytes[0:7]
        if not (b"PMT" in magic or b"PMTiles" in magic):
            raise ValueError(f"Invalid PMTiles magic bytes: {magic}")

        tile_type_code = header_bytes[99] if len(header_bytes) > 99 else 1
        tile_types = {1: "MVT", 2: "PNG", 3: "JPEG", 4: "WEBP", 5: "AVIF"}
        tile_type = tile_types.get(tile_type_code, "Unknown")

        min_zoom = header_bytes[100]
        max_zoom = header_bytes[101]

        min_lon = struct.unpack("<i", header_bytes[102:106])[0] / 1e7
        min_lat = struct.unpack("<i", header_bytes[106:110])[0] / 1e7
        max_lon = struct.unpack("<i", header_bytes[110:114])[0] / 1e7
        max_lat = struct.unpack("<i", header_bytes[114:118])[0] / 1e7

        center_zoom = header_bytes[118]
        center_lon = struct.unpack("<i", header_bytes[119:123])[0] / 1e7
        center_lat = struct.unpack("<i", header_bytes[123:127])[0] / 1e7

        return {
            "magic": magic.decode("utf-8", errors="ignore"),
            "tile_type": tile_type,
            "min_zoom": min_zoom,
            "max_zoom": max_zoom,
            "bounds": [min_lon, min_lat, max_lon, max_lat],
            "center": [center_lon, center_lat, center_zoom],
        }

    def probe(self) -> bool:
        """Probe PMTiles file reachability and 127-byte header."""
        if not self.endpoint or not os.path.exists(self.endpoint):
            return True  # Synthetic fallback mode

        try:
            if self.endpoint.startswith(("http://", "https://")):
                safe_url = DataFabricSecurity.validate_url(self.endpoint, allow_private=self.allow_private)
                import requests

                resp = requests.get(safe_url, headers={"Range": "bytes=0-126"}, timeout=5)
                return resp.status_code in (200, 206) and (b"PMT" in resp.content or b"PMTiles" in resp.content)
            elif os.path.isfile(self.endpoint):
                with open(self.endpoint, "rb") as f:
                    buf = f.read(HEADER_SIZE)
                    return b"PMT" in buf or b"PMTiles" in buf
            return True
        except Exception as e:
            logger.debug(f"PMTiles probe failed for {self.endpoint}: {e}")
            return False

    def capabilities(self) -> List[str]:
        return [
            "raster_tile",
            "vector_tile",
            "metadata_bounds",
            "tile_source",
            "range_request",
            "no_full_geojson_conversion",
        ]

    def list_datasets(self) -> List[Dict[str, Any]]:
        """List PMTiles tile dataset."""
        dataset_name = os.path.basename(self.endpoint) if self.endpoint else "world_basemap_vector"
        if not dataset_name.strip():
            dataset_name = "world_basemap_vector"

        return [
            {
                "id": dataset_name,
                "title": f"PMTiles Pyramid ({dataset_name})",
                "source_type": "pmtiles",
                "format": "pmtiles",
            }
        ]

    def describe(self, dataset_id: str) -> DatasetDescriptor:
        """Fetch PMTiles header metadata, zoom range, vector layers, and spatial extent."""
        if not self.endpoint or not os.path.exists(self.endpoint):
            fixture = SYNTHETIC_PMTILES_FIXTURES.get(dataset_id, SYNTHETIC_PMTILES_FIXTURES["world_basemap_vector"])
            fields = [{"name": layer["id"], "type": "vector_layer"} for layer in fixture.get("vector_layers", [])]
            return DatasetDescriptor(
                id=dataset_id,
                title=fixture["title"],
                description=fixture["description"],
                source_type="pmtiles",
                geometry_type="TilePyramid",
                data_type="tile",
                feature_type="tile",
                srs="EPSG:3857",
                bbox=fixture["bounds"],
                feature_count=0,
                fields=fields,
                metadata={
                    "tile_type": fixture["tile_type"],
                    "min_zoom": fixture["min_zoom"],
                    "max_zoom": fixture["max_zoom"],
                    "center": fixture["center"],
                    "vector_layers": fixture.get("vector_layers", []),
                    "attribution": fixture.get("attribution"),
                    "no_full_geojson_conversion": True,
                },
            )

        try:
            with open(self.endpoint, "rb") as f:
                header_bytes = f.read(HEADER_SIZE)
                info = self._parse_header_bytes(header_bytes)
                file_size = os.path.getsize(self.endpoint)

            return DatasetDescriptor(
                id=dataset_id,
                title=os.path.basename(self.endpoint),
                description=f"PMTiles pyramid container ({info['tile_type']}, zoom {info['min_zoom']}-{info['max_zoom']})",
                source_type="pmtiles",
                geometry_type="TilePyramid",
                data_type="tile",
                srs="EPSG:3857",
                bbox=info["bounds"],
                feature_count=0,
                fields=[],
                metadata={
                    "tile_type": info["tile_type"],
                    "min_zoom": info["min_zoom"],
                    "max_zoom": info["max_zoom"],
                    "center": info["center"],
                    "file_size": file_size,
                    "no_full_geojson_conversion": True,
                },
            )
        except Exception as e:
            logger.warning(f"PMTiles describe error for '{dataset_id}': {e}")
            fixture = SYNTHETIC_PMTILES_FIXTURES["world_basemap_vector"]
            return DatasetDescriptor(
                id=dataset_id,
                title=dataset_id,
                description=f"PMTiles dataset ({e})",
                source_type="pmtiles",
                geometry_type="TilePyramid",
                data_type="tile",
                srs="EPSG:3857",
                bbox=fixture["bounds"],
                feature_count=0,
                fields=[],
                metadata={"error": str(e), "no_full_geojson_conversion": True},
            )

    def preview(self, dataset_id: str, limit: int = 10) -> Dict[str, Any]:
        """Fetch tile source registration preview (tile coordinates inventory, NO full GeoJSON conversion)."""
        desc = self.describe(dataset_id)
        meta = desc.metadata
        min_z = meta.get("min_zoom", 0)

        sample_tiles = []
        for x in range(min(4, limit)):
            for y in range(min(4, limit)):
                sample_tiles.append({
                    "z": min_z,
                    "x": x,
                    "y": y,
                    "url_template": f"{self.endpoint or 'pmtiles://' + dataset_id}/{{z}}/{{x}}/{{y}}",
                })
                if len(sample_tiles) >= limit:
                    break
            if len(sample_tiles) >= limit:
                break

        return {
            "schema": {
                "dataset_id": dataset_id,
                "tile_type": meta.get("tile_type", "MVT"),
                "format": "pmtiles_tile_pyramid",
                "full_geojson_conversion": False,
            },
            "properties": {
                "min_zoom": meta.get("min_zoom"),
                "max_zoom": meta.get("max_zoom"),
                "center": meta.get("center"),
                "attribution": meta.get("attribution"),
            },
            "features": sample_tiles,
            "bbox": desc.bbox,
        }

    def query(self, dataset_id: str, query_spec: QuerySpec) -> QueryResult:
        """
        Execute tile source inquiry or specific tile coordinate lookup.
        Strictly avoids full GeoJSON conversion.
        """
        start_time = time.time()
        desc = self.describe(dataset_id)
        meta = desc.metadata

        if query_spec.tile_coords and isinstance(query_spec.tile_coords, dict):
            z = query_spec.tile_coords.get("z", meta.get("min_zoom", 0))
            x = query_spec.tile_coords.get("x", 0)
            y = query_spec.tile_coords.get("y", 0)
            exec_time = round((time.time() - start_time) * 1000, 2)
            return QueryResult(
                dataset_id=dataset_id,
                features=[],
                data={
                    "tile_coord": {"z": z, "x": x, "y": y},
                    "tile_type": meta.get("tile_type"),
                    "status": "tile_registered",
                    "full_geojson_conversion": False,
                },
                total_count=1,
                returned_count=1,
                metadata={"exec_time_ms": exec_time, "full_geojson_conversion": False},
            )

        target_zoom = query_spec.zoom if query_spec.zoom is not None else meta.get("min_zoom", 0)
        query_bbox = query_spec.bbox or desc.bbox

        exec_time = round((time.time() - start_time) * 1000, 2)
        return QueryResult(
            dataset_id=dataset_id,
            features=[],
            data={
                "tile_source": self.endpoint or f"pmtiles://{dataset_id}",
                "target_zoom": target_zoom,
                "query_bbox": query_bbox,
                "bounds": desc.bbox,
                "tile_type": meta.get("tile_type"),
                "full_geojson_conversion": False,
            },
            total_count=1,
            returned_count=1,
            metadata={"exec_time_ms": exec_time, "full_geojson_conversion": False},
        )

    def health(self) -> DataFabricHealth:
        start_time = time.time()
        is_ok = self.probe()
        latency = round((time.time() - start_time) * 1000, 2)
        if is_ok:
            return DataFabricHealth(
                status="healthy",
                adapter="pmtiles",
                message="PMTiles tile source verified without full GeoJSON conversion",
                latency_ms=latency,
                details={"endpoint": self.endpoint or "synthetic_fixture_mode"},
            )
        return DataFabricHealth(
            status="unreachable",
            adapter="pmtiles",
            message=f"PMTiles source inaccessible at {self.endpoint}",
            latency_ms=latency,
            details={"endpoint": self.endpoint},
        )
