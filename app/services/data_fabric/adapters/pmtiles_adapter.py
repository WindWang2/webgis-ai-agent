"""
PMTiles Data Source Adapter — V2 (ADR-0094 Wave F)

相对 V1 的升级（PMTiles v3 头修复 + Wave F 契约）：
- ``_parse_header_bytes`` 按 PMTiles v3 权威 spec（protomaps/PMTiles
  spec/v3/spec.md §3）解析：bytes 0-6 魔数、byte 7 版本（=3）、bytes 8-95
  七组段偏移/长度 u64 小端（root_dir/metadata/leaf_dirs/tile_data + 计数）、
  byte 96 clustered、97/98 内部/瓦片压缩、99 tile_type、100/101 zooms、
  bytes 102+ 位置（i32 小端 × 1e-7）。（V2 曾反将 spec 判为错误 —— 见
  _parse_header_bytes docstring 的历史注。）
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

# PMTiles v3 spec（protomaps/PMTiles spec/v3/spec.md）tile_type 编码
_TILE_TYPES = {
    0: "Unknown",
    1: "MVT",
    2: "PNG",
    3: "JPEG",
    4: "WEBP",
    5: "AVIF",
    6: "MapLibre MVT",
}

# 压缩编码（header IC/TC 字段）
_COMPRESSIONS = {0: "unknown", 1: "none", 2: "gzip", 3: "brotli", 4: "zstd"}

# 有界读取上限（spec：根目录压缩后 ≤16384 字节；其余为本地安全阈）
MAX_ROOT_DIR_BYTES = 64 * 1024
MAX_LEAF_DIR_BYTES = 1024 * 1024
MAX_TILE_BYTES = int(os.getenv("PMTILES_MAX_TILE_BYTES", str(4 * 1024 * 1024)))
# query() 应答内联 tile 字节的上界（更大只报可用性；走专用 tile 通道）
INLINE_TILE_BYTES = int(os.getenv("PMTILES_INLINE_TILE_BYTES", str(64 * 1024)))

# 解压后输出上限（F1 解压炸弹防护）：gzip.decompress 对攻击者可控输入没有
# 输出上限，小体积压缩炸弹可膨胀至 GB 级；根/叶目录与瓦片各自的解压输出
# 受独立硬上限约束（与上面的“网络传输”上限相互独立）。
MAX_ROOT_DIR_DECOMPRESSED_BYTES = 4 * 1024 * 1024
MAX_LEAF_DIR_DECOMPRESSED_BYTES = 16 * 1024 * 1024
MAX_TILE_DECOMPRESSED_BYTES = max(MAX_TILE_BYTES, 64 * 1024 * 1024)


def _encode_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _decode_varints(buf: bytes, count: int, pos: int = 0):
    """解码 count 个小端 varint，返回 (values, new_pos)。"""
    values = []
    for _ in range(count):
        shift = 0
        result = 0
        while True:
            if pos >= len(buf):
                raise ValueError("truncated varint in PMTiles directory")
            b = buf[pos]
            pos += 1
            result |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
        values.append(result)
    return values, pos


# ── 有界解压 / 有界流式读取（hardening F1/F2/F3）─────────────────────────────

STREAM_CHUNK_SIZE = 64 * 1024


def _bounded_gzip_decompress(buf: bytes, max_out: int) -> bytes:
    """gzip 解压，解压输出硬上限 ``max_out``（F1 解压炸弹防护）。

    ``gzip.decompress`` 对攻击者可控输入无输出上限——小体积压缩炸弹可膨胀
    至 GB 级。这里用 ``zlib.decompressobj`` 以 ``max_length`` 分块产出，累计
    一旦超过 ``max_out`` 立即抛错中止（坏流在撑爆内存之前就被拒）。
    只解压第一个 gzip 成员（PMTiles 目录/瓦片均为单成员流）。
    """
    import zlib

    d = zlib.decompressobj(zlib.MAX_WBITS | 16)
    out = bytearray()
    data = buf
    while data:
        out += d.decompress(data, max_out - len(out) + 1)
        if len(out) > max_out:
            raise ValueError(
                f"decompressed PMTiles data ({len(out)} bytes) exceeds cap {max_out}"
            )
        if d.eof:
            break
        data = d.unconsumed_tail
    if not d.eof:
        raise ValueError("truncated gzip stream in PMTiles archive")
    return bytes(out)


def _bounded_stream_read(resp, length: int, chunk_size: int = STREAM_CHUNK_SIZE) -> bytes:
    """从流式响应读取至多 ``length`` 字节，收满即断（F2 有界响应体）。

    绝不缓冲超过 ``length + 一个 chunk``：收满 length 立即 break 并 close。
    无视 Range 回 200 + 巨大响应体的服务器在收满所需字节后被提前掐断，
    而不是整包下载。（不复用 ``security.bounded_get``：该工具一次性消费
    整个响应，无法在读取前校验 206/Content-Range，而区段读取必须先验
    状态码再读体。）
    """
    chunks = []
    total = 0
    try:
        for chunk in resp.iter_content(chunk_size=chunk_size):
            if not chunk:
                continue
            chunks.append(chunk)
            total += len(chunk)
            if total >= length:
                break
    finally:
        resp.close()
    return b"".join(chunks)[:length]


def _parse_content_range_start(content_range: str) -> int:
    """``bytes 100-226/1234`` → 100。畸形/``*``（无法核验）→ ValueError。"""
    text = content_range.strip().lower()
    if not text.startswith("bytes"):
        raise ValueError(f"unparseable Content-Range header: {content_range!r}")
    first = text[len("bytes"):].strip().split("/")[0].strip()
    try:
        start_str, _end_str = first.split("-")
        return int(start_str)
    except ValueError:
        raise ValueError(
            f"unparseable Content-Range header: {content_range!r}"
        ) from None


def zxy_to_tile_id(z: int, x: int, y: int) -> int:
    """(z,x,y) → PMTiles TileID（Hilbert 曲线累积位置，spec §4.1）。

    spec 锚点：(0,0,0)→0；(1,0,0)→1；(1,0,1)→2；(1,1,1)→3；(1,1,0)→4；
    (2,0,0)→5。
    """
    if z < 0 or z > 26:
        raise ValueError(f"tile zoom out of range: {z}")
    if x < 0 or y < 0 or x > (1 << z) - 1 or y > (1 << z) - 1:
        raise ValueError(f"tile x/y outside zoom level: z={z} x={x} y={y}")
    acc = (4 ** z - 1) // 3 if z > 0 else 0
    # 标准 Hilbert xy2d（与 protomaps 参考实现同一旋转约定）
    n = 1 << z
    d = 0
    s = n // 2
    while s > 0:
        rx = 1 if (x & s) > 0 else 0
        ry = 1 if (y & s) > 0 else 0
        d += s * s * ((3 * rx) ^ ry)
        if ry == 0:
            if rx == 1:
                x = s - 1 - x
                y = s - 1 - y
            x, y = y, x
        s //= 2
    return acc + d


def _hilbert_d2xy(n: int, d: int) -> Tuple[int, int]:
    """Hilbert 距离 → 网格坐标（rot 按 sub-quadrant 尺度 s 旋转，
    与 protomaps 参考实现同约定）。"""
    x = y = 0
    t = d
    s = 1
    while s < n:
        rx = 1 & (t // 2)
        ry = 1 & (t ^ rx)
        # rot(s, x, y, rx, ry)
        if ry == 0:
            if rx == 1:
                x = s - 1 - x
                y = s - 1 - y
            x, y = y, x
        x += s * rx
        y += s * ry
        t //= 4
        s *= 2
    return x, y


def tile_id_to_zxy(tile_id: int) -> Tuple[int, int, int]:
    """TileID → (z,x,y)（zxy_to_tile_id 的逆；属性测试锁定互逆性）。"""
    if tile_id < 0:
        raise ValueError(f"negative tile id: {tile_id}")
    z = 0
    base = 0
    while True:
        count = 4 ** z if z > 0 else 1
        if tile_id < base + count:
            break
        base += count
        z += 1
        if z > 26:
            raise ValueError(f"tile id out of range: {tile_id}")
    x, y = _hilbert_d2xy(1 << z, tile_id - base)
    return z, x, y

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
        """按 PMTiles v3 权威 spec 解析 127 字节头。

        布局（spec/v3/spec.md §3，全部小端）：
          bytes 0-6 魔数 ``PMTiles``；byte 7 版本（=3）；
          bytes 8-95 七组 u64 LE：root_dir off/len、metadata off/len、
          leaf_dirs off/len、tile_data off/len、addressed/entries/contents 计数；
          byte 96 clustered、97 internal_compression、98 tile_compression、
          99 tile_type、100 min_zoom、101 max_zoom；
          bytes 102-109 min(lon,lat)、110-117 max(lon,lat)、118 center_zoom、
          119-126 center(lon,lat)（i32 LE × 1e-7）。

        历史注：V2 曾把 bytes 8+ 当作大端位置字段解析 —— 与权威 spec 不符，
        对真实归档会产生错误 bounds/zooms；本实现恢复 spec 真值。
        """
        if len(header_bytes) < HEADER_SIZE:
            raise ValueError("Insufficient header size for PMTiles v3")

        magic = header_bytes[0:7]
        if magic != PMTILES_MAGIC:
            raise ValueError(f"Invalid PMTiles magic bytes: {magic!r}")
        version = header_bytes[7]
        if version != PMTILES_VERSION:
            raise ValueError(f"Unsupported PMTiles version: {version} (expected 3)")

        u64s = struct.unpack("<7Q", header_bytes[8:64])
        # (root_off, root_len, meta_off, meta_len, leaf_off, leaf_len,
        #  tile_data_off) = u64s[0:7] —— 后续跟 tile_data_len 与三个计数
        tile_data_len, num_addressed, num_entries, num_contents = struct.unpack(
            "<4Q", header_bytes[64:96]
        )
        clustered = header_bytes[96]
        internal_compression = header_bytes[97]
        tile_compression = header_bytes[98]
        tile_type_code = header_bytes[99]
        min_zoom = header_bytes[100]
        max_zoom = header_bytes[101]
        min_lon, min_lat = struct.unpack("<2i", header_bytes[102:110])
        max_lon, max_lat = struct.unpack("<2i", header_bytes[110:118])
        center_zoom = header_bytes[118]
        center_lon, center_lat = struct.unpack("<2i", header_bytes[119:127])

        tile_type = _TILE_TYPES.get(tile_type_code, "Unknown")
        return {
            "magic": PMTILES_MAGIC.decode("ascii"),
            "version": version,
            "tile_type": tile_type,
            "tile_type_code": tile_type_code,
            "min_zoom": min_zoom,
            "max_zoom": max_zoom,
            "center": [center_lon / 1e7, center_lat / 1e7, center_zoom],
            "bounds": [min_lon / 1e7, min_lat / 1e7, max_lon / 1e7, max_lat / 1e7],
            "clustered": bool(clustered),
            "internal_compression": _COMPRESSIONS.get(internal_compression, "unknown"),
            "tile_compression": _COMPRESSIONS.get(tile_compression, "unknown"),
            "sections": {
                "root_dir": {"offset": u64s[0], "length": u64s[1]},
                "metadata": {"offset": u64s[2], "length": u64s[3]},
                "leaf_dirs": {"offset": u64s[4], "length": u64s[5]},
                "tile_data": {"offset": u64s[6], "length": tile_data_len},
            },
            "num_addressed_tiles": num_addressed,
            "num_tile_entries": num_entries,
            "num_tile_contents": num_contents,
        }

    def _resolve_local(self):
        """Section 44 本地路径守卫（traversal / symlink escape / 敏感目录 / 超限）。

        ``DataFabricSecurityError`` → ``SecurityBlockedError``（typed），
        与 ``_read_header`` / ``_read_range`` / ``probe`` 共用同一守卫。
        """
        try:
            return resolve_safe_local_path(
                self.endpoint,
                _local_file_roots_from_settings(),
                _local_file_max_bytes_from_settings(),
            )
        except DataFabricSecurityError as e:
            raise SecurityBlockedError(str(e)) from e

    def _read_header(self) -> Tuple[Dict[str, Any], Optional[int]]:
        """读取并解析 127 字节头（HTTP 有界 Range / 本地守卫）。typed 错误。"""
        if self.endpoint.startswith(("http://", "https://")):
            # offset==0 → 200（切片）或 206 均可；流式有界读取（F2）
            body = self._read_range(0, HEADER_SIZE, cap=HEADER_SIZE, what="header")
            return self._parse_header_bytes(body[:HEADER_SIZE]), None
        resolved = self._resolve_local()
        if not resolved.is_file():
            raise FileNotFoundError(f"PMTiles source not found: {self.endpoint}")
        with open(resolved, "rb") as f:
            header_bytes = f.read(HEADER_SIZE)
        return self._parse_header_bytes(header_bytes), resolved.stat().st_size

    def _read_range(self, offset: int, length: int, *, cap: int, what: str) -> bytes:
        """有界区段读取（HTTP Range / 本地 seek）。超限 → 类型化拒绝。

        F3：offset>0 必须返回 206 —— 200 意味着服务器无视 Range，字节来自
        文件起点（静默错位），一律拒绝；且 Content-Range 若存在，其起始偏移
        必须与请求 offset 一致。offset==0 接受 200（切片）或 206。
        F2：响应体流式有界读取，收满即断，绝不整包缓冲。
        """
        if length <= 0 or length > cap:
            raise ValueError(
                f"PMTiles {what} length {length} exceeds bound {cap}"
            )
        if self.endpoint.startswith(("http://", "https://")):
            safe_url = DataFabricSecurity.validate_url(self.endpoint, allow_private=self.allow_private)
            resp = self.session.get(
                safe_url,
                headers={"Range": f"bytes={offset}-{offset + length - 1}"},
                timeout=5,
                stream=True,
            )
            status = resp.status_code
            if offset == 0:
                if status not in (200, 206):
                    resp.close()
                    raise ValueError(f"PMTiles range read failed: HTTP {status}")
            else:
                if status != 206:
                    resp.close()
                    if status == 200:
                        raise ValueError(
                            "source is not range-capable (HTTP 200 for ranged read)"
                        )
                    raise ValueError(f"PMTiles range read failed: HTTP {status}")
                content_range = resp.headers.get("Content-Range") or ""
                if content_range:
                    start = _parse_content_range_start(content_range)
                    if start != offset:
                        resp.close()
                        raise ValueError(
                            f"PMTiles range read: Content-Range start {start} does "
                            f"not match requested offset {offset}"
                        )
            return _bounded_stream_read(resp, length)
        resolved = self._resolve_local()
        with open(resolved, "rb") as f:
            f.seek(offset)
            return f.read(length)

    @staticmethod
    def _decompress_dir(buf: bytes, internal_compression: str, max_out: int) -> bytes:
        """目录段解压（F1：解压输出受 ``max_out`` 硬上限）。"""
        if internal_compression == "gzip":
            return _bounded_gzip_decompress(buf, max_out)
        if internal_compression in ("none", "unknown"):
            return buf
        raise ValueError(
            f"unsupported PMTiles internal compression: {internal_compression!r} "
            "(brotli/zstd need an optional codec dependency)"
        )

    @staticmethod
    def _parse_directory(buf: bytes) -> List[Tuple[int, int, int, int]]:
        """目录 → 有序 entries [(tile_id, offset, length, run_length)]（spec §4.2）。"""
        n, pos = _decode_varints(buf, 1)
        n = n[0] if isinstance(n, list) else n
        ids, pos = _decode_varints(buf, n, pos)
        runs, pos = _decode_varints(buf, n, pos)
        lengths, pos = _decode_varints(buf, n, pos)
        raw_offsets, pos = _decode_varints(buf, n, pos)
        entries: List[Tuple[int, int, int, int]] = []
        prev_offset = prev_length = 0
        tile_id = 0
        for i in range(n):
            tile_id = ids[i] if i == 0 else tile_id + ids[i]  # delta 编码
            offset = (
                prev_offset + prev_length if raw_offsets[i] == 0 else raw_offsets[i] - 1
            )
            entries.append((tile_id, offset, lengths[i], runs[i]))
            prev_offset, prev_length = offset, lengths[i]
        return entries

    @staticmethod
    def _find_entry(
        entries: List[Tuple[int, int, int, int]], tile_id: int
    ) -> Optional[Tuple[int, int, int, int]]:
        lo, hi = 0, len(entries) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if entries[mid][0] == tile_id:
                return entries[mid]
            if entries[mid][0] < tile_id:
                lo = mid + 1
            else:
                hi = mid - 1
        return None

    def read_tile_bytes(self, z: int, x: int, y: int) -> Optional[Tuple[bytes, str]]:
        """按 z/x/y 读取 tile 字节（有界 range read；V3 tile-byte serving）。

        返回 ``(tile_bytes, content_type)``；瓦片不存在 → None。
        目录→叶子目录两级解析（spec 禁止更深层）。Header/根目录/瓦片各受
        独立上界约束（网络传输与解压输出各自封顶）；SSRF/本地路径守卫与
        header 读取同一套。

        F4：头内 min_zoom=0/max_zoom=0 是 spec 的“未设置”默认值而非真实
        金字塔范围，不作为 zoom 门控依据（仅当 (0,0) 之外才门控）；z/x/y
        越界 varint 检查保持不变（zxy_to_tile_id）。
        """
        if not self.endpoint:
            return None  # demo 模式无归档可读（F6：先于任何 I/O 诚实短路）
        raw = self._read_range(0, HEADER_SIZE, cap=HEADER_SIZE, what="header")
        hdr = self._parse_header_bytes(raw)
        if (hdr["min_zoom"], hdr["max_zoom"]) != (0, 0) and not (
            hdr["min_zoom"] <= z <= hdr["max_zoom"]
        ):
            return None
        tile_id = zxy_to_tile_id(z, x, y)
        sections = hdr["sections"]

        root_buf = self._read_range(
            sections["root_dir"]["offset"], sections["root_dir"]["length"],
            cap=MAX_ROOT_DIR_BYTES, what="root directory",
        )
        entries = self._parse_directory(
            self._decompress_dir(
                root_buf, hdr["internal_compression"],
                MAX_ROOT_DIR_DECOMPRESSED_BYTES,
            )
        )
        entry = self._find_entry(entries, tile_id)
        leaf_read = 0
        while entry is not None and entry[3] == 0:
            # run_length == 0 → 叶子目录引用（offset 相对 leaf 区段）
            leaf_read += 1
            if leaf_read > 1:
                raise ValueError("PMTiles archive nests leaf directories >1 level")
            leaf_buf = self._read_range(
                sections["leaf_dirs"]["offset"] + entry[1], entry[2],
                cap=MAX_LEAF_DIR_BYTES, what="leaf directory",
            )
            entries = self._parse_directory(
                self._decompress_dir(
                    leaf_buf, hdr["internal_compression"],
                    MAX_LEAF_DIR_DECOMPRESSED_BYTES,
                )
            )
            entry = self._find_entry(entries, tile_id)
        if entry is None or entry[3] == 0:
            return None
        tile_buf = self._read_range(
            sections["tile_data"]["offset"] + entry[1], entry[2],
            cap=MAX_TILE_BYTES, what="tile",
        )
        if hdr["tile_compression"] == "gzip":
            tile_buf = _bounded_gzip_decompress(tile_buf, MAX_TILE_DECOMPRESSED_BYTES)
        return tile_buf, self._tile_content_type(hdr["tile_type_code"])

    @staticmethod
    def _tile_content_type(tile_type_code: int) -> str:
        return {
            1: "application/vnd.mapbox-vector-tile",
            2: "image/png",
            3: "image/jpeg",
            4: "image/webp",
            5: "image/avif",
            6: "application/vnd.maplibre-vector-tile",
        }.get(tile_type_code, "application/octet-stream")

    def probe(self) -> bool:
        """Probe PMTiles file reachability and exact v3 magic+version.

        Truthfulness: no-endpoint = explicit demo mode (reachable); an endpoint
        that IS configured but points at a missing/unreadable source is NOT.
        F5：本地路径与 _read_header 走同一 Section 44 守卫（守卫拒绝 → typed
        SecurityBlockedError，不再裸读任意路径）；不可达（文件缺失 / 坏
        magic / HTTP 失败）→ False。
        """
        if not self.endpoint:
            return True  # explicit demo mode
        try:
            if self.endpoint.startswith(("http://", "https://")):
                # F2：流式有界读取首块而非 resp.content（200 全量响应体被掐断）
                body = self._read_range(0, HEADER_SIZE, cap=HEADER_SIZE, what="header")
                return (
                    body[:7] == PMTILES_MAGIC
                    and len(body) > 7
                    and body[7] == PMTILES_VERSION
                )
            resolved = self._resolve_local()
            if not resolved.is_file():
                return False
            with open(resolved, "rb") as f:
                buf = f.read(8)
            return buf[:7] == PMTILES_MAGIC and len(buf) > 7 and buf[7] == PMTILES_VERSION
        except SecurityBlockedError:
            raise
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
            "tile_bytes_serving",  # V3：真实 z/x/y 字节读取（read_tile_bytes）
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
                    "internal_compression": info["internal_compression"],
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
            if not is_demo:
                # V3 tile-byte serving：真实归档 → 有界 range read 取字节
                try:
                    tile_bytes = self.read_tile_bytes(int(z), int(x), int(y))
                except (ValueError, DataFabricError) as exc:
                    data["tile_read"] = f"failed: {exc}"
                else:
                    if tile_bytes is None:
                        data["tile_read"] = "tile absent in archive"
                    else:
                        body, ctype = tile_bytes
                        data["tile_content_type"] = ctype
                        if len(body) <= INLINE_TILE_BYTES:
                            import base64

                            data["tile_bytes_b64"] = base64.b64encode(body).decode("ascii")
                            data["tile_bytes_len"] = len(body)
                        else:
                            data["tile_read"] = (
                                f"tile is {len(body)} bytes; exceeds inline cap "
                                f"{INLINE_TILE_BYTES} — use read_tile_bytes channel"
                            )
                        data["tile_bytes_len"] = len(body)
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
