"""Worker 侧 durable job 运行时（规范 §12 / §17 / §23 / §24 / §25）。

一个 Celery 任务体包起来长这样：

    @celery_app.task(bind=True, name="...")
    def run_ndvi_analysis(self, raster_path, ...):
        with durable_job(self, job_id, task_type="ndvi") as job:
            for i, window in enumerate(windows):
                job.checkpoint()                     # 协作式取消
                job.progress(i * 100 // n, "计算中")  # 节流后落库
                ...
            with job.artifact(out_path) as tmp:      # 原子 finalize
                write_geotiff(tmp)
            return {...}

它负责的事：
  * 取消：DB 里的 ``cancel_requested_at`` 是持久事实源，探针按节流周期轮询它并驱动
    一个 CancellationToken；token 通过 contextvar 传给下游 GIS 代码，
    ``checkpoint()`` 在安全边界抛 OperationCancelled。**先协作、后 revoke、
    hard terminate 只作为有界最后手段** —— 不再拿 terminate=True 当唯一取消手段。
  * 进度：ProgressReporter 节流写库，DB 写入速率有上界。
  * 心跳：进度写入顺带刷新 heartbeat_at；长时间无进度也按周期刷新。stale 清扫据此
    识别挂掉的 worker，避免永远 running。
  * 终态：正常返回 → completed；OperationCancelled → cancelled；异常 → failed，
    错误只落单行摘要。已 cancelling 时的 late success 收敛为 cancelled（§14）。
  * 重复执行防护：acks_late 下 broker 可能重投消息；进入时若 job 已是终态则直接
    跳过执行（返回 AlreadyFinished），不会重复 finalize 产物（§16）。
  * 产物：``job.artifact(path)`` 写 ``path.part-<uuid>`` 再 os.replace，成功才可见；
    取消/失败时删除临时文件，绝不留下半个 GeoTIFF 或 status=ready 的损坏资产。
"""
from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Optional

from app.services.jobs.artifacts import atomic_output
from app.services.jobs.cancellation import (
    CancellationToken,
    OperationCancelled,
    use_token,
)
from app.services.jobs.lifecycle import JobStatus, coerce_status, is_terminal
from app.services.jobs.progress import JobProgress, ProgressReporter, ProgressThrottle
from app.services.jobs.store import DurableJobStore

logger = logging.getLogger(__name__)

#: 取消探针轮询周期。500ms 足够让用户感知「点了就停」，又不会把 DB 打满。
CANCEL_POLL_INTERVAL_S = 0.5
#: 无进度时的心跳周期。远小于 DEFAULT_STALE_AFTER_S(300s)。
HEARTBEAT_INTERVAL_S = 30.0


class AlreadyFinished(Exception):
    """job 已处于终态，本次投递应被忽略（重复投递防护）。"""


@dataclass
class _JobRuntimeState:
    observed_cancel_at: Optional[float] = None
    last_heartbeat_at: float = 0.0
    checkpoints: int = 0


class DurableJobHandle:
    """暴露给任务体的运行时句柄。"""

    def __init__(
        self,
        job_id: str | int,
        token: CancellationToken,
        reporter: ProgressReporter,
        *,
        cancel_probe: Callable[[], bool] | None = None,
        state: _JobRuntimeState | None = None,
        watchdog: Any = None,
    ):
        self.job_id = str(job_id)
        self.token = token
        self._reporter = reporter
        self._cancel_probe = cancel_probe
        self._state = state or _JobRuntimeState()
        self._temp_paths: set[str] = set()
        #: 看门狗（可选，测试可为 None）。暴露出来便于断言 DB 轮询/心跳次数。
        self.watchdog = watchdog

    # ── 取消 ────────────────────────────────────────────────────────

    @property
    def cancelled(self) -> bool:
        return self.token.cancelled

    def poll_cancel(self) -> bool:
        """主动拉一次持久取消事实（受探针内部节流约束）。"""
        if self._cancel_probe is not None:
            return self._cancel_probe()
        return self.token.cancelled

    def checkpoint(self) -> None:
        """安全边界取消检查点。已取消则抛 OperationCancelled。

        这里**不**主动读 DB —— 看门狗线程已经在按周期推送取消事实，checkpoint 只需
        读一次内存标志。因此检查点可以撒得很密（每个 chunk），开销依然可忽略。
        """
        self._state.checkpoints += 1
        if self.token.cancelled:
            if self._state.observed_cancel_at is None:
                self._state.observed_cancel_at = time.monotonic()
            raise OperationCancelled(self.token.reason or "job cancelled", job_id=self.job_id)

    def ensure_not_cancelled(self) -> None:
        """**强制**读一次持久取消事实后再检查 —— 用于不可逆副作用之前。

        ``checkpoint()`` 只读内存 token（便宜、可密集调用），代价是取消可能还没被
        看门狗推过来。所以在「注册资产 / 提交产物 / 写不可逆记录」这类点上必须用
        本方法：它同步查一次 DB，确保不会给一个已被取消的 job 留下副作用
        （规范 §23 —— cancelled 的任务不得留下 status=ready 的资产）。

        每个 job 只会在少数几个关键点调用，DB 开销可忽略。
        """
        if self._cancel_probe is not None:
            self._cancel_probe()
        self._state.checkpoints += 1
        if self.token.cancelled:
            if self._state.observed_cancel_at is None:
                self._state.observed_cancel_at = time.monotonic()
            raise OperationCancelled(self.token.reason or "job cancelled", job_id=self.job_id)

    @property
    def checkpoints(self) -> int:
        """已执行的检查点次数。取消延迟测试用它断言「取消后不再跑后续 chunk」。"""
        return self._state.checkpoints

    def chunks(self, iterable, every: int = 1) -> Iterator:
        """把循环变成可取消循环。"""
        if every < 1:
            every = 1
        for index, item in enumerate(iterable):
            if index % every == 0:
                self.checkpoint()
            yield item

    # ── 进度 ────────────────────────────────────────────────────────

    def progress(
        self,
        value: int | None = None,
        message: str | None = None,
        *,
        phase: str | None = None,
        current_step: int | None = None,
        total_steps: int | None = None,
        force: bool = False,
    ) -> bool:
        return self._reporter.report(
            progress=value,
            message=message,
            phase=phase,
            current_step=current_step,
            total_steps=total_steps,
            force=force,
        )

    @property
    def progress_writes(self) -> int:
        """实际落库的进度写入次数。基准测试用它断言写入速率有界。"""
        return self._reporter.throttle.emitted

    # ── 产物 ────────────────────────────────────────────────────────

    @contextlib.contextmanager
    def artifact(self, final_path: str) -> Iterator[str]:
        """原子产物提交：temporary output → 成功 → os.replace → ready。

        失败/取消时删除临时文件，绝不把半成品暴露为最终路径。已存在的成功产物
        不会被删除（清理只针对本次的 .part 文件）。
        """
        # should_abort: 取消可能在计算刚结束、finalize 之前到达 —— 取消优先
        with atomic_output(final_path, should_abort=lambda: self.token.cancelled) as temp_path:
            self._temp_paths.add(temp_path)
            try:
                yield temp_path
            except BaseException:
                self._temp_paths.discard(temp_path)
                raise
        self._temp_paths.discard(temp_path)

    def track_temp(self, path: str) -> str:
        """登记一个由任务体自行创建的临时路径，交给运行时兜底清理。"""
        self._temp_paths.add(path)
        return path

    def _discard(self, path: str) -> None:
        self._temp_paths.discard(path)
        with contextlib.suppress(FileNotFoundError, OSError):
            if os.path.isfile(path):
                os.unlink(path)

    def cleanup_temps(self) -> None:
        """清理所有尚未 finalize 的临时文件（finally 路径调用）。"""
        for path in list(self._temp_paths):
            self._discard(path)


class _CancelWatchdog:
    """后台看门狗线程：把「持久取消事实」**推**给 token，并独立维持心跳。

    为什么必须推而不是拉：探针若只在任务体调用 ``job.checkpoint()`` 时才读 DB，那么
    一段不可中断的 numpy/rasterio 调用期间（NDVI 就是一次 ``np.divide``）没人会去看
    DB —— 用户点了取消，计算仍然跑到自然结束，CPU 根本没被释放。那正是 ADR-0052
    要消除的「取消只是 UI 状态」。

    看门狗按固定周期轮询 DB 并点燃 token，于是散布在 GIS 库里的模块级
    ``jobs.checkpoint()``（它只读 contextvar 里的 token，不碰 DB）真正生效 ——
    栅格窗口循环、要素批循环都能在下一个检查点退出。

    它同时按 ``HEARTBEAT_INTERVAL_S`` 刷新 heartbeat_at：心跳不能依赖进度上报，
    否则一个长时间进度不变的 job 会被 stale 清扫误判成「worker 已死」，跑完的结果
    反而被丢弃。
    """

    def __init__(
        self,
        job_id: str | int,
        token: CancellationToken,
        session_factory: Callable[[], Any],
        *,
        poll_interval: float = CANCEL_POLL_INTERVAL_S,
        heartbeat_interval: float = HEARTBEAT_INTERVAL_S,
    ):
        self._job_id = job_id
        self._token = token
        self._session_factory = session_factory
        self._poll_interval = max(0.01, poll_interval)
        self._heartbeat_interval = heartbeat_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.db_polls = 0
        self.heartbeats = 0

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name=f"job-watchdog-{self._job_id}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def poll_once(self) -> bool:
        """立即读一次持久取消事实并在命中时点燃 token。返回是否已取消。"""
        if self._token.cancelled:
            return True
        try:
            with self._session_factory() as db:
                requested = DurableJobStore.is_cancel_requested_sync(db, self._job_id)
            self.db_polls += 1
        except Exception:
            logger.warning("[jobs] cancel probe failed job_id=%s", self._job_id, exc_info=True)
            return False
        if requested:
            self._token.cancel("cancel requested (durable)")
            logger.info("[jobs] cancel observed by worker job_id=%s", self._job_id)
            return True
        return False

    def _heartbeat(self) -> None:
        try:
            with self._session_factory() as db:
                DurableJobStore.heartbeat_sync(db, self._job_id)
                db.commit()
            self.heartbeats += 1
        except Exception:
            logger.warning("[jobs] heartbeat failed job_id=%s", self._job_id, exc_info=True)

    def _run(self) -> None:
        last_heartbeat = time.monotonic()
        while not self._stop.wait(self._poll_interval):
            if self.poll_once():
                return  # token 已点燃，无需继续轮询
            now = time.monotonic()
            if now - last_heartbeat >= self._heartbeat_interval:
                last_heartbeat = now
                self._heartbeat()


def _default_session_factory():
    from app.tools._utils import db_session

    return db_session()


@contextlib.contextmanager
def durable_job(
    job_id: str | int,
    *,
    worker_id: Optional[str] = None,
    session_factory: Callable[[], Any] | None = None,
    progress_throttle: ProgressThrottle | None = None,
    cancel_interval: float = CANCEL_POLL_INTERVAL_S,
    celery_task: Any = None,
) -> Iterator[DurableJobHandle]:
    """durable job 执行上下文。

    Args:
        job_id: durable job（analysis_tasks.id）。
        worker_id: 执行者标识，默认 hostname:pid。
        session_factory: 返回同步 Session 的上下文管理器工厂，默认 ``db_session``。
        progress_throttle: 覆盖默认 1%/500ms 节流（测试用）。
        cancel_interval: 取消探针轮询周期。
        celery_task: bind=True 的 Celery task 实例；给了就同步 update_state，
            保持既有 ``/tasks/status/{celery_task_id}`` 端点的进度语义不变。

    Raises:
        AlreadyFinished: job 已终态（重复投递），任务体不应执行。
    """
    factory = session_factory or _default_session_factory
    token = CancellationToken(job_id=str(job_id))
    state = _JobRuntimeState(last_heartbeat_at=time.monotonic())

    # 入口守卫：重复投递 / 已被取消的 job 不执行任务体。
    # pending|queued → running 的迁移本身就是「认领」动作：它是条件更新，所以
    # 两个 worker 拿到同一条消息时只有一个能成功，另一个 rowcount=0 → 直接放弃。
    # 这挡住了 acks_late 下 broker 重投导致的重复执行（规范 §16）。
    with factory() as db:
        record = DurableJobStore.get_sync(db, job_id)
        if record is None:
            raise AlreadyFinished(f"job {job_id} not found")
        status = coerce_status(record.status)
        if is_terminal(status):
            logger.info("[jobs] skip duplicate delivery job_id=%s status=%s", job_id, status.value)
            raise AlreadyFinished(f"job {job_id} already {status.value}")
        if status == JobStatus.cancelling or record.cancel_requested_at is not None:
            DurableJobStore.confirm_cancelled_sync(db, job_id, message="cancelled before execution")
            db.commit()
            raise AlreadyFinished(f"job {job_id} cancelled before execution")
        claimed = DurableJobStore.mark_running_sync(db, job_id, worker_id=worker_id)
        db.commit()
    if not claimed:
        # 认领失败：别人已经在跑（running），或取消刚好插进来。绝不重复执行。
        with factory() as db:
            current = coerce_status(
                getattr(DurableJobStore.get_sync(db, job_id), "status", None)
            )
        logger.info("[jobs] claim failed job_id=%s status=%s", job_id, current.value)
        raise AlreadyFinished(f"job {job_id} could not be claimed (status={current.value})")

    watchdog = _CancelWatchdog(job_id, token, factory, poll_interval=cancel_interval)

    def _sink(snapshot: JobProgress) -> None:
        try:
            with factory() as db:
                DurableJobStore.update_progress_sync(db, job_id, snapshot)
                db.commit()
        except Exception:
            logger.warning("[jobs] progress write failed job_id=%s", job_id, exc_info=True)
        state.last_heartbeat_at = time.monotonic()
        if celery_task is not None:
            with contextlib.suppress(Exception):
                celery_task.update_state(
                    state="PROGRESS",
                    meta={
                        "progress": snapshot.progress if snapshot.progress is not None else 0,
                        "message": snapshot.as_message() or "",
                        "job_id": str(job_id),
                    },
                )

    reporter = ProgressReporter(_sink, throttle=progress_throttle or ProgressThrottle())
    handle = DurableJobHandle(
        job_id, token, reporter, cancel_probe=watchdog.poll_once, state=state, watchdog=watchdog
    )

    logger.info("[jobs] started job_id=%s worker=%s", job_id, worker_id or "-")
    watchdog.start()
    try:
        with use_token(token):
            yield handle
    except OperationCancelled:
        handle.cleanup_temps()
        with factory() as db:
            DurableJobStore.confirm_cancelled_sync(db, job_id, message="cancelled by user")
            db.commit()
        latency = None
        if token.cancelled_at is not None and state.observed_cancel_at is not None:
            latency = state.observed_cancel_at - token.cancelled_at
        logger.info(
            "[jobs] cancelled job_id=%s checkpoints=%s observe_latency_s=%s",
            job_id, state.checkpoints, (f"{latency:.3f}" if latency is not None else "-"),
        )
        raise
    except BaseException as exc:
        handle.cleanup_temps()
        # 取消期间抛出的其它异常（例如底层库把取消翻译成自己的异常）按取消收敛
        if token.cancelled:
            with factory() as db:
                DurableJobStore.confirm_cancelled_sync(db, job_id, message="cancelled by user")
                db.commit()
        else:
            with factory() as db:
                # cancelling → failed 也是合法迁移；若此刻已被取消确认，rowcount=0
                # 且状态保持 cancelled（终态不被覆盖）。
                DurableJobStore.mark_failed_sync(db, job_id, error=exc)
                db.commit()
            logger.warning("[jobs] failed job_id=%s error=%s", job_id, type(exc).__name__)
        raise
    else:
        handle.cleanup_temps()
    finally:
        # 看门狗是守护线程，但必须在任务体结束时立刻停 —— 否则每个 job 留一个线程
        # 持续轮询 DB，长跑 worker 会累积成连接池压力。
        watchdog.stop()


def finish_job(
    job_id: str | int,
    *,
    result: Any = None,
    result_ref: Optional[str] = None,
    session_factory: Callable[[], Any] | None = None,
) -> JobStatus:
    """把 job 落成成功终态，返回实际终态。

    与 ``durable_job`` 分开是因为：任务体正常返回后才知道结果，而 ``cancelling``
    期间的 late success 必须被丢弃 —— 该判定在 store.mark_succeeded 里做。
    """
    factory = session_factory or _default_session_factory
    with factory() as db:
        status = DurableJobStore.mark_succeeded_sync(db, job_id, result=result, result_ref=result_ref)
        db.commit()
    return status
