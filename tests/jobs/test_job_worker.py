"""Worker 侧 durable job 运行时：协作取消、进度节流、原子产物、重复投递防护。

用真实 SQLite（同步 Session，与 Celery worker 相同的访问方式）+ 真实临时文件，
不 mock 掉被测语义。
"""
import os
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.db_model import Base
from app.services.jobs import (
    AlreadyFinished,
    DurableJobStore,
    JobKind,
    JobStatus,
    OperationCancelled,
    ProgressThrottle,
    coerce_status,
    durable_job,
    finish_job,
)
from app.services.jobs.artifacts import atomic_output


@pytest.fixture
def worker_env(tmp_path):
    """同步 engine + session_factory，模拟 Celery worker 的 db_session。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'worker.db'}")
    Base.metadata.create_all(engine)
    Sess = sessionmaker(bind=engine)

    class _Factory:
        def __call__(self):
            return Sess()

    factory = _Factory()

    def make_job(**overrides) -> int:
        params = {
            "task_type": "ndvi",
            "kind": JobKind.analysis,
            "owner_id": "user-a",
            "session_id": "sess-a",
            "parameters": {"raster_path": "/data/a.tif"},
            "dispatch_spec": {
                "task": "app.services.spatial_tasks.run_ndvi_analysis",
                "args": ["/data/a.tif"],
                "kwargs": {},
            },
        }
        params.update(overrides)
        with Sess() as db:
            job = DurableJobStore.create_sync(db, **params)
            DurableJobStore.transition_sync(
                db, job.id, JobStatus.queued, expected=[JobStatus.pending]
            )
            db.commit()
            return int(job.id)

    def status_of(job_id: int) -> JobStatus:
        with Sess() as db:
            return coerce_status(DurableJobStore.get_sync(db, job_id).status)

    def record(job_id: int):
        with Sess() as db:
            job = DurableJobStore.get_sync(db, job_id)
            db.expunge(job)
            return job

    yield {
        "factory": factory,
        "make_job": make_job,
        "status_of": status_of,
        "record": record,
        "tmp_path": tmp_path,
    }
    engine.dispose()


# ── 正常路径 ────────────────────────────────────────────────────────


def test_durable_job_marks_running_then_completed(worker_env):
    job_id = worker_env["make_job"]()

    with durable_job(job_id, session_factory=worker_env["factory"]) as job:
        assert worker_env["status_of"](job_id) is JobStatus.running
        job.progress(50, "计算中", phase="compute")

    status = finish_job(job_id, result={"mean": 0.42}, session_factory=worker_env["factory"])
    assert status is JobStatus.completed

    rec = worker_env["record"](job_id)
    assert coerce_status(rec.status) is JobStatus.completed
    assert rec.progress == 100
    assert rec.worker_id, "worker_id 必须落库，stale 归因需要它"
    assert rec.started_at is not None


def test_progress_writes_are_throttled(worker_env):
    """规范 §20：大循环不得每次迭代写一次 DB。"""
    job_id = worker_env["make_job"]()
    throttle = ProgressThrottle(min_delta_pct=10.0, min_interval_s=3600.0)

    with durable_job(
        job_id, session_factory=worker_env["factory"], progress_throttle=throttle
    ) as job:
        for i in range(1000):
            job.progress(i * 100 // 1000, "计算中")

    # 首次 + 每 10% 一次 + 100% ≈ 11 次，绝不是 1000 次
    assert job.progress_writes <= 12, job.progress_writes
    assert job.progress_writes >= 2


def test_heartbeat_is_refreshed_by_progress(worker_env):
    job_id = worker_env["make_job"]()
    with durable_job(job_id, session_factory=worker_env["factory"]) as job:
        job.progress(10, "start", force=True)
        rec = worker_env["record"](job_id)
        assert rec.heartbeat_at is not None


# ── 取消 ────────────────────────────────────────────────────────────


def test_cooperative_cancel_stops_loop_early(worker_env):
    """规范 §13 / Scenario 3：取消后不再执行后续 chunk，CPU 真正释放。

    取消事实写进 DB（与 API 走同一条路），然后显式驱动一次看门狗轮询 —— 这样断言
    完全确定，不依赖后台线程的调度时序。看门狗自己会推送这件事由下一个测试覆盖。
    """
    job_id = worker_env["make_job"]()
    executed: list[int] = []
    total_chunks = 100

    with pytest.raises(OperationCancelled):
        with durable_job(
            job_id, session_factory=worker_env["factory"], cancel_interval=3600.0
        ) as job:
            for i in job.chunks(range(total_chunks)):
                executed.append(i)
                if i == 4:
                    with worker_env["factory"]() as db:
                        DurableJobStore.request_cancel_sync(db, job_id)
                        db.commit()
                    job.poll_cancel()  # 看门狗每 tick 做的正是这件事

    assert len(executed) <= 7, f"取消后仍执行了 {len(executed)}/{total_chunks} 个 chunk"
    assert worker_env["status_of"](job_id) is JobStatus.cancelled


def test_watchdog_pushes_cancel_without_body_polling(worker_env):
    """取消必须被**推**给执行体：任务体不主动查 DB 也要能感知（规范 §11/§12）。

    这是「取消不只是 UI 状态」的核心机制 —— 一段不可中断的 numpy 计算期间没人会去
    读 DB，只有后台看门狗能点燃 token，让下一个 checkpoint 生效。
    """
    job_id = worker_env["make_job"]()

    with pytest.raises(OperationCancelled):
        with durable_job(
            job_id, session_factory=worker_env["factory"], cancel_interval=0.02
        ) as job:
            with worker_env["factory"]() as db:
                DurableJobStore.request_cancel_sync(db, job_id)
                db.commit()

            # 任务体只睡觉，从不 poll_cancel —— token 必须由看门狗线程点燃
            deadline = time.monotonic() + 10.0
            while not job.token.cancelled and time.monotonic() < deadline:
                time.sleep(0.02)

            assert job.token.cancelled, "看门狗未在 10s 内推送取消"
            assert job.watchdog.db_polls >= 1
            job.checkpoint()  # 现在应当抛出

    assert worker_env["status_of"](job_id) is JobStatus.cancelled


def test_checkpoints_do_not_hit_the_database(worker_env):
    """checkpoint 只读内存 token —— 密集检查点不得产生 DB 查询。"""
    job_id = worker_env["make_job"]()
    with durable_job(
        job_id, session_factory=worker_env["factory"], cancel_interval=3600.0
    ) as job:
        before = job.watchdog.db_polls
        for _ in job.chunks(range(5000)):
            pass
        assert job.checkpoints == 5000
        assert job.watchdog.db_polls == before, "checkpoint 不应触发 DB 轮询"
    finish_job(job_id, result={}, session_factory=worker_env["factory"])


def test_cancel_before_execution_skips_body(worker_env):
    """job 在 worker 拿到它之前就被取消 → 任务体一次都不执行。"""
    job_id = worker_env["make_job"]()
    with worker_env["factory"]() as db:
        DurableJobStore.request_cancel_sync(db, job_id)
        db.commit()

    executed: list[int] = []
    with pytest.raises(AlreadyFinished):
        with durable_job(job_id, session_factory=worker_env["factory"]):
            executed.append(1)

    assert executed == []
    assert worker_env["status_of"](job_id) is JobStatus.cancelled


def test_late_success_after_cancel_converges_to_cancelled(worker_env):
    """Scenario 4：worker 在 cancelling 期间跑完 → 终态仍是 cancelled。"""
    job_id = worker_env["make_job"]()

    with durable_job(job_id, session_factory=worker_env["factory"], cancel_interval=3600.0):
        # 取消到达，但任务体没有 checkpoint（模拟一段不可中断的 numpy 计算）
        with worker_env["factory"]() as db:
            DurableJobStore.request_cancel_sync(db, job_id)
            db.commit()

    status = finish_job(job_id, result={"mean": 0.9}, session_factory=worker_env["factory"])
    assert status is JobStatus.cancelled
    rec = worker_env["record"](job_id)
    assert coerce_status(rec.status) is JobStatus.cancelled
    assert rec.result_summary is None


# ── 重复投递防护 ────────────────────────────────────────────────────


def test_duplicate_delivery_of_terminal_job_is_skipped(worker_env):
    """acks_late 下 broker 可能重投；已终态的 job 绝不能重跑（规范 §16）。"""
    job_id = worker_env["make_job"]()
    with durable_job(job_id, session_factory=worker_env["factory"]):
        pass
    finish_job(job_id, result={"first": True}, session_factory=worker_env["factory"])

    executed: list[int] = []
    with pytest.raises(AlreadyFinished):
        with durable_job(job_id, session_factory=worker_env["factory"]):
            executed.append(1)
    assert executed == []

    rec = worker_env["record"](job_id)
    assert rec.result_summary == {"first": True}, "重投不得改写已完成的结果"


def test_unknown_job_raises_already_finished(worker_env):
    with pytest.raises(AlreadyFinished):
        with durable_job(999999, session_factory=worker_env["factory"]):
            pass


# ── 失败 ────────────────────────────────────────────────────────────


def test_exception_marks_failed_with_redacted_error(worker_env):
    """错误只落单行摘要，绝不落 traceback（规范 §7 / §10）。"""
    job_id = worker_env["make_job"]()
    with pytest.raises(ValueError):
        with durable_job(job_id, session_factory=worker_env["factory"]):
            raise ValueError("invalid CRS: EPSG:99999")

    rec = worker_env["record"](job_id)
    assert coerce_status(rec.status) is JobStatus.failed
    assert rec.error_trace == "ValueError: invalid CRS: EPSG:99999"
    assert "Traceback" not in (rec.error_trace or "")
    assert "\n" not in (rec.error_trace or "")


# ── 产物原子性 ──────────────────────────────────────────────────────


def test_artifact_is_finalized_atomically(worker_env):
    """Scenario 14：成功产物 finalize 恰好一次，且中途不可见。"""
    job_id = worker_env["make_job"]()
    final = str(worker_env["tmp_path"] / "out" / "ndvi.tif")

    with durable_job(job_id, session_factory=worker_env["factory"]) as job:
        with job.artifact(final) as tmp:
            assert not os.path.exists(final), "finalize 之前最终路径不得存在"
            assert tmp != final
            with open(tmp, "wb") as fh:
                fh.write(b"GEOTIFF")

    assert os.path.exists(final)
    with open(final, "rb") as fh:
        assert fh.read() == b"GEOTIFF"
    assert not os.path.exists(tmp)  # 临时文件已被 replace 掉


def test_artifact_is_discarded_on_failure(worker_env):
    """Scenario 13：失败不留 half-ready 产物。"""
    job_id = worker_env["make_job"]()
    final = str(worker_env["tmp_path"] / "fail.tif")
    leaked: list[str] = []

    with pytest.raises(RuntimeError):
        with durable_job(job_id, session_factory=worker_env["factory"]) as job:
            with job.artifact(final) as tmp:
                leaked.append(tmp)
                with open(tmp, "wb") as fh:
                    fh.write(b"HALF")
                raise RuntimeError("compute exploded")

    assert not os.path.exists(final)
    assert not os.path.exists(leaked[0]), "临时文件必须被清理"
    assert worker_env["status_of"](job_id) is JobStatus.failed


def test_artifact_is_discarded_on_cancel(worker_env):
    """取消后不得留下半个 GeoTIFF（规范 §24）。"""
    job_id = worker_env["make_job"]()
    final = str(worker_env["tmp_path"] / "cancelled.tif")
    leaked: list[str] = []

    with pytest.raises(OperationCancelled):
        with durable_job(
            job_id, session_factory=worker_env["factory"], cancel_interval=3600.0
        ) as job:
            with job.artifact(final) as tmp:
                leaked.append(tmp)
                with open(tmp, "wb") as fh:
                    fh.write(b"PARTIAL")
                with worker_env["factory"]() as db:
                    DurableJobStore.request_cancel_sync(db, job_id)
                    db.commit()
                job.poll_cancel()
                job.checkpoint()

    assert not os.path.exists(final)
    assert not os.path.exists(leaked[0])
    assert worker_env["status_of"](job_id) is JobStatus.cancelled


def test_artifact_producer_writing_nothing_is_an_error(worker_env):
    """声明了产物却什么都没写 → 视为失败，不注册不存在的 artifact。"""
    job_id = worker_env["make_job"]()
    final = str(worker_env["tmp_path"] / "empty.tif")
    with pytest.raises(FileNotFoundError):
        with durable_job(job_id, session_factory=worker_env["factory"]) as job:
            with job.artifact(final):
                pass
    assert not os.path.exists(final)


def test_tracked_temp_files_are_cleaned_on_failure(worker_env):
    job_id = worker_env["make_job"]()
    temp = str(worker_env["tmp_path"] / "scratch.dat")
    with open(temp, "wb") as fh:
        fh.write(b"scratch")

    with pytest.raises(RuntimeError):
        with durable_job(job_id, session_factory=worker_env["factory"]) as job:
            job.track_temp(temp)
            raise RuntimeError("boom")

    assert not os.path.exists(temp)


def test_successful_artifact_is_never_deleted_by_later_cleanup(worker_env):
    """清理只针对本次的 .part 文件 —— 已 finalize 的成功产物绝不能被删。"""
    job_id = worker_env["make_job"]()
    final = str(worker_env["tmp_path"] / "keep.tif")

    with pytest.raises(RuntimeError):
        with durable_job(job_id, session_factory=worker_env["factory"]) as job:
            with job.artifact(final) as tmp:
                with open(tmp, "wb") as fh:
                    fh.write(b"GOOD")
            assert os.path.exists(final)
            raise RuntimeError("later step failed")

    assert os.path.exists(final), "已成功 finalize 的产物不应被后续失败清理掉"


# ── atomic_output 独立单测 ──────────────────────────────────────────


def test_atomic_output_should_abort_discards(tmp_path):
    final = str(tmp_path / "aborted.tif")
    captured: list[str] = []
    with pytest.raises(RuntimeError):
        with atomic_output(final, should_abort=lambda: True) as tmp:
            captured.append(tmp)
            with open(tmp, "wb") as fh:
                fh.write(b"x")
    assert not os.path.exists(final)
    assert not os.path.exists(captured[0])


def test_atomic_output_creates_parent_dirs(tmp_path):
    final = str(tmp_path / "deep" / "nested" / "out.tif")
    with atomic_output(final) as tmp:
        with open(tmp, "wb") as fh:
            fh.write(b"x")
    assert os.path.exists(final)


def test_atomic_output_overwrites_existing_file(tmp_path):
    """重跑覆盖旧产物必须是原子替换，不能出现「删了旧的还没写新的」窗口。"""
    final = str(tmp_path / "out.tif")
    with open(final, "wb") as fh:
        fh.write(b"OLD")
    with atomic_output(final) as tmp:
        with open(tmp, "wb") as fh:
            fh.write(b"NEW")
        with open(final, "rb") as fh:
            assert fh.read() == b"OLD", "替换前旧产物必须仍然完整可读"
    with open(final, "rb") as fh:
        assert fh.read() == b"NEW"
