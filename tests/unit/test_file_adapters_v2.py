"""文件型适配器 V2 契约测试（ADR-0094 Wave F）。

覆盖：
- GeoParquet：真实 parquet 文件（tmp_path）上的列投影 / limit / where /
  STATISTICS（group_by 聚合）/ demo is_demo 标注 / 真实端点失败 → typed
  raise 且绝不泄漏 SYNTHETIC_US_STATES fixture（审计 C2）。
- FlatGeobuf：demo is_demo 标注 + 真实端点不存在路径 → typed raise。
- PMTiles：按 v3 真实 spec 构造 127 字节头，断言 _parse_header_bytes 的
  tile_type / zooms / bounds 解析（V1 的错误偏移回归）。
- S3 seam：query 不再伪造 bytes_read（=0）且携带诚实 note（审计 minor-4）。
"""
import struct

import pytest

from app.schemas.data_fabric_schema import ConnectionProfile, QuerySpec
from app.services.data_fabric.errors import SourceUnreachableError

# ── GeoParquet：真实 parquet 文件 ────────────────────────────────────────────


def _build_attr_parquet(tmp_path, n_rows=50):
    """构造纯属性 parquet（id/name/val，无 geometry 列）。"""
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table({
        "id": list(range(n_rows)),
        "name": [f"row_{i % 5}" for i in range(n_rows)],
        "val": [i * 1.5 for i in range(n_rows)],
    })
    path = str(tmp_path / "attrs.parquet")
    pq.write_table(table, path)
    return path


def _gp_adapter(monkeypatch, tmp_path, endpoint):
    """GeoParquetAdapter，其本地文件根指向 tmp_path（Section 44 守卫）。"""
    from app.services.data_fabric.adapters import geoparquet_adapter as gp_mod
    from app.services.data_fabric.adapters.geoparquet_adapter import GeoParquetAdapter

    monkeypatch.setattr(gp_mod, "_local_file_roots_from_settings", lambda: [str(tmp_path)])
    monkeypatch.setattr(gp_mod, "_local_file_max_bytes_from_settings", lambda: 64 * 1024 * 1024)
    return GeoParquetAdapter(ConnectionProfile(
        source_type="geoparquet",
        endpoint_url=endpoint,
        name="test_geoparquet",
    ))


@pytest.fixture
def attr_parquet(tmp_path):
    pytest.importorskip("pyarrow")
    return _build_attr_parquet(tmp_path)


def test_geoparquet_projection_returns_only_requested_columns(monkeypatch, tmp_path, attr_parquet):
    adapter = _gp_adapter(monkeypatch, tmp_path, attr_parquet)
    res = adapter.query("attrs", QuerySpec(limit=10, fields=["name"]))
    assert res.returned_count == 10
    assert res.metadata["column_projection"] is True
    for feat in res.features:
        assert set(feat["properties"].keys()) == {"name"}, "unprojected columns must not leak"


def test_geoparquet_limit_respected(monkeypatch, tmp_path, attr_parquet):
    adapter = _gp_adapter(monkeypatch, tmp_path, attr_parquet)
    res = adapter.query("attrs", QuerySpec(limit=7))
    assert res.returned_count == 7
    assert res.metadata["query_plan"]["source_type"] == "geoparquet"
    assert res.metadata["is_demo"] is False


def test_geoparquet_where_filter_applied(monkeypatch, tmp_path, attr_parquet):
    adapter = _gp_adapter(monkeypatch, tmp_path, attr_parquet)
    res = adapter.query("attrs", QuerySpec(limit=100, where="val >= 25"))
    vals = [f["properties"]["val"] for f in res.features]
    assert vals, "filtered result must be non-empty"
    assert all(v >= 25 for v in vals)
    # id 0..16 → val 0..24（val=i*1.5 < 25），id>=17 保留 → 33 行
    assert len(vals) == 33


def test_geoparquet_offset_pagination_with_has_more(monkeypatch, tmp_path, attr_parquet):
    """哨兵行语义：窗口读满 → has_more=True；扫尽 → has_more=False 且
    total_matching=全量（回归：scan 提前停在窗口边界导致 has_more 恒 False）。"""
    adapter = _gp_adapter(monkeypatch, tmp_path, attr_parquet)
    page1 = adapter.query("attrs", QuerySpec(limit=10, offset=0))
    assert page1.returned_count == 10
    assert page1.has_more is True and page1.truncated is True
    assert page1.total_matching is None  # 未扫尽 → 未知

    page5 = adapter.query("attrs", QuerySpec(limit=10, offset=40))
    assert [f["properties"]["id"] for f in page5.features] == list(range(40, 50))
    assert page5.has_more is False
    assert page5.total_matching == 50  # 扫尽 → 诚实总量


def test_geoparquet_sample_mode_deterministic(monkeypatch, tmp_path, attr_parquet):
    adapter = _gp_adapter(monkeypatch, tmp_path, attr_parquet)
    a = adapter.query("attrs", QuerySpec(limit=5, result_mode="sample", sample_size=7))
    b = adapter.query("attrs", QuerySpec(limit=5, result_mode="sample", sample_size=7))
    assert a.result_mode == "sample"
    assert len(a.features) == 7
    assert [f["properties"]["id"] for f in a.features] == [f["properties"]["id"] for f in b.features]


def test_geoparquet_statistics_group_by(monkeypatch, tmp_path, attr_parquet):
    adapter = _gp_adapter(monkeypatch, tmp_path, attr_parquet)
    spec = QuerySpec(
        limit=100,
        aggregate=[{"func": "count"}],
        group_by=["name"],
    )
    res = adapter.query("attrs", spec)
    assert res.result_mode == "statistics"
    assert res.features == []  # 聚合行不是 features
    rows = res.data
    assert len(rows) == 5  # name 有 5 个取值 row_0..row_4
    by_name = {r["name"]: r["count"] for r in rows}
    assert all(c == 10 for c in by_name.values())  # 50 行 / 5 组


def test_geoparquet_demo_mode_is_labeled():
    from app.services.data_fabric.adapters.geoparquet_adapter import GeoParquetAdapter

    adapter = GeoParquetAdapter(ConnectionProfile(source_type="geoparquet", endpoint_url=""))
    res = adapter.query("us_states_geoparquet", QuerySpec(limit=5))
    assert res.metadata["is_demo"] is True
    assert res.metadata["source"] == "synthetic-demo"
    assert res.is_demo is True


def test_geoparquet_real_endpoint_failure_raises_typed_never_fixture(monkeypatch, tmp_path, attr_parquet):
    """审计 C2：真实端点读失败 → typed raise，绝不回落 SYNTHETIC fixture。"""
    adapter = _gp_adapter(monkeypatch, tmp_path, str(tmp_path / "missing.parquet"))
    with pytest.raises(SourceUnreachableError):
        adapter.query("us_states_geoparquet", QuerySpec(limit=5))

    # describe 同样诚实：stub 无伪造 count，也不携带 fixture 元数据
    desc = adapter.describe("us_states_geoparquet")
    assert desc.feature_count is None
    assert desc.metadata.get("is_demo") is False
    assert desc.metadata.get("error_type")


def test_geoparquet_real_endpoint_failure_does_not_leak_fixture_features(monkeypatch, tmp_path, attr_parquet):
    """typed raise 之外，任何路径都不得把 50 州 fixture 当作真实数据返回。"""
    from app.services.data_fabric.adapters.geoparquet_adapter import (
        SYNTHETIC_GEOPARQUET_FIXTURES,
    )

    adapter = _gp_adapter(monkeypatch, tmp_path, str(tmp_path / "missing.parquet"))
    raised = False
    try:
        res = adapter.query("us_states_geoparquet", QuerySpec(limit=5))
    except SourceUnreachableError:
        raised = True
        res = None
    assert raised, "real-endpoint failure must raise typed SourceUnreachableError"
    if res is not None:  # 若未来回到 in-band 语义，兜底断言不泄漏 fixture 特征
        fixture_states = {
            f["properties"]["state_name"]
            for fx in SYNTHETIC_GEOPARQUET_FIXTURES.values()
            for f in fx["features"]
        }
        assert not fixture_states & {
            (f.get("properties") or {}).get("state_name") for f in res.features
        }
        assert res.returned_count != 50
    assert adapter.probe() is False


def test_geoparquet_corrupt_file_raises_bad_response(monkeypatch, tmp_path):
    """存在但非 parquet 的文件 → SourceBadResponseError（诚实失败）。"""
    pytest.importorskip("pyarrow")
    bad = tmp_path / "bad.parquet"
    bad.write_bytes(b"NOT A PARQUET FILE" * 8)
    adapter = _gp_adapter(monkeypatch, tmp_path, str(bad))
    from app.services.data_fabric.errors import SourceBadResponseError

    with pytest.raises(SourceBadResponseError):
        adapter.query("bad", QuerySpec(limit=5))


# ── GeoParquet：几何路径（需 geopandas 写带 WKB 几何列的文件）──────────────


def test_geoparquet_bbox_filters_real_geometry(monkeypatch, tmp_path):
    geopandas = pytest.importorskip("geopandas")
    pytest.importorskip("pyarrow")

    gdf = geopandas.GeoDataFrame(
        {
            "city": ["west", "east"],
            "geometry": geopandas.points_from_xy([-120.0, -70.0], [40.0, 40.0]),
        },
        crs="EPSG:4326",
    )
    path = str(tmp_path / "points.parquet")
    gdf.to_parquet(path)

    adapter = _gp_adapter(monkeypatch, tmp_path, path)
    res = adapter.query("points", QuerySpec(limit=10, bbox=[-125.0, 35.0, -115.0, 45.0]))
    assert [f["properties"]["city"] for f in res.features] == ["west"]
    res2 = adapter.query("points", QuerySpec(limit=10, bbox=[-75.0, 35.0, -65.0, 45.0]))
    assert [f["properties"]["city"] for f in res2.features] == ["east"]


def test_geoparquet_footer_count_statistics(monkeypatch, tmp_path, attr_parquet):
    """无过滤纯 count → footer num_rows（零扫描）。"""
    adapter = _gp_adapter(monkeypatch, tmp_path, attr_parquet)
    res = adapter.query("attrs", QuerySpec(limit=1, aggregate=[{"func": "count"}]))
    assert res.result_mode == "statistics"
    assert res.data == [{"count": 50}]
    assert res.metadata["footer_count_used"] is True


# ── FlatGeobuf ──────────────────────────────────────────────────────────────


def test_flatgeobuf_demo_mode_is_labeled():
    from app.services.data_fabric.adapters.flatgeobuf_adapter import FlatGeobufAdapter

    adapter = FlatGeobufAdapter(ConnectionProfile(source_type="flatgeobuf", endpoint_url=""))
    res = adapter.query("beijing_subway_stations", QuerySpec(limit=5))
    assert res.metadata["is_demo"] is True
    assert res.metadata["source"] == "synthetic-demo"
    assert res.is_demo is True


def test_flatgeobuf_nonexistent_real_path_raises_typed(monkeypatch, tmp_path):
    from app.services.data_fabric.adapters import flatgeobuf_adapter as fgb_mod
    from app.services.data_fabric.adapters.flatgeobuf_adapter import FlatGeobufAdapter

    monkeypatch.setattr(fgb_mod, "_local_file_roots_from_settings", lambda: [str(tmp_path)])
    monkeypatch.setattr(fgb_mod, "_local_file_max_bytes_from_settings", lambda: 64 * 1024 * 1024)
    adapter = FlatGeobufAdapter(ConnectionProfile(
        source_type="flatgeobuf",
        endpoint_url=str(tmp_path / "missing.fgb"),
    ))
    with pytest.raises(SourceUnreachableError):
        adapter.query("beijing_subway_stations", QuerySpec(limit=5))
    # describe 诚实 stub：无 fixture 元数据冒充
    desc = adapter.describe("beijing_subway_stations")
    assert desc.feature_count is None
    assert desc.metadata.get("is_demo") is False
    assert desc.metadata.get("error_type")


# ── PMTiles v3 header 解析 ──────────────────────────────────────────────────


def _build_pmtiles_v3_header(
    tile_type=1,
    min_zoom=0,
    max_zoom=14,
    center_zoom=2,
    center=(-10.5, 40.25),
    bounds=(-120.0, -20.0, 60.0, 70.0),
    sections=None,
):
    """按 PMTiles v3 权威 spec（spec/v3/spec.md §3，小端）构造 127 字节头。

    bytes 8-95 = 七组 u64 LE 段字段；96 clustered；97/98 压缩；99 tile_type；
    100/101 zooms；102+ 位置（i32 LE × 1e-7）。
    """
    center_lon, center_lat = center
    min_lon, min_lat, max_lon, max_lat = bounds
    buf = bytearray(127)
    buf[0:7] = b"PMTiles"
    buf[7] = 3                     # version
    s = sections or {}
    root = s.get("root_dir", {"offset": 127, "length": 0})
    meta = s.get("metadata", {"offset": 127, "length": 0})
    leaf = s.get("leaf_dirs", {"offset": 0, "length": 0})
    tdata = s.get("tile_data", {"offset": 127, "length": 0})
    struct.pack_into("<Q", buf, 8, root["offset"])
    struct.pack_into("<Q", buf, 16, root["length"])
    struct.pack_into("<Q", buf, 24, meta["offset"])
    struct.pack_into("<Q", buf, 32, meta["length"])
    struct.pack_into("<Q", buf, 40, leaf["offset"])
    struct.pack_into("<Q", buf, 48, leaf["length"])
    struct.pack_into("<Q", buf, 56, tdata["offset"])
    struct.pack_into("<Q", buf, 64, tdata["length"])
    struct.pack_into("<Q", buf, 72, s.get("num_addressed", 0))
    struct.pack_into("<Q", buf, 80, s.get("num_entries", 0))
    struct.pack_into("<Q", buf, 88, s.get("num_contents", 0))
    buf[96] = s.get("clustered", 1)
    buf[97] = s.get("internal_compression", 1)   # none
    buf[98] = s.get("tile_compression", 1)       # none
    buf[99] = tile_type
    buf[100] = min_zoom
    buf[101] = max_zoom
    struct.pack_into("<2i", buf, 102, int(min_lon * 1e7), int(min_lat * 1e7))
    struct.pack_into("<2i", buf, 110, int(max_lon * 1e7), int(max_lat * 1e7))
    buf[118] = center_zoom
    struct.pack_into("<2i", buf, 119, int(center_lon * 1e7), int(center_lat * 1e7))
    return bytes(buf)


def test_pmtiles_parse_header_bytes_v3_spec():
    from app.services.data_fabric.adapters.pmtiles_adapter import PMTilesAdapter

    adapter = PMTilesAdapter(ConnectionProfile(source_type="pmtiles", endpoint_url=""))
    header = _build_pmtiles_v3_header()
    info = adapter._parse_header_bytes(header)

    assert info["magic"] == "PMTiles"
    assert info["version"] == 3
    assert info["tile_type"] == "MVT"
    assert info["min_zoom"] == 0
    assert info["max_zoom"] == 14
    assert info["center"] == [-10.5, 40.25, 2]
    assert info["bounds"] == pytest.approx([-120.0, -20.0, 60.0, 70.0], abs=1e-6)
    assert info["internal_compression"] == "none"
    assert info["tile_compression"] == "none"
    assert info["clustered"] is True
    assert info["sections"]["root_dir"]["offset"] == 127


def test_pmtiles_parse_header_rejects_wrong_magic_or_version():
    from app.services.data_fabric.adapters.pmtiles_adapter import PMTilesAdapter

    adapter = PMTilesAdapter(ConnectionProfile(source_type="pmtiles", endpoint_url=""))

    bad_magic = _build_pmtiles_v3_header()
    with pytest.raises(ValueError):
        adapter._parse_header_bytes(b"PMTile\x03" + bad_magic[7:])

    bad_version = bytearray(_build_pmtiles_v3_header())
    bad_version[7] = 2
    with pytest.raises(ValueError):
        adapter._parse_header_bytes(bytes(bad_version))

    with pytest.raises(ValueError):
        adapter._parse_header_bytes(b"\x00" * 127)


def test_pmtiles_probe_and_describe_real_file(monkeypatch, tmp_path):
    """本地真实 PMTiles 文件：probe（精确 magic+version）与 describe（头 bounds）。"""
    from app.services.data_fabric.adapters import pmtiles_adapter as pm_mod
    from app.services.data_fabric.adapters.pmtiles_adapter import PMTilesAdapter

    monkeypatch.setattr(pm_mod, "_local_file_roots_from_settings", lambda: [str(tmp_path)])
    monkeypatch.setattr(pm_mod, "_local_file_max_bytes_from_settings", lambda: 64 * 1024 * 1024)
    path = tmp_path / "basemap.pmtiles"
    path.write_bytes(_build_pmtiles_v3_header(tile_type=2, min_zoom=3, max_zoom=9))

    adapter = PMTilesAdapter(ConnectionProfile(source_type="pmtiles", endpoint_url=str(path)))
    assert adapter.probe() is True

    desc = adapter.describe("basemap.pmtiles")
    assert desc.bbox == pytest.approx([-120.0, -20.0, 60.0, 70.0], abs=1e-6)
    assert desc.metadata["min_zoom"] == 3
    assert desc.metadata["max_zoom"] == 9
    assert desc.metadata["tile_type"] == "PNG"
    assert desc.metadata["is_demo"] is False
    assert desc.feature_count is None  # tile 容器无 feature 概念 → 未知


def test_pmtiles_probe_rejects_v1_style_magic(monkeypatch, tmp_path):
    """V1 的宽松 ``b"PMT" in buf`` 检查必须收紧（否则坏文件误判可达）。"""
    from app.services.data_fabric.adapters import pmtiles_adapter as pm_mod
    from app.services.data_fabric.adapters.pmtiles_adapter import PMTilesAdapter

    monkeypatch.setattr(pm_mod, "_local_file_roots_from_settings", lambda: [str(tmp_path)])
    monkeypatch.setattr(pm_mod, "_local_file_max_bytes_from_settings", lambda: 64 * 1024 * 1024)
    path = tmp_path / "junk.pmtiles"
    path.write_bytes(b"PMTXYZ-not-a-real-header" + b"\x00" * 104)

    adapter = PMTilesAdapter(ConnectionProfile(source_type="pmtiles", endpoint_url=str(path)))
    assert adapter.probe() is False


def test_pmtiles_vector_tile_mode_honest_bounds(monkeypatch, tmp_path):
    from app.services.data_fabric.adapters import pmtiles_adapter as pm_mod
    from app.services.data_fabric.adapters.pmtiles_adapter import PMTilesAdapter

    monkeypatch.setattr(pm_mod, "_local_file_roots_from_settings", lambda: [str(tmp_path)])
    monkeypatch.setattr(pm_mod, "_local_file_max_bytes_from_settings", lambda: 64 * 1024 * 1024)
    path = tmp_path / "basemap.pmtiles"
    path.write_bytes(_build_pmtiles_v3_header())

    adapter = PMTilesAdapter(ConnectionProfile(source_type="pmtiles", endpoint_url=str(path)))
    res = adapter.query("basemap.pmtiles", QuerySpec(limit=1, tile_coords={"z": 3, "x": 2, "y": 1}))
    assert res.result_mode == "vector_tile"
    assert res.data["tile_coord"] == {"z": 3, "x": 2, "y": 1}
    assert res.data["bounds"] == pytest.approx([-120.0, -20.0, 60.0, 70.0], abs=1e-6)
    assert res.metadata["is_demo"] is False


# ── S3 seam（审计 minor-4）──────────────────────────────────────────────────


def test_s3_query_reports_zero_bytes_and_honest_note():
    from app.services.data_fabric.adapters.s3_storage_seam import S3StorageAdapter

    adapter = S3StorageAdapter(ConnectionProfile(source_type="s3", endpoint="s3://bucket/x.fgb"))
    res = adapter.query("x.fgb", QuerySpec(limit=16))
    # 审计 minor-4：metadata-only seam —— 不得伪造读取量
    assert res.data["bytes_read"] == 0
    assert "content reads go through format adapters" in res.data["note"]
    assert res.metadata["bytes_read"] == 0
    assert res.metadata["is_demo"] is True
    assert res.is_demo is True


def test_s3_describe_demo_metadata_labeled():
    from app.services.data_fabric.adapters.s3_storage_seam import S3StorageAdapter

    adapter = S3StorageAdapter(ConnectionProfile(source_type="s3", endpoint="s3://bucket/x.fgb"))
    desc = adapter.describe("s3://bucket/x.fgb")
    assert desc.metadata["source"] == "synthetic-demo"


# ── PMTiles V3 tile-byte serving（ADR-0096 / Data Fabric V3 §8）──────────────


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


def _encode_dir_entries(entries) -> bytes:
    """entries: [(tile_id, offset, length, run_length)] → varint 目录流（spec §4.2）。

    段顺序：count、tile_id 增量、run_length、length、offset。offset 采用
    ``raw = offset + 1`` 显式编码（raw=0 的“与前项连续”捷径不使用）。
    """
    buf = bytearray()
    buf += _encode_varint(len(entries))
    last = 0
    for tid, *_rest in entries:
        buf += _encode_varint(tid - last)
        last = tid
    for _t, _o, _l, rl in entries:
        buf += _encode_varint(rl)
    for _t, _o, ln, _rl in entries:
        buf += _encode_varint(ln)
    for _t, off, _l, _rl in entries:
        buf += _encode_varint(off + 1)
    return bytes(buf)


def _build_pmtiles_archive(
    tiles: dict,
    *,
    gzip_dir: bool = False,
    tile_type: int = 2,
    zooms=None,
    leaf_mode: bool = False,
) -> bytes:
    """构造最小合法 PMTiles v3 归档（header + 根目录 [+ 叶子目录] + 瓦片数据）。

    tiles: {(z,x,y): body_bytes}；run_length=1、连续偏移。
    zooms: 覆写头内 (min_zoom, max_zoom)（F4：spec 默认 0/0 = 未设置）。
    leaf_mode: 根目录只含一个 run_length=0 的叶子目录引用，真实 entries 放入
    leaf_dirs 区段（F7 叶子解析路径）；两个目录都按 gzip_dir 决定是否压缩。
    """
    from app.services.data_fabric.adapters.pmtiles_adapter import zxy_to_tile_id

    ids = sorted(zxy_to_tile_id(z, x, y) for (z, x, y) in tiles)
    bodies = [tiles[t] for t in sorted(tiles, key=lambda t: zxy_to_tile_id(*t))]

    def _maybe_gz(raw: bytes) -> bytes:
        import gzip

        return gzip.compress(raw) if gzip_dir else raw

    leaf_entries = []
    off = 0
    for tid, body in zip(ids, bodies):
        leaf_entries.append((tid, off, len(body), 1))
        off += len(body)
    leaf_stored = _maybe_gz(_encode_dir_entries(leaf_entries))
    if leaf_mode:
        # 根目录：单一叶子引用（offset=0 相对 leaf 区段；length=叶子目录存储长度）
        root_stored = _maybe_gz(_encode_dir_entries([(ids[0], 0, len(leaf_stored), 0)]))
    else:
        root_stored = _maybe_gz(_encode_dir_entries(leaf_entries))
    internal_compression = 2 if gzip_dir else 1

    root_off = 127
    root_len = len(root_stored)
    leaf_off = root_off + root_len
    leaf_len = len(leaf_stored) if leaf_mode else 0
    tile_data_off = leaf_off + leaf_len
    blob = bytearray()
    blob += _build_pmtiles_v3_header(
        tile_type=tile_type,
        min_zoom=(zooms[0] if zooms else min(t[0] for t in tiles)),
        max_zoom=(zooms[1] if zooms else max(t[0] for t in tiles)),
        sections={
            "root_dir": {"offset": root_off, "length": root_len},
            "metadata": {"offset": tile_data_off, "length": 0},
            "leaf_dirs": {"offset": leaf_off if leaf_mode else 0, "length": leaf_len},
            "tile_data": {"offset": tile_data_off, "length": sum(len(b) for b in bodies)},
            "num_addressed": len(tiles),
            "num_entries": len(tiles),
            "num_contents": len(tiles),
            "internal_compression": internal_compression,
        },
    )
    blob += root_stored
    if leaf_mode:
        blob += leaf_stored
    for body in bodies:
        blob += body
    return bytes(blob)


def test_pmtiles_tile_bytes_end_to_end(monkeypatch, tmp_path):
    """真实归档字节读取：命中/未命中/越界 + gzip 目录 + 内容类型。"""
    from app.services.data_fabric.adapters import pmtiles_adapter as pm_mod
    from app.services.data_fabric.adapters.pmtiles_adapter import PMTilesAdapter

    monkeypatch.setattr(pm_mod, "_local_file_roots_from_settings", lambda: [str(tmp_path)])
    monkeypatch.setattr(pm_mod, "_local_file_max_bytes_from_settings", lambda: 64 * 1024 * 1024)
    tiles = {
        (0, 0, 0): b"\x89PNG-zero",
        (2, 1, 1): b"\x89PNG-z2-1-1",
        (3, 5, 3): b"\x89PNG-z3-5-3",
    }
    for gzip_dir in (False, True):
        path = tmp_path / f"basemap-{'gz' if gzip_dir else 'raw'}.pmtiles"
        path.write_bytes(_build_pmtiles_archive(tiles, gzip_dir=gzip_dir))
        adapter = PMTilesAdapter(ConnectionProfile(source_type="pmtiles", endpoint_url=str(path)))
        assert adapter.capabilities() and "tile_bytes_serving" in adapter.capabilities()

        body, ctype = adapter.read_tile_bytes(2, 1, 1)
        assert body == b"\x89PNG-z2-1-1"
        assert ctype == "image/png"
        assert adapter.read_tile_bytes(2, 3, 3) is None       # 归档中不存在
        assert adapter.read_tile_bytes(9, 0, 0) is None       # 超出 max_zoom
        # 越界 x/y → 类型化拒绝
        with pytest.raises(ValueError, match="outside zoom"):
            adapter.read_tile_bytes(1, 7, 0)


def test_pmtiles_query_inlines_small_tile_bytes(monkeypatch, tmp_path):
    from app.services.data_fabric.adapters import pmtiles_adapter as pm_mod
    from app.services.data_fabric.adapters.pmtiles_adapter import PMTilesAdapter

    monkeypatch.setattr(pm_mod, "_local_file_roots_from_settings", lambda: [str(tmp_path)])
    monkeypatch.setattr(pm_mod, "_local_file_max_bytes_from_settings", lambda: 64 * 1024 * 1024)
    path = tmp_path / "basemap.pmtiles"
    path.write_bytes(_build_pmtiles_archive({(1, 0, 1): b"\x89PNG-tiny"}, tile_type=2))
    adapter = PMTilesAdapter(ConnectionProfile(source_type="pmtiles", endpoint_url=str(path)))
    res = adapter.query("basemap.pmtiles", QuerySpec(limit=1, tile_coords={"z": 1, "x": 0, "y": 1}))
    import base64

    assert base64.b64decode(res.data["tile_bytes_b64"]) == b"\x89PNG-tiny"
    assert res.data["tile_content_type"] == "image/png"
    # 未命中 → 诚实标记而非伪造
    res_miss = adapter.query("basemap.pmtiles", QuerySpec(limit=1, tile_coords={"z": 1, "x": 1, "y": 0}))
    assert res_miss.data["tile_read"] == "tile absent in archive"


# ── PMTiles hardening（F1–F7）────────────────────────────────────────────────


def test_pmtiles_bounded_gzip_decompress_roundtrip_and_cap():
    """F1：正常 gzip 完整解压；超帽炸弹提前拒绝且错误携带 cap 信息。"""
    import gzip

    from app.services.data_fabric.adapters.pmtiles_adapter import _bounded_gzip_decompress

    payload = b"pmtiles-dir-bytes" * 1000
    assert _bounded_gzip_decompress(gzip.compress(payload), 1 << 20) == payload

    bomb = gzip.compress(b"\x00" * (2 * 1024 * 1024))  # 2 MiB 全零 → 压缩后极小
    with pytest.raises(ValueError, match="exceeds cap"):
        _bounded_gzip_decompress(bomb, 1024)


def test_pmtiles_decompress_dir_enforces_decompressed_cap():
    """F1：_decompress_dir 的 gzip 路径按解压输出上限拒绝（4 MiB 根目录帽）。"""
    import gzip

    from app.services.data_fabric.adapters.pmtiles_adapter import (
        MAX_ROOT_DIR_DECOMPRESSED_BYTES,
        PMTilesAdapter,
    )

    over = gzip.compress(b"\x00" * (MAX_ROOT_DIR_DECOMPRESSED_BYTES + 1024))
    with pytest.raises(ValueError, match="exceeds cap"):
        PMTilesAdapter._decompress_dir(over, "gzip", MAX_ROOT_DIR_DECOMPRESSED_BYTES)
    # 非 gzip 路径不受影响
    assert PMTilesAdapter._decompress_dir(b"raw-dir", "none", 1024) == b"raw-dir"


def test_pmtiles_spec_default_zooms_0_0_do_not_gate(monkeypatch, tmp_path):
    """F4：min_zoom=0/max_zoom=0 是 spec 默认（未设置），不得把所有 z>0 判 None。"""
    from app.services.data_fabric.adapters import pmtiles_adapter as pm_mod
    from app.services.data_fabric.adapters.pmtiles_adapter import PMTilesAdapter

    monkeypatch.setattr(pm_mod, "_local_file_roots_from_settings", lambda: [str(tmp_path)])
    monkeypatch.setattr(pm_mod, "_local_file_max_bytes_from_settings", lambda: 64 * 1024 * 1024)
    path = tmp_path / "unsetzoom.pmtiles"
    path.write_bytes(_build_pmtiles_archive({(2, 1, 1): b"\x89PNG-z2"}, zooms=(0, 0)))
    adapter = PMTilesAdapter(ConnectionProfile(source_type="pmtiles", endpoint_url=str(path)))

    body, ctype = adapter.read_tile_bytes(2, 1, 1)
    assert body == b"\x89PNG-z2"
    assert ctype == "image/png"
    assert adapter.read_tile_bytes(2, 2, 2) is None  # 归档中不存在 → 诚实缺席
    with pytest.raises(ValueError, match="zoom out of range"):
        adapter.read_tile_bytes(27, 0, 0)  # varint 越界检查保留


def test_pmtiles_leaf_directory_resolution(monkeypatch, tmp_path):
    """F7：根目录 run_length=0 → 经叶子目录解析路径命中瓦片（gzip 双目录）。"""
    from app.services.data_fabric.adapters import pmtiles_adapter as pm_mod
    from app.services.data_fabric.adapters.pmtiles_adapter import PMTilesAdapter

    monkeypatch.setattr(pm_mod, "_local_file_roots_from_settings", lambda: [str(tmp_path)])
    monkeypatch.setattr(pm_mod, "_local_file_max_bytes_from_settings", lambda: 64 * 1024 * 1024)
    path = tmp_path / "leafy.pmtiles"
    path.write_bytes(
        _build_pmtiles_archive({(2, 1, 1): b"\x89PNG-leaf"}, gzip_dir=True, leaf_mode=True)
    )

    reads = []
    orig_read_range = PMTilesAdapter._read_range

    def _spy(self, offset, length, *, cap, what):
        reads.append(what)
        return orig_read_range(self, offset, length, cap=cap, what=what)

    monkeypatch.setattr(PMTilesAdapter, "_read_range", _spy)
    adapter = PMTilesAdapter(ConnectionProfile(source_type="pmtiles", endpoint_url=str(path)))
    body, ctype = adapter.read_tile_bytes(2, 1, 1)
    assert body == b"\x89PNG-leaf"
    assert ctype == "image/png"
    # 证明确实走了叶子目录（root → leaf directory → tile），而非根目录直取
    assert reads == ["header", "root directory", "leaf directory", "tile"]


def test_pmtiles_nested_leaf_dirs_rejected(monkeypatch, tmp_path):
    """F7：>1 层叶子目录嵌套 → typed ValueError（spec 只允许一层）。"""
    from app.services.data_fabric.adapters import pmtiles_adapter as pm_mod
    from app.services.data_fabric.adapters.pmtiles_adapter import (
        PMTilesAdapter,
        zxy_to_tile_id,
    )

    monkeypatch.setattr(pm_mod, "_local_file_roots_from_settings", lambda: [str(tmp_path)])
    monkeypatch.setattr(pm_mod, "_local_file_max_bytes_from_settings", lambda: 64 * 1024 * 1024)

    body = b"\x89PNG-deep"
    tid = zxy_to_tile_id(2, 1, 1)
    leaf_b = _encode_dir_entries([(tid, 0, len(body), 1)])
    # leaf_a 又是 run_length=0 的叶子引用 → 第二层嵌套
    leaf_a = _encode_dir_entries([(tid, len(leaf_b), len(leaf_b), 0)])
    root = _encode_dir_entries([(tid, 0, len(leaf_a), 0)])
    root_off = 127
    leaf_off = root_off + len(root)
    tile_off = leaf_off + len(leaf_a) + len(leaf_b)
    blob = bytearray()
    blob += _build_pmtiles_v3_header(
        tile_type=2,
        min_zoom=2,
        max_zoom=2,
        sections={
            "root_dir": {"offset": root_off, "length": len(root)},
            "leaf_dirs": {"offset": leaf_off, "length": len(leaf_a) + len(leaf_b)},
            "tile_data": {"offset": tile_off, "length": len(body)},
        },
    )
    blob += root + leaf_a + leaf_b + body
    path = tmp_path / "nested.pmtiles"
    path.write_bytes(bytes(blob))

    adapter = PMTilesAdapter(ConnectionProfile(source_type="pmtiles", endpoint_url=str(path)))
    with pytest.raises(ValueError, match="nests leaf directories"):
        adapter.read_tile_bytes(2, 1, 1)


class _FakeStreamResponse:
    """流式响应桩：iter_content 按 chunk_size 切块（与 requests 行为一致）。"""

    def __init__(self, status_code, body, headers=None, chunk_size=64 * 1024):
        self.status_code = status_code
        self._body = body
        self._chunk_size = chunk_size
        self.headers = headers or {}
        self.consumed = 0
        self.closed = False

    def iter_content(self, chunk_size=None):
        size = chunk_size or self._chunk_size
        for i in range(0, len(self._body), size):
            piece = self._body[i:i + size]
            self.consumed += len(piece)
            yield piece

    def close(self):
        self.closed = True


class _FakePMTilesServer:
    """可编排假源：honest（206+Content-Range）/ ignores_range（200 全量）/
    lies_about_content_range（206 但起始偏移撒谎）。"""

    def __init__(self, blob, *, ignores_range=False, lies_about_content_range=False):
        self.blob = blob
        self.ignores_range = ignores_range
        self.lies_about_content_range = lies_about_content_range
        self.responses = []

    def get(self, url, headers=None, timeout=None, stream=None):
        rng = (headers or {}).get("Range", "")
        if self.ignores_range:
            resp = _FakeStreamResponse(200, self.blob)
            self.responses.append(resp)
            return resp
        start_s, end_s = rng.removeprefix("bytes=").split("-")
        start, end = int(start_s), int(end_s)
        cr_start = 0 if self.lies_about_content_range else start
        resp = _FakeStreamResponse(
            206,
            self.blob[start:end + 1],
            headers={"Content-Range": f"bytes {cr_start}-{end}/{len(self.blob)}"},
        )
        self.responses.append(resp)
        return resp


_HTTP_URL = "https://pmtiles.example.com/downloads/basemap.pmtiles"


def _http_pmtiles_adapter(server):
    from app.services.data_fabric.adapters.pmtiles_adapter import PMTilesAdapter

    adapter = PMTilesAdapter(ConnectionProfile(source_type="pmtiles", endpoint_url=_HTTP_URL))
    adapter.session = server
    return adapter


def test_pmtiles_http_range_capable_server_roundtrip():
    """诚实 Range 源（206 + 匹配 Content-Range）端到端命中。"""
    tiles = {(1, 0, 1): b"\x89PNG-http", (2, 1, 1): b"\x89PNG-http-z2"}
    adapter = _http_pmtiles_adapter(_FakePMTilesServer(_build_pmtiles_archive(tiles)))
    assert adapter.probe() is True
    body, ctype = adapter.read_tile_bytes(2, 1, 1)
    assert body == b"\x89PNG-http-z2"
    assert ctype == "image/png"


def test_pmtiles_http_range_ignoring_server_rejected_for_ranged_read():
    """F3：offset>0 收到 200 = 服务器无视 Range（字节来自文件起点）→ 拒绝。"""
    server = _FakePMTilesServer(
        _build_pmtiles_archive({(1, 0, 1): b"\x89PNG"}), ignores_range=True
    )
    adapter = _http_pmtiles_adapter(server)
    assert adapter.probe() is True  # offset==0 的 200 仍可接受（切片）
    with pytest.raises(ValueError, match="range-capable"):
        adapter.read_tile_bytes(1, 0, 1)


def test_pmtiles_http_huge_200_body_aborted_early():
    """F2：无视 Range 的 200 + 8MB 响应体 —— header 读取收满即断，不整包下载。"""
    blob = _build_pmtiles_archive({(1, 0, 1): b"\x89PNG"}) + b"\x00" * (8 * 1024 * 1024)
    server = _FakePMTilesServer(blob, ignores_range=True)
    adapter = _http_pmtiles_adapter(server)

    got = adapter._read_range(0, 127, cap=127, what="header")
    assert got == blob[:127]
    resp = server.responses[-1]
    assert resp.closed is True
    assert resp.consumed <= 127 + 64 * 1024  # 请求长度 + 一个 chunk

    with pytest.raises(ValueError, match="range-capable"):
        adapter.read_tile_bytes(1, 0, 1)  # 区段读取在状态码处直接拒绝
    assert server.responses[-1].consumed == 0  # 响应体一个字节都没拉取


def test_pmtiles_http_content_range_mismatch_rejected():
    """F3：206 但 Content-Range 起始偏移与请求不符 → 拒绝（防静默错位）。"""
    server = _FakePMTilesServer(
        _build_pmtiles_archive({(1, 0, 1): b"\x89PNG"}),
        lies_about_content_range=True,
    )
    adapter = _http_pmtiles_adapter(server)
    with pytest.raises(ValueError, match="Content-Range"):
        adapter.read_tile_bytes(1, 0, 1)


def test_pmtiles_probe_blocked_path_raises_security_blocked(monkeypatch, tmp_path):
    """F5：probe 本地路径走 Section 44 守卫；越界路径 → typed SecurityBlockedError。"""
    from app.services.data_fabric.adapters import pmtiles_adapter as pm_mod
    from app.services.data_fabric.adapters.pmtiles_adapter import PMTilesAdapter
    from app.services.data_fabric.errors import SecurityBlockedError

    monkeypatch.setattr(pm_mod, "_local_file_roots_from_settings", lambda: [str(tmp_path)])
    monkeypatch.setattr(pm_mod, "_local_file_max_bytes_from_settings", lambda: 64 * 1024 * 1024)
    outside = tmp_path.parent / "outside-secrets.pmtiles"
    outside.write_bytes(b"PMTiles\x03" + b"\x00" * 120)
    adapter = PMTilesAdapter(ConnectionProfile(source_type="pmtiles", endpoint_url=str(outside)))
    with pytest.raises(SecurityBlockedError):
        adapter.probe()
