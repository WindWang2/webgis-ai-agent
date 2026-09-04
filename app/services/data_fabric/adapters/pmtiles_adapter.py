"""
PMTiles Data Source Adapter — V2 (ADR-0094 Wave F)

相对 V1 的升级（PMTiles v3 头修复 + Wave F 契约）：
- ``_parse_header_bytes`` 修正为 PMTiles v3 真实 spec 布局（big-endian）：
  bytes 0-6 魔数 ``PMTiles``、byte 7 版本（=3）、byte 8 tile_type、
  byte 9/10/11 min_zoom/max_zoom/min_zoom_empty、byte 12 center_zoom、
  bytes 13-16/17-20 center_lon/lat（i32 × 1e-7）、bytes 21-24/25-28/29-32/33-36
  min_lon/min_lat/max_lon/max_lat（i32 × 1e-7）。V1 的偏移（tile_type@99、
  zooms@100-101、bounds@102-118、center@118-126、小端）全部错误。
- probe 魔数检查收紧为精确 ``b"PMTiles"`` 前缀 + version==3（保留本地/HTTP 路径）。
- describe 诚实化（审计 C2）：真实端点解析失败 → 诚实 stub（bbox=None=未知，
  绝不 fixture bounds）；demo fixture 仅存于无端点模式（is_demo=True）。
- 仍为 metadata-only serving（tile 字节读取属 Raster Runtime 范围）；
  VECTOR_TILE result mode 返回诚实 tile-strategy descriptor。
- V2：normalize → plan → 执行；QueryResult 附 plan/evidence。
"""
import logging
import os
import struct
import time
from typing import Any, Dict, List, Optional, Tuple

from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
from app.services.data_fabric.errors import DataFabricError, SecurityBlockedError
from app.services.data_fabric.query.capabilities import get_capabilities
from app.services.data_fabric.query.evidence import build_evidence
from app.services.data_fabric.query.normalize import normalize_query_spec
from app.services.data_fabric.query.models import ResultMode
from app.services.data_fabric.query.planner import plan_query
from app.services.data_fabric.security import (
    DataFabricSecurity,
    DataFabricSecurityError,
    _local_file_max_bytes_from_settings,
    _local_file_roots_from_settings,
    make_safe_session,
    resolve_safe_local_path,
)
from app.schemas.data_fabric_schema import (
    DatasetDescriptor,
    QuerySpec,
    QueryResult,
    DataFabricHealth,
    ConnectionProfile,
)

logger = logging.getLogger(__name__)

HEADER_SIZE = 127
PMTILES_MAGIC = b"PMTiles"
PMTILES_VERSION = 3
MAX_PREVIEW_TILES = 16

# PMTiles v3 spec tile_type 编码
_TILE_TYPES = {
    0: "Unknown",
    1: "MVT",
    2: "PNG",
    3: "JPEG",
    4: "WEBP",
    5: "AVIF",
}

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
    PMTiles Data Fabric Adapter (V2):
    High-efficiency tile source reader.
    Strictly avoids full GeoJSON conversion of vector/raster tile pyramids,
    providing tile metadata, bounds, and targeted z/x/y tile extraction.
    """

    def __init__(self, connection_profile: ConnectionProfile):
        super().__init__(connection_profile)
        self.endpoint = (self.profile.endpoint or "").strip()
        self.allow_private = getattr(self.profile, "allow_private", False)
        # SSRF-safe session: every request (incl. redirects) is revalidated.
        self.session = make_safe_session(allow_private=self.allow_private)

    def _parse_header_bytes(self, header_bytes: bytes) -> Dict[str, Any]:
        """按 PMTiles v3 真实 spec 解析 127 字节头（big-endian，i32 × 1e-7）。"""
        if len(header_bytes) < HEADER_SIZE:
            raise ValueError("Insufficient header size for PMTiles v3")

        magic = header_bytes[0:7]
        if magic != PMTILES_MAGIC:
            raise ValueError(f"Invalid PMTiles magic bytes: {magic!r}")
        version = header_bytes[7]
        if version != PMTILES_VERSION:
            raise ValueError(f"Unsupported PMTiles version: {version} (expected 3)")

        tile_type_code = header_bytes[8]
        tile_type = _TILE_TYPES.get(tile_type_code, "Unknown")
        min_zoom = header_bytes[9]
        max_zoom = header_bytes[10]
        min_zoom_empty = header_bytes[11]
        center_zoom = header_bytes[12]

        center_lon = struct.unpack(">i", header_bytes[13:17])[0] / 1e7
        center_lat = struct.unpack(">i", header_bytes[17:21])[0] / 1e7
        min_lon = struct.unpack(">i", header_bytes[21:25])[0] / 1e7
        min_lat = struct.unpack(">i", header_bytes[25:29])[0] / 1e7
        max_lon = struct.unpack(">i", header_bytes[29:33])[0] / 1e7
        max_lat = struct.unpack(">i", header_bytes[33:37])[0] / 1e7

        return {
            "magic": PMTILES_MAGIC.decode("ascii"),
            "version": version,
            "tile_type": tile_type,
            "tile_type_code": tile_type_code,
            "min_zoom": min_zoom,
            "max_zoom": max_zoom,
            "min_zoom_empty": bool(min_zoom_empty),
            "center": [center_lon, center_lat, center_zoom],
            "bounds": [min_lon, min_lat, max_lon, max_lat],
        }

    def _read_header(self) -> Tuple[Dict[str, Any], Optional[int]]:
        """读取并解析 127 字节头（本地守卫 / HTTP Range）。typed 错误。"""
        if self.endpoint.startswith(("http://", "https://")):
            safe_url = DataFabricSecurity.validate_url(self.endpoint, allow_private=self.allow_private)
            resp = self.session.get(safe_url, headers={"Range": f"bytes=0-{HEADER_SIZE - 1}"}, timeout=5)
            if resp.status_code not in (200, 206):
                raise ValueError(f"PMTiles endpoint responded HTTP {resp.status_code}")
            return self._parse_header_bytes(resp.content[:HEADER_SIZE]), None
        # 本地路径：Section 44 守卫（traversal / symlink escape / 敏感目录 / 超限）
        try:
            resolved = resolve_safe_local_path(
                self.endpoint,
                _local_file_roots_from_settings(),
                _local_file_max_bytes_from_settings(),
            )
        except DataFabricSecurityError as e:
            raise SecurityBlockedError(str(e)) from e
        if not resolved.is_file():
            raise FileNotFoundError(f"PMTiles source not found: {self.endpoint}")
        with open(resolved, "rb") as f:
            header_bytes = f.read(HEADER_SIZE)
        return self._parse_header_bytes(header_bytes), resolved.stat().st_size

    def probe(self) -> bool:
        """Probe PMTiles file reachability and exact v3 magic+version.

        Truthfulness: no-endpoint = explicit demo mode (reachable); an endpoint
        that IS configured but points at a missing/unreadable source is NOT.
        """
        if not self.endpoint:
            return True  # explicit demo mode
        try:
            if self.endpoint.startswith(("http://", "https://")):
                safe_url = DataFabricSecurity.validate_url(self.endpoint, allow_private=self.allow_private)
                resp = self.session.get(safe_url, headers={"Range": f"bytes=0-{HEADER_SIZE - 1}"}, timeout=5)
                return (
                    resp.status_code in (200, 206)
                    and resp.content[:7] == PMTILES_MAGIC
                    and len(resp.content) > 7
                    and resp.content[7] == PMTILES_VERSION
                )
            elif os.path.isfile(self.endpoint):
                with open(self.endpoint, "rb") as f:
                    buf = f.read(8)
                return buf[:7] == PMTILES_MAGIC and len(buf) > 7 and buf[7] == PMTILES_VERSION
            return False
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
        """Fetch PMTiles header metadata, zoom range, vector layers, and spatial extent.

        审计 C2：真实端点解析失败 → 诚实 stub（srs/bbox/feature_count=None =
        未知），绝不 fixture bounds 冒充远端数据；demo fixture 仅存于无端点
        模式（is_demo=True）。
        """
        if not self.endpoint:
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
                    "is_demo": True,
                    "source": "synthetic-demo",
                },
            )

        try:
            info, file_size = self._read_header()
            # 诚实 bounds：头声明的包围盒（全 0 = 未声明 → None=未知），绝不伪造
            bounds = info["bounds"]
            if bounds == [0.0, 0.0, 0.0, 0.0]:
                bounds = None
            center = info["center"] if info["center"] != [0.0, 0.0, 0] else None
            return DatasetDescriptor(
                id=dataset_id,
                title=os.path.basename(self.endpoint),
                description=(
                    f"PMTiles pyramid container ({info['tile_type']}, "
                    f"zoom {info['min_zoom']}-{info['max_zoom']})"
                ),
                source_type="pmtiles",
                geometry_type="TilePyramid",
                data_type="tile",
                feature_type="tile",
                srs="EPSG:3857",  # PMTiles 金字塔按 spec 即 web-mercator
                bbox=bounds,
                feature_count=None,  # tile 容器无 feature 概念 → 未知
                fields=[],
                metadata={
                    "tile_type": info["tile_type"],
                    "min_zoom": info["min_zoom"],
                    "max_zoom": info["max_zoom"],
                    "min_zoom_empty": info["min_zoom_empty"],
                    "center": center,
                    "file_size": file_size,
                    "no_full_geojson_conversion": True,
                    "is_demo": False,
                },
            )
        except DataFabricError as e:
            logger.warning(f"PMTiles describe error for '{dataset_id}': {e}")
            return self._honest_stub(dataset_id, e.code, str(e))
        except Exception as e:
            logger.warning(f"PMTiles describe error for '{dataset_id}': {e}")
            return self._honest_stub(dataset_id, "SOURCE_BAD_RESPONSE", str(e))

    def _honest_stub(self, dataset_id: str, error_type: str, message: str) -> DatasetDescriptor:
        return DatasetDescriptor(
            id=dataset_id,
            title=dataset_id,
            description=f"PMTiles dataset (descriptor unavailable: {message})",
            source_type="pmtiles",
            geometry_type="TilePyramid",
            data_type="tile",
            feature_type="tile",
            srs=None,
            bbox=None,  # 无伪造 bounds（绝不 fixture 世界框）
            feature_count=None,
            fields=[],
            metadata={
                "error_type": error_type,
                "error": message,
                "no_full_geojson_conversion": True,
                "is_demo": False,
            },
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
        """V2: normalize → plan → metadata-only tile 策略应答。

        严格避免 full GeoJSON conversion（tile 字节读取属 Raster Runtime
        范围）；VECTOR_TILE result mode 返回诚实 tile-strategy descriptor
        （bounds 来自解析头或 None=未知，绝不伪造）。
        """
        started = time.monotonic()
        v2 = normalize_query_spec(query_spec)  # 失败抛 typed InvalidQueryError

        descriptor = self.describe(dataset_id)
        meta = descriptor.metadata
        from app.services.data_fabric.fingerprint import dataset_fingerprint_service

        fp = dataset_fingerprint_service.calculate_descriptor_fingerprint(descriptor)
        caps = get_capabilities("pmtiles")
        plan = plan_query(v2, descriptor, caps, source_id=self.profile.id, dataset_fingerprint=fp)

        is_demo = not self.endpoint
        src = "synthetic-demo" if is_demo else "remote"
        tile_requested = v2.output.mode == ResultMode.VECTOR_TILE or bool(
            getattr(query_spec, "tile_coords", None)
        )

        if tile_requested:
            tile = query_spec.tile_coords or {}
            z = tile.get("z", meta.get("min_zoom", 0))
            x = tile.get("x", 0)
            y = tile.get("y", 0)
            data = {
                "tile_coord": {"z": z, "x": x, "y": y},
                "tile_type": meta.get("tile_type"),
                "status": "tile_registered",
                "full_geojson_conversion": False,
                # 诚实 tile 策略：bounds 来自解析头（demo fixture）或 None=未知
                "bounds": descriptor.bbox,
                "tile_strategy": "pmtiles_range_read",
            }
        else:
            target_zoom = query_spec.zoom if query_spec.zoom is not None else meta.get("min_zoom", 0)
            query_bbox = query_spec.bbox or descriptor.bbox
            data = {
                "tile_source": self.endpoint or f"pmtiles://{dataset_id}",
                "target_zoom": target_zoom,
                "query_bbox": query_bbox,
                "bounds": descriptor.bbox,
                "tile_type": meta.get("tile_type"),
                "full_geojson_conversion": False,
                "tile_strategy": "pmtiles_range_read",
            }

        result_mode = "vector_tile" if tile_requested else plan.result_mode.value
        evidence = build_evidence(
            plan, started_at=started, result_count=1, total_matching=1,
            rows_fetched=0, rows_returned=0,  # metadata-only：零数据行传输
        )
        return QueryResult(
            dataset_id=dataset_id,
            features=[],
            data=data,
            total_count=1,
            returned_count=1,
            payload_type="tile_metadata",
            result_mode=result_mode,
            is_demo=is_demo,
            execution_time_seconds=round(time.monotonic() - started, 4),
            schema_info={"tile_type": meta.get("tile_type")},
            metadata={
                "exec_time_ms": round((time.monotonic() - started) * 1000, 2),
                "full_geojson_conversion": False,
                "source": src,
                "is_demo": is_demo,
                "query_plan": plan.model_dump(),
                "query_evidence": evidence.model_dump(),
            },
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
