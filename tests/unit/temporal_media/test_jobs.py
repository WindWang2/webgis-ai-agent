"""WP07/WP08/WP09/WP10 任务链：真实 runner、取消/恢复、owner 隔离、工具 dispatch。

真实执行：逐帧渲染、编码、独立解码、原子发布、下载文件回读；
只 stub 掉「外部会话存储/Celery 派发」两个边界，不 mock 主链。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict

import pytest

from app.services.temporal_media.contracts import (
    JobRefArgs, MediaPlanArgs, ResultArgs, SubmitArgs, TemporalMediaError,
)
from app.services.temporal_media.freeze import build_frozen
from app.services.temporal_media import service as svc
from app.services.temporal_media.runner import execute_media_job
from app.services.temporal_media.store import (
    initial_state, job_dir, load_plan, load_state, save_plan, save_state,
    update_state,
)
from app.tools.registry import ToolRegistry
from app.tools.temporal_media_tools import register_temporal_media_tools

from .conftest import make_plan, temporal_document

PRINCIPAL = "session:sess-g35"


def _seed_job(media_env, plan: Dict[str, Any], job_id: str = "g35-job-1",
              principal: str = PRINCIPAL) -> None:
    save_plan(principal, job_id, plan)
    save_state(principal, job_id,
               initial_state(principal, job_id, plan, durable_job_id="77"))


def test_runner_end_to_end_publishes_real_downloadable_artifacts(media_env, small_plan):
    _seed_job(media_env, small_plan)
    receipt = execute_media_job(PRINCIPAL, "g35-job-1", recheck=lambda: None)
    assert receipt["status"] == "completed"
    state = load_state(PRINCIPAL, "g35-job-1")
    assert state["status"] == "completed"
    assert state["verification"]["passed"] is True
    assert state["rendered_frames"] == 5
    assert state["progress"] == 100
    artifacts = state["artifacts"]
    assert set(artifacts) == {"animation", "evidence", "manifest"}
    exports = media_env["data_dir"] / "exports"
    for item in artifacts.values():
        assert item["bytes"] > 0
        assert "?exp=" in item["url"] and "&sig=" in item["url"]
        assert (exports / item["filename"]).is_file()
        assert (exports / f"{item['filename']}.owner").read_text() == PRINCIPAL
    animation = Path(exports / artifacts["animation"]["filename"]).read_bytes()
    assert animation[:8] == b"\x89PNG\r\n\x1a\n"
    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(
            (exports / artifacts["evidence"]["filename"]).read_bytes())) as zf:
        assert "manifest.json" in zf.namelist()
        embedded = json.loads(zf.read("manifest.json"))
    assert embedded["verification"]["passed"] is True
    assert embedded["container"]["total_duration_ms"] == 5000
    assert len(embedded["observations"]) == 5


def test_scenario_20_recovery_reuses_only_same_fingerprint_frames(media_env, small_plan):
    # 终态不可重开（update_state 文件锁 + 终态字段整体保护）；worker 重启用
    # 同一冻结计划的**新域任务**模拟，只允许同指纹帧缓存命中。
    _seed_job(media_env, small_plan, "g35-recovery-a")
    first = execute_media_job(PRINCIPAL, "g35-recovery-a", recheck=lambda: None)
    assert first["resources"]["cache_hits"] == 0
    assert first["resources"]["unique_rendered_frames"] == 5
    _seed_job(media_env, small_plan, "g35-recovery-b")
    second = execute_media_job(PRINCIPAL, "g35-recovery-b", recheck=lambda: None)
    assert second["status"] == "completed"
    assert second["resources"]["cache_hits"] == 5
    assert second["resources"]["unique_rendered_frames"] == 0
    # 换样式 / 计划指纹后不得复用旧图
    _, _, changed = make_plan(temporal_document([2019, 2020, 2021]),
                              owner=PRINCIPAL, width=640, height=360,
                              dwell_ms=2500)
    _seed_job(media_env, changed, "g35-recovery-c")
    third = execute_media_job(PRINCIPAL, "g35-recovery-c", recheck=lambda: None)
    assert third["status"] == "completed"
    assert third["resources"]["cache_hits"] == 0


def test_scenario_19_cancel_is_terminal_and_late_success_cannot_override(media_env,
                                                                        small_plan):
    _seed_job(media_env, small_plan, "g35-cancel")
    from app.services.temporal_media.store import request_cancel
    cancelled = request_cancel(PRINCIPAL, "g35-cancel")
    assert cancelled["status"] == "cancelled"
    receipt = execute_media_job(PRINCIPAL, "g35-cancel", recheck=lambda: None)
    assert receipt["status"] == "cancelled"
    # 迟到的成功写入不得覆盖 cancelled
    late = update_state(PRINCIPAL, "g35-cancel", lambda s: s.update({
        "status": "completed", "phase": "completed", "progress": 100,
        "artifacts": {"animation": {"filename": "late.apng"}},
    }))
    assert late["status"] == "cancelled"
    assert late["artifacts"] == {}


def test_scenario_17_source_change_before_publish_aborts(media_env, small_plan):
    _seed_job(media_env, small_plan, "g35-source-change")
    calls = {"n": 0}

    def recheck() -> None:
        calls["n"] += 1
        if calls["n"] > 1:
            raise TemporalMediaError(
                "SOURCE_CHANGED_MID_RENDER", "revision moved", status_code=409)

    with pytest.raises(TemporalMediaError) as exc:
        execute_media_job(PRINCIPAL, "g35-source-change", recheck=recheck)
    assert exc.value.code == "SOURCE_CHANGED_MID_RENDER"
    state = load_state(PRINCIPAL, "g35-source-change")
    assert state["status"] == "failed"
    assert not state["artifacts"]
    assert not (job_dir(PRINCIPAL, "g35-source-change") / "work").exists()


def test_scenario_21_owner_isolation_on_state_cache_and_download(media_env, small_plan):
    other = "session:sess-other"
    _seed_job(media_env, small_plan, "g35-owner", PRINCIPAL)
    with pytest.raises(TemporalMediaError):
        load_plan(other, "g35-owner")
    with pytest.raises(TemporalMediaError):
        load_state(other, "g35-owner")
    receipt = execute_media_job(PRINCIPAL, "g35-owner", recheck=lambda: None)
    assert receipt["status"] == "completed"
    # 同计划不同 owner 的缓存键不同，不会命中别人的帧
    plan2 = dict(small_plan)
    plan2["owner"] = other
    from app.services.temporal_media.planning import plan_hash_of
    plan2["plan_hash"] = plan_hash_of(plan2)
    _seed_job(media_env, plan2, "g35-owner-other", other)
    second = execute_media_job(other, "g35-owner-other", recheck=lambda: None)
    assert second["resources"]["cache_hits"] == 0
    exports = media_env["data_dir"] / "exports"
    for item in load_state(PRINCIPAL, "g35-owner")["artifacts"].values():
        assert (exports / f"{item['filename']}.owner").read_text() == PRINCIPAL


@pytest.fixture()
def service_env(media_env, monkeypatch):
    document = temporal_document([2019, 2021, 2024])
    frozen = build_frozen("sess-g35", document, 3)

    async def fake_freeze(session_id: str, expected_revision=None):
        return frozen

    def fake_submit(**kwargs):
        return {"job_id": "77", "task_id": "t-77", "idempotent_reuse": False}

    import app.services.jobs.submit as submit_mod
    monkeypatch.setattr(svc, "freeze_document", fake_freeze)
    monkeypatch.setattr(submit_mod, "submit_durable_job", fake_submit)
    return {"frozen": frozen}


def test_service_preview_submit_status_result_chain(service_env):
    async def scenario() -> None:
        args = MediaPlanArgs(session_id="sess-g35", width=480, height=270)
        preview = await svc.preview(args, PRINCIPAL)
        assert preview["status"] == "preview"
        assert preview["plan"]["display"]["missing_period_count"] == 3
        submit = await svc.submit(
            SubmitArgs(session_id="sess-g35", width=480, height=270,
                       plan_hash=preview["plan_hash"]), PRINCIPAL)
        assert submit["status"] == "pending"
        job_id = submit["job_id"]
        status = await svc.status(JobRefArgs(session_id="sess-g35",
                                             job_id=job_id), PRINCIPAL)
        assert status["status"] == "pending" and status["completed"] is False
        with pytest.raises(TemporalMediaError) as exc:
            await svc.result(ResultArgs(session_id="sess-g35",
                                        job_id=job_id), PRINCIPAL)
        assert exc.value.code == "RESULT_NOT_READY"
        # 真实 worker 执行（不经 Celery broker）
        execute_media_job(PRINCIPAL, job_id, recheck=lambda: None)
        done = await svc.status(JobRefArgs(session_id="sess-g35",
                                           job_id=job_id), PRINCIPAL)
        assert done["completed"] is True
        result = await svc.result(ResultArgs(session_id="sess-g35",
                                             job_id=job_id), PRINCIPAL)
        assert result["status"] == "completed"
        assert "animation" in result["artifacts"]
        assert result["manifest_summary"]["missing_periods"] == 3
        assert "?exp=" in result["artifacts"]["animation"]["url"]

    asyncio.run(scenario())


def test_duplicate_submit_reuses_one_job_without_orphan_pending(service_env):
    async def scenario() -> None:
        args = MediaPlanArgs(session_id="sess-g35", width=480, height=270)
        preview = await svc.preview(args, PRINCIPAL)
        first = await svc.submit(
            SubmitArgs(session_id="sess-g35", width=480, height=270,
                       plan_ref=preview["plan_ref"]), PRINCIPAL)
        second = await svc.submit(
            SubmitArgs(session_id="sess-g35", width=480, height=270,
                       plan_ref=preview["plan_ref"]), PRINCIPAL)
        assert second["job_id"] == first["job_id"]
        assert second["idempotent_reuse"] is True
        assert second["status"] == "pending"
        from app.services.temporal_media.store import list_jobs
        jobs = [j for j in list_jobs(PRINCIPAL)
                if j["plan_hash"] == preview["plan_hash"]]
        assert len(jobs) == 1

    asyncio.run(scenario())


def test_scenario_22_unknown_plan_version_is_refused(service_env):
    async def scenario() -> None:
        preview = await svc.preview(
            MediaPlanArgs(session_id="sess-g35", width=480, height=270), PRINCIPAL)
        with pytest.raises(TemporalMediaError) as exc:
            await svc.submit(
                SubmitArgs(session_id="sess-g35", width=480, height=270,
                           plan_hash="f" * 64), PRINCIPAL)
        assert exc.value.code == "PLAN_VERSION_MISMATCH"
        assert preview["plan_hash"] != "f" * 64

    asyncio.run(scenario())


def test_pi_tool_dispatch_uses_same_service(service_env, monkeypatch):
    registry = ToolRegistry()
    register_temporal_media_tools(registry)

    async def scenario() -> None:
        caps = await registry.dispatch(
            "webgis_temporal_media_capabilities", {}, session_id="sess-g35")
        assert caps["success"] is True
        assert caps["capabilities"]["renderer"]["available"] is True
        plan = await registry.dispatch(
            "webgis_temporal_media_plan",
            {"width": 480, "height": 270}, session_id="sess-g35")
        assert plan["success"] is True
        assert plan["plan"]["observations"]
        submit = await registry.dispatch(
            "webgis_temporal_media_submit",
            {"width": 480, "height": 270, "plan_hash": plan["plan_hash"]},
            session_id="sess-g35")
        assert submit["status"] == "pending"
        state = await registry.dispatch(
            "webgis_temporal_media_status", {"job_id": submit["job_id"]},
            session_id="sess-g35")
        assert state["status"] == "pending" and state["completed"] is False

    asyncio.run(scenario())


def test_dispatch_failure_marks_failed_and_retry_is_allowed(service_env, monkeypatch):
    async def scenario() -> None:
        import app.services.jobs.submit as submit_mod
        from app.services.temporal_media.contracts import (
            MediaPlanArgs, SubmitArgs, TemporalMediaError,
        )
        from app.services.temporal_media.store import list_jobs

        args = MediaPlanArgs(session_id="sess-g35", width=480, height=270)
        preview = await svc.preview(args, PRINCIPAL)

        def boom(**kwargs):
            raise RuntimeError("broker unavailable")

        monkeypatch.setattr(submit_mod, "submit_durable_job", boom)
        with pytest.raises(TemporalMediaError) as exc:
            await svc.submit(
                SubmitArgs(session_id="sess-g35", width=480, height=270,
                           plan_ref=preview["plan_ref"]), PRINCIPAL)
        assert exc.value.code == "DURABLE_DISPATCH_FAILED"
        jobs = list_jobs(PRINCIPAL)
        assert jobs and jobs[-1]["status"] == "failed"
        assert not any(j["status"] == "pending" for j in jobs)

        def recovered(**kwargs):
            return {"job_id": "78", "task_id": "t-78", "idempotent_reuse": False}

        monkeypatch.setattr(submit_mod, "submit_durable_job", recovered)
        retry = await svc.submit(
            SubmitArgs(session_id="sess-g35", width=480, height=270,
                       plan_ref=preview["plan_ref"]), PRINCIPAL)
        assert retry["status"] == "pending"
        assert retry["job_id"] != jobs[-1]["job_id"]

    asyncio.run(scenario())


def test_concurrent_cancel_and_finalize_never_loses_terminal_state(media_env,
                                                                  small_plan):
    """flock + 终态整体保护：并发取消/迟到成功不得出现成果与 cancelled 混合态。"""
    import threading
    from app.services.temporal_media.store import request_cancel

    for index in range(30):
        job_id = f"g35-race-{index}"
        _seed_job(media_env, small_plan, job_id)
        barrier = threading.Barrier(2)

        def do_cancel() -> None:
            barrier.wait()
            request_cancel(PRINCIPAL, job_id)

        def do_finalize() -> None:
            barrier.wait()
            update_state(PRINCIPAL, job_id, lambda s: s.update({
                "status": "completed", "phase": "completed", "progress": 100,
                "artifacts": {"animation": {"filename": "late.apng"}},
            }))

        threads = [threading.Thread(target=do_cancel),
                   threading.Thread(target=do_finalize)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        state = load_state(PRINCIPAL, job_id)
        assert state["status"] in ("cancelled", "completed")
        if state.get("cancel_requested"):
            assert state["status"] == "cancelled"
            assert state["artifacts"] == {}
            assert state["phase"] == "cancelled"
        else:
            assert state["status"] == "completed"


def test_T3_g35_tools_follow_production_deferred_surface_contract(monkeypatch):
    """生产 dump 零改写：6 工具在超集里、带完整 schema、deferred 可按名直调。

    与 G26 `webgis_map_export` 同款 deferred 契约；不改
    `NATIVE_TOOL_NAMES` / `default_active`（4 个既有契约测试钉住该不变量）。
    """
    from app.services.chat.pi_native_surface import (
        NATIVE_TOOL_NAMES, pi_surface_for_spawn,
    )
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    names = (
        "webgis_temporal_media_capabilities", "webgis_temporal_media_plan",
        "webgis_temporal_media_submit", "webgis_temporal_media_status",
        "webgis_temporal_media_cancel", "webgis_temporal_media_result",
    )
    registry = ToolRegistry()
    init_tools(registry)
    surface = pi_surface_for_spawn(registry)
    assert surface["default_active"] == list(NATIVE_TOOL_NAMES)
    definitions = {t["name"]: t for t in surface["tools"]}
    for name in names:
        assert name in definitions
        assert definitions[name].get("exposure") == "deferred"
        assert (definitions[name].get("parameters") or {}).get("properties")
    monkeypatch.delenv("PI_DYNAMIC_TOOL_SURFACE", raising=False)


def test_video_codec_probe_does_not_open_undeployed_formats(monkeypatch):
    from app.services.temporal_media.capabilities import (
        probe_capabilities, probe_video_codecs,
    )
    monkeypatch.delenv("WEBGIS_TEMPORAL_MEDIA_ENABLE_VIDEO", raising=False)
    probe = probe_video_codecs()
    caps = probe_capabilities()
    for fmt in ("mp4", "webm"):
        entry = next(f for f in caps["formats"] if f["name"] == fmt)
        assert entry["available"] is False
        assert "reason" in entry
    assert probe["deployment_enabled"] is False
    assert probe["ffmpeg"] in (None, "present")
