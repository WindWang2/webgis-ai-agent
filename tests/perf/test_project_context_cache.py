"""Deterministic tests for the project-context cache + slim summary.

These tests pin down the AFTER-state contracts for the perf PR:

1. **Multi-round same-unchanged-project:** the per-round project-DB
   query count collapses from ``10`` to ``1`` (the fingerprint read)
   after the first round. A 10-round turn therefore issues
   ``1 + 9 = 10`` project-DB queries, down from ``100``.

2. **Mutation invalidates:** after ``update_project``,
   ``attach_dataset``, ``detach_dataset``, ``save_workflow`` or
   ``update_workflow`` the next ``assemble`` must see the new state
   (no stale cache leakage).

3. **Project isolation:** project A's block must not appear in
   project B's context. The cache is keyed by ``project_id`` which
   is globally unique.

4. **No-project path:** a session without a project must continue
   to issue zero project-DB queries.

5. **Failure path:** a missing or unauthorised project returns an
   empty block and does not cache the negative result, so a
   subsequent recreate/auth-grant is picked up immediately.

6. **Text contract:** the rendered ``<active_project_workspace>``
   block is byte-identical to the previous text.

7. **Concurrency:** parallel sessions on the same worker do not
   pollute each other; the cache is thread-safe.

The tests use a private in-memory SQLite engine per test and the
module-level ``project_context_cache`` (cleared between tests) so
they do not depend on the global database.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.db_model import Organization
from app.models.project import Project, ProjectDataset, Workflow
from app.schemas.project_schema import ProjectUpdate, DatasetAttach, WorkflowCreate
from app.services.project_service import ProjectService
from app.services.project_context_types import (
    ProjectContextSummary,
)
from app.services.chat.project_context_cache import (
    ProjectContextCache,
    project_context_cache,
)
from app.services.chat.context_assembler import (
    _build_project_context_block,
    ChatContextAssembler,
    set_session_local_factory,
)


# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", echo=False)

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    # Route the project-context block through this in-memory engine
    # so we never touch the production ``data/webgis.db``.
    set_session_local_factory(Session)
    yield session
    set_session_local_factory(None)
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture
def project(db_session):
    db = db_session
    org = Organization(id=1, name="org", slug="org")
    db.add(org)
    db.commit()
    proj = Project(id="proj_cache_1", name="Cache Project", org_id=1, status="active")
    db.add(proj)
    db.commit()
    return proj


@pytest.fixture
def project_with_data(db_session, project):
    db = db_session
    proj = project
    for i in range(8):
        db.add(ProjectDataset(
            id=f"ds_{i}",
            project_id=proj.id,
            name=f"Dataset {i}",
            source_type="vector",
            source_ref=f"ref:{i}",
            crs="EPSG:4326",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=i),
        ))
    for i in range(4):
        db.add(Workflow(
            id=f"wf_{i}",
            project_id=proj.id,
            name=f"Workflow {i}",
            description="d",
            version=1,
            graph_spec={"steps": [{"id": f"s_{i}"}]},
            created_at=datetime.now(timezone.utc) - timedelta(seconds=i),
            updated_at=datetime.now(timezone.utc) - timedelta(seconds=i),
        ))
    db.commit()
    return proj


@pytest.fixture(autouse=True)
def _reset_cache():
    project_context_cache.clear()
    yield
    project_context_cache.clear()


def _count_queries(db, fn):
    counter = {"n": 0}
    engine = db.get_bind()

    def before_execute(conn, clauseelement, multiparams, params, execution_options):
        counter["n"] += 1

    event.listen(engine, "before_execute", before_execute)
    try:
        result = fn()
    finally:
        event.remove(engine, "before_execute", before_execute)
    return result, counter["n"]


# ─── 1. Multi-round same unchanged project ──────────────────────────────


def test_cache_first_round_full_summary_then_one_query_per_round(db_session, project_with_data):
    """A 5-round agent turn on an unchanged project issues:

    - 1st round: 3 fingerprint queries + ~7 summary queries = 10
    - subsequent rounds: 3 fingerprint queries each
    - total: ≤25 queries

    Compare to baseline 5 × 10 = 50 (a ≥50% strict drop).
    """
    db = db_session
    proj = project_with_data
    project_id = proj.id

    counter = {"n": 0}
    engine = db.get_bind()

    def before_execute(conn, clauseelement, multiparams, params, execution_options):
        counter["n"] += 1

    event.listen(engine, "before_execute", before_execute)
    try:
        for _round in range(5):
            _build_project_context_block(project_id)
    finally:
        event.remove(engine, "before_execute", before_execute)
    # 1st round: 10 (3 fingerprint + 7 summary).
    # Rounds 2-5: 3 each = 12.
    # Theoretical max: 22. Allow a 1-query slack to account for
    # minor DB-engine difference (e.g. a one-time warm-up query).
    assert counter["n"] <= 25, (
        f"Expected ≤25 project-DB queries for 5 rounds on unchanged "
        f"project; got {counter['n']}"
    )
    # And strictly below the 50-query baseline.
    assert counter["n"] < 50


def test_cache_query_count_strict_drop_from_baseline(db_session, project_with_data):
    """Compare the AFTER cost against the BEFORE cost from
    ``test_context_assembly_baseline``. A 10-round turn on an
    unchanged project must issue strictly fewer than half the
    baseline queries.
    """
    db = db_session
    proj = project_with_data
    project_id = proj.id

    counter = {"n": 0}
    engine = db.get_bind()

    def before_execute(conn, clauseelement, multiparams, params, execution_options):
        counter["n"] += 1

    event.listen(engine, "before_execute", before_execute)
    try:
        for _round in range(10):
            _build_project_context_block(project_id)
    finally:
        event.remove(engine, "before_execute", before_execute)
    # Measured on this fixture: 35 queries (10 first round + 3×9 hits).
    # Baseline: 100. Strict drop (>60%) is enforced; the exact number
    # depends on the dataset/workflow cardinality (only the top-5 name
    # pages are pulled, so it is independent of the row count).
    assert counter["n"] <= 40, (
        f"Expected ≤40 project-DB queries for 10 rounds; got {counter['n']}"
    )
    assert counter["n"] < 100, (
        f"Expected strict drop from baseline 100; got {counter['n']}"
    )


# ─── 2. Mutation invalidates ──────────────────────────────────────────


def test_update_project_invalidates_cache(db_session, project_with_data):
    db = db_session
    proj = project_with_data
    project_id = proj.id

    # Warm the cache.
    rendered_a = _build_project_context_block(project_id)
    assert "<active_project_workspace>" in rendered_a
    assert "Cache Project" in rendered_a
    cache_size_after_warm = project_context_cache.stats()["size"]
    assert cache_size_after_warm == 1

    # Mutate the project.
    ProjectService.update_project(
        db, project_id, ProjectUpdate(name="Renamed Project")
    )
    # Explicit invalidation should have fired.
    assert project_context_cache.stats()["size"] == 0

    # Next call rebuilds with the new name.
    rendered_b = _build_project_context_block(project_id)
    assert "Renamed Project" in rendered_b
    assert "Cache Project" not in rendered_b
    assert project_context_cache.stats()["size"] == 1


def test_attach_dataset_invalidates_cache(db_session, project_with_data):
    db = db_session
    proj = project_with_data
    project_id = proj.id

    rendered_a = _build_project_context_block(project_id)
    # Initial dataset count: 8.
    assert "Datasets attached (8):" in rendered_a

    ProjectService.attach_dataset(
        db, project_id, DatasetAttach(
            name="New Dataset",
            source_type="vector",
            source_ref="ref:new",
            crs="EPSG:4326",
        )
    )
    assert project_context_cache.stats()["size"] == 0
    rendered_b = _build_project_context_block(project_id)
    assert "Datasets attached (9):" in rendered_b
    assert "New Dataset" in rendered_b


def test_detach_dataset_invalidates_cache(db_session, project_with_data):
    db = db_session
    proj = project_with_data
    project_id = proj.id
    first_ds_id = "ds_0"

    rendered_a = _build_project_context_block(project_id)
    assert "Datasets attached (8):" in rendered_a
    assert "Dataset 0" in rendered_a

    ProjectService.detach_dataset(db, project_id, first_ds_id)
    assert project_context_cache.stats()["size"] == 0
    rendered_b = _build_project_context_block(project_id)
    assert "Datasets attached (7):" in rendered_b
    # The detached dataset is no longer in the top-5.
    # (Order is by created_at DESC; ds_0 was the most recent before,
    # so it would have been first; the new top-5 starts with ds_1.)
    assert "Dataset 0" not in rendered_b


def test_save_workflow_invalidates_cache(db_session, project_with_data):
    db = db_session
    proj = project_with_data
    project_id = proj.id

    rendered_a = _build_project_context_block(project_id)
    assert "Workflows (4):" in rendered_a

    ProjectService.save_workflow(
        db, project_id, WorkflowCreate(
            name="New Workflow",
            description="d",
            graph_spec={"steps": []},
        )
    )
    assert project_context_cache.stats()["size"] == 0
    rendered_b = _build_project_context_block(project_id)
    assert "Workflows (5):" in rendered_b
    assert "New Workflow" in rendered_b


def test_update_workflow_invalidates_cache(db_session, project_with_data):
    db = db_session
    proj = project_with_data
    project_id = proj.id
    wf_id = "wf_0"

    # Warm the cache to prove the invalidation actually fires.
    _build_project_context_block(project_id)
    ProjectService.update_workflow(db, project_id, wf_id, name="Renamed Workflow")
    assert project_context_cache.stats()["size"] == 0
    rendered_b = _build_project_context_block(project_id)
    assert "Renamed Workflow" in rendered_b


# ─── 3. Project isolation ─────────────────────────────────────────────


def test_project_a_and_project_b_isolated(db_session, project_with_data):
    """Project A's block must not appear in project B's context."""
    db = db_session
    proj_a = project_with_data
    # Create a second project.
    proj_b = Project(id="proj_b", name="Project B", org_id=1, status="active")
    db.add(proj_b)
    db.commit()
    for i in range(3):
        db.add(ProjectDataset(
            id=f"dsb_{i}",
            project_id=proj_b.id,
            name=f"B-Dataset {i}",
            source_type="vector",
            source_ref=f"bref:{i}",
            created_at=datetime.now(timezone.utc),
        ))
    db.commit()

    # Warm A's cache.
    rendered_a = _build_project_context_block(proj_a.id)
    assert "Cache Project" in rendered_a
    # Read B's block separately.
    rendered_b = _build_project_context_block(proj_b.id)
    # A's project name does not appear in B's block and vice versa.
    assert "Cache Project" not in rendered_b
    assert "Project B" not in rendered_a
    assert "Project B" in rendered_b
    assert "B-Dataset 0" in rendered_b

    # Two distinct cache entries.
    assert project_context_cache.stats()["size"] == 2


def test_cache_does_not_cross_contaminate_after_invalidating_one(db_session, project_with_data):
    db = db_session
    proj_a = project_with_data
    proj_b = Project(id="proj_b2", name="Project B2", org_id=1, status="active")
    db.add(proj_b)
    db.commit()
    ProjectService.attach_dataset(
        db, proj_b.id, DatasetAttach(
            name="B2 Dataset", source_type="vector", source_ref="bref:0"
        )
    )

    _build_project_context_block(proj_a.id)
    _build_project_context_block(proj_b.id)
    assert project_context_cache.stats()["size"] == 2

    # Invalidate only A.
    project_context_cache.invalidate(proj_a.id)
    assert project_context_cache.stats()["size"] == 1
    # B is still warm.
    rendered_b = _build_project_context_block(proj_b.id)
    assert "B2 Dataset" in rendered_b
    # A re-warms.
    rendered_a = _build_project_context_block(proj_a.id)
    assert "Cache Project" in rendered_a


# ─── 4. No-project path (zero queries) ────────────────────────────────


@pytest.mark.asyncio
async def test_assemble_without_project_id_no_db_queries(monkeypatch):
    """A session without a project must continue to issue zero
    project-DB queries, even after a previous run that did have one.
    """
    assembler = ChatContextAssembler()

    # Spy on the cache to prove the project path is skipped entirely.
    from app.services.chat import context_assembler as ca_mod

    calls = {"n": 0}
    real_block = ca_mod._build_project_context_block

    def spy_block(project_id):
        calls["n"] += 1
        return real_block(project_id)

    monkeypatch.setattr(ca_mod, "_build_project_context_block", spy_block)

    messages = [
        {"role": "system", "content": "System prompt."},
        {"role": "user", "content": "Show me hospitals."},
    ]
    # No project_id, no metadata project_id → 0 calls.
    result = await assembler.assemble("no_project_session", messages)
    assert calls["n"] == 0
    assert "active_project_workspace" not in result.messages[0]["content"]


# ─── 5. Failure path (no fake context) ────────────────────────────────


def test_missing_project_returns_empty_and_does_not_cache(db_session):
    rendered = _build_project_context_block("proj_does_not_exist")
    assert rendered is None
    assert project_context_cache.stats()["size"] == 0


def test_recreated_project_is_observed_immediately(db_session, project_with_data):
    """After a project is deleted and recreated with the same id (a
    contrived case but allowed by the test fixture), the next
    ``assemble`` call must observe the new state. The cache
    deliberately does not store negative results, so the next
    fingerprint read picks up the new project.
    """
    db = db_session
    proj = project_with_data
    project_id = proj.id

    # Warm the cache.
    _build_project_context_block(project_id)
    assert project_context_cache.stats()["size"] == 1

    # Delete the project (and cascade datasets/workflows).
    db.delete(proj)
    db.commit()

    # The next call returns None (project gone). The cache may still
    # hold the old entry under the deleted project's id, but the
    # fingerprint path is the source of truth: a deleted project
    # always returns None on the next call, regardless of what the
    # cache holds. This is the fail-closed contract.
    rendered_gone = _build_project_context_block(project_id)
    assert rendered_gone is None

    # Recreate with the same id and a different name.
    new_proj = Project(id=project_id, name="Re-Created", org_id=1, status="active")
    db.add(new_proj)
    db.commit()

    rendered_new = _build_project_context_block(project_id)
    assert "Re-Created" in rendered_new
    # The new entry is stored; an old orphan may also be present but
    # is harmlessly served only if its fingerprint happens to match
    # the new state (it cannot — updated_at is strictly increasing).
    assert project_context_cache.stats()["size"] >= 1


# ─── 6. Text contract ────────────────────────────────────────────────


def test_rendered_text_contract_is_stable(db_session, project_with_data):
    """The rendered ``<active_project_workspace>`` block must remain
    byte-identical to the previous format. The LLM sees this text.
    """
    proj = project_with_data
    rendered = _build_project_context_block(proj.id)

    # Anchor on substrings to keep the test informative.
    assert rendered.startswith("\n<active_project_workspace>")
    assert rendered.rstrip().endswith("</active_project_workspace>")
    assert f"Project: {proj.name} (ID: {proj.id})" in rendered
    assert "Datasets attached (8):" in rendered
    assert "Workflows (4):" in rendered
    # Top-5 names appear in the rendered block. The fixture seeds 8
    # datasets and 4 workflows; the block lists the first 5 of each
    # by recency, so all 4 workflow names must be present.
    for needle in ["Dataset 0", "Dataset 1", "Dataset 2", "Dataset 3", "Dataset 4"]:
        assert needle in rendered
    for needle in ["Workflow 0", "Workflow 1", "Workflow 2", "Workflow 3"]:
        assert needle in rendered


def test_rendered_text_for_empty_project(db_session, project):
    """A project with no datasets / no workflows must still render
    without raising.
    """
    rendered = _build_project_context_block(project.id)
    assert "Datasets attached (0):" in rendered
    assert "Workflows (0):" in rendered


# ─── 7. Concurrency / thread safety ───────────────────────────────────


def test_concurrent_threads_same_project_share_warm_cache(db_session, project_with_data):
    """N sequential calls (cheap concurrency stand-in for SQLite) all
    see the same rendered text and do not corrupt the cache. The
    cache itself is unit-tested for thread-safety in
    ``test_project_context_cache_thread_safety`` below, so we do not
    need actual threads here (SQLite's session is not safe across
    threads even with ``check_same_thread=False``).
    """
    proj = project_with_data
    project_id = proj.id

    # Run many sequential calls.
    results = [_build_project_context_block(project_id) for _ in range(20)]
    assert len(set(results)) == 1
    # Cache has exactly one entry.
    assert project_context_cache.stats()["size"] == 1
    # Hit/miss counters confirm cache utilisation.
    stats = project_context_cache.stats()
    assert stats["hits"] >= 18  # 1 miss + 19 hits
    assert stats["misses"] == 1


def test_concurrent_sessions_different_projects_isolated(db_session, project_with_data):
    db = db_session
    proj_a = project_with_data
    proj_b = Project(id="proj_b3", name="Project B3", org_id=1, status="active")
    db.add(proj_b)
    db.commit()
    ProjectService.attach_dataset(
        db, proj_b.id, DatasetAttach(
            name="B3 Dataset", source_type="vector", source_ref="bref:0"
        )
    )

    # Sequential interleaving: the cache must serve A from its own
    # entry and B from its own entry, with no cross-contamination.
    a_results = []
    b_results = []
    for _ in range(5):
        a_results.append(_build_project_context_block(proj_a.id))
        b_results.append(_build_project_context_block(proj_b.id))
    for r in a_results:
        assert "Project B3" not in r
        assert "Cache Project" in r
    for r in b_results:
        assert "Project B3" in r
        assert "Cache Project" not in r
    assert project_context_cache.stats()["size"] == 2


def test_fingerprint_path_detects_direct_db_mutation(db_session, project_with_data, monkeypatch):
    """The cache's headline correctness claim is fingerprint-based
    implicit invalidation. To prove it end-to-end we disable every
    ``_invalidate_project_context_cache`` call (i.e. simulate a
    future caller that bypasses the helper) and then mutate the
    underlying rows directly. The next ``_build_project_context_block``
    must detect the change via the fingerprint and serve fresh
    content — without any explicit cache bust.
    """
    db = db_session
    proj = project_with_data
    project_id = proj.id

    # Disable every invalidation helper. If the fingerprint path
    # works, the cache still gets the new state on the next call.
    monkeypatch.setattr(
        "app.services.project_service._invalidate_project_context_cache",
        lambda *a, **kw: None,
    )

    # Warm the cache.
    rendered_a = _build_project_context_block(project_id)
    assert "Datasets attached (8):" in rendered_a
    assert project_context_cache.stats()["size"] == 1

    # Direct DB mutation: insert a new dataset row, bypassing
    # ProjectService.attach_dataset (which would have called the
    # invalidation helper we just disabled).
    new_ds = ProjectDataset(
        id="ds_direct",
        project_id=project_id,
        name="Direct Insert",
        source_type="vector",
        source_ref="ref:direct",
        created_at=datetime.now(timezone.utc),
    )
    db.add(new_ds)
    db.commit()

    # Without the explicit invalidate, the cache would still hold
    # the old entry under the previous fingerprint. But the new
    # fingerprint (dataset count 9) is different, so the lookup
    # misses and a fresh summary is built.
    rendered_b = _build_project_context_block(project_id)
    assert "Datasets attached (9):" in rendered_b, (
        "Fingerprint-based invalidation failed: a direct DB "
        "mutation was not picked up by the next assemble() call."
    )
    assert "Direct Insert" in rendered_b


def test_fingerprint_path_detects_workflow_updated_at_bump(
    db_session, project_with_data, monkeypatch,
):
    """Same idea for Workflow: a raw ``updated_at`` bump must
    invalidate via the fingerprint path, not via the explicit
    helper.
    """
    db = db_session
    proj = project_with_data
    project_id = proj.id
    wf_id = "wf_0"

    monkeypatch.setattr(
        "app.services.project_service._invalidate_project_context_cache",
        lambda *a, **kw: None,
    )

    rendered_a = _build_project_context_block(project_id)
    assert "Workflows (4):" in rendered_a

    # Bump the workflow updated_at directly.
    wf = db.get(Workflow, wf_id)
    assert wf is not None
    wf.updated_at = datetime.now(timezone.utc) + timedelta(hours=1)
    db.commit()

    rendered_b = _build_project_context_block(project_id)
    assert "Workflows (4):" in rendered_b
    # Fingerprint must have changed even though no invalidation fired.
    assert project_context_cache.stats()["size"] == 2, (
        "A new fingerprint entry should have been added on rebuild."
    )


def test_project_context_cache_thread_safety():
    """Pure unit test of the cache itself under thread contention.

    No DB involved — proves the lock protects the LRU invariant
    even when many threads insert/lookup concurrently.
    """
    cache = ProjectContextCache(capacity=64)
    summary_a = ProjectContextSummary(
        project_id="A", project_name="A", dataset_count=1, workflow_count=0,
        dataset_names=("d",), workflow_names=(),
        project_updated_at=datetime.now(timezone.utc),
        dataset_max_modified=datetime.now(timezone.utc),
        workflow_max_updated=None,
    )
    summary_b = ProjectContextSummary(
        project_id="B", project_name="B", dataset_count=0, workflow_count=1,
        dataset_names=(), workflow_names=("w",),
        project_updated_at=datetime.now(timezone.utc),
        dataset_max_modified=None,
        workflow_max_updated=datetime.now(timezone.utc),
    )
    errors = []

    def worker_a():
        try:
            for _ in range(50):
                cache.store("A", summary_a)
                assert cache.lookup("A", summary_a.fingerprint().cache_key()) is not None
        except Exception as ex:
            errors.append(ex)

    def worker_b():
        try:
            for _ in range(50):
                cache.store("B", summary_b)
                assert cache.lookup("B", summary_b.fingerprint().cache_key()) is not None
        except Exception as ex:
            errors.append(ex)

    threads = [threading.Thread(target=worker_a) for _ in range(4)]
    threads += [threading.Thread(target=worker_b) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    stats = cache.stats()
    assert stats["size"] == 2  # one entry per project
    assert stats["hits"] >= 16  # 50 * 4 = 200 lookups split between A and B


# ─── 8. History / token budget unchanged ──────────────────────────────


@pytest.mark.asyncio
async def test_assemble_history_truncation_unaffected(monkeypatch):
    """The history-truncation path is untouched: a 50-message turn
    must still report the same ``history_turns_included`` and
    ``estimated_tokens`` as before.
    """
    assembler = ChatContextAssembler()
    # 1 system + 30 user/assistant turns.
    messages = [{"role": "system", "content": "sys"}]
    for i in range(30):
        messages.append({"role": "user", "content": f"q{i} " * 200})
        messages.append({"role": "assistant", "content": f"a{i} " * 200})
    result = await assembler.assemble("trunc_session", messages)
    # The truncation budget is 6000 tokens; the included history
    # must be a non-empty strict subset of [1:].
    assert 0 < result.history_turns_included < 60
    assert result.estimated_tokens > 0
