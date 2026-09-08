"""Lakehouse V6 Wave 4 — GeoParquet 写入升级 + 窗口扫描（row-group 剪枝）。

覆盖面（ADR-0118 lazy materialization 的 vector 侧）：
- 写入：row-group 粒度 + 显式列统计 + row-group bbox 地图（schema metadata
  ``webgis:row_groups``，从真实几何算得）；
- 剪枝：空间不相交的 row-group **不被读取**（结构性证据 row_groups_read
  < row_groups_total）；无元数据文件退化为有界顺序读（正确性不变）；
- 预算：max_rows 硬界 → honest truncated；
- ref 面：scan_fabric_parquet_ref 会话域解析 + traversal 拒绝 + 死 ref
  typed 失败；
- schema evolution：读侧容忍新增/缺席属性列。
"""
from __future__ import annotations

import json

import pytest

from app.services.lakehouse.vector_scan import (
    LakehouseScanError,
    scan_fabric_parquet_ref,
    scan_parquet_window,
)

pytest.importorskip("pyarrow")


def _cluster_features(cx, cy, n, tag):
    return [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [cx + i * 1e-4, cy + i * 1e-4]},
            "properties": {"tag": tag, "i": i},
        }
        for i in range(n)
    ]


@pytest.fixture()
def clustered_parquet(tmp_path):
    """三个空间上相距极远的簇 → 3 个 row-group，bbox 两两不相交。"""
    import pyarrow.parquet as pq

    from app.services.data_fabric.vector_carrier import features_to_arrow

    feats = (
        _cluster_features(0.0, 0.0, 5, "a")
        + _cluster_features(60.0, 0.0, 5, "b")
        + _cluster_features(120.0, 0.0, 5, "c")
    )
    table = features_to_arrow(feats, crs="EPSG:4326")
    path = tmp_path / "clustered.parquet"
    from app.services.data_fabric.vector_carrier import table_to_geoparquet

    table_to_geoparquet(table, str(path), row_group_size=5)
    return path, pq.ParquetFile(str(path)).num_row_groups


def test_write_embeds_row_group_bbox_map(clustered_parquet):
    path, total = clustered_parquet
    assert total == 3
    import pyarrow.parquet as pq

    meta = pq.ParquetFile(str(path)).schema_arrow.metadata or {}
    raw = meta.get(b"webgis:row_groups")
    assert raw, "row-group bbox 地图必须随 schema 落盘"
    parsed = json.loads(raw)
    groups = parsed["row_groups"]
    assert len(groups) == 3
    bboxes = [g["bbox"] for g in groups]
    for bb in bboxes:
        assert bb is not None and len(bb) == 4
    # 簇间 bbox 两两不相交（剪枝空间存在性）。
    assert not (
        bboxes[0][2] >= bboxes[1][0] or bboxes[1][2] >= bboxes[2][0]
    )


def test_window_scan_prunes_row_groups(clustered_parquet):
    path, total = clustered_parquet
    res = scan_parquet_window(path, [-0.01, -0.01, 0.01, 0.01])
    assert res["properties"]["row_groups_total"] == 3
    assert res["properties"]["row_groups_read"] == 1
    assert res["properties"]["truncated"] is False
    assert [f["properties"]["tag"] for f in res["features"]] == ["a"] * 5


def test_window_scan_full_window_reads_all(clustered_parquet):
    path, total = clustered_parquet
    res = scan_parquet_window(path, [-1.0, -1.0, 180.0, 2.0])
    assert res["properties"]["row_groups_read"] == total
    assert len(res["features"]) == 15


def test_window_scan_without_metadata_degrades_bounded(tmp_path):
    """外来/降级文件（无剪枝元数据）：有界顺序读，结果仍正确。"""
    import pyarrow.parquet as pq

    from app.services.data_fabric.vector_carrier import (
        features_to_arrow,
    )

    feats = _cluster_features(1.0, 1.0, 4, "x")
    table = features_to_arrow(feats, crs="EPSG:4326")
    table = table.replace_schema_metadata(
        {k: v for k, v in (table.schema.metadata or {}).items()
         if k != b"webgis:row_groups"}
    )
    path = tmp_path / "plain.parquet"
    pq.write_table(table, str(path), row_group_size=2)
    res = scan_parquet_window(path, [0.99, 0.99, 1.02, 1.02])
    assert res["properties"]["row_groups_read"] == res["properties"]["row_groups_total"]
    assert len(res["features"]) == 4


def test_window_scan_max_rows_truncates_honestly(clustered_parquet):
    path, _total = clustered_parquet
    res = scan_parquet_window(path, [-1.0, -1.0, 180.0, 2.0], max_rows=7)
    assert len(res["features"]) == 7
    assert res["properties"]["truncated"] is True


def test_window_scan_column_projection(clustered_parquet):
    path, _total = clustered_parquet
    res = scan_parquet_window(path, [-0.01, -0.01, 0.01, 0.01], columns=["tag"])
    assert res["features"]
    for f in res["features"]:
        assert "i" not in f["properties"]
        assert f["properties"]["tag"] == "a"


def test_invalid_window_and_missing_file(tmp_path):
    with pytest.raises(LakehouseScanError, match="invalid bbox"):
        scan_parquet_window(tmp_path / "x.parquet", [0, 1])
    with pytest.raises(LakehouseScanError, match="not found"):
        scan_parquet_window(tmp_path / "missing.parquet", [0, 0, 1, 1])
    with pytest.raises(LakehouseScanError, match="max_rows"):
        scan_parquet_window(tmp_path / "x.parquet", [0, 0, 1, 1], max_rows=0)


# ── ref 面 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scan_fabric_parquet_ref_session_scoped(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    from app.services.data_fabric.materialization_service import materialization_service
    from app.services.data_fabric.vector_carrier import features_to_arrow

    feats = (
        _cluster_features(0.0, 0.0, 4, "a")
        + _cluster_features(90.0, 0.0, 4, "b")
    )
    table = features_to_arrow(feats, crs="EPSG:4326")
    res = await materialization_service.materialize_geoparquet(
        "sess-scan", table, "T", row_group_size=4,
    )
    ref = res["ref"]

    out = scan_fabric_parquet_ref("sess-scan", ref, [-0.01, -0.01, 0.01, 0.01])
    assert [f["properties"]["tag"] for f in out["features"]] == ["a"] * 4
    assert out["properties"]["row_groups_read"] < out["properties"]["row_groups_total"]

    # 非 owner 会话解析 → 死 ref typed 失败（不泄漏文件存在性）。
    with pytest.raises(LakehouseScanError, match="not alive"):
        scan_fabric_parquet_ref("other-session", ref, [0, 0, 1, 1])
    # 非 fabric-parquet ref 拒绝。
    with pytest.raises(LakehouseScanError, match="not a fabric-parquet"):
        scan_fabric_parquet_ref("sess-scan", "ref:raster/abc", [0, 0, 1, 1])


# ── schema evolution（读侧容忍）─────────────────────────────────────────


def test_read_tolerates_added_property_column(tmp_path):
    """新增属性列的文件：features 携带新属性，既有消费面不破坏。"""
    import pyarrow.parquet as pq

    from app.services.data_fabric.vector_carrier import (
        arrow_to_features,
        features_to_arrow,
    )

    feats = _cluster_features(1.0, 1.0, 2, "a")
    table = features_to_arrow(feats, crs="EPSG:4326")
    # 模拟 schema evolution：新版本写入方追加一列。
    import pyarrow as pa

    extended = table.append_column(
        pa.field("extra_prop", pa.string()),
        pa.array(["e1", "e2"], type=pa.string()),
    )
    path = tmp_path / "evolved.parquet"
    pq.write_table(extended, str(path))
    back = arrow_to_features(pq.read_table(str(path)))
    assert back[0]["properties"]["extra_prop"] == "e1"
    assert back[0]["geometry"] == feats[0]["geometry"]
