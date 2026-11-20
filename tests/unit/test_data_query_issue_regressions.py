"""Regressions for silent query truncation and wrong windows (#2068–#2050)."""
from __future__ import annotations

import math
import time

import pytest

from app.schemas.data_fabric_schema import QueryResult
from app.services.data_fabric.errors import QueryBudgetExceededError


def test_large_ring_self_intersection_is_not_skipped_and_batch_is_fast():
    from app.services.data_quality.rule_functions import RuleEvalContext, rule_geometry_validity

    corners = [(0.0, 0.0), (2.0, 2.0), (2.0, 0.0), (0.0, 2.0), (0.0, 0.0)]
    ring = []
    steps = 160
    for start, end in zip(corners, corners[1:]):
        for i in range(steps):
            t = i / steps
            ring.append([
                start[0] + (end[0] - start[0]) * t,
                start[1] + (end[1] - start[1]) * t,
            ])
    ring.append(ring[0])
    assert len(ring) > 512
    bowtie = rule_geometry_validity(RuleEvalContext(features=[{
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }]))
    assert bowtie.metric["self_intersected"] == 1
    assert bowtie.metric["rings_skipped_over_cap"] == 0

    features = []
    for k in range(5000):
        circle = []
        for i in range(500):
            ang = 2 * math.pi * i / 500
            circle.append([k + math.cos(ang), math.sin(ang)])
        circle.append(circle[0])
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [circle]},
        })
    started = time.perf_counter()
    result = rule_geometry_validity(RuleEvalContext(features=features))
    assert time.perf_counter() - started < 5.0
    assert result.status == "pass"
    assert result.metric["rings_skipped_over_cap"] == 0


def test_quality_rule_and_evaluation_stop_at_budget():
    from app.services.data_quality.engine import evaluate_payload
    from app.services.data_quality.rule_functions import RuleEvalContext, rule_geometry_validity

    skipped = rule_geometry_validity(RuleEvalContext(
        features=[{"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}}],
        deadline_monotonic=time.monotonic() - 1,
    ))
    assert skipped.status == "skipped"
    assert skipped.message == "reason=budget"

    report = evaluate_payload(
        {"geojson": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 1]}, "properties": {}},
        ]}},
        deadline_s=0,
    )
    assert report["results"]
    assert all(row["status"] == "skipped" and row["message"] == "reason=budget" for row in report["results"])


def test_flatgeobuf_statistics_reject_prefix_instead_of_partial_agg(tmp_path, monkeypatch):
    import geopandas as gpd
    from shapely.geometry import Point

    from app.schemas.data_fabric_schema import ConnectionProfile, QuerySpec
    from app.services.data_fabric.adapters.flatgeobuf_adapter import FlatGeobufAdapter

    path = tmp_path / "stats.fgb"
    gpd.GeoDataFrame(
        {"value": list(range(20))},
        geometry=[Point(i, 0) for i in range(20)],
        crs="EPSG:4326",
    ).to_file(path, driver="FlatGeobuf")
    adapter = FlatGeobufAdapter(ConnectionProfile(id="fgb", source_type="flatgeobuf", url=str(path)))
    monkeypatch.setattr(adapter, "_readable_path", lambda: str(path))

    full = adapter.query(path.name, QuerySpec(aggregate=[{"func": "sum", "field": "value"}]))
    assert full.data[0]["sum_value"] == pytest.approx(sum(range(20)))

    with pytest.raises(QueryBudgetExceededError):
        adapter.query(
            path.name,
            QuerySpec(aggregate=[{"func": "sum", "field": "value"}], max_rows=5),
        )


class _Budget:
    deadline_s = 30.0
    max_rows = 10_000
    max_bytes = 10**9
    max_vertices = 10**9


class _Token:
    def check(self):
        return None


def _features(n):
    return [
        {"type": "Feature", "geometry": None, "properties": {"i": i}}
        for i in range(n)
    ]


class _PagingAdapter:
    def __init__(self, n):
        self._rows = _features(n)

    def query(self, dataset_id, spec):
        limit = spec.limit or 100
        offset = spec.offset or 0
        page = self._rows[offset:offset + limit]
        more = offset + len(page) < len(self._rows)
        return QueryResult(
            dataset_id=dataset_id, features=page, has_more=more, truncated=more,
        )


def test_v6_scan_rejects_rows_past_fetch_window():
    from app.services.data_fabric.query.federated.physical import iter_scan_pages

    with pytest.raises(QueryBudgetExceededError, match="join candidates"):
        list(iter_scan_pages(
            _PagingAdapter(10), "d", where=None, fields=None, bbox=None,
            fetch_limit=3, budget=_Budget(), token=_Token(),
        ))
    pages = list(iter_scan_pages(
        _PagingAdapter(3), "d", where=None, fields=None, bbox=None,
        fetch_limit=3, budget=_Budget(), token=_Token(),
    ))
    assert sum(len(page) for page in pages) == 3


def test_v5_and_v6_reject_source_over_window():
    from app.services.data_fabric.query.federation import (
        ChainJoin,
        ChainSource,
        FederatedChainRequest,
        FederatedExecutor,
    )
    from app.services.data_fabric.query.models import ExecutionBudget

    class Adapter:
        def query(self, dataset_id, spec):
            rows = _features(30) if dataset_id == "pts" else [
                {"properties": {"region": "R", "label": "L"}},
            ]
            limit = spec.limit or 100
            offset = spec.offset or 0
            page = rows[offset:offset + limit]
            more = offset + len(page) < len(rows)
            return QueryResult(
                dataset_id=dataset_id, features=page, has_more=more, truncated=more,
            )

    adapters = {"sA": Adapter(), "sC": Adapter()}
    budget = ExecutionBudget(max_rows=5)
    base = dict(
        sources=[
            ChainSource(source_id="sA", dataset_id="pts"),
            ChainSource(source_id="sC", dataset_id="dims"),
        ],
        joins=[ChainJoin(
            kind="attribute_join", join_field_left="i", join_field_right="region",
            left_source_id="sA", right_source_id="sC",
        )],
        limit=100,
        budget=budget,
        use_cache=False,
    )
    executor = FederatedExecutor(lambda sid: adapters.get(sid))
    with pytest.raises(QueryBudgetExceededError):
        executor.execute_chain(FederatedChainRequest(engine="v5", **base))
    with pytest.raises(QueryBudgetExceededError):
        executor.execute_chain(FederatedChainRequest(engine="v6", **base))


def test_aggregate_join_count_uses_full_join_not_output_limit():
    from app.services.data_fabric.query.federation import (
        FederatedExecutor,
        FederatedQueryRequest,
    )

    class Adapter:
        def __init__(self, rows):
            self._rows = rows

        def query(self, dataset_id, spec):
            limit = spec.limit or 100
            offset = spec.offset or 0
            return QueryResult(
                dataset_id=dataset_id,
                features=self._rows[offset:offset + limit],
            )

    left = [
        {"properties": {"district": "金牛", "students": 1}}
        for _ in range(25)
    ]
    right = [{"properties": {"name": "金牛"}}]
    adapters = {"srcA": Adapter(left), "srcB": Adapter(right)}
    result = FederatedExecutor(lambda sid: adapters.get(sid)).execute(FederatedQueryRequest(
        "srcA", "points", "srcB", "dims",
        join_field_left="district", join_field_right="name",
        aggregates=[{"func": "count"}],
        group_by_right=["name"],
        limit=5,
    ))
    assert result["status"] == "success"
    assert result["rows"][0]["count"] == 25


def test_cog_disjoint_aoi_is_not_clamped_to_the_edge():
    from rasterio.windows import Window

    from app.services.data_fabric.cog_materialization import (
        CogMaterializationError,
        clip_raster_window,
    )

    with pytest.raises(CogMaterializationError) as east:
        clip_raster_window(Window(20, 0, 4, 4), 10, 10)
    assert east.value.reason_code == "aoi_outside"
    with pytest.raises(CogMaterializationError) as north:
        clip_raster_window(Window(0, -8, 4, 3), 10, 10)
    assert north.value.reason_code == "aoi_outside"
    # Partial overlap keeps the intersection; it does not slide the window.
    assert clip_raster_window(Window(-2, 1, 5, 4), 10, 10) == (0, 1, 3, 4)


def test_empty_row_group_plan_reads_no_batches():
    from app.services.data_fabric.adapters.geoparquet_adapter import iter_planned_batches

    class _File:
        def __init__(self):
            self.calls = 0

        def iter_batches(self, **kwargs):
            self.calls += 1
            yield "batch"

    empty = _File()
    assert list(iter_planned_batches(
        empty, batch_size=8, columns=["a"], row_groups=[],
    )) == []
    assert empty.calls == 0

    kept = _File()
    assert list(iter_planned_batches(
        kept, batch_size=8, columns=None, row_groups=[1],
    )) == ["batch"]
    assert kept.calls == 1


def test_wfs10_post_offset_requests_offset_plus_limit():
    from app.schemas.data_fabric_schema import ConnectionProfile, QuerySpec
    from app.services.data_fabric.adapters.wfs_adapter import WFSAdapter
    from tests.unit.test_ogc_adapters_753 import WFS_CAPABILITIES, FakeResponse

    profile = ConnectionProfile(provider_type="wfs", endpoint="")
    profile.url = "https://example.com/wfs"
    adapter = WFSAdapter(profile)
    adapter.version = "1.0.0"
    captured = {}

    def fake_post(url, data=None, **kwargs):
        body = data.decode("utf-8") if isinstance(data, bytes) else str(data)
        captured["body"] = body
        import re
        match = re.search(r'maxFeatures="(\d+)"', body)
        count = int(match.group(1)) if match else 0
        features = [
            {"type": "Feature", "properties": {"name": f"f{i}"}, "geometry": None}
            for i in range(count)
        ]
        return FakeResponse(json_data={"type": "FeatureCollection", "features": features})

    adapter.session.post = fake_post
    adapter.session.get = lambda url, params=None, timeout=None, **kwargs: FakeResponse(
        content=WFS_CAPABILITIES,
    )
    result = adapter.query("roads", QuerySpec(
        limit=3, offset=4, filter_expr={"op": "eq", "field": "name", "value": "f"},
    ))
    assert 'maxFeatures="7"' in captured["body"]
    assert "startIndex" not in captured["body"]
    assert [feature["properties"]["name"] for feature in result.features] == ["f4", "f5", "f6"]
