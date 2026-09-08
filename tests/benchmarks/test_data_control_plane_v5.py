"""Data Control Plane V5 — structural guards for the data-control +
geo-compute plane (Wave 14).

Same discipline as ``test_geobench_v3.py``: generated fixtures only (no
committed datasets, no network, no docker; deterministic seeds, bounded
memory, small payloads). **No fragile wall-clock gates** — throughput
numbers (features/sec, descriptors/sec) are printed as INFORMATIONAL
summaries and never asserted; the only timing-shaped assertions are wide
structural scaling-ratio bands that catch accidental super-linear (O(n²))
blowups, plus non-timing evidence (statuses, row counts, scan caps,
truncation flags, query counts, cache-hit invocation counts).

Selection contract (#664): every test carries the ``perf`` marker. The
root ``tests/conftest.py`` self-skips perf items on unfiltered full-suite
runs, so the default ``pytest tests/`` lane stays fast; run this file with
``pytest tests/benchmarks/test_data_control_plane_v5.py -m perf --no-cov``.

Asserted bounds and rationale:

1. data plane scale
   - vector profile 10k/100k: deep profiling completes at both sizes;
     10k scans everything (COMPLETE), 100k is deterministically stride-
     sampled and the scanned-rows evidence stays at the DEFAULT_MAX_SCAN_ROWS
     cap (bounded-scan contract — a regression that whole-scans would
     report scanned_rows == row_count and fail). features/sec printed only.
   - descriptor planning 1M: feeding 1M synthetic column-stat descriptors
     through the selectivity/cardinality model must stay O(n) — the
     t(1M)/t(250k) ratio must land in [0.8, 8] (linear expectation 4; the
     band only absorbs scheduler noise while still failing an accidental
     O(n²) at ~16x). Descriptors are produced by a generator (no 1M-object
     materialization).
   - wide schema 512: features_to_arrow preserves all 512 property columns
     (+geometry); arrow_ops projection prunes to the requested columns; the
     dict-lane (streaming) fallback prunes identically on the same input
     (pyarrow-guarded: importorskip, same skip discipline as geobench_v3).
   - long lineage 200: session-side traversal honors max_depth on closures,
     the depth-bounded view declares truncation honestly, the full 200-deep
     view stays under the node cap; DB-side get_lineage_graph over the same
     chain keeps the #602 discipline — query count bounded by depth, not
     chain size.
   - large catalog 2500: the session source never scans past the 2000-entry
     catalog cap (entries beyond the cap cannot match — proven with a bbox
     whose last toucher sits exactly past the cap), results are page-bounded
     with an explicit truncated flag, and keyword/field/bbox axes return
     deterministic subsets.
   - many revisions 500: head_revision and list_revisions cost exactly one
     query each regardless of revision count (SQLAlchemy event counter,
     mirroring test_lineage_query_count.py) and list_revisions caps its
     page at 200 rows.
2. streaming
   - 100k-feature Arrow pipeline filter→project→aggregate completes with
     exactly n/2 surviving rows and 7 deterministic groups; the dict lane
     over the identical input agrees group-for-group.
   - cancellation fires at a batch boundary (never mid-batch): after the
     token cancels, exactly k whole batches were consumed and every
     consumed batch was atomically filtered.
3. compute
   - 50- and 100-node spine+fan-out DAGs complete with deterministic
     rows_emitted; a second identical run is served from the reuse store
     (every node status "reused" — asserted via the reuse flag, never via
     wall-time).
   - worker loss: a durable node job whose heartbeat ages out is swept
     stale and ``await_node_job`` surfaces the typed NodeExecutionError
     with failure_class worker_loss (retryable class, per ADR-0101 D5).
   - resource denial: a plan whose row estimate exceeds the governor scope
     is denied with a typed BudgetExceededError carrying actionable
     suggestions, no leaked execution scope, and the engine stays usable
     for an within-budget run.
4. federation
   - 2/3/4-source chain planning: join-order enumeration is structurally
     bounded (≤ MAX_ORDER_CANDIDATES = 24 candidates, never exponential),
     the cost order is deterministic, and projection derivation emits the
     minimal per-source key set (one join key each — never the full
     schema). >4 sources is a typed planning failure.
   - semi-join reduction fires when cardinality says so (right side
     shrinks to the left key set) and honestly gives up (identity return)
     beyond the key-set cap.
5. raster windows (2048² synthetic, rasterio-guarded)
   - chunk descriptors partition the grid exactly (16 disjoint 512² chunks
     summing to the full area) with deterministic chunk ids;
   - chunk cache second-run hit: fn is invoked ZERO times when every chunk
     is served from the cache (invocation counting, not wall-time);
   - cancellation mid-run preserves exactly the completed chunk prefix
     (5 chunks done, 5 on_chunk_done callbacks, typed
     OperationCancelled out).
"""
from __future__ import annotations

import math
import time
import uuid

import pytest

pytestmark = pytest.mark.perf

_SEED = 20260907


def _summary(name: str, **kv) -> None:
    """INFORMATIONAL summary print — never asserted against."""
    payload = " ".join(f"{k}={v}" for k, v in kv.items())
    print(f"[data-control-v5] {name}: {payload}")


def _grid_features(n: int) -> list[dict]:
    """Deterministic synthetic points: v cycles 0..9, grp cycles 0..6."""
    return [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [i % 100, (i * 7) % 100]},
            "properties": {"v": i % 10, "grp": i % 7},
        }
        for i in range(n)
    ]


def _chunked(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


# ---------------------------------------------------------------------------
# 1. data plane scale guards
# ---------------------------------------------------------------------------


async def test_vector_profile_scale_10k_100k():
    """Deep profiling at 10k/100k: bounded-scan evidence, throughput printed."""
    from app.lib.data.profile import DEFAULT_MAX_SCAN_ROWS, ProfileQuality
    from app.services.data_profile.profiler import DatasetProfiler
    from app.services.session_data import session_data_manager

    profiler = DatasetProfiler()  # fresh instance: no cross-test cache pollution
    for n in (10_000, 100_000):
        feats = _grid_features(n)
        sid = f"v5-prof-{n}"
        ref = await session_data_manager.store(
            sid, {"type": "FeatureCollection", "features": feats}, prefix="geojson"
        )
        try:
            t0 = time.perf_counter()
            prof = await profiler.profile_session_ref(sid, ref, deep=True)
            dt = time.perf_counter() - t0
        finally:
            await session_data_manager.delete_ref(sid, ref)

        assert prof is not None, f"deep profile at n={n} must produce a profile"
        assert prof.category == "vector"
        assert prof.vector.row_count == n
        assert prof.vector.extent is not None and len(prof.vector.extent) == 4
        # bounded evidence: the scan cap contract, not a wall-clock claim
        if n == 10_000:
            assert prof.profile_quality is ProfileQuality.COMPLETE
            assert prof.vector.scanned_rows == n
        else:
            # 100k > 50k scan cap → deterministic stride sampling; a
            # regression that silently whole-scans reports scanned_rows ==
            # row_count and fails here.
            assert prof.profile_quality is ProfileQuality.SAMPLED
            assert 0 < prof.vector.scanned_rows <= DEFAULT_MAX_SCAN_ROWS
        _summary(
            f"vector_profile_{n}",
            seconds=round(dt, 3),
            feats_per_s=round(n / dt),
            quality=prof.profile_quality.value,
            scanned=prof.vector.scanned_rows,
        )


def test_descriptor_planning_million_scale_linear():
    """1M synthetic column-stat descriptors through the selectivity model.

    The descriptor stream is a generator of compact tuples; each is fed
    through ``estimate_predicate_selectivity`` (+ periodically
    ``estimate_group_cardinality``). Guard: t(1M)/t(250k) ∈ [0.8, 8] —
    linear expectation is 4x, the band absorbs noise, an accidental O(n²)
    planning loop lands near 16x and fails.
    """
    from app.services.data_fabric.query.predicates import predicate_from_dict
    from app.services.data_fabric.query.selectivity import (
        estimate_group_cardinality,
        estimate_predicate_selectivity,
    )
    from app.services.data_fabric.query.statistics import (
        ColumnStatistics,
        DatasetStatistics,
    )

    def descriptors(n: int):
        """Generator: compact (ndv, min, max, null_fraction) stat tuples."""
        for i in range(n):
            yield (50 + (i * 37) % 10_000, float(i % 1000), float(i % 1000 + 500), (i % 10) / 100)

    nodes = tuple(
        predicate_from_dict(p)
        for p in (
            {"op": "eq", "field": "c", "value": 7},
            {"op": "gt", "field": "c", "value": 250},
            {"op": "is_null", "field": "c"},
        )
    )

    def run(n: int) -> float:
        t0 = time.perf_counter()
        for i, (ndv, lo, hi, nf) in enumerate(descriptors(n)):
            stats = DatasetStatistics(
                dataset_fingerprint="v5-bench",
                columns=[ColumnStatistics(
                    name="c", ndv=ndv, min_value=lo, max_value=hi, null_fraction=nf,
                )],
            )
            est = estimate_predicate_selectivity(nodes[i % 3], stats)
            assert 0.0 < est.value <= 1.0
            if i == 0:
                assert est.basis == "statistics", "ndv stats must engage the model"
            if i % 16 == 0:
                assert estimate_group_cardinality(["c"], stats, 10_000) >= 1
        return time.perf_counter() - t0

    t_small = run(250_000)
    t_big = run(1_000_000)
    ratio = t_big / t_small
    assert 0.8 <= ratio <= 8.0, (
        f"descriptor planning must stay ~O(n): t(1M)/t(250k)={ratio:.2f} "
        "(~4 expected; >8 smells like an accidental O(n²) planning loop)"
    )
    _summary(
        "descriptor_planning_1m",
        t_250k_s=round(t_small, 3),
        t_1m_s=round(t_big, 3),
        ratio=round(ratio, 2),
        descriptors_per_s=round(1_000_000 / t_big),
    )


def test_wide_schema_512_columns_arrow_and_dict_lane():
    """512-column fixture: encode preserves the column set, both lanes prune."""
    pytest.importorskip("pyarrow")
    from app.services.data_fabric import arrow_ops, vector_carrier
    from app.services.data_fabric.streaming import stream_project

    rows, cols = 64, 512
    feats = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [i % 100, i % 97]},
            "properties": {f"c{j:04d}": i * cols + j for j in range(cols)},
        }
        for i in range(rows)
    ]

    table = vector_carrier.features_to_arrow(feats, crs="EPSG:4326")
    assert table.num_rows == rows
    assert len(table.column_names) == cols + 1, (
        f"encode must preserve the full 512-column schema (+geometry), got {len(table.column_names)}"
    )

    projected = arrow_ops.arrow_project(table, ["c0000", "c0511", "geometry"])
    assert projected.column_names == ["c0000", "c0511", "geometry"]
    assert projected.num_rows == rows

    # dict-lane fallback: identical pruning contract over the identical input
    dict_rows = list(stream_project(feats, ["c0000", "c0511"], batch_size=16))
    assert len(dict_rows) == rows
    assert all(set(r["properties"]) <= {"c0000", "c0511"} for r in dict_rows)
    # both lanes agree on the projected payload
    assert projected.column("c0000").to_pylist() == [r["properties"]["c0000"] for r in dict_rows]
    _summary("wide_schema_512", columns=len(table.column_names), projected=len(projected.column_names))


def test_lineage_session_chain_200_deep_bounded_traversal():
    """200-deep session lineage chain: depth caps + honest truncation + node cap."""
    from app.services.artifact_registry import ArtifactRecord
    from app.services.data_catalog.lineage_query import _MAX_VIEW_NODES, SessionLineageQuery

    depth = 200
    records = {
        f"a{i:03d}": ArtifactRecord(
            artifact_id=f"a{i:03d}",
            artifact_type="feature_collection",
            producer_tool="bench",
            inputs=[f"a{i - 1:03d}"] if i else [],
        )
        for i in range(depth)
    }
    q = SessionLineageQuery(records)

    # closures honor max_depth (never walk the full 200 chain past the cap)
    assert len(q.upstream("a199", max_depth=16)) <= 16
    assert len(q.downstream("a000", max_depth=16)) <= 16

    # full traversal of the 200-deep chain stays under the view-node cap
    full = q.view("a000", max_depth=depth + 1)
    assert len(full.nodes) == depth
    assert len(full.edges) == depth - 1
    assert full.truncated is False
    assert len(full.nodes) <= _MAX_VIEW_NODES

    # depth-bounded view declares truncation honestly
    part = q.view("a000", max_depth=3)
    assert len(part.nodes) == 4
    assert part.truncated is True
    _summary(
        "lineage_session_200",
        full_nodes=len(full.nodes),
        full_edges=len(full.edges),
        capped_nodes=len(part.nodes),
    )


def test_lineage_db_chain_200_deep_query_count_bounded(tmp_path):
    """DB-side 200-deep chain: BFS query count bounded by max_depth, not size
    (mirrors the #602 discipline in tests/unit/test_lineage_query_count.py)."""
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    import app.models.db_model  # noqa: F401 — register ORM models on Base.metadata
    import app.models.project  # noqa: F401
    from app.core.database import Base
    from app.models.db_model import Organization
    from app.models.project import Artifact, ArtifactLineage, Project
    from app.services.lineage_service import LineageService

    engine = create_engine(f"sqlite:///{tmp_path / 'v5-lineage.db'}", echo=False)

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, connection_record):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        depth = 200
        db.add(Organization(id=1, name="org", slug="org"))
        db.add(Project(id="proj_v5", name="p", org_id=1, status="active"))
        db.flush()
        for i in range(depth):
            db.add(Artifact(id=f"a{i:03d}", project_id="proj_v5", name=f"a{i}",
                            artifact_type="dataset", storage_ref="ref:x"))
        for i in range(depth):
            db.add(ArtifactLineage(
                id=f"lin_{i:03d}", artifact_id=f"a{i:03d}",
                parent_artifact_id=f"a{i - 1:03d}" if i else None,
                producing_tool="bench", workflow_run_id=None,
            ))
        db.commit()

        counter = {"n": 0}

        def before_execute(*a, **kw):
            counter["n"] += 1

        event.listen(engine, "before_execute", before_execute)
        try:
            graph = LineageService.get_lineage_graph(db, "a000", max_depth=8, project_id="proj_v5")
        finally:
            event.remove(engine, "before_execute", before_execute)

        assert len(graph["consumers"]) == 8, "max_depth must cap the downstream walk"
        n_queries = counter["n"]
        # 1 upstream level + 8 downstream levels + 1 tenant check (+slack);
        # a per-node cascade would fire ~200+ queries on this chain.
        assert n_queries <= 12, (
            f"#602 regression: 200-deep chain with max_depth=8 fired {n_queries} queries "
            "(expected ≤12, one per BFS level + tenant check)"
        )
        _summary("lineage_db_200", max_depth=8, consumers=len(graph["consumers"]), queries=n_queries)
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


async def test_catalog_search_2500_entries_bounded_scan(monkeypatch):
    """2500 synthetic entries: session source stops at the 2000 catalog cap,
    filter axes return deterministic subsets, pages are bounded + flagged."""
    from app.services.artifact_registry import ArtifactRecord
    from app.services.data_catalog.catalog import (
        _MAX_ENTRIES_SCANNED,
        CatalogFilter,
        DataCatalog,
    )

    total = _MAX_ENTRIES_SCANNED + 500  # 500 entries sit beyond the scan cap
    records = [
        ArtifactRecord(
            artifact_id=f"art_{i:05d}",
            artifact_type="feature_collection",
            producer_tool="bench",
            metadata={
                "display_name": f"layer_{i:05d}" + (" needle" if i % 100 == 0 else ""),
                **({"field_schema": {"temp": {"type": "double"}, "id": {"type": "int64"}}}
                   if i % 2 == 0 else {}),
            },
            bbox=[float(i), float(i), float(i) + 1.0, float(i) + 1.0],
            feature_count=i,
            created_at=1_000_000.0 + i,
            updated_at=1_000_000.0 + i,
        )
        for i in range(total)
    ]
    catalog = DataCatalog()

    async def _loader(session_id: str):
        return records

    monkeypatch.setattr(catalog, "_session_records_loader", _loader)

    async def search(flt: CatalogFilter, limit: int = 50):
        t0 = time.perf_counter()
        res = await catalog.search(session_id="v5-catalog", filter_=flt, limit=limit)
        return res, time.perf_counter() - t0

    all_res, dt = await search(CatalogFilter(scope="session"))
    # bounded scan: the source never scans past the catalog cap
    assert all_res.sources_queried[0].count == _MAX_ENTRIES_SCANNED, (
        "session source must stop at the 2000-entry catalog cap"
    )
    assert all_res.total_matched == _MAX_ENTRIES_SCANNED < total
    assert len(all_res.entries) == 50
    assert all_res.truncated is True

    kw, _ = await search(CatalogFilter(scope="session", keyword="needle"))
    assert kw.total_matched == 20  # i % 100 == 0 within the capped scan window
    assert kw.truncated is False

    fld, _ = await search(CatalogFilter(scope="session", field="temp"))
    assert fld.total_matched == 1000  # every second record declares the field
    assert fld.truncated is True

    # bbox: boundary-touching extents match (i=1997..1999, all within the
    # capped scan); the i=2000 toucher sits exactly past the scan cap and
    # cannot match — the bounded scan (not overlap math) decides the result
    bbx, _ = await search(CatalogFilter(scope="session", bbox=[1998.0, 1998.0, 2000.0, 2000.0]))
    assert bbx.total_matched == 3

    # newest-first ordering is deterministic over the capped scan
    assert all_res.entries[0].entry_id == f"art_{_MAX_ENTRIES_SCANNED - 1:05d}"
    _summary(
        "catalog_search_2500",
        seconds=round(dt, 3),
        scanned=all_res.sources_queried[0].count,
        keyword_matches=kw.total_matched,
        field_matches=fld.total_matched,
    )


def test_revisions_500_head_list_bounded_queries(tmp_path):
    """500 revisions for one artifact: head/list cost one query each and the
    history page is capped (bounded result contract)."""
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    import app.models.db_model  # noqa: F401 — register ORM models on Base.metadata
    import app.models.project  # noqa: F401
    from app.core.database import Base
    from app.models.db_model import Organization
    from app.models.project import Artifact, ArtifactRevision, Project
    from app.services.artifact_revisions import head_revision, list_revisions

    engine = create_engine(f"sqlite:///{tmp_path / 'v5-revisions.db'}", echo=False)

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, connection_record):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        db.add(Organization(id=1, name="org", slug="org"))
        db.add(Project(id="proj_v5", name="p", org_id=1, status="active"))
        db.flush()
        db.add(Artifact(id="art_v5", project_id="proj_v5", name="out",
                        artifact_type="dataset", storage_ref="ref:x"))
        db.flush()
        db.add_all(
            ArtifactRevision(
                id=str(uuid.uuid4()),
                artifact_id="art_v5",
                revision_no=i,
                content_sha256=f"{i:064x}",
                content_location=f"shard0/{i:064x}.json",
                content_type="json",
                byte_size=8,
            )
            for i in range(1, 501)
        )
        db.commit()

        counter = {"n": 0}

        def before_execute(*a, **kw):
            counter["n"] += 1

        event.listen(engine, "before_execute", before_execute)
        try:
            head = head_revision(db, "art_v5")
            n_head = counter["n"]
            counter["n"] = 0
            page = list_revisions(db, "art_v5", limit=20)
            n_list = counter["n"]
        finally:
            event.remove(engine, "before_execute", before_execute)

        assert head is not None and head.revision_no == 500
        assert n_head == 1, f"head_revision must be a single query (got {n_head})"
        assert [r.revision_no for r in page] == list(range(500, 480, -1))
        assert n_list == 1, f"list_revisions must be a single query (got {n_list})"

        # bounded result: the page cap holds even for a huge caller limit
        capped = list_revisions(db, "art_v5", limit=10_000)
        assert len(capped) == 200
        _summary("revisions_500", head_query=n_head, list_query=n_list, page_cap=len(capped))
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


# ---------------------------------------------------------------------------
# 2. streaming guards
# ---------------------------------------------------------------------------


def test_arrow_stream_pipeline_100k_end_to_end():
    """Arrow batch pipeline (100k features) filter→project→aggregate: exact
    deterministic counts, dict-lane parity; throughput printed only."""
    pytest.importorskip("pyarrow")
    from app.services.data_fabric import arrow_ops, vector_carrier
    from app.services.data_fabric.streaming import (
        stream_aggregate,
        stream_filter,
        stream_project,
    )

    n = 100_000
    feats = _grid_features(n)

    t0 = time.perf_counter()
    batches = list(vector_carrier.iter_features_to_arrow_batches(
        _chunked(feats, 4096), chunk_size=4096, crs="EPSG:4326"))
    assert len(batches) == math.ceil(n / 4096)
    filtered = [arrow_ops.arrow_filter(b, {"op": "ge", "field": "v", "value": 5}) for b in batches]
    total = sum(b.num_rows for b in filtered)
    agg = arrow_ops.arrow_aggregate_batches(filtered, [{"func": "count"}], group_by=["grp"])
    dt = time.perf_counter() - t0

    assert total == n // 2, f"v≥5 on cycling 0..9 keeps exactly n/2 rows (got {total})"
    assert len(agg) == 7
    assert sum(int(r["count"]) for r in agg) == n // 2

    # dict-lane parity over the identical input (generator-fed, no list reuse)
    dict_rows = list(stream_aggregate(
        stream_project(
            stream_filter(feats, {"op": "ge", "field": "v", "value": 5}, batch_size=4096),
            ["v", "grp"], batch_size=4096,
        ),
        [{"func": "count"}], ["grp"], batch_size=4096,
    ))
    assert len(dict_rows) == 7
    arrow_by_grp = {int(r["grp"]): int(r["count"]) for r in agg}
    dict_by_grp = {int(r["grp"]): int(r["count"]) for r in dict_rows}
    assert arrow_by_grp == dict_by_grp, "both lanes must agree group-for-group"
    _summary(
        "arrow_stream_100k",
        seconds=round(dt, 3),
        feats_per_s=round(n / dt),
        survivors=total,
        groups=len(agg),
    )


def test_arrow_stream_cancellation_at_batch_boundary():
    """Cancellation lands on a batch boundary: exactly k whole batches were
    consumed (≤ total), each filtered atomically — never a partial batch."""
    pytest.importorskip("pyarrow")
    from app.lib.cancellation import CancellationToken, OperationCancelled
    from app.services.data_fabric import arrow_ops, vector_carrier
    from app.services.data_fabric.streaming import batch_checkpoint_hook, iter_batches

    n, batch_size, k = 50_000, 4096, 4
    feats = _grid_features(n)
    token = CancellationToken("v5-arrow-cancel")
    hook = batch_checkpoint_hook(token)

    def on_batch(count: int) -> None:
        hook(count)  # raises OperationCancelled once the token is cancelled
        if count == k:
            token.cancel("v5 boundary cancel")

    consumed = 0
    rows_kept = 0
    total_batches = math.ceil(n / batch_size)
    with pytest.raises(OperationCancelled):
        for batch in iter_batches(feats, batch_size, on_batch=on_batch):
            for arrow_batch in vector_carrier.iter_features_to_arrow_batches(
                [batch], chunk_size=batch_size, crs="EPSG:4326"
            ):
                rows_kept += arrow_ops.arrow_filter(
                    arrow_batch, {"op": "ge", "field": "v", "value": 5}
                ).num_rows
            consumed += 1

    assert consumed == k, f"stream must stop at the k-th batch boundary (consumed {consumed})"
    assert consumed < total_batches
    # every consumed batch was atomic: the filter applied to whole batches only
    # (expected survivors over global indices [0, consumed*batch_size))
    consumed_n = consumed * batch_size
    expected_kept = (consumed_n // 10) * 5 + sum(1 for r in range(consumed_n % 10) if r >= 5)
    assert rows_kept == expected_kept
    _summary("arrow_cancel_boundary", consumed_batches=consumed, total_batches=total_batches)


# ---------------------------------------------------------------------------
# 3. compute guards
# ---------------------------------------------------------------------------


def _kind_features(n: int) -> list[dict]:
    return [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [116.0 + i * 0.01, 39.0]},
            "properties": {"v": i, "kind": "a" if i % 2 == 0 else "b", "grp": i % 2},
        }
        for i in range(n)
    ]


def _spine_wide_plan(n_spine: int):
    """Spine of n_spine FILTER nodes + n_spine AGGREGATE fan-out children
    (linear + wide; tiny payloads, in-process fake-free default ops)."""
    from app.services.geocompute import ExecutionNode, ExecutionPlan, NodeCategory, ResourceBudget

    feats = _kind_features(8)
    nodes = []
    prev = None
    for i in range(n_spine):
        nid = f"spine_{i:03d}"
        nodes.append(ExecutionNode(
            node_id=nid, category=NodeCategory.FILTER, inputs=[prev] if prev else [],
            parameters={
                "predicate": {"op": "eq", "field": "kind", "value": "a"},
                "features": feats,
            },
        ))
        prev = nid
    for i in range(n_spine):
        nodes.append(ExecutionNode(
            node_id=f"agg_{i:03d}", category=NodeCategory.AGGREGATE,
            inputs=[f"spine_{i:03d}"],
            parameters={"aggregates": [{"func": "count"}], "group_by": ["grp"]},
        ))
    return ExecutionPlan(
        plan_id=f"v5-dag-{n_spine}", nodes=nodes,
        # the default plan budget caps nodes at 64 — the 100-node guard needs
        # an explicit (still bounded) node budget
        budget=ResourceBudget(max_nodes=n_spine * 2 + 8),
    )


def test_dag_50_and_100_node_completion_and_reuse():
    """50/100-node spine+wide DAGs complete deterministically; the second
    identical run hits the reuse store for every node (reuse flag, not time)."""
    from app.services.geocompute import GeoExecutionEngine

    for n_spine, label in ((25, "50"), (50, "100")):
        plan = _spine_wide_plan(n_spine)
        engine = GeoExecutionEngine(retain_outputs=True)

        t0 = time.perf_counter()
        run = engine.execute_plan(plan)
        dt = time.perf_counter() - t0

        assert run.status.value == "completed"
        assert len(run.evidence) == n_spine * 2
        for i in range(n_spine):
            # deterministic emission: 8 rows, kind=="a" keeps 4; single group
            assert run.evidence[f"spine_{i:03d}"].rows_emitted == 4
            assert run.evidence[f"agg_{i:03d}"].rows_emitted == 1

        run2 = engine.execute_plan(plan)
        assert run2.status.value == "completed"
        reused = sum(1 for ev in run2.evidence.values() if ev.status == "reused")
        assert reused == n_spine * 2, (
            f"second identical run must serve every node from the reuse store "
            f"(got {reused}/{n_spine * 2})"
        )
        _summary(f"dag_{label}_nodes", first_run_s=round(dt, 3), reused=reused)


def test_worker_loss_stale_job_typed_failure(tmp_path, monkeypatch):
    """Worker killed mid-node: heartbeat ages out, the real sweep_stale flips
    the job stale, await_node_job surfaces the typed worker-loss failure."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.db_model import Base
    from app.services.geocompute import durable as durable_mod
    from app.services.geocompute.durable import await_node_job
    from app.services.geocompute.errors import NodeExecutionError
    from app.services.jobs import DurableJobStore, JobKind, JobStatus, coerce_status

    engine = create_engine(f"sqlite:///{tmp_path / 'v5-jobs.db'}")
    Base.metadata.create_all(engine)
    Sess = sessionmaker(bind=engine)
    try:
        with Sess() as db:
            job = DurableJobStore.create_sync(
                db,
                task_type="geocompute_node",
                kind=JobKind.analysis,
                owner_id="user-v5",
                session_id="sess-v5",
                parameters={},
                dispatch_spec={"task": "t", "args": [], "kwargs": {}},
            )
            assert DurableJobStore.transition_sync(db, job.id, JobStatus.queued,
                                                   expected=[JobStatus.pending])
            assert DurableJobStore.mark_running_sync(db, job.id)
            db.commit()
            job_id = int(job.id)

        # worker dies: heartbeat ages beyond the stale window (sweep semantics)
        with Sess() as db:
            row = DurableJobStore.get_sync(db, job_id)
            row.heartbeat_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=900)
            db.commit()

        async def _sweep() -> int:
            from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

            eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'v5-jobs.db'}")
            SF = async_sessionmaker(bind=eng, expire_on_commit=False)
            try:
                async with SF() as s:
                    swept = await DurableJobStore.sweep_stale(s, stale_after_s=300)
                    await s.commit()
                return swept
            finally:
                await eng.dispose()

        import asyncio

        assert asyncio.run(_sweep()) == 1
        with Sess() as db:
            assert coerce_status(DurableJobStore.get_sync(db, job_id).status) is JobStatus.stale

        monkeypatch.setattr(durable_mod, "session_factory", Sess)
        with pytest.raises(NodeExecutionError) as ei:
            await_node_job(job_id, session_id="sess-v5", deadline_ts=None, cancel_token=None)
        assert ei.value.code == "NODE_FAILED"
        assert ei.value.failure_class.value == "worker_loss"
        assert ei.value.retry_safe is True
        assert "stale" in str(ei.value)
        _summary("worker_loss", job_id=job_id, failure_class=ei.value.failure_class.value)
    finally:
        engine.dispose()


def test_governor_denies_plan_over_row_budget():
    """Resource denial: a plan whose row estimate exceeds the governor scope
    is denied typed with suggestions, no leaked scope, engine still usable."""
    from app.services.geocompute import (
        BudgetExceededError,
        ExecutionNode,
        ExecutionPlan,
        GeoExecutionEngine,
        NodeCategory,
        ResourceEstimate,
    )
    from app.services.geocompute.budgets import BudgetLimits, ResourceGovernor

    gov = ResourceGovernor(global_limits=BudgetLimits(max_rows=100))

    def plan_with_estimate(rows: int, plan_id: str) -> ExecutionPlan:
        return ExecutionPlan(plan_id=plan_id, nodes=[ExecutionNode(
            node_id="n", category=NodeCategory.FILTER, estimate=ResourceEstimate(rows=rows),
            parameters={
                "predicate": {"op": "eq", "field": "kind", "value": "a"},
                "features": _kind_features(4),
            },
        )])

    with pytest.raises(BudgetExceededError) as ei:
        GeoExecutionEngine(max_workers=1).execute_plan(
            plan_with_estimate(500, "v5-denial"), governor=gov)
    assert "rows" in str(ei.value)
    assert ei.value.details.get("suggestions"), "denial must carry actionable suggestions"
    # denied admission must not leak the execution scope into the governor tree
    assert gov._find("global:root").children == []

    # the governor stays usable for a within-budget plan
    run = GeoExecutionEngine(max_workers=1).execute_plan(
        plan_with_estimate(10, "v5-admitted"), governor=gov)
    assert run.status.value == "completed"
    assert run.evidence["n"].rows_emitted == 2
    _summary("governor_denial", suggestions=len(ei.value.details["suggestions"]))


# ---------------------------------------------------------------------------
# 4. federation guards
# ---------------------------------------------------------------------------


def _chain_request(n: int):
    from app.services.data_fabric.query.federation import (
        ChainJoin,
        ChainSource,
        ChainSourceStats,
        FederatedChainRequest,
    )

    est = [100, 10_000, 1_000, 100_000, 500]
    keys = ["a", "b", "c", "d", "e"]
    sources = [
        ChainSource(source_id=f"s{i}", dataset_id=f"d{i}", estimated_rows=est[i])
        for i in range(n)
    ]
    joins = [
        ChainJoin(kind="attribute_join", join_field_left=keys[i], join_field_right=keys[i + 1],
                  left_source_id=f"s{i}", right_source_id=f"s{i + 1}")
        for i in range(n - 1)
    ]
    hints = {
        f"s{i}": ChainSourceStats(estimated_rows=est[i], column_ndv={keys[i]: 10})
        for i in range(n)
    }
    return FederatedChainRequest(
        sources=sources, joins=joins, limit=1000,
        order_strategy="cost_stats", stats_hints=hints,
    )


def test_federation_chain_planning_2_3_4_sources_bounded():
    """2/3/4-source chains: bounded order enumeration (≤24 candidates, never
    exponential), deterministic cost order, minimal derived projections."""
    from app.services.data_fabric.query.federation import (
        MAX_FEDERATED_SOURCES,
        MAX_ORDER_CANDIDATES,
        FederatedQueryError,
        plan_federated_chain,
    )

    for n in (2, 3, 4):
        plans = plan_federated_chain(_chain_request(n))
        assert len(plans) == n - 1
        warning = plans[0].warnings[0]
        assert "bounded enumeration" in warning, warning
        candidates = int(warning.split("(")[1].split("candidates")[0])
        assert 1 <= candidates <= MAX_ORDER_CANDIDATES <= 24, (
            f"order enumeration must stay structurally bounded (got {candidates})"
        )
        # deterministic cost order: the linear chain keeps its declared order
        order = [plans[0].left["source_id"]] + [p.right["source_id"] for p in plans]
        assert order == [f"s{i}" for i in range(n)]
        # projection derivation: exactly the minimal join key per source
        derived = {plans[0].left["source_id"]: plans[0].left["fields"]}
        derived.update({p.right["source_id"]: p.right["fields"] for p in plans})
        keys = ["a", "b", "c", "d"]
        for i in range(n):
            assert derived[f"s{i}"] == [keys[i]], (
                f"source s{i} must project only its minimal join key (got {derived[f's{i}']})"
            )
        _summary(f"federation_chain_{n}src", candidates=candidates,
                 derived={k: v for k, v in derived.items()})

    # the source bound is a typed planning failure (no silent 5th source)
    with pytest.raises(FederatedQueryError) as ei:
        plan_federated_chain(_chain_request(MAX_FEDERATED_SOURCES + 1))
    assert "at most" in str(ei.value)


def test_semi_join_reduction_fires_on_cardinality():
    """Semi-join reduction: right side collapses to the left key set when
    cardinality says so; honest identity give-up beyond the key-set cap."""
    from app.services.data_fabric.query.federation import (
        SEMI_JOIN_MAX_KEYS,
        ChainJoin,
        _semi_join_reduce_right,
    )

    join = ChainJoin(kind="attribute_join", join_field_left="k", join_field_right="k")
    left = [{"properties": {"k": i % 50}} for i in range(500)]
    right = [{"properties": {"k": j % 100}} for j in range(5000)]
    reduced, original = _semi_join_reduce_right(left, right, join)
    assert original == 5000
    assert reduced is not right, "reduction must fire when keys are a strict subset"
    assert len(reduced) == 2500  # keys 0..49 × 50 occurrences each
    left_keys = {row["properties"]["k"] for row in left}
    assert all(row["properties"]["k"] in left_keys for row in reduced)

    # key set beyond the cap → honest give-up (identity, no semantic risk)
    wide_left = [{"properties": {"k": i}} for i in range(SEMI_JOIN_MAX_KEYS + 1)]
    kept, _original = _semi_join_reduce_right(wide_left, right, join)
    assert kept is right
    _summary("semi_join", reduced=len(reduced), original=original, cap=SEMI_JOIN_MAX_KEYS)


# ---------------------------------------------------------------------------
# 5. raster window guards (rasterio-guarded)
# ---------------------------------------------------------------------------


def _write_raster_2048(path) -> str:
    """Synthetic tiled 2048² uint8 raster (fixture build bounded by rows)."""
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    rng = np.random.default_rng(_SEED)
    size, chunk = 2048, 512
    with rasterio.open(
        path, "w", driver="GTiff", width=size, height=size, count=1,
        dtype="uint8", crs="EPSG:4326", transform=from_origin(0, size, 1, 1),
        nodata=None, tiled=True, blockxsize=512, blockysize=512,
    ) as dst:
        for row0 in range(0, size, chunk):
            h = min(chunk, size - row0)
            dst.write(
                rng.integers(1, 200, size=(h, size), dtype=np.uint8), 1,
                window=rasterio.windows.Window(0, row0, size, h),
            )
    return str(path)


def test_raster_window_partition_and_cache_resume(tmp_path, monkeypatch):
    """2048² raster: descriptors partition the grid exactly; the chunk cache
    serves a second identical run with ZERO fn invocations."""
    pytest.importorskip("rasterio")
    from app.lib.artifact_cache import clear_chunk_cache
    from app.lib.geo_raster import AlgorithmProfile, RasterReader, execute_windowed
    from app.lib.geo_raster.chunk import ChunkCacheBackend, iter_chunk_descriptors

    path = _write_raster_2048(tmp_path / "v5_raster.tif")

    with RasterReader.open(path) as reader:
        descs = list(iter_chunk_descriptors(reader, window_size=(512, 512)))
        assert len(descs) == 16
        # exact partition: same total area, disjoint origins, all in-bounds
        assert sum(d.window[2] * d.window[3] for d in descs) == 2048 * 2048
        origins = [(d.window[0], d.window[1]) for d in descs]
        assert len(set(origins)) == 16
        # deterministic chunk identity: rebuilding mints identical ids
        descs2 = list(iter_chunk_descriptors(reader, window_size=(512, 512)))
        assert [d.chunk_id for d in descs] == [d.chunk_id for d in descs2]

    monkeypatch.chdir(tmp_path)  # data/artifacts/chunks lands inside tmp_path
    clear_chunk_cache()
    try:
        cache = ChunkCacheBackend(path, "v5_bench_op")
        calls = {"n": 0}

        def fn(a, core, read):
            calls["n"] += 1
            return a

        with RasterReader.open(path) as reader:
            run1 = execute_windowed(reader, AlgorithmProfile(), fn, window_size=(512, 512),
                                    chunk_cache=cache)
            assert calls["n"] == 16  # cold run computes every chunk once
            calls["n"] = 0
            run2 = execute_windowed(reader, AlgorithmProfile(), fn, window_size=(512, 512),
                                    chunk_cache=cache)
            assert calls["n"] == 0, (
                "second identical run must serve every chunk from the cache (fn invoked 0 times)"
            )
        assert run1.array.shape == run2.array.shape == (2048, 2048)
        assert (run1.array == run2.array).all(), "cached rerun must reproduce identical output"
        _summary("raster_partition_cache", chunks=16, second_run_fn_calls=calls["n"])
    finally:
        clear_chunk_cache()


def test_raster_window_cancellation_preserves_completed_prefix(tmp_path):
    """Cancellation mid-run: exactly the completed chunk prefix survives and
    the typed cancellation propagates (resume story, not silent loss)."""
    pytest.importorskip("rasterio")
    from app.lib.cancellation import OperationCancelled
    from app.lib.geo_raster import AlgorithmProfile, RasterReader, execute_windowed

    path = _write_raster_2048(tmp_path / "v5_raster_cancel.tif")
    done = {"chunks": 0, "callbacks": 0}

    def fn(a, core, read):
        if done["chunks"] >= 5:
            raise OperationCancelled("v5 raster cancel")
        done["chunks"] += 1
        return a

    def on_chunk_done(descriptor, digest, nbytes):
        done["callbacks"] += 1

    with RasterReader.open(path) as reader:
        with pytest.raises(OperationCancelled):
            execute_windowed(reader, AlgorithmProfile(), fn, window_size=(512, 512),
                             on_chunk_done=on_chunk_done)

    assert done["chunks"] == 5
    assert done["callbacks"] == 5, "completed chunks and callbacks must agree exactly"
    _summary("raster_cancel", completed_chunks=done["chunks"], total_chunks=16)
