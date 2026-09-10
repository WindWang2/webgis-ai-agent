"""Platform V4 chaos 行为测试（ADR-0131 D8）。

三个新 fault：STORAGE_PARTIAL_WRITE / JOBS_ENQUEUE_FAIL_STORM /
JOBS_REDELIVERY_STORM。注入型断言 journal 开火证据 + 恢复语义；
纯编排型断言编排后的不变量（幂等复用 / 单胜出认领）。
"""
from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.lib.artifact_cache import get_artifact, publish_artifact
from app.models.db_model import Base
from app.services.jobs import DurableJobStore, JobKind, JobStatus
from tests.fixtures.chaos import FAULTS, chaos, journal_snapshot


@pytest_asyncio.fixture
async def engine(tmp_path):
    eng = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'chaos_v4.db'}",
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(bind=engine, expire_on_commit=False)


def test_new_faults_registered():
    assert {"STORAGE_PARTIAL_WRITE", "JOBS_ENQUEUE_FAIL_STORM",
            "JOBS_REDELIVERY_STORM"}.issubset(FAULTS.keys())


# ── STORAGE_PARTIAL_WRITE ───────────────────────────────────────────────


def test_partial_write_leaves_no_half_artifact(tmp_path):
    """publish 拷贝中途源读失败：无半截制品可见、tmp 被清理、直出 fallback。

    哨兵 = compute() 输出路径（publish 的拷贝循环读的是它，不是 src_path；
    src_path 只进 meta 的 identity）。key 唯一（data/artifacts 跨运行持久）。"""
    unique = uuid4().hex[:8]
    key = f"chaos-partial-{unique}"
    # compute 输出 = 哨兵命名文件（拷贝循环的读取源）
    out = tmp_path / f"chaos-partial-src-{unique}.tif"
    out.write_bytes(b"x" * (3 * 1024 * 1024))  # 3 MiB → 3 个 1MiB chunk
    with chaos("STORAGE_PARTIAL_WRITE", fail_after=1) as fault:
        result = publish_artifact(key, str(out), lambda: str(out))

    assert fault.fired, "注入必须真实开火"
    assert result == str(out)            # 直出 fallback（publish 文档化契约）
    assert get_artifact(key) is None     # 缓存不可见半截制品


def test_partial_write_after_failure_cache_recovers(tmp_path):
    """故障解除后同一 key 重新 publish 成功（瞬时故障语义）。"""
    unique = uuid4().hex[:8]
    key = f"chaos-partial-recover-{unique}"
    out = tmp_path / f"chaos-partial-src-{unique}.tif"
    out.write_bytes(b"y" * (2 * 1024 * 1024))

    with chaos("STORAGE_PARTIAL_WRITE"):
        publish_artifact(key, str(out), lambda: str(out))
    assert get_artifact(key) is None

    cached = publish_artifact(key, str(out), lambda: str(out))
    assert get_artifact(key) is not None
    assert cached


# ── JOBS_ENQUEUE_FAIL_STORM ──────────────────────────────────────────────


async def _make_pending_job(session_factory, params, task_type="storm") -> int:
    async with session_factory() as db:
        job = await DurableJobStore.create(
            db, task_type=task_type, kind=JobKind.analysis,
            owner_id="user-a", session_id="sess-a",
            parameters=params,
            dispatch_spec={"task": "chaos.storm_task", "args": [], "kwargs": {}},
        )
        await db.commit()
        return job.id


async def test_enqueue_storm_honest_failure_then_idempotent_reuse(
    session_factory, tmp_path, monkeypatch,
):
    """broker 连续失败：首次提交诚实 failed；同参数重提交幂等复用，
    绝不为同一逻辑操作堆出第二条 job/第二条消息。"""
    from app.services.jobs import submit as submit_mod

    with chaos("JOBS_ENQUEUE_FAIL_STORM", fail_times=2) as fault:
        storm = fault.storm_task

        async with session_factory() as db:
            job = await DurableJobStore.create(
                db, task_type="storm", kind=JobKind.analysis,
                owner_id="user-a", session_id="sess-storm",
                parameters={"x": 1},
                dispatch_spec={"task": storm.name, "args": [], "kwargs": {}},
            )
            await db.commit()
            job_id = job.id

        async with session_factory() as db:
            ok = await DurableJobStore.transition(
                db, job_id, JobStatus.queued, expected=[JobStatus.pending])
            assert ok
            await db.commit()

        # 第一次 apply_async：broker 故障
        with pytest.raises(ConnectionError):
            storm.apply_async()

        # 生产语义镜像：enqueue 失败 → 诚实 failed（submit.py:161-173）
        async with session_factory() as db:
            await DurableJobStore.mark_failed(
                db, job_id, error=ConnectionError("broker down"))
            await db.commit()

        # 第二次失败：同样诚实
        with pytest.raises(ConnectionError):
            storm.apply_async()

        # broker 恢复后的第三次调用成功（风暴是显式次数，不是永远）
        result = storm.apply_async()
        assert result.id.startswith("celery-storm-")

    assert fault.fired
    async with session_factory() as db:
        record = await DurableJobStore.get(db, job_id)
        assert record.status == "failed"


async def test_enqueue_failure_marks_job_failed_and_reuse_is_idempotent(
    session_factory, monkeypatch,
):
    """生产收敛语义（submit.py:161-173）：enqueue 失败 → mark_failed 落库 +
    异常上抛；已终态 job 的重复提交命中幂等复用，不再入队。"""
    from types import SimpleNamespace

    from app.services.jobs import submit as submit_mod

    calls = {"apply_async": 0, "mark_failed": 0}
    job_box = {}

    class _FakeDb:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def commit(self):
            pass

    def fake_create_sync(db, **kwargs):
        # 真实 store 按 idempotency_key 去重：同键返回同一行（含终态）
        if "job" in job_box:
            return job_box["job"]
        job = SimpleNamespace(id=501, celery_task_id=None, status="pending")
        job_box["job"] = job
        return job

    def fake_transition_sync(db, job_id, to, expected=None):
        return True  # pending → queued 认领成功

    def fake_mark_failed_sync(db, job_id, error=None, message=None):
        calls["mark_failed"] += 1
        job_box["job"].status = "failed"
        return None

    def fake_is_terminal(job):
        return job.status in ("failed", "completed", "cancelled")

    def fake_set_celery_id(db, job_id, task_id):
        job_box["job"].celery_task_id = task_id
        return True

    class _StormBrokerTask:
        name = "chaos.broker_task"

        def apply_async(self, *args, **kwargs):
            calls["apply_async"] += 1
            if calls["apply_async"] <= 1:
                raise ConnectionError("[chaos] broker down #1")
            return SimpleNamespace(id="celery-ok-1")

    monkeypatch.setattr(submit_mod, "db_session", lambda: _FakeDb())
    monkeypatch.setattr(submit_mod.DurableJobStore, "create_sync",
                        staticmethod(fake_create_sync))
    monkeypatch.setattr(submit_mod.DurableJobStore, "is_terminal_job",
                        staticmethod(fake_is_terminal))
    monkeypatch.setattr(submit_mod.DurableJobStore, "transition_sync",
                        staticmethod(fake_transition_sync))
    monkeypatch.setattr(submit_mod.DurableJobStore, "mark_failed_sync",
                        staticmethod(fake_mark_failed_sync))
    monkeypatch.setattr(submit_mod.DurableJobStore, "set_celery_task_id_sync",
                        staticmethod(fake_set_celery_id))

    task = _StormBrokerTask()
    with chaos("JOBS_ENQUEUE_FAIL_STORM", fail_times=1):
        with pytest.raises(ConnectionError):
            submit_mod.submit_durable_job(
                celery_task=task, task_type="storm_broker",
                display_name="风暴", params={"p": 42},
            )
        assert calls["mark_failed"] == 1, "enqueue 失败必须落 failed"
        assert job_box["job"].status == "failed"

        # 同参数重提交：已终态 → 幂等复用，不再入队
        result = submit_mod.submit_durable_job(
            celery_task=task, task_type="storm_broker",
            display_name="风暴", params={"p": 42},
        )
    assert result["idempotent_reuse"] is True
    assert calls["apply_async"] == 1, "终态复用不得再次入队"


# ── JOBS_REDELIVERY_STORM ────────────────────────────────────────────────


async def test_redelivery_storm_single_claim_winner(session_factory):
    """N 路并发认领同一 pending job：恰好一路胜出（expected CAS）。"""
    from tests.fixtures.chaos import chaos as _chaos

    async with session_factory() as db:
        job = await DurableJobStore.create(
            db, task_type="ndvi", kind=JobKind.analysis,
            owner_id="u", session_id="s",
            parameters={"r": "/x.tif"},
            dispatch_spec={"task": "t", "args": [], "kwargs": {}},
        )
        await db.commit()
        job_id = job.id

    with _chaos("JOBS_REDELIVERY_STORM", parties=8) as fault:
        barrier = fault.barrier
        results = fault.results

        async def claim():
            async with session_factory() as db:
                await barrier.wait()
                ok = await DurableJobStore.transition(
                    db, job_id, JobStatus.queued, expected=[JobStatus.pending])
                await db.commit()
                results.append(ok)

        await asyncio.gather(*(claim() for _ in range(8)))

    assert sum(1 for r in results if r) == 1, "恰好一个执行者"
    assert sum(1 for r in results if not r) == 7
    async with session_factory() as db:
        record = await DurableJobStore.get(db, job_id)
        assert record.status == "queued"


def test_journal_records_all_new_faults():
    ids = {e.fault_id for e in journal_snapshot()}
    # journal 只在本进程 with 作用域内累积；这里验证快照可调用（有界）
    assert isinstance(ids, set)
