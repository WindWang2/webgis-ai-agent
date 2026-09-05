"""GeoCompute V3 / Data Fabric V3 benchmark — GeoBench categories, larger sizes.

Informational companion to ``tests/benchmarks/test_geobench_v3.py`` (the
CI quick-tier gate). Standalone, generated fixtures only (nothing committed,
tmpdir cleaned up, no network / docker). Machine-sensitive wall-clock numbers
are printed as **[INFO timing]** rows and NEVER gate the exit code — only
structural, machine-independent invariants do (statuses, deterministic row
counts, estimator ratios, tracemalloc peaks, row-group pruning counts).

Usage:
    .venv/bin/python tests/benchmarks/bench_geocompute_v3.py [--quick] [--heavy]

``--quick`` (default when neither flag is given) caps sizes; ``--heavy``
raises to 100k-feature DAGs / a 10000x10000 float32 raster / a 400k-row
GeoParquet fixture. Expect ~15s quick, ~2-4min heavy.

Gate (exit 1) covers only: cost-model monotonicity bands, join result
correctness, executor status/row determinism, reuse flags, chain cost order,
typed fail-fast budget error, windowed-memory ceiling, cancel terminal state,
GeoParquet row-group pruning. Timings are informational observations.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import threading
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import random

import numpy as np

from app.schemas.data_fabric_schema import (
    ConnectionProfile,
    DatasetDescriptor,
    QueryResult,
    QuerySpec,
)
from app.services.data_fabric.query.capabilities import default_capabilities
from app.services.data_fabric.query.federation import (
    ChainJoin,
    ChainSource,
    FederatedChainRequest,
    execute_chain,
    spatial_join_local,
)
from app.services.data_fabric.query.models import ExecutionBudget
from app.services.data_fabric.query.normalize import normalize_query_spec
from app.services.data_fabric.query.planner import plan_query
from app.services.geocompute import ops
from app.services.geocompute.executor import GeoExecutionEngine
from app.services.geocompute.plan import (
    ExecutionNode,
    ExecutionPlan,
    NodeCategory,
    ResourceBudget,
)

_DEFAULT_QUERY_FN = ops.query_catalog_fn
_SEED = 42
ROWS: list[tuple[str, str, str]] = []  # category, metric, value


def _row(category: str, metric: str, value: str, timing: bool = False) -> None:
    ROWS.append((category, metric + ("  [INFO timing]" if timing else ""), value))


def _failures() -> list[str]:
    return [f"{c} / {m}: {v}" for c, m, v in ROWS if m.startswith("FAIL")]


def _ok(category: str, metric: str, detail: str = "") -> None:
    _row(category, f"OK {metric}", detail)


def _fail(category: str, metric: str, detail: str = "") -> None:
    _row(category, f"FAIL {metric}", detail)


# ── fixture builders ────────────────────────────────────────────────────────


def _descriptor(feature_count: int) -> DatasetDescriptor:
    return DatasetDescriptor(
        id="geobench_d", source_type="postgis", feature_count=feature_count,
        srs="EPSG:4326", bbox=[0.0, 0.0, 100.0, 100.0],
        fields=[{"name": "v", "type": "int"}, {"name": "grp", "type": "int"}],
        metadata={"has_geometry_index": True},
    )


def _point_features(n: int) -> list[dict]:
    return [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [i % 100, (i * 7) % 100]},
         "properties": {"v": i % 10, "grp": i % 7, "idx": i}}
        for i in range(n)
    ]


def _dag_plan(n_features: int, budget: ResourceBudget | None = None) -> ExecutionPlan:
    return ExecutionPlan(
        plan_id=f"geobench-{n_features}",
        budget=budget or ResourceBudget(),
        nodes=[
            ExecutionNode(node_id="src", category=NodeCategory.QUERY,
                          parameters={"dataset_id": "fake", "query": {"limit": n_features}}),
            ExecutionNode(node_id="flt", category=NodeCategory.FILTER, inputs=["src"],
                          parameters={"predicate": {"op": "ge", "field": "v", "value": 5}}),
            ExecutionNode(node_id="agg", category=NodeCategory.AGGREGATE, inputs=["flt"],
                          parameters={"aggregates": [{"func": "count"}], "group_by": ["grp"]}),
        ],
    )


def _grid_polygons(m: int, span: float = 100.0) -> list[dict]:
    import math

    side = max(1, int(math.ceil(m**0.5)))
    cell = span / side
    polys = []
    for i in range(m):
        gx, gy = i % side, i // side
        x0, y0 = gx * cell, gy * cell
        polys.append({
            "type": "Feature", "properties": {"pid": i},
            "geometry": {"type": "Polygon", "coordinates": [[
                [x0, y0], [x0 + cell, y0], [x0 + cell, y0 + cell],
                [x0, y0 + cell], [x0, y0],
            ]]},
        })
    return polys


def _random_points(n: int, span: float = 100.0) -> list[dict]:
    rng = random.Random(_SEED)
    return [
        {"type": "Feature", "properties": {"pt_id": i},
         "geometry": {"type": "Point",
                      "coordinates": [rng.uniform(0, span), rng.uniform(0, span)]}}
        for i in range(n)
    ]


def _write_raster(path: Path, size: int, seed: int = 11) -> int:
    """Bounded chunked write; never materializes the full array."""
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
            dst.write(rng.uniform(0, 1000, (h, size)).astype("float32"), 1,
                      window=rasterio.windows.Window(0, row0, size, h))
    return size * size * 4


class _FakeChainAdapter:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def query(self, dataset_id: str, spec) -> QueryResult:
        limit = int(getattr(spec, "limit", 100) or 100)
        return QueryResult(dataset_id=str(dataset_id), features=self._rows[:limit])


# ── categories ──────────────────────────────────────────────────────────────


def bench_planner(pair: tuple[int, int]) -> None:
    from app.services.data_fabric.query.models import MAX_PAGE_LIMIT
    from app.services.data_fabric.query.optimizer import MAX_ALTERNATIVES, PlanCost

    cat = "1 planner-cost"
    plans = {}
    for fc in pair:
        t0 = time.perf_counter()
        plan = plan_query(normalize_query_spec(QuerySpec(limit=10_000)), _descriptor(fc),
                          default_capabilities("postgis"))
        plans[fc] = plan
        _row(cat, f"plan_query feature_count={fc}", f"{time.perf_counter() - t0:.4f}s", timing=True)
        _row(cat, f"est_rows@{fc}", str(plan.estimated_rows))
        _row(cat, f"est_bytes@{fc}", str(plan.estimated_bytes))
        _row(cat, f"alternatives@{fc}", f"{len(plan.alternatives)} (max {MAX_ALTERNATIVES})")

    small, big = plans[pair[0]], plans[pair[1]]
    ratio_rows = big.estimated_rows / small.estimated_rows
    ratio_bytes = big.estimated_bytes / small.estimated_bytes
    target_rows = pair[1] / pair[0]
    # bytes grow linearly until the page-window cap, then stay flat (bounded transfer)
    target_bytes = min(pair[1], MAX_PAGE_LIMIT) / min(pair[0], MAX_PAGE_LIMIT)
    _row(cat, "est_rows ratio", f"x{ratio_rows:.1f} (target x{target_rows:.0f}, band 80%-125%)")
    _row(cat, "est_bytes ratio",
         f"x{ratio_bytes:.1f} (page-window-capped linear, target x{target_bytes:.0f})")
    # PlanCost.score is per-page (bytes are page-capped, rows_emitted is unweighted),
    # so once both sides saturate the page window the score ties — monotone (>=),
    # not strictly increasing.
    if len(big.alternatives) <= MAX_ALTERNATIVES \
            and 0.8 * target_rows <= ratio_rows <= 1.25 * target_rows \
            and 0.8 * target_bytes <= ratio_bytes <= 1.25 * target_bytes \
            and PlanCost(**big.cost).score() >= PlanCost(**small.cost).score():
        _ok(cat, "monotonicity + bounded alternatives")
    else:
        _fail(cat, "monotonicity + bounded alternatives")


def bench_spatial_join(pair: tuple[int, int]) -> None:
    pytest_skip = None
    try:
        from shapely.geometry import shape
        from shapely.strtree import STRtree  # noqa: F401
    except Exception as exc:  # pragma: no cover
        pytest_skip = str(exc)
    if pytest_skip is not None:
        _row("2 spatial-join", "SKIPPED (shapely unavailable)", pytest_skip)
        return

    from app.services.data_fabric.query.federation import _LocalSpatialIndex

    cat = "2 spatial-join"
    probe = _LocalSpatialIndex([shape(p["geometry"]) for p in _grid_polygons(16)])
    if probe._tree is None:
        _fail(cat, "strtree-available")
        return
    _ok(cat, "strtree-available")

    spatial_join_local(_random_points(50), _grid_polygons(9), spatial_op="within")  # warm-up
    for n in pair:
        points, polys = _random_points(n), _grid_polygons(n)
        t0 = time.perf_counter()
        rows = spatial_join_local(points, polys, spatial_op="within")
        dt = time.perf_counter() - t0
        _row(cat, f"join {n}x{n}", f"{dt:.3f}s", timing=True)
        _row(cat, f"joined_rows@{n}", str(len(rows)))
        if not (0 < len(rows) <= n and len(rows) >= 0.95 * n):
            _fail(cat, f"join-coverage@{n}", f"{len(rows)}/{n}")
            return
        if n == pair[1]:
            poly_by_pid = {p["properties"]["pid"]: shape(p["geometry"]) for p in polys}
            pt_by_id = {p["properties"]["pt_id"]: shape(p["geometry"]) for p in points}
            bad = [
                r for r in rows[:25]
                if not poly_by_pid[r["__right__"]["pid"]].contains(pt_by_id[r["pt_id"]])
            ]
            if bad:
                _fail(cat, "spot-check-within", f"{len(bad)}/25 pairs wrong")
                return
    _ok(cat, "coverage + 25-pair within spot-check")


def _inject_source(features_by_limit: dict[int, list[dict]]) -> None:
    def fake_query(db, item_id, spec_dict):
        n = int((spec_dict or {}).get("limit") or 0)
        return QueryResult(dataset_id=str(item_id), features=features_by_limit[n])

    ops.query_catalog_fn = fake_query


def bench_executor_dag(sizes: tuple[int, int]) -> None:
    cat = "3 executor-dag"
    budget = ResourceBudget(max_rows=1_000_000) if max(sizes) > 200_000 else None
    features = {n: _point_features(n) for n in (*sizes, 1_000)}
    _inject_source(features)

    def run_once(n: int):
        engine = GeoExecutionEngine()
        t0 = time.perf_counter()
        run = engine.execute_plan(_dag_plan(n, budget))
        return run, engine.get_node_output(run.run_id, "agg"), time.perf_counter() - t0

    run_once(1_000)  # warm-up
    runs = {}
    for n in sizes:
        best = None
        for _ in range(2):
            got = run_once(n)
            best = got if best is None or got[2] < best[2] else best
        runs[n] = best
        run = best[0]
        _row(cat, f"chain QUERY→FILTER→AGGREGATE n={n}", f"{best[2]:.3f}s", timing=True)
        _row(cat, f"rows_emitted flt/agg @{n}",
             f"{run.evidence['flt'].rows_emitted}/{run.evidence['agg'].rows_emitted}")
        if run.status.value != "completed" or run.evidence["flt"].rows_emitted != n // 2 \
                or run.evidence["agg"].rows_emitted != 7:
            _fail(cat, f"status+rows@{n}", run.status.value)
            return

    t_small, t_big = runs[sizes[0]][2], runs[sizes[1]][2]
    _row(cat, "scaling ratio", f"x{t_big / max(t_small, 1e-4):.2f} for x{sizes[1] // sizes[0]} size-up")
    if runs[sizes[0]][1]["rows"] != run_once(sizes[0])[1]["rows"]:
        _fail(cat, "determinism-two-runs")
        return
    _ok(cat, "status, exact rows, determinism across runs")


def bench_reuse() -> None:
    cat = "4 cache-reuse"
    features = {1_000: _point_features(1_000)}
    _inject_source(features)
    engine = GeoExecutionEngine()
    plan = _dag_plan(1_000)
    t0 = time.perf_counter()
    run1 = engine.execute_plan(plan)
    cold = time.perf_counter() - t0
    t0 = time.perf_counter()
    run2 = engine.execute_plan(plan)
    warm = time.perf_counter() - t0
    statuses = {nid: ev.status for nid, ev in run2.evidence.items()}
    _row(cat, "cold run", f"{cold:.4f}s", timing=True)
    _row(cat, "warm run", f"{warm:.4f}s (informational; gate is the flag)", timing=True)
    _row(cat, "warm statuses", json.dumps(statuses))
    if run1.status.value == "completed" and run2.status.value == "completed" \
            and set(statuses.values()) == {"reused"}:
        _ok(cat, "second run fully reused")
    else:
        _fail(cat, "second run fully reused", json.dumps(statuses))


def bench_federation(n_c: int, n_a: int, n_b: int) -> None:
    """3-source chain. Requires n_c < n_a < n_b (the asserted cost order).
    Key space = max(100, n_b // 10): each C row matches exactly 1 A row and
    10 B rows → joined rows = n_c * 10 (bounded, deterministic)."""
    cat = "5 federation-chain"
    key_space = max(100, n_b // 10)
    src_c = [{"a_key": k % key_space, "payload": k} for k in range(n_c)]
    src_a = [{"id": i, "b_key": i % n_a} for i in range(n_a)]
    src_b = [{"b_id": j, "a_key_val": j % key_space} for j in range(n_b)]
    adapters = {"src_a": _FakeChainAdapter(src_a), "src_b": _FakeChainAdapter(src_b),
                "src_c": _FakeChainAdapter(src_c)}

    def make_request(rows_b, budget=None):
        kwargs = {"budget": budget} if budget is not None else {}
        return FederatedChainRequest(
            sources=[
                ChainSource(source_id="src_b", dataset_id="b", estimated_rows=len(rows_b)),
                ChainSource(source_id="src_a", dataset_id="a", estimated_rows=n_a),
                ChainSource(source_id="src_c", dataset_id="c", estimated_rows=n_c),
            ],
            joins=[
                # cost order is c→a→b (hints ascending); the accumulated row keeps
                # C's fields top-level, so both hops join from C.a_key
                ChainJoin(kind="attribute_join", join_field_left="a_key", join_field_right="id"),
                ChainJoin(kind="attribute_join", join_field_left="a_key", join_field_right="a_key_val"),
            ],
            limit=10_000 if budget is None else 200,
            **kwargs,
        )

    t0 = time.perf_counter()
    out = execute_chain(make_request(src_b), adapter_factory=lambda sid: adapters[sid])
    _row(cat, "chain execution", f"{time.perf_counter() - t0:.3f}s", timing=True)
    _row(cat, "cost order", json.dumps(out["order"]))
    _row(cat, "rows_fetched / joined / returned",
         f"{out['rows_fetched']} / {out['joined_row_count']} / {out['row_count']}")
    nb_fetched = min(n_b, 10_000)  # chain pulls at most req.limit (10k) per source
    expected_joined = min(n_c, 10_000) * (nb_fetched // key_space)
    expected_fetched = min(n_c, 10_000) + min(n_a, 10_000) + nb_fetched
    if out["order"] != ["src_c", "src_a", "src_b"] \
            or out["joined_row_count"] != expected_joined \
            or out["row_count"] != min(expected_joined, 10_000) \
            or out["rows_fetched"] != expected_fetched:
        _fail(cat, "cost-order + deterministic counts",
              f"expected joined {expected_joined}, got {out['joined_row_count']}")
        return

    # fail-fast: exploding B (single key) fans the last hop past a tight budget
    exploding = [{"b_id": j, "a_key_val": 0} for j in range(n_b)]
    adapters2 = dict(adapters)
    adapters2["src_b"] = _FakeChainAdapter(exploding)
    from app.services.data_fabric.errors import DataFabricError

    t0 = time.perf_counter()
    try:
        execute_chain(
            make_request(exploding, budget=ExecutionBudget(max_rows=200)),
            adapter_factory=lambda sid: adapters2[sid],
        )
        _fail(cat, "fail-fast-budget", "no error raised")
        return
    except DataFabricError as exc:
        _row(cat, "fail-fast latency", f"{time.perf_counter() - t0:.3f}s", timing=True)
        _row(cat, "fail-fast error", str(exc.code))
        if exc.code != "QUERY_BUDGET_EXCEEDED":
            _fail(cat, "fail-fast-budget", str(exc.code))
            return
    _ok(cat, "cost order, counts, typed fail-fast")


def bench_raster_memory(size: int) -> None:
    cat = "6 raster-memory"
    try:
        import rasterio  # noqa: F401
    except Exception as exc:
        _row(cat, "SKIPPED (rasterio unavailable)", str(exc))
        return
    from app.lib.geo_raster import AlgorithmProfile, RasterSource, execute_windowed

    tmp = Path(tempfile.mkdtemp(prefix="geobench_raster_"))
    try:
        t0 = time.perf_counter()
        raster_bytes = _write_raster(tmp / "bench.tif", size)
        _row(cat, f"fixture {size}x{size} float32", f"{time.perf_counter() - t0:.2f}s, "
             f"{raster_bytes / 1e6:.0f}MB uncompressed", timing=True)

        tracemalloc.start()
        t0 = time.perf_counter()
        with RasterSource.from_path(str(tmp / "bench.tif")).reader() as reader:
            result = execute_windowed(
                reader, AlgorithmProfile(halo=8),
                lambda a, core, read: a[
                    core[1] - read[1]: core[1] - read[1] + core[3],
                    core[0] - read[0]: core[0] - read[0] + core[2],
                ],
            )
        dt = time.perf_counter() - t0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        ceiling = raster_bytes + 160 * 1024 * 1024
        _row(cat, "windowed execution", f"{dt:.2f}s, {result.windows_processed} windows", timing=True)
        _row(cat, "peak allocation", f"{peak / 1e6:.0f}MB (ceiling {ceiling / 1e6:.0f}MB)")
        if result.windows_processed > 0 and result.array.shape == (size, size) and peak < ceiling:
            _ok(cat, "windowed path stays under ceiling (no whole-array op)")
        else:
            _fail(cat, "windowed-memory-ceiling", f"peak {peak / 1e6:.0f}MB")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def bench_cancel() -> None:
    from app.lib.cancellation import CancellationToken, checkpoint

    cat = "7 cancel"
    _inject_source({1_000: _point_features(1_000)})
    engine = GeoExecutionEngine()
    engine.execute_plan(_dag_plan(1_000))  # warm-up

    entered = threading.Event()

    def slow_source(db, item_id, spec_dict):
        entered.set()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            checkpoint()  # cooperative cancel point
            time.sleep(0.005)
        return QueryResult(dataset_id=str(item_id), features=[])

    ops.query_catalog_fn = slow_source
    plan = ExecutionPlan(plan_id="geobench-cancel", nodes=[
        ExecutionNode(node_id="src", category=NodeCategory.QUERY,
                      parameters={"dataset_id": "fake", "query": {"limit": 1_000}}),
        ExecutionNode(node_id="flt", category=NodeCategory.FILTER, inputs=["src"],
                      parameters={"predicate": {"op": "ge", "field": "v", "value": 5}}),
    ])
    token = CancellationToken("geobench-bench-cancel")
    box: dict = {}

    def execute():
        box["run"] = engine.execute_plan(plan, cancel_token=token)

    worker = threading.Thread(target=execute, daemon=True)
    t0 = time.perf_counter()
    worker.start()
    if not entered.wait(10):
        _fail(cat, "slow-source-started")
        return
    token.cancel("bench cancel")
    worker.join(timeout=5)
    wall = time.perf_counter() - t0
    ops.query_catalog_fn = _DEFAULT_QUERY_FN  # restore injection point
    run = box.get("run")
    _row(cat, "cancel-to-terminal wall", f"{wall:.3f}s", timing=True)
    if run is None or worker.is_alive() or run.status.value != "cancelled" \
            or not any(ev.status == "cancelled" for ev in run.evidence.values()) \
            or (run.wall_time_s or 9) >= 2.0:
        _fail(cat, "cancelled-bounded", run.status.value if run else "no run")
        return
    _ok(cat, "run CANCELLED within 2s, nodes marked cancelled")


def bench_geoparquet(n_rows: int, group_size: int) -> None:
    cat = "8 geoparquet-pushdown"
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
        from shapely import points as shp_points, to_wkb
    except Exception as exc:
        _row(cat, "SKIPPED (pyarrow/shapely unavailable)", str(exc))
        return
    from app.services.data_fabric.adapters import geoparquet_adapter as gp_mod

    tmp = Path(tempfile.mkdtemp(prefix="geobench_gp_"))
    try:
        rng = np.random.default_rng(7)
        xs = np.sort(rng.uniform(0.0, 100.0, n_rows))  # x-sorted → x-disjoint groups
        ys = rng.uniform(0.0, 100.0, n_rows)
        geo_meta = {
            "version": "1.0.0", "primary_column": "geometry",
            "columns": {"geometry": {
                "encoding": "wkb",
                "covering": {"bbox": {"xmin": "xmin", "ymin": "ymin", "xmax": "xmax", "ymax": "ymax"}},
            }},
        }
        table = pa.table({
            "id": pa.array(np.arange(n_rows), type=pa.int64()),
            "geometry": pa.array(to_wkb(shp_points(xs, ys))),
            "xmin": pa.array(xs), "ymin": pa.array(ys),
            "xmax": pa.array(xs), "ymax": pa.array(ys),
        }).replace_schema_metadata({"geo": json.dumps(geo_meta)})
        path = tmp / "pts.parquet"
        t0 = time.perf_counter()
        pq.write_table(table, str(path), row_group_size=group_size)
        _row(cat, "fixture build", f"{n_rows} rows, {path.stat().st_size / 1e6:.1f}MB, "
             f"{time.perf_counter() - t0:.2f}s", timing=True)

        gp_mod._local_file_roots_from_settings = lambda: [str(tmp)]
        gp_mod._local_file_max_bytes_from_settings = lambda: 1 << 30
        adapter = gp_mod.GeoParquetAdapter(ConnectionProfile(
            id="gp_bench", source_type="geoparquet", endpoint=str(path)))

        total_groups = (n_rows + group_size - 1) // group_size
        t0 = time.perf_counter()
        res = adapter.query("pts.parquet", QuerySpec(
            bbox=[45.0, 0.0, 55.0, 100.0], limit=100, fields=["id"]))
        pushdown_s = time.perf_counter() - t0
        md, ev = res.metadata, res.metadata["query_evidence"]
        t0 = time.perf_counter()
        naive = adapter.query("pts.parquet", QuerySpec(limit=100, fields=["id"]))
        naive_s = time.perf_counter() - t0
        groups_read = md["row_groups_read"]

        _row(cat, "bbox query (pushdown)", f"{pushdown_s:.3f}s", timing=True)
        _row(cat, "no-bbox query", f"{naive_s:.3f}s", timing=True)
        _row(cat, "row groups read / total", f"{groups_read}/{total_groups} "
             f"(pruned {md['row_groups_pruned']})")
        _row(cat, "rows_fetched pushdown vs naive",
             f"{ev['rows_fetched']} vs {naive.metadata['query_evidence']['rows_fetched']}")
        _row(cat, "rows transferred (page-bounded)", str(ev["rows_returned"]))
        _row(cat, "pushdown_ratio (evidence)", str(ev.get("pushdown_ratio")))
        _row(cat, "bytes read (est.) pushdown vs naive",
             f"{groups_read / total_groups * path.stat().st_size / 1e6:.1f}MB vs "
             f"{path.stat().st_size / 1e6:.1f}MB")
        max_groups = max(3, total_groups // 5)
        if md["pushdown_bbox"] is True and len(res.features) == 100 \
                and groups_read <= max_groups and ev["rows_fetched"] <= 100 + 11 * max_groups:
            _ok(cat, f"row-group pruning ≤ {max_groups}/{total_groups} groups, "
                     f"page-bounded fetch vs naive full-file scan")
        else:
            _fail(cat, "row-group-pruning", json.dumps({k: md.get(k) for k in
                 ("row_groups_read", "row_groups_pruned", "pushdown_bbox")}))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── driver ──────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true", help="cap sizes (default tier)")
    parser.add_argument("--heavy", action="store_true", help="raise to 100k features / 10000^2 raster")
    args = parser.parse_args()
    heavy = args.heavy

    planner_pair = (1_000_000, 10_000_000) if heavy else (100_000, 1_000_000)
    join_sizes = (100_000, 20_000) if heavy else (10_000, 40_000)
    dag_sizes = (200_000, 450_000) if heavy else (20_000, 80_000)
    raster_size = 10_000 if heavy else 4_000
    fed_sizes = (4_000, 5_000, 20_000) if heavy else (400, 500, 1_000)
    gp_rows, gp_group = (400_000, 20_000) if heavy else (100_000, 10_000)

    bench_planner(planner_pair)
    bench_spatial_join(join_sizes)
    bench_executor_dag(dag_sizes)
    bench_reuse()
    bench_federation(*fed_sizes)
    bench_raster_memory(raster_size)
    bench_cancel()
    bench_geoparquet(gp_rows, gp_group)

    width = max(len(m) for _, m, _ in ROWS) + 2
    print("=" * 100)
    print(f"{'category':<22}{'metric':<{width}}value")
    print("-" * 100)
    for category, metric, value in ROWS:
        print(f"{category:<22}{metric:<{width}}{value}")
    print("=" * 100)

    failures = _failures()
    print(f"\ngeobench-v3 structural invariants: {'PASS' if not failures else 'FAIL'}"
          + ("" if not failures else f" -> {len(failures)} failure(s)"))
    for f in failures:
        print(f"  - {f}")
    print("note: rows marked [INFO timing] are machine-sensitive observations, not gates")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
