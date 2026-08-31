"""真实 Celery 任务 + submit 桥的端到端测试（ADR-0052）。

审计发现：`durable_job` 句柄和状态机测得很扎实，但**真正的 Celery 任务体**
（`run_ndvi_analysis`）和**提交桥**（`submit_durable_job`）此前零覆盖 —— 而集成层
恰恰是取消、心跳、幂等、重复投递等语义真正生效的地方。

诚实说明测试真实性（规范 §47）：
  * 这些测试是 **worker simulation**：Celery 以 eager 模式（`USE_REDIS=false`）在
    当前进程内同步执行任务体，不存在真实的 broker、独立 worker 进程或 prefork 池。
  * 真实的是：任务体全部代码路径、durable job 状态机、DB 读写、看门狗线程、
    取消传播、原子产物落盘。
  * 不真实的是：跨进程投递、broker 重投、revoke/terminate、prefork 隔离。
    需要本地 Redis + `celery worker` 才能覆盖那些（环境缺少 Redis 时无法执行）。
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.db_model import Base
from app.services.jobs import (
    DurableJobStore,
    JobKind,
    JobStatus,
    coerce_status,
)
from app.services.jobs.context import JobOrigin, use_origin
from app.services.jobs.submit import build_execution_key, submit_durable_job


@pytest.fixture(autouse=True)
def _celery_offline(monkeypatch):
    """钉死 Celery 为 eager + 无 Redis backend（双保险，见 conftest 占位说明）。

    直接调用任务体时 update_state 依赖 backend；若 backend 回退成 RedisBackend
    （历史上 load_dotenv 注入 CELERY_* 的竞态；#663 后 shell 导出同样防），会去连 localhost:16379 并抛连接错误。
    """
    from app.services.task_queue import celery_app

    monkeypatch.setitem(celery_app.conf, "result_backend", None)
    monkeypatch.setitem(celery_app.conf, "broker_url", "memory://")
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)


@pytest.fixture
def job_db(tmp_path, monkeypatch):
    """把 `db_session`（工具/worker 用的同步会话）指向临时 SQLite。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'e2e.db'}")
    Base.metadata.create_all(engine)
    Sess = sessionmaker(bind=engine)

    import contextlib

    @contextlib.contextmanager
    def fake_db_session():
        db = Sess()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # Branch runtime v3: durable job path calls calculate_index while
    # e2e cases historically mock calculate_ndvi. Bypass the path guards
    # (validate_data_path + exists) and prime calculate_index so the mock
    # is reached regardless of which name the test patches.
    monkeypatch.setattr("app.utils.path.validate_data_path", lambda p, **_: p)

    # submit / worker / spatial_tasks 各自 import 了同一个名字，全部替换
    monkeypatch.setattr("app.tools._utils.db_session", fake_db_session)
    monkeypatch.setattr("app.services.jobs.submit.db_session", fake_db_session)
    monkeypatch.setattr("app.services.spatial_tasks.db_session", fake_db_session)
    monkeypatch.setattr(
        "app.services.jobs.worker._default_session_factory", fake_db_session
    )
    yield {"factory": fake_db_session, "Sess": Sess}
    engine.dispose()


# ── submit 桥 ───────────────────────────────────────────────────────


def test_submit_creates_durable_job_and_enqueues(job_db):
    """Scenario 2：agent 重工具创建 durable job 并入队。"""
    calls: list[dict] = []

    class _FakeTask:
        name = "tests.fake_task"

        def apply_async(self, args=None, kwargs=None, **_):
            calls.append({"args": args, "kwargs": kwargs})

            class _R:
                id = "celery-abc"

            return _R()

    origin = JobOrigin(
        session_id="sess-a",
        owner_id="user-a",
        agent_task_id="task-1234",
        agent_step_id="step-1",
        tool_call_id="call-1",
    )
    with use_origin(origin):
        result = submit_durable_job(
            celery_task=_FakeTask(),
            task_type="ndvi",
            display_name="NDVI 分析",
            params={"raster_path": "/data/a.tif"},
            task_args=("/data/a.tif",),
            session_id="sess-a",
        )

    assert result["status"] == "analysis_task_started"
    assert result["task_id"] == "celery-abc"
    job_id = int(result["job_id"])

    # job_id 回流给 agent step（Turn → Step → Job 链）
    assert origin.created_job_ids == [str(job_id)]

    # Celery 收到了 job_id
    assert calls[0]["kwargs"]["job_id"] == job_id

    with job_db["Sess"]() as db:
        job = DurableJobStore.get_sync(db, job_id)
        assert coerce_status(job.status) is JobStatus.queued
        assert job.celery_task_id == "celery-abc"
        assert job.session_id == "sess-a"
        assert job.creator_id == "user-a"
        assert job.agent_task_id == "task-1234"
        assert job.agent_step_id == "step-1"
        assert job.dispatch_spec["task"] == "tests.fake_task"


def test_submit_is_idempotent_for_duplicate_requests(job_db):
    """规范 §16 / Scenario 5 前置：双击 / SSE 重连不得提交两次。"""
    enqueued: list[str] = []

    class _FakeTask:
        name = "tests.fake_task"

        def apply_async(self, args=None, kwargs=None, **_):
            enqueued.append("sent")

            class _R:
                id = f"celery-{len(enqueued)}"

            return _R()

    def submit():
        return submit_durable_job(
            celery_task=_FakeTask(),
            task_type="ndvi",
            display_name="NDVI 分析",
            params={"raster_path": "/data/a.tif"},
            task_args=("/data/a.tif",),
            session_id="sess-a",
        )

    first = submit()
    second = submit()

    assert first["job_id"] == second["job_id"], "同一逻辑提交必须复用同一个 job"
    assert second["idempotent_reuse"] is True
    assert len(enqueued) == 1, "不得重复入队（会重复执行不可逆的 GIS 操作）"


def test_submit_marks_job_failed_when_enqueue_raises(job_db):
    """broker 挂了：job 不能永远停在 pending（stale 清扫不覆盖 pending）。"""

    class _BrokenTask:
        name = "tests.broken"

        def apply_async(self, args=None, kwargs=None, **_):
            raise RuntimeError("broker unreachable")

    with pytest.raises(RuntimeError):
        submit_durable_job(
            celery_task=_BrokenTask(),
            task_type="ndvi",
            display_name="NDVI 分析",
            params={"raster_path": "/data/a.tif"},
            session_id="sess-a",
        )

    with job_db["Sess"]() as db:
        from app.models.db_model import AnalysisTask

        jobs = db.query(AnalysisTask).all()
        assert len(jobs) == 1
        assert coerce_status(jobs[0].status) is JobStatus.failed


def test_execution_key_is_stable_and_discriminating():
    a = build_execution_key("ndvi", "sess-a", {"path": "/x.tif", "band": 4})
    b = build_execution_key("ndvi", "sess-a", {"band": 4, "path": "/x.tif"})
    assert a == b, "dict 顺序不应改变幂等键"

    assert a != build_execution_key("ndvi", "sess-b", {"path": "/x.tif", "band": 4})
    assert a != build_execution_key("ndvi", "sess-a", {"path": "/y.tif", "band": 4})
    assert a != build_execution_key("ndwi", "sess-a", {"path": "/x.tif", "band": 4})


# ── 真实 Celery 任务体（eager 模式，worker simulation） ──────────────


def _make_ndvi_job(job_db, **overrides) -> int:
    params = {
        "task_type": "ndvi",
        "kind": JobKind.analysis,
        "owner_id": "user-a",
        "session_id": "sess-a",
        "parameters": {"raster_path": "/data/a.tif"},
    }
    params.update(overrides)
    with job_db["Sess"]() as db:
        job = DurableJobStore.create_sync(db, **params)
        DurableJobStore.transition_sync(
            db, job.id, JobStatus.queued, expected=[JobStatus.pending]
        )
        db.commit()
        return int(job.id)


def test_ndvi_task_completes_and_records_progress(job_db, monkeypatch, tmp_path):
    """任务体走通 durable job：running → 进度 → completed + result_ref。"""
    from app.services import spatial_tasks

    out = tmp_path / "NDVI_out.tif"
    out.write_bytes(b"GEOTIFF")

    monkeypatch.setattr(
        spatial_tasks.NatureResourceAnalyzer,
        "calculate_ndvi",
        staticmethod(
            lambda **kw: {
                "success": True,
                "result_path": str(out),
                "filename": "NDVI_out.tif",
                "stats": {"mean": 0.42},
                "bbox": [0, 0, 1, 1],
                "crs": "EPSG:4326",
            }
        ),
    )

    job_id = _make_ndvi_job(job_db)
    result = spatial_tasks.run_ndvi_analysis(
        "/data/a.tif", None, None, "sess-a", job_id=job_id
    )

    assert result["success"] is True
    assert result["job_id"] == str(job_id)
    assert result["data"]["result_path"] == str(out)

    with job_db["Sess"]() as db:
        job = DurableJobStore.get_sync(db, job_id)
        assert coerce_status(job.status) is JobStatus.completed
        assert job.progress == 100
        assert job.result_ref == str(out)
        assert job.worker_id, "worker_id 必须落库供 stale 归因"
        # 结果摘要只留统计量，不含巨型数据
        assert job.result_summary["stats"]["mean"] == 0.42


def test_ndvi_task_cancelled_before_execution_does_no_work(job_db, monkeypatch):
    """取消在 worker 拿到任务之前到达 → 计算一次都不跑，也不注册资产。"""
    from app.services import spatial_tasks

    called: list[int] = []

    def _should_not_run(**kw):
        called.append(1)
        return {"success": True}

    monkeypatch.setattr(
        spatial_tasks.NatureResourceAnalyzer, "calculate_ndvi", staticmethod(_should_not_run)
    )

    job_id = _make_ndvi_job(job_db)
    with job_db["Sess"]() as db:
        DurableJobStore.request_cancel_sync(db, job_id)
        db.commit()

    result = spatial_tasks.run_ndvi_analysis(
        "/data/a.tif", None, None, "sess-a", job_id=job_id
    )

    assert called == [], "已取消的 job 不得执行任务体"
    assert result["skipped"] is True
    with job_db["Sess"]() as db:
        job = DurableJobStore.get_sync(db, job_id)
        assert coerce_status(job.status) is JobStatus.cancelled


def test_ndvi_task_cancelled_mid_compute_discards_asset(job_db, monkeypatch, tmp_path):
    """取消在计算期间到达：不注册资产，终态为 cancelled（规范 §14/§23）。"""
    from app.services import spatial_tasks

    out = tmp_path / "NDVI_mid.tif"

    def _compute_then_cancel(**kw):
        # 计算过程中用户点了取消（持久事实写进 DB）
        with job_db["factory"]() as db:
            DurableJobStore.request_cancel_sync(db, job_holder["id"])
            db.commit()
        out.write_bytes(b"GEOTIFF")
        return {
            "success": True,
            "result_path": str(out),
            "filename": "NDVI_mid.tif",
            "stats": {"mean": 0.1},
            "bbox": [0, 0, 1, 1],
            "crs": "EPSG:4326",
        }

    monkeypatch.setattr(
        spatial_tasks.NatureResourceAnalyzer,
        "calculate_ndvi",
        staticmethod(_compute_then_cancel),
    )

    job_holder = {"id": _make_ndvi_job(job_db)}
    result = spatial_tasks.run_ndvi_analysis(
        "/data/a.tif", None, None, "sess-a", job_id=job_holder["id"]
    )

    assert result.get("cancelled") is True
    with job_db["Sess"]() as db:
        job = DurableJobStore.get_sync(db, job_holder["id"])
        assert coerce_status(job.status) is JobStatus.cancelled
        assert job.result_summary is None, "已取消的 job 不得保留结果"

        from app.models.upload import UploadRecord

        assert db.query(UploadRecord).count() == 0, "已取消的 job 不得注册资产"


def test_ndvi_task_failure_is_recorded_without_traceback(job_db, monkeypatch):
    from app.services import spatial_tasks

    monkeypatch.setattr(
        spatial_tasks.NatureResourceAnalyzer,
        "calculate_ndvi",
        staticmethod(lambda **kw: {"success": False, "error": "无法确定波段索引"}),
    )

    job_id = _make_ndvi_job(job_db)
    result = spatial_tasks.run_ndvi_analysis(
        "/data/a.tif", None, None, "sess-a", job_id=job_id
    )

    assert result["success"] is False
    with job_db["Sess"]() as db:
        job = DurableJobStore.get_sync(db, job_id)
        assert coerce_status(job.status) is JobStatus.failed
        assert "波段索引" in job.error_trace
        assert "Traceback" not in job.error_trace
        assert "\n" not in job.error_trace


def test_ndvi_task_duplicate_delivery_does_not_recompute(job_db, monkeypatch, tmp_path):
    """acks_late 下 broker 可能重投：第二次投递不得重复计算或重复注册资产。"""
    from app.services import spatial_tasks

    out = tmp_path / "NDVI_dup.tif"
    out.write_bytes(b"GEOTIFF")
    computes: list[int] = []

    def _compute(**kw):
        computes.append(1)
        return {
            "success": True,
            "result_path": str(out),
            "filename": "NDVI_dup.tif",
            "stats": {"mean": 0.3},
            "bbox": [0, 0, 1, 1],
            "crs": "EPSG:4326",
        }

    monkeypatch.setattr(
        spatial_tasks.NatureResourceAnalyzer, "calculate_ndvi", staticmethod(_compute)
    )

    job_id = _make_ndvi_job(job_db)
    first = spatial_tasks.run_ndvi_analysis("/data/a.tif", None, None, "sess-a", job_id=job_id)
    second = spatial_tasks.run_ndvi_analysis("/data/a.tif", None, None, "sess-a", job_id=job_id)

    assert first["success"] is True
    assert second.get("skipped") is True
    assert len(computes) == 1, "重投不得重复计算"

    with job_db["Sess"]() as db:
        from app.models.upload import UploadRecord

        assert db.query(UploadRecord).count() == 1, "重投不得重复注册资产"


def test_ndvi_legacy_path_without_job_id_still_works(job_db, monkeypatch, tmp_path):
    """兼容：未迁移的调用方（无 job_id）保持原行为。"""
    from app.services import spatial_tasks

    out = tmp_path / "NDVI_legacy.tif"
    out.write_bytes(b"GEOTIFF")
    monkeypatch.setattr(
        spatial_tasks.NatureResourceAnalyzer,
        "calculate_ndvi",
        staticmethod(
            lambda **kw: {
                "success": True,
                "result_path": str(out),
                "filename": "NDVI_legacy.tif",
                "stats": {"mean": 0.5},
                "bbox": [0, 0, 1, 1],
                "crs": "EPSG:4326",
            }
        ),
    )

    result = spatial_tasks.run_ndvi_analysis("/data/a.tif", None, None, "sess-a")
    assert result["success"] is True
    assert result["data"]["result_path"] == str(out)
    assert "job_id" not in result  # 旧形状不变


def test_ndvi_task_missing_job_row_is_skipped(job_db, monkeypatch):
    """job 行不存在（被清理/错误 id）时安全跳过，不抛给 Celery。"""
    from app.services import spatial_tasks

    monkeypatch.setattr(
        spatial_tasks.NatureResourceAnalyzer,
        "calculate_ndvi",
        staticmethod(lambda **kw: {"success": True}),
    )
    result = spatial_tasks.run_ndvi_analysis(
        "/data/a.tif", None, None, "sess-a", job_id=999999
    )
    assert result["skipped"] is True
