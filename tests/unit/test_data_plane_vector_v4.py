"""ADR-0101 D8：GeoArrow 载体、流式向量缝隙、空间分区/索引设施。"""
from __future__ import annotations

import threading

import pytest

from app.services.data_fabric.spatial_index_runtime import (
    SpatialIndexRuntime,
    build_strtree_index,
    grid_partition,
    h3_partition,
)
from app.services.data_fabric.streaming import (
    DEFAULT_BATCH_SIZE,
    batch_size_for,
    iter_batches,
    stream_aggregate,
    stream_bbox_filter,
    stream_filter,
    stream_partition,
    stream_project,
    stream_transform,
)
from app.services.data_fabric.vector_carrier import (
    VectorCarrierUnavailable,
    arrow_available,
    arrow_to_features,
    features_to_arrow,
    iter_arrow_chunks,
    table_crs,
)


def _fc(n: int = 6) -> list[dict]:
    return [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [116.0 + i, 39.0 + i]},
         "properties": {"v": i, "kind": "a" if i % 2 == 0 else "b",
                        "nullable": None if i % 3 == 0 else f"x{i}"}}
        for i in range(n)
    ]


# ------------------------------------------------------------- GeoArrow 载体


class TestVectorCarrier:
    def test_unavailable_is_typed_when_no_pyarrow(self, monkeypatch):
        import app.services.data_fabric.vector_carrier as vc

        monkeypatch.setattr(vc, "arrow_available", lambda: False)
        with pytest.raises(VectorCarrierUnavailable, match="pyarrow"):
            features_to_arrow(_fc(2), crs="EPSG:4326")

    @pytest.mark.skipif(not arrow_available(), reason="pyarrow not installed")
    def test_round_trip_preserves_features_and_crs(self):
        feats = _fc(6)
        table = features_to_arrow(feats, crs="EPSG:4326")
        assert table_crs(table) == "EPSG:4326"
        back = arrow_to_features(table)
        assert len(back) == len(feats)
        for orig, got in zip(feats, back):
            assert got["properties"] == orig["properties"]
            assert got["geometry"] == orig["geometry"]

    @pytest.mark.skipif(not arrow_available(), reason="pyarrow not installed")
    def test_nulls_preserved_in_columns(self):
        table = features_to_arrow(_fc(6))
        assert table.column_names[-1] == "geometry"
        col = table.column("nullable").to_pylist()
        assert col[0] is None and col[1] == "x1"

    @pytest.mark.skipif(not arrow_available(), reason="pyarrow not installed")
    def test_chunked_transfer(self):
        batches = list(iter_arrow_chunks(_fc(10), crs="EPSG:4326", chunk_size=4))
        assert len(batches) >= 3  # 10 行 / 4 → 3 批
        assert sum(b.num_rows for b in batches) == 10

    @pytest.mark.skipif(not arrow_available(), reason="pyarrow not installed")
    def test_geoparquet_interop(self, tmp_path):
        from app.services.data_fabric.vector_carrier import (
            geoparquet_to_features,
            table_to_geoparquet,
        )

        feats = _fc(4)
        path = str(tmp_path / "out.parquet")
        table_to_geoparquet(features_to_arrow(feats, crs="EPSG:4326"), path)
        back = geoparquet_to_features(path)
        assert [b["properties"]["v"] for b in back] == [0, 1, 2, 3]

    @pytest.mark.skipif(not arrow_available(), reason="pyarrow not installed")
    def test_empty_geometry_survives_roundtrip(self):
        """round-1 review MAJOR：空几何（POINT EMPTY）是合法数据 —— 解码
        必须返回空 GeoJSON 几何字典，None 只留给不可读 WKB。"""
        import json

        import shapely

        from app.services.data_fabric.vector_carrier import _wkb_to_geometry

        empty_point = shapely.from_wkt("POINT EMPTY")
        wkb = shapely.to_wkb(empty_point)
        geom = _wkb_to_geometry(wkb)
        assert geom is not None
        assert geom["type"] == "Point"
        # 空几何字典可被 shapely 再次解析（GeoJSON 合法）
        assert shapely.from_geojson(json.dumps(geom)) is not None
        # 完整 feature 往返：编码（GeoJSON → WKB）→ 解码保持空几何
        feats = [{"type": "Feature",
                  "geometry": {"type": "Point", "coordinates": []},
                  "properties": {"v": 1}}]
        table = features_to_arrow(feats)
        back = arrow_to_features(table)
        assert back[0]["geometry"] is not None
        assert back[0]["geometry"]["type"] == "Point"

    @pytest.mark.skipif(not arrow_available(), reason="pyarrow not installed")
    def test_unreadable_wkb_still_decodes_to_none(self):
        """容错外部 lane 不变：垃圾 WKB → None（debug 日志，不抛）。"""
        from app.services.data_fabric.vector_carrier import _wkb_to_geometry

        assert _wkb_to_geometry(b"not-wkb-at-all") is None
        assert _wkb_to_geometry(None) is None
        assert _wkb_to_geometry(b"") is None


# --------------------------------------------------------------- 流式缝隙


class TestStreamingSeams:
    def test_iter_batches_bounds_and_collaboration_point(self):
        rows = list(range(25))
        batches = list(iter_batches([{"v": r} for r in rows], batch_size=10))
        assert [len(b) for b in batches] == [10, 10, 5]
        seen = []
        list(iter_batches([{"v": r} for r in rows], 10, on_batch=seen.append))
        assert seen == [1, 2, 3]

    def test_stream_filter_matches_local_semantics(self):
        from app.services.data_fabric.query.predicates import (
            evaluate_predicate,
            predicate_from_dict,
        )

        feats = _fc(6)
        pred = {"op": "eq", "field": "kind", "value": "a"}
        typed = predicate_from_dict(pred)
        expected = [f for f in feats if evaluate_predicate(typed, f["properties"]) is True]
        got = list(stream_filter(iter(feats), pred, batch_size=2))
        assert got == expected

    def test_stream_bbox_filter(self):
        feats = _fc(6)  # coordinates 116..121 / 39..44
        got = list(stream_bbox_filter(feats, [116.0, 39.0, 118.0, 41.0]))
        assert [g["properties"]["v"] for g in got] == [0, 1, 2]

    def test_stream_project_keeps_geometry(self):
        got = list(stream_project(_fc(3), ["v"]))
        assert got[0]["properties"] == {"v": 0}
        assert got[0]["geometry"]["type"] == "Point"

    def test_stream_transform_can_drop_rows(self):
        got = list(stream_transform(_fc(4), lambda f: None if f["properties"]["v"] % 2 else f))
        assert [g["properties"]["v"] for g in got] == [0, 2]

    def test_stream_aggregate_scalar_accumulators(self):
        feats = _fc(6)
        out = list(stream_aggregate(
            feats,
            [{"func": "sum", "field": "v"}, {"func": "avg", "field": "v"},
             {"func": "distinct_count", "field": "kind"}],
            ["kind"],
        ))
        by_kind = {r["kind"]: r for r in out}
        assert by_kind["a"]["count"] == 3          # v = 0, 2, 4
        assert by_kind["a"]["sum_v"] == 6
        assert by_kind["a"]["avg_v"] == 2.0
        assert by_kind["a"]["distinct_count_kind"] == 1

    def test_stream_partition_bounded(self):
        parts = dict(stream_partition(_fc(6), lambda f: f["properties"]["kind"]))
        assert set(parts) == {"a", "b"}
        with pytest.raises(ValueError, match="partitions"):
            list(stream_partition(_fc(6), lambda f: str(f["properties"]["v"]),
                                  max_partitions=2))

    def test_batch_size_pressure_aware(self):
        class FakeGov:
            def __init__(self, ratio):
                self._r = ratio

            def usage_full(self, path):
                from app.services.geocompute.budgets import ScopeUsage

                return ScopeUsage(bytes=int(self._r * 1000))

            def _find(self, path):
                from app.services.geocompute.budgets import BudgetLimits

                class S:
                    limits = BudgetLimits(max_bytes=1000)

                return S()

        assert batch_size_for(None, None) == DEFAULT_BATCH_SIZE
        assert batch_size_for(FakeGov(0.2), "global:root") == DEFAULT_BATCH_SIZE
        assert batch_size_for(FakeGov(0.9), "global:root") < DEFAULT_BATCH_SIZE
        assert batch_size_for(FakeGov(0.99), "global:root") >= 64


# ------------------------------------------------------- 空间分区 / 索引


class TestSpatialIndexRuntime:
    def test_strtree_cache_reuse(self):
        import shapely

        rt = SpatialIndexRuntime()
        feats = _fc(6)
        key = "fp-abc"
        tree1, _ = build_strtree_index(feats, content_fingerprint=key, runtime=rt)
        tree2, _ = build_strtree_index(feats, content_fingerprint=key, runtime=rt)
        assert tree1 is tree2  # 命中缓存，不重建
        hits = tree1.query(shapely.box(116.0, 39.0, 117.0, 40.0))
        assert len(hits) >= 1

    def test_cache_bounded_and_invalidatable(self):
        rt = SpatialIndexRuntime(max_entries=2)
        for i in range(4):
            rt.put(f"k{i}", object(), 10)
        assert len(rt._entries) <= 2
        rt.put("big", object(), 10**9)  # 超字节界 → 拒收
        assert "big" not in rt._entries
        rt.invalidate()
        assert len(rt._entries) == 0

    def test_grid_partition_stable_and_bounded(self):
        parts = grid_partition(_fc(6), cells_per_axis=4)
        total = sum(len(v) for v in parts.values())
        assert total == 6
        assert len(parts) <= 16
        again = grid_partition(_fc(6), cells_per_axis=4)
        assert {k: len(v) for k, v in parts.items()} == {k: len(v) for k, v in again.items()}

    def test_grid_partition_rejects_absurd_cell_counts(self):
        with pytest.raises(ValueError, match="cells_per_axis"):
            grid_partition(_fc(2), cells_per_axis=1000)

    def test_h3_partition_or_honest_failure(self):
        feats = _fc(6)
        try:
            parts = h3_partition(feats, resolution=7)
            assert sum(len(v) for v in parts.values()) == 6
            assert all(isinstance(k, str) for k in parts)
        except Exception as exc:
            from app.services.data_fabric.spatial_index_runtime import (
                SpatialIndexUnavailable,
            )

            assert isinstance(exc, SpatialIndexUnavailable)

    def test_index_runtime_thread_safety(self):
        rt = SpatialIndexRuntime(max_entries=8)
        errors = []

        def worker(i):
            try:
                for j in range(50):
                    rt.put(f"k{i}-{j % 4}", object(), 8)
                    rt.get(f"k{i}-{j % 4}")
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert not errors
