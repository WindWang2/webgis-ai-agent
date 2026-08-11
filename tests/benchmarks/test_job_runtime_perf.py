"""Durable job runtime 的确定性性能基准（ADR-0052，规范 §37）。

这些不是墙钟计时基准 —— 它们断言**行为量**，因此在任何机器上结果一致：

  cancelled_chunked_cpu_work   取消后实际执行的 chunk 数（证明 CPU 真的释放了）
  progress_write_rate          10 万次上报对应的 DB 写入次数（写入速率有上界）
  task_summary_query           1000 条 job 的任务中心查询（单查询、有界返回）
  running_job_poll_payload     轮询响应体大小（前端兜底轮询的带宽成本）
  job_transition_contention    高并发状态迁移下的胜出者数量（正好 1）

跑：pytest -m perf tests/benchmarks/test_job_runtime_perf.py
"""
import asyncio
import json
import time

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.db_model import Base
from app.services.jobs import (
    DurableJobStore,
    JobProgress,
    JobStatus,
    ProgressThrottle,
    coerce_status,
)
from app.services.jobs.cancellation import (
    CancellationToken,
    OperationCancelled,
    cancellable,
    use_token,
)
from app.services.jobs.views import build_list_response, job_from_record

pytestmark = pytest.mark.perf


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'perf.db'}",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(bind=engine, expire_on_commit=False)
    await engine.dispose()


# ── 1. cancelled_chunked_cpu_work ──────────────────────────────────


def test_bench_cancelled_chunked_cpu_work():
    """取消后必须**不再**执行后续 chunk —— 这是「取消不只是 UI 状态」的量化证据。

    before（ADR-0052 之前）：取消只翻内存 bool，工具内部无检查点，
                              10_000 个 chunk 全部跑完。
    after ：第 50 个 chunk 后取消 → 实际执行 ≤ 52。
    """
    total_chunks = 10_000
    cancel_after = 50
    token = CancellationToken("bench")
    executed = 0

    with use_token(token):
        with pytest.raises(OperationCancelled):
            for i in cancellable(range(total_chunks)):
                executed += 1
                # 每个 chunk 模拟一点真实计算
                sum(x * x for x in range(50))
                if i == cancel_after:
                    token.cancel("user cancelled")

    assert executed <= cancel_after + 2, executed
    saved_ratio = 1 - executed / total_chunks
    assert saved_ratio > 0.99, f"仅省下 {saved_ratio:.2%} 的计算"
    print(f"\n[bench] cancelled_chunked_cpu_work: executed={executed}/{total_chunks} "
          f"saved={saved_ratio:.2%}")


def test_bench_checkpoint_overhead_is_negligible_without_token():
    """未绑定 token 时 checkpoint 必须近乎零成本 —— 普通请求路径也会经过这些循环。

    断言的是「相对开销有上界」而不是绝对耗时，避免机器差异导致 flaky。
    """
    n = 200_000

    start = time.perf_counter()
    for _ in range(n):
        pass
    baseline = time.perf_counter() - start

    start = time.perf_counter()
    for _ in cancellable(range(n)):
        pass
    instrumented = time.perf_counter() - start

    overhead_per_iter_us = (instrumented - baseline) / n * 1e6
    assert overhead_per_iter_us < 5.0, f"{overhead_per_iter_us:.3f} µs/iter"
    print(f"\n[bench] checkpoint overhead: {overhead_per_iter_us:.3f} µs/iter")


# ── 2. progress_write_rate ─────────────────────────────────────────


def test_bench_progress_write_rate_is_bounded():
    """规范 §20：10 万次进度上报的 DB 写入次数必须是两位数。

    before：每次迭代一次 update_state / DB 写 → 100_000 次写。
    after ：1% / 500ms 节流 → ≈101 次。
    """
    reports = 100_000
    now = [0.0]
    throttle = ProgressThrottle(min_delta_pct=1.0, min_interval_s=0.5, clock=lambda: now[0])

    for i in range(reports):
        now[0] += 0.0001  # 模拟 10s 总时长
        throttle.should_emit(i * 100 // reports)

    assert throttle.emitted <= 120, throttle.emitted
    print(f"\n[bench] progress_write_rate: {throttle.emitted} writes / {reports} reports "
          f"({throttle.emitted / reports:.4%})")


@pytest.mark.asyncio
async def test_bench_progress_writes_never_touch_terminal_jobs(session_factory):
    """终态 job 上的进度写入必须 0 命中 —— 否则 stale progress 会复活终态。"""
    async with session_factory() as db:
        job = await DurableJobStore.create(db, task_type="bench", owner_id="u", parameters={})
        await db.commit()
        await DurableJobStore.mark_queued(db, job.id)
        await DurableJobStore.mark_running(db, job.id)
        await DurableJobStore.mark_succeeded(db, job.id, result={})
        await db.commit()

        hits = 0
        for i in range(200):
            if await DurableJobStore.update_progress(db, job.id, JobProgress(progress=i % 100)):
                hits += 1
        await db.commit()

    assert hits == 0
    print("\n[bench] terminal-job progress writes: 0/200 (as required)")


# ── 3. task_summary_query ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_bench_1000_task_summary_query(session_factory):
    """1000 条 job 下的任务中心查询：单次查询、返回条数有界。

    重点不是毫秒数，而是「不会随任务总量线性膨胀返回体」。
    """
    total = 1000
    async with session_factory() as db:
        for i in range(total):
            await DurableJobStore.create(
                db,
                task_type="bench",
                owner_id="user-a",
                session_id="sess-a",
                display_name=f"job {i}",
                parameters={"i": i},
            )
        await db.commit()

    async with session_factory() as db:
        start = time.perf_counter()
        rows = await DurableJobStore.list_for_owner(db, owner_id="user-a", limit=50)
        elapsed_ms = (time.perf_counter() - start) * 1000

    assert len(rows) == 50, "返回条数必须被 limit 钳住"
    print(f"\n[bench] 1000_task_summary_query: {elapsed_ms:.1f} ms for 50/{total} rows")


@pytest.mark.asyncio
async def test_bench_list_limit_cannot_be_raised_by_caller(session_factory):
    """调用方不能通过超大 limit 拉全表 —— MAX_LIST_LIMIT 是硬上界。"""
    async with session_factory() as db:
        for i in range(300):
            await DurableJobStore.create(
                db, task_type="bench", owner_id="user-a", parameters={"i": i}
            )
        await db.commit()

    async with session_factory() as db:
        rows = await DurableJobStore.list_for_owner(db, owner_id="user-a", limit=100_000)
    assert len(rows) <= 200
    print(f"\n[bench] list hard cap: requested 100000, got {len(rows)}")


# ── 4. running_job_poll_payload ────────────────────────────────────


@pytest.mark.asyncio
async def test_bench_running_job_poll_payload_size(session_factory):
    """轮询响应体必须小 —— 前端每 3s 一次，体积直接决定带宽成本。

    同时验证巨型参数/结果不会被塞进响应（规范 §38）。
    """
    async with session_factory() as db:
        for i in range(20):
            job = await DurableJobStore.create(
                db,
                task_type="ndvi",
                owner_id="user-a",
                session_id="sess-a",
                display_name=f"NDVI 分析 {i}",
                parameters={
                    "features": [{"geometry": {"coordinates": [[1, 2]] * 100}}] * 5000,
                    "token": "secret",
                },
            )
            await db.commit()
            await DurableJobStore.mark_queued(db, job.id)
            await DurableJobStore.mark_running(db, job.id)
            await DurableJobStore.update_progress(
                db, job.id, JobProgress(progress=42, message="计算中", phase="compute")
            )
        await db.commit()

        rows = await DurableJobStore.list_for_owner(db, owner_id="user-a", active_only=True)

    response = build_list_response([job_from_record(r) for r in rows])
    payload = response.model_dump_json()
    size = len(payload.encode("utf-8"))

    assert response.has_active is True
    assert size < 20_000, f"20 个活跃 job 的轮询响应 {size} 字节，过大"
    assert "secret" not in payload
    assert "coordinates" not in payload
    print(f"\n[bench] running_job_poll_payload: {size} bytes for {len(rows)} active jobs "
          f"({size / max(1, len(rows)):.0f} B/job)")


# ── 5. job_transition_contention ───────────────────────────────────


@pytest.mark.asyncio
async def test_bench_job_transition_contention(session_factory):
    """N 个并发写入者争抢同一个终态迁移，胜出者必须正好 1 个。

    这是「cancelled → completed」和「重复 finalize」两类缺陷的量化闸门。
    """
    writers = 32
    async with session_factory() as db:
        job = await DurableJobStore.create(db, task_type="bench", owner_id="u", parameters={})
        await db.commit()
        await DurableJobStore.mark_queued(db, job.id)
        await DurableJobStore.mark_running(db, job.id)
        await db.commit()
        job_id = job.id

    ready = asyncio.Event()

    async def contend(index: int) -> bool:
        async with session_factory() as db:
            await ready.wait()
            won = await DurableJobStore.transition(
                db, job_id, JobStatus.completed, progress=100, result_summary={"w": index}
            )
            await db.commit()
            return won

    tasks = [asyncio.create_task(contend(i)) for i in range(writers)]
    start = time.perf_counter()
    ready.set()
    results = await asyncio.gather(*tasks)
    elapsed_ms = (time.perf_counter() - start) * 1000

    winners = results.count(True)
    assert winners == 1, f"{writers} 个并发写入者中有 {winners} 个胜出（应为 1）"

    async with session_factory() as db:
        fresh = await DurableJobStore.get(db, job_id)
        assert coerce_status(fresh.status) is JobStatus.completed
    print(f"\n[bench] job_transition_contention: {winners}/{writers} winner in {elapsed_ms:.1f} ms")


@pytest.mark.asyncio
async def test_bench_cancel_vs_complete_contention(session_factory):
    """取消与完成对撞：终态必须唯一且确定，绝不留在 cancelling 悬挂态。"""
    rounds = 16
    outcomes: dict[str, int] = {}

    for r in range(rounds):
        async with session_factory() as db:
            job = await DurableJobStore.create(
                db, task_type="bench", owner_id="u", parameters={"r": r}
            )
            await db.commit()
            await DurableJobStore.mark_queued(db, job.id)
            await DurableJobStore.mark_running(db, job.id)
            await db.commit()
            job_id = job.id

        ready = asyncio.Event()

        async def do_cancel():
            async with session_factory() as db:
                await ready.wait()
                await DurableJobStore.request_cancel(db, job_id)
                await db.commit()

        async def do_complete():
            async with session_factory() as db:
                await ready.wait()
                await DurableJobStore.mark_succeeded(db, job_id, result={"ok": True})
                await db.commit()

        t1 = asyncio.create_task(do_cancel())
        t2 = asyncio.create_task(do_complete())
        ready.set()
        await asyncio.gather(t1, t2, return_exceptions=True)

        async with session_factory() as db:
            fresh = await DurableJobStore.get(db, job_id)
            final = coerce_status(fresh.status)
        outcomes[final.value] = outcomes.get(final.value, 0) + 1
        assert final in (JobStatus.completed, JobStatus.cancelled, JobStatus.cancelling), final

    # cancelling 是非终态：若对撞后停在这里，说明取消确认路径漏了
    hung = outcomes.get("cancelling", 0)
    assert hung == 0, f"{hung}/{rounds} 轮停在 cancelling 悬挂态"
    print(f"\n[bench] cancel_vs_complete over {rounds} rounds: {json.dumps(outcomes)}")
