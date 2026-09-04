"""GeoBench V3 — quick-tier structural benchmark suite (GeoCompute V3 / ADR-0096).

Covers the GeoBench categories against GENERATED fixtures only (no committed
datasets, no network, no docker; everything deterministic-seeded and cleaned
up). The suite is quick-tier and CI-safe: every test < 30s, whole file well
under 4 minutes on the CI lane.

Design rule: **no fragile microsecond thresholds**. Wall-clock numbers are
only ever compared through wide structural-ratio bounds (documented per
test) that hold across machine speed variance, or asserted via non-timing
evidence (statuses, row counts, row-group pruning counts, tracemalloc
peaks). Machine-sensitive timings belong to ``bench_geocompute_v3.py``,
where they are printed as INFORMATIONAL and never gate.

Asserted bounds and rationale:

1. planner cost monotonicity
   - estimated_rows must scale ~linearly with descriptor feature_count
     (1000 vs 100_000 → ratio band [80, 125]; the model is exactly linear,
     the band only absorbs estimator rounding).
   - estimated_bytes grows linearly until the page-window cap
     (normalize clamps limit to 10_000) → ratio band [9, 11] (target 10).
     This is a deliberate bounded-transfer contract, not a timing claim.
   - alternatives are bounded (≤ MAX_ALTERNATIVES = 8; no combinatorial
     explosion) and cost_of_chosen is consistent with the plan cost.
2. spatial-join complexity: sub-quadratic guard. Linear scaling at 4x the
   points would be ~4x; the guard allows t(8k) ≤ 48 × t(2k) (12x slack over
   the 4x size-up, i.e. up to ~O(N log N)-ish constants plus timer noise)
   while still failing on accidental O(N·M) blowups at real feature counts.
   Absolute guard t(8k) < 15s. Correctness: every point matches ≤ 1 grid
   polygon, count within [0.95N, N], and 25 joined rows are re-verified
   with shapely contains. STRtree path asserted (no linear-scan fallback).
3. executor DAG scaling: FILTER→AGGREGATE chain over 5k/20k features.
   t(20k) ≤ 8 × t(5k) + 0.75s (linear expectation 4x; 2x slack + additive
   constant absorbs scheduler/import jitter; measured ~2-5x). rows_emitted
   deterministic: filter emits exactly n/2, aggregate 7 groups; two runs on
   fresh engines produce identical aggregate payloads.
4. cache/reuse: second identical run shows node status "reused" for every
   node (asserted via the reuse flag, NOT via wall-time comparison).
5. federation: 3-source chain with estimated_rows hints → deterministic
   cost order; a join fan-out beyond the budget fails fast with a typed
   QUERY_BUDGET_EXCEEDED (bounded, no explosion).
6. memory ceiling: windowed execution over a synthetic 4000x4000 float32
   raster (~64MB) must stay under raster_bytes + 96MB tracemalloc peak.
   The windowed path allocates the merged output array plus small window
   buffers (~68MB measured); a whole-array op (read_full + temp + output)
   would need ≥ 2× raster_bytes and must exceed the bound — so the bound
   proves the windowed path, not a timing property.
7. cancel: a slow DAG cancelled mid-flight (cooperative checkpoint inside
   the injected source) completes within 2s wall and marks nodes cancelled
   and the run CANCELLED. Deterministic: the fake source blocks until the
   cancel token fires, so no sleep-race decides the outcome.

GeoParquet row-group pruning (pyarrow) is a bonus quick-tier test guarded
by ``pytest.importorskip`` — the canonical venv does not ship pyarrow, and
existing suites (test_file_adapters_v2, test_data_fabric_benchmarks_v2 B7)
skip identically. Larger sizes live in bench_geocompute_v3.py.
"""
from __future__ import annotations

import json
import random
import threading
import time
import tracemalloc

import pytest

from app.schemas.data_fabric_schema import (
    ConnectionProfile,
    DatasetDescriptor,
    QueryResult,
    QuerySpec,
)
from app.services.data_fabric.query.capabilities import default_capabilities
from app.services.data_fabric.query.normalize import normalize_query_spec
from app.services.data_fabric.query.planner import plan_query
from app.services.geocompute import ops
from app.services.geocompute.executor import GeoExecutionEngine
from app.services.geocompute.plan import ExecutionNode, ExecutionPlan, NodeCategory

# ---------------------------------------------------------------------------
# shared fixture builders (deterministic; nothing committed)
# ---------------------------------------------------------------------------

_SEED = 42


def _descriptor(feature_count: int, dataset_id: str = "geobench_d") -> DatasetDescriptor:
    return DatasetDescriptor(
        id=dataset_id,
        source_type="postgis",
        feature_count=feature_count,
        srs="EPSG:4326",
        bbox=[0.0, 0.0, 100.0, 100.0],
        fields=[{"name": "v", "type": "int"}, {"name": "grp", "type": "int"}],
        metadata={"has_geometry_index": True},
    )


def _point_features(n: int) -> list[dict]:
    """Deterministic synthetic points; v cycles 0..9, grp cycles 0..6."""
    return [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [i % 100, (i * 7) % 100]},
            "properties": {"v": i % 10, "grp": i % 7, "idx": i},
        }
        for i in range(n)
    ]


def _dag_plan(n_features: int) -> ExecutionPlan:
    """QUERY (injected fake source) → FILTER (v ≥ 5) → AGGREGATE (count by grp)."""
    return ExecutionPlan(
        plan_id=f"geobench-{n_features}",
        nodes=[
            ExecutionNode(
                node_id="src",
                category=NodeCategory.QUERY,
                parameters={"dataset_id": "fake", "query": {"limit": n_features}},
            ),
            ExecutionNode(
                node_id="flt",
                category=NodeCategory.FILTER,
                inputs=["src"],
                parameters={"predicate": {"op": "ge", "field": "v", "value": 5}},
            ),
            ExecutionNode(
                node_id="agg",
                category=NodeCategory.AGGREGATE,
                inputs=["flt"],
                parameters={"aggregates": [{"func": "count"}], "group_by": ["grp"]},
            ),
        ],
    )


def _inject_source(monkeypatch: pytest.MonkeyPatch, features_by_limit: dict[int, list[dict]]):
    """Wire ops.query_catalog_fn to an in-memory fake (offline; no DB)."""

    def fake_query(db, item_id, spec_dict):
        n = int((spec_dict or {}).get("limit") or 0)
        return QueryResult(dataset_id=str(item_id), features=features_by_limit[n])

    monkeypatch.setattr(ops, "query_catalog_fn", fake_query)


# ---------------------------------------------------------------------------
# 1. planner cost model monotonicity
# ---------------------------------------------------------------------------


def test_planner_cost_monotonic_in_feature_count():
    from app.services.data_fabric.query.optimizer import MAX_ALTERNATIVES, PlanCost, cost_of_chosen

    specs = {}
    plans = {}
    for fc in (1_000, 100_000):
        spec = normalize_query_spec(QuerySpec(limit=10_000))
        plan = plan_query(spec, _descriptor(fc), default_capabilities("postgis"))
        specs[fc], plans[fc] = spec, plan

    small, big = plans[1_000], plans[100_000]
    # rows scale linearly with feature_count (selectivity constant, no filters)
    ratio_rows = big.estimated_rows / small.estimated_rows
    assert 80.0 <= ratio_rows <= 125.0, (
        f"estimated_rows must grow ~linearly with feature_count (got x{ratio_rows:.2f})"
    )
    # transfer bytes: linear per page until the 10k page-window cap (target ratio 10)
    ratio_bytes = big.estimated_bytes / small.estimated_bytes
    assert 9.0 <= ratio_bytes <= 11.0, (
        f"estimated_bytes must stay on the page-window-capped linear curve (got x{ratio_bytes:.2f})"
    )

    for plan in (small, big):
        cost = PlanCost(**plan.cost)
        # cost components consistent with cost_of_chosen (no second truth)
        recomputed = cost_of_chosen(
            estimated_rows=plan.estimated_rows,
            estimated_bytes=plan.estimated_bytes,
            pushed_any=bool(plan.pushed_filters or plan.pushed_spatial or plan.pushed_aggregation),
            local_rows=plan.estimated_rows if plan.local_filters else 0,
        )
        assert cost.rows_emitted == recomputed.rows_emitted
        assert cost.bytes_transferred == recomputed.bytes_transferred
        assert cost.memory_class == recomputed.memory_class
        # bounded alternatives: never a combinatorial plan explosion
        assert len(plan.alternatives) <= MAX_ALTERNATIVES

    big_cost, small_cost = PlanCost(**big.cost), PlanCost(**small.cost)
    assert big_cost.score() > small_cost.score(), "cost score must grow with feature_count"


# ---------------------------------------------------------------------------
# 2. spatial-join complexity (STRtree, sub-quadratic guard)
# ---------------------------------------------------------------------------


def _grid_polygons(m: int, span: float = 100.0) -> list[dict]:
    """First m cells of a ceil(sqrt(m))-side grid covering [0, span]^2."""
    import math

    side = max(1, int(math.ceil(m**0.5)))
    cell = span / side
    polys = []
    for i in range(m):
        gx, gy = i % side, i // side
        x0, y0 = gx * cell, gy * cell
        polys.append(
            {
                "type": "Feature",
                "properties": {"pid": i},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[x0, y0], [x0 + cell, y0], [x0 + cell, y0 + cell], [x0, y0 + cell], [x0, y0]]
                    ],
                },
            }
        )
    return polys


def _random_points(n: int, span: float = 100.0) -> list[dict]:
    rng = random.Random(_SEED)
    return [
        {
            "type": "Feature",
            "properties": {"pt_id": i},
            "geometry": {
                "type": "Point",
                "coordinates": [rng.uniform(0, span), rng.uniform(0, span)],
            },
        }
        for i in range(n)
    ]


def test_spatial_join_subquadratic_and_correct(monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("shapely")
    from shapely.geometry import shape

    from app.services.data_fabric.query.federation import _LocalSpatialIndex, spatial_join_local

    # STRtree path is live (no silent linear-scan fallback)
    probe = _LocalSpatialIndex([shape(p["geometry"]) for p in _grid_polygons(16)])
    assert probe._tree is not None, "STRtree unavailable; join would degenerate to O(N*M)"

    tiny = spatial_join_local(_random_points(50), _grid_polygons(9), spatial_op="within")
    assert len(tiny) > 0  # warm-up: pays one-time shapely/STRtree import cost

    timings = {}
    results = {}
    for n in (2_000, 8_000):
        points, polys = _random_points(n), _grid_polygons(n)
        best = None
        for _ in range(2):  # best-of-2 dampens scheduler noise
            t0 = time.perf_counter()
            rows = spatial_join_local(points, polys, spatial_op="within")
            dt = time.perf_counter() - t0
            best = dt if best is None else min(best, dt)
        timings[n], results[n] = best, rows

    t_small, t_big = timings[2_000], timings[8_000]
    # documented bound: 48 * t(2k) = 4x size-up * 12x slack over linear (see docstring)
    assert t_big <= 12 * max(t_small, 0.01) * 4, (
        f"spatial join scaling guard blown: t(8k)={t_big:.3f}s vs 48x t(2k)={12 * t_small * 4:.3f}s"
    )
    assert t_big < 15.0, f"8k x 8k STRtree join must stay seconds-scale (got {t_big:.2f}s)"

    for n, rows in results.items():
        # grid cells overlap-free → each point matches at most one polygon
        assert 0 < len(rows) <= n
        assert len(rows) >= 0.95 * n, (
            f"expected ~full coverage of the random points (got {len(rows)}/{n})"
        )

    # correctness spot-check: re-verify 25 joined pairs with shapely
    poly_by_pid = {p["properties"]["pid"]: shape(p["geometry"]) for p in _grid_polygons(8_000)}
    pt_by_id = {p["properties"]["pt_id"]: shape(p["geometry"]) for p in _random_points(8_000)}
    for row in results[8_000][:25]:
        pt, pid = pt_by_id[row["pt_id"]], row["__right__"]["pid"]
        assert poly_by_pid[pid].contains(pt), (
            f"joined pair (pt {row['pt_id']}, poly {pid}) fails the within predicate"
        )


# ---------------------------------------------------------------------------
# 3. executor DAG scaling + determinism
# ---------------------------------------------------------------------------


def test_executor_dag_scaling_and_determinism(monkeypatch: pytest.MonkeyPatch):
    sizes = (5_000, 20_000)
    features = {n: _point_features(n) for n in (*sizes, 1_000)}
    _inject_source(monkeypatch, features)

    def run_once(n: int):
        engine = GeoExecutionEngine()  # fresh engine: no cross-run reuse pollution
        t0 = time.perf_counter()
        run = engine.execute_plan(_dag_plan(n))
        return run, engine.get_node_output(run.run_id, "agg"), time.perf_counter() - t0

    run_once(1_000)  # warm-up: one-time operator imports must not skew the ratio

    runs: dict[int, tuple] = {}
    for n in sizes:
        best = None
        for _ in range(2):  # best-of-2, fresh engine each time (reuse would fake speed)
            run, out, dt = run_once(n)
            best = (run, out, dt) if best is None or dt < best[2] else best
        runs[n] = best
        run = best[0]
        assert run.status.value == "completed"
        # deterministic emission: filter keeps exactly v>=5 → n/2; aggregate → 7 groups
        assert run.evidence["flt"].rows_emitted == n // 2, (
            f"FILTER must emit exactly n/2 rows at n={n} (got {run.evidence['flt'].rows_emitted})"
        )
        assert run.evidence["agg"].rows_emitted == 7

    t_small, t_big = runs[5_000][2], runs[20_000][2]
    # documented bound: linear expectation is 4x; 8x + 0.75s additive absorbs
    # scheduler/import jitter while still catching super-linear regressions
    assert t_big <= 8 * t_small + 0.75, (
        f"DAG execution must scale ~linearly: t(20k)={t_big:.3f}s vs bound {8 * t_small + 0.75:.3f}s"
    )
    # determinism across independent engines: identical aggregate payloads
    r_small_a = runs[5_000][1]["rows"]
    r_small_b = run_once(5_000)[1]["rows"]
    assert r_small_a == r_small_b, "two identical runs must produce identical aggregates"


# ---------------------------------------------------------------------------
# 4. cache / reuse
# ---------------------------------------------------------------------------


def test_executor_reuses_nodes_on_second_identical_run(monkeypatch: pytest.MonkeyPatch):
    features = {1_000: _point_features(1_000)}
    _inject_source(monkeypatch, features)
    engine = GeoExecutionEngine()
    plan = _dag_plan(1_000)

    run1 = engine.execute_plan(plan)
    run2 = engine.execute_plan(plan)  # same engine, same plan semantics

    assert run1.status.value == "completed"
    assert run2.status.value == "completed"
    statuses = {nid: ev.status for nid, ev in run2.evidence.items()}
    assert statuses == {"src": "reused", "flt": "reused", "agg": "reused"}, (
        f"second identical run must hit the reuse store (got {statuses})"
    )
    for nid in ("src", "flt", "agg"):
        assert run2.evidence[nid].rows_emitted == run1.evidence[nid].rows_emitted


# ---------------------------------------------------------------------------
# 5. federation: 3-source cost-ordered chain + fail-fast budget
# ---------------------------------------------------------------------------


def _fake_chain_adapter(rows: list[dict]):
    class _Adapter:
        def query(self, dataset_id: str, spec) -> QueryResult:
            limit = int(getattr(spec, "limit", 100) or 100)
            return QueryResult(dataset_id=str(dataset_id), features=rows[:limit])

    return _Adapter()


def _chain_sources(rows_b: list[dict]) -> dict:
    src_a = [{"id": i, "b_key": i % 50} for i in range(50)]
    src_c = [{"a_key": k % 10, "payload": k} for k in range(40)]
    return {
        "src_a": _fake_chain_adapter(src_a),
        "src_b": _fake_chain_adapter(rows_b),
        "src_c": _fake_chain_adapter(src_c),
    }

def test_federated_chain_cost_order_and_fail_fast_budget():
    from app.services.data_fabric.errors import QueryBudgetExceededError
    from app.services.data_fabric.query.federation import (
        ChainJoin,
        ChainSource,
        FederatedChainRequest,
        execute_chain,
    )
    from app.services.data_fabric.query.models import ExecutionBudget

    # hints deliberately shuffled: cost order must be c(40) < a(50) < b(100)
    def make_request(rows_b, budget=None):
        kwargs = {}
        if budget is not None:
            kwargs["budget"] = budget
        return FederatedChainRequest(
            sources=[
                ChainSource(source_id="src_b", dataset_id="b", estimated_rows=len(rows_b)),
                ChainSource(source_id="src_a", dataset_id="a", estimated_rows=50),
                ChainSource(source_id="src_c", dataset_id="c", estimated_rows=40),
            ],
            joins=[
                # cost order is c→a→b. join0 links C.a_key to A.id; the
                # accumulated row keeps C's fields top-level (the chain executor
                # joins on accumulated top-level keys only), so join1 links the
                # accumulated C.a_key to B.a_key_val.
                ChainJoin(kind="attribute_join", join_field_left="a_key", join_field_right="id"),
                ChainJoin(kind="attribute_join", join_field_left="a_key", join_field_right="a_key_val"),
            ],
            limit=10_000 if budget is None else 200,
            **kwargs,
        )

    rows_b = [{"b_id": j, "a_key_val": j % 10} for j in range(100)]
    adapters = _chain_sources(rows_b)
    out = execute_chain(make_request(rows_b), adapter_factory=lambda sid: adapters[sid])

    assert out["status"] == "success"
    assert out["order"] == ["src_c", "src_a", "src_b"], (
        f"chain must order sources by estimated_rows hints (got {out['order']})"
    )
    assert out["rows_fetched"] == 190  # 40 + 50 + 100, one bounded pull per source
    assert out["row_count"] == 400  # 40 C rows × 1 A match × 10 B matches — deterministic
    assert out["pushdown_ratio"] is not None

    # fail-fast: B fan-out (all a_key_val=0) explodes join1 past a tight budget →
    # typed error, chain stops (no silent truncation, no explosion)
    exploding_b = [{"b_id": j, "a_key_val": 0} for j in range(100)]
    adapters2 = _chain_sources(exploding_b)
    tight = ExecutionBudget(max_rows=200)
    with pytest.raises(QueryBudgetExceededError) as exc:
        execute_chain(
            make_request(exploding_b, budget=tight),
            adapter_factory=lambda sid: adapters2[sid],
        )
    assert exc.value.code == "QUERY_BUDGET_EXCEEDED"


# ---------------------------------------------------------------------------
# 6. memory ceiling: windowed raster execution (bounded, no whole-array op)
# ---------------------------------------------------------------------------


def _write_raster(path, size: int, seed: int = 11) -> int:
    """Synthetic tiled float32 raster, written in bounded row chunks."""
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    rng = np.random.default_rng(seed)
    chunk = 1024
    with rasterio.open(
        path, "w", driver="GTiff", width=size, height=size, count=1,
        dtype="float32", crs="EPSG:3857", transform=from_origin(0, 0, 10, 10),
        nodata=-9999.0, tiled=True, blockxsize=512, blockysize=512,
    ) as dst:
        for row0 in range(0, size, chunk):
            h = min(chunk, size - row0)
            dst.write(
                rng.uniform(0, 1000, (h, size)).astype("float32"), 1,
                window=rasterio.windows.Window(0, row0, size, h),
            )
    return size * size * 4  # uncompressed bytes


def test_windowed_raster_memory_ceiling(tmp_path):
    pytest.importorskip("rasterio")
    from app.lib.geo_raster import AlgorithmProfile, RasterSource, execute_windowed

    size = 4_000
    raster_bytes = _write_raster(tmp_path / "geobench.tif", size)

    tracemalloc.start()
    with RasterSource.from_path(str(tmp_path / "geobench.tif")).reader() as reader:
        result = execute_windowed(
            reader,
            AlgorithmProfile(halo=8),
            lambda a, core, read: a[
                core[1] - read[1]: core[1] - read[1] + core[3],
                core[0] - read[0]: core[0] - read[0] + core[2],
            ],
        )
        _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert result.windows_processed > 0
    assert result.array.shape == (size, size)
    # bound = merged output array + windowed streaming margin (see docstring);
    # a whole-array op would need >= 2x raster bytes and must fail this bound
    ceiling = raster_bytes + 96 * 1024 * 1024
    assert peak < ceiling, (
        f"windowed execution peak {peak / 1e6:.0f}MB exceeded ceiling "
        f"{ceiling / 1e6:.0f}MB — whole-array op suspicion"
    )


# ---------------------------------------------------------------------------
# 7. cancellation: bounded wall time + cancelled terminal state
# ---------------------------------------------------------------------------


def test_cancelled_slow_dag_completes_bounded(monkeypatch: pytest.MonkeyPatch):
    from app.lib.cancellation import CancellationToken, checkpoint

    _inject_source(monkeypatch, {1_000: _point_features(1_000)})

    entered = threading.Event()

    def slow_source(db, item_id, spec_dict):
        """Simulates a long-running source: blocks until cancelled via the
        cooperative checkpoint — the cancel token, not a sleep race, ends it."""
        entered.set()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            checkpoint()  # raises OperationCancelled once the token fires
            time.sleep(0.005)
        return QueryResult(dataset_id=str(item_id), features=[])

    plan = ExecutionPlan(
        plan_id="geobench-cancel",
        nodes=[
            ExecutionNode(
                node_id="src",
                category=NodeCategory.QUERY,
                parameters={"dataset_id": "fake", "query": {"limit": 1_000}},
            ),
            ExecutionNode(
                node_id="flt",
                category=NodeCategory.FILTER,
                inputs=["src"],
                parameters={"predicate": {"op": "ge", "field": "v", "value": 5}},
            ),
        ],
    )

    engine = GeoExecutionEngine()
    warm = engine.execute_plan(_dag_plan(1_000))  # warm-up: pay one-time imports
    assert warm.status.value == "completed"

    monkeypatch.setattr(ops, "query_catalog_fn", slow_source)
    token = CancellationToken("geobench-cancel")
    box: dict = {}

    def execute():
        box["run"] = engine.execute_plan(plan, cancel_token=token)

    t0 = time.perf_counter()
    worker = threading.Thread(target=execute, daemon=True)
    worker.start()
    assert entered.wait(10), "slow source never started"
    token.cancel("geobench cancel")
    worker.join(timeout=5)
    wall = time.perf_counter() - t0

    assert not worker.is_alive(), "cancelled run did not return within 5s"
    run = box["run"]
    assert run.status.value == "cancelled"
    cancelled_nodes = {nid for nid, ev in run.evidence.items() if ev.status == "cancelled"}
    assert cancelled_nodes, f"cancellation must mark nodes cancelled (got {run.evidence})"
    assert run.wall_time_s is not None and run.wall_time_s < 2.0, (
        f"cancelled run must finish within 2s (wall_time_s={run.wall_time_s}, join wall {wall:.2f}s)"
    )


# ---------------------------------------------------------------------------
# bonus quick-tier: GeoParquet row-group pruning (skips when pyarrow absent)
# ---------------------------------------------------------------------------


def test_geoparquet_bbox_row_group_pruning(monkeypatch, tmp_path):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    shapely = pytest.importorskip("shapely")
    import numpy as np

    from app.services.data_fabric.adapters import geoparquet_adapter as gp_mod

    n_rows, group_size = 100_000, 10_000
    rng = np.random.default_rng(7)
    xs = np.sort(rng.uniform(0.0, 100.0, n_rows))  # sorted x → x-disjoint row groups
    ys = rng.uniform(0.0, 100.0, n_rows)
    geo_meta = {
        "version": "1.0.0",
        "primary_column": "geometry",
        "columns": {
            "geometry": {
                "encoding": "wkb",
                "covering": {"bbox": {"xmin": "xmin", "ymin": "ymin", "xmax": "xmax", "ymax": "ymax"}},
            }
        },
    }
    table = pa.table(
        {
            "id": pa.array(np.arange(n_rows), type=pa.int64()),
            "geometry": pa.array(shapely.to_wkb(shapely.points(xs, ys))),
            "xmin": pa.array(xs), "ymin": pa.array(ys),
            "xmax": pa.array(xs), "ymax": pa.array(ys),
        }
    ).replace_schema_metadata({"geo": json.dumps(geo_meta)})
    path = tmp_path / "pts.parquet"
    pq.write_table(table, str(path), row_group_size=group_size)

    monkeypatch.setattr(gp_mod, "_local_file_roots_from_settings", lambda: [str(tmp_path)])
    monkeypatch.setattr(gp_mod, "_local_file_max_bytes_from_settings", lambda: 1 << 30)
    adapter = gp_mod.GeoParquetAdapter(
        ConnectionProfile(id="gp_bench", source_type="geoparquet", endpoint=str(path))
    )

    res = adapter.query("pts.parquet", QuerySpec(bbox=[45.0, 0.0, 55.0, 100.0], limit=100, fields=["id"]))
    md = res.metadata
    ev = md["query_evidence"]
    assert md["pushdown_bbox"] is True
    assert len(res.features) == 100
    # x-sorted row groups: a 10%-wide bbox can touch at most 3 of the 10 groups
    assert md["row_groups_read"] <= 3, (
        f"bbox pushdown must prune most row groups (read {md['row_groups_read']}/10)"
    )
    assert md["row_groups_pruned"] >= 6
    # page-bounded transfer: page + at most one sentinel row per touched group
    assert ev["rows_fetched"] <= 1_100
