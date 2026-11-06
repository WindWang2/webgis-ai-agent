"""Actual thread, task-origin, worker-log and fairness boundaries for #1862."""
import json
import multiprocessing
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_promotion_database_steps_all_leave_event_loop(monkeypatch):
    from app.services import project_artifact_promotion as promotion
    main = threading.get_ident()
    calls = []
    artifact = SimpleNamespace(id="a", project_id="p", storage_ref="ref:data", content_fingerprint="",
                               metadata_json={})

    class DB:
        def execute(self, statement):
            calls.append(("execute", threading.get_ident()))
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [artifact]),
                                   scalar_one_or_none=lambda: None)

        def commit(self):
            calls.append(("commit", threading.get_ident()))

    def revision(*args, **kwargs):
        calls.append(("revision", threading.get_ident()))
        return SimpleNamespace(revision_no=1), True

    def quota(*args, **kwargs):
        calls.append(("quota", threading.get_ident()))
        return SimpleNamespace(allowed=True)

    monkeypatch.setattr("app.services.session_data.session_data_manager.get", AsyncMock(return_value={"rows": [1]}))
    monkeypatch.setattr("app.services.artifact_revisions.record_revision", revision)
    monkeypatch.setattr("app.services.data_lifecycle.quota.check_quota", quota)
    monkeypatch.setattr("app.services.durable_blob_store.get_filesystem_blob_store",
                        lambda: SimpleNamespace(exists=lambda key: False))
    monkeypatch.setattr(promotion, "materialize_blob", lambda *args: "content.json")
    run = SimpleNamespace(id="run", project_id="p", run_manifest={"artifacts": [{"id": "a"}]})
    result = await promotion.promote_run_artifacts(DB(), run, session_id="s", project_id="p")
    assert result[0]["status"] == "promoted"
    assert {name for name, _ in calls} == {"execute", "commit", "revision", "quota"}
    assert all(thread != main for _, thread in calls)


def test_celery_worker_restores_origin_and_clears_it_after_execution(monkeypatch):
    from app.services.jobs.context import JobOrigin, use_origin, current_origin
    from app.services.jobs.celery_origin import origin_headers
    from app.services.spatial_tasks import run_sync_tool_isolated
    with use_origin(JobOrigin(session_id="session", owner_id="owner", owner_token="secret",
                              org_id=7, project_id="project", turn_id="turn")):
        headers = origin_headers()
    assert "secret" not in json.dumps(headers)
    observed = []
    monkeypatch.setattr("app.services.spatial_tasks._claim_tool_delivery", lambda task: True)
    monkeypatch.setattr("app.services.spatial_tasks._worker_sync_tool", lambda name:
                        lambda **kwargs: observed.append(current_origin()) or {"success": True})
    run_sync_tool_isolated.push_request(id="task", headers=headers)
    try:
        assert run_sync_tool_isolated.run("tool", {}) == {"success": True}
    finally:
        run_sync_tool_isolated.pop_request()
    assert observed[0].session_id == "session"
    assert observed[0].owner_id == "owner" and observed[0].org_id == 7
    assert observed[0].project_id == "project" and observed[0].turn_id == "turn"
    assert current_origin() is None


@pytest.mark.asyncio
async def test_viewport_session_cannot_spend_another_session_budget_and_precision_is_reduced(monkeypatch):
    from app.services import viewport_naming as naming
    naming.clear_cache()
    calls = []

    async def fetch(lng, lat):
        calls.append((lng, lat))
        return "region"

    monkeypatch.setattr(naming, "_fetch_nominatim", fetch)
    monkeypatch.setattr(naming, "_SESSION_RATE_MAX_PER_MINUTE", 2)
    try:
        for index in range(4):
            await naming._populate(116.12345 + index, 39.12345, "busy")
        await naming._populate(126.12345, 39.12345, "quiet")
        assert len(calls) == 3
        assert calls[-1] == (126.1, 39.1)
        assert len(naming._session_rate_windows["busy"]) == 2
        assert len(naming._session_rate_windows["quiet"]) == 1
    finally:
        naming.clear_cache()


def _metrics_writer(path, worker, barrier):
    from app.services import tool_metrics
    tool_metrics.LOG_PATH = path
    tool_metrics._MAX_LOG_BYTES = 128
    tool_metrics._MAX_ROTATIONS = 64
    barrier.wait(timeout=10)
    for index in range(24):
        tool_metrics._append_batch([json.dumps({"worker": worker, "index": index}) + "\n"])


def test_two_process_metrics_rotation_preserves_every_record(tmp_path):
    context = multiprocessing.get_context("spawn")
    path = tmp_path / "metrics.jsonl"
    barrier = context.Barrier(3)
    processes = [context.Process(target=_metrics_writer, args=(str(path), worker, barrier))
                 for worker in range(2)]
    for process in processes:
        process.start()
    barrier.wait(timeout=10)
    for process in processes:
        process.join(timeout=15)
        if process.is_alive():
            process.terminate()
            process.join()
        assert process.exitcode == 0
    rows = [json.loads(line) for file in tmp_path.glob("metrics.jsonl*")
            if not file.name.endswith(".lock") for line in file.read_text().splitlines()]
    assert len(rows) == 48
    assert {(row["worker"], row["index"]) for row in rows} == {
        (worker, index) for worker in range(2) for index in range(24)}
