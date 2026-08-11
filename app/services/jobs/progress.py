"""统一进度语义与写入节流（规范 §19 / §20）。

原状态：Celery 侧是 ``update_state(PROGRESS, {'progress': n})``（只存在于 Redis
result backend），Agent 侧是 step_start/step_result 事件，两者没有共同契约，前端
也拿不到统一百分比。

统一契约（JobProgress）：

    phase          当前阶段名（可空），如 "reproject" / "compute" / "finalize"
    progress       0–100 整数，或 None 表示 indeterminate（不确定进度）
    message        人类可读短消息
    current_step   已完成步数（可空）
    total_steps    总步数（可空）

允许 ``progress=None``：不是所有 job 都能算出真实百分比，假装 99% 然后卡十分钟
比 indeterminate 更糟（规范 §19 明令禁止）。

节流：大循环不得每个 feature 写一次 DB。``ProgressThrottle`` 只在「进度变化 ≥1%」
或「距上次写入 ≥500ms」时放行，终态与首次上报无条件放行 —— 保证 DB 写入速率有上界
（规范 §20，基准见 tests/benchmarks/test_job_runtime_perf.py）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

#: 默认节流阈值。1% / 500ms 是规范给的起点；基准测试确认在 100k 次上报下
#: DB 写入次数被压到两位数。
DEFAULT_MIN_DELTA_PCT = 1.0
DEFAULT_MIN_INTERVAL_S = 0.5


@dataclass(frozen=True)
class JobProgress:
    """统一进度快照。"""

    progress: Optional[int] = None
    message: Optional[str] = None
    phase: Optional[str] = None
    current_step: Optional[int] = None
    total_steps: Optional[int] = None

    @property
    def indeterminate(self) -> bool:
        return self.progress is None

    def clamped(self) -> "JobProgress":
        """把 progress 收敛到 [0, 100]，满足 ck_task_progress CHECK 约束。"""
        if self.progress is None:
            return self
        value = int(self.progress)
        if value < 0:
            value = 0
        elif value > 100:
            value = 100
        if value == self.progress:
            return self
        return JobProgress(
            progress=value,
            message=self.message,
            phase=self.phase,
            current_step=self.current_step,
            total_steps=self.total_steps,
        )

    def as_message(self) -> Optional[str]:
        """合成落库用的 progress_message（analysis_tasks.progress_message 为 String(255)）。"""
        parts: list[str] = []
        if self.phase:
            parts.append(f"[{self.phase}]")
        if self.message:
            parts.append(self.message)
        if self.current_step is not None and self.total_steps:
            parts.append(f"({self.current_step}/{self.total_steps})")
        text = " ".join(parts).strip()
        if not text:
            return None
        return text[:255]


class ProgressThrottle:
    """判断一次进度上报是否应该落库。

    有状态、非线程安全 —— 每个 job 执行体持有自己的实例（执行体本身是单线程的）。
    """

    __slots__ = ("_min_delta", "_min_interval", "_last_pct", "_last_at", "_emitted", "_clock")

    def __init__(
        self,
        min_delta_pct: float = DEFAULT_MIN_DELTA_PCT,
        min_interval_s: float = DEFAULT_MIN_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._min_delta = float(min_delta_pct)
        self._min_interval = float(min_interval_s)
        self._last_pct: float | None = None
        self._last_at: float | None = None
        self._emitted = 0
        self._clock = clock

    @property
    def emitted(self) -> int:
        """已放行次数。基准测试用它断言 DB 写入速率有界。"""
        return self._emitted

    def should_emit(self, progress: int | None, *, force: bool = False) -> bool:
        now = self._clock()

        if force:
            self._record(progress, now)
            return True

        # 首次上报总是放行 —— 让前端立刻看到 job 已开始推进
        if self._last_at is None:
            self._record(progress, now)
            return True

        # indeterminate 进度只能靠时间节流
        if progress is None:
            if now - self._last_at >= self._min_interval:
                self._record(progress, now)
                return True
            return False

        # 100% 是有意义的边界，总是放行
        if progress >= 100:
            self._record(progress, now)
            return True

        if self._last_pct is not None and abs(float(progress) - self._last_pct) >= self._min_delta:
            self._record(progress, now)
            return True

        if now - self._last_at >= self._min_interval:
            self._record(progress, now)
            return True

        return False

    def _record(self, progress: int | None, now: float) -> None:
        if progress is not None:
            self._last_pct = float(progress)
        self._last_at = now
        self._emitted += 1

    def reset(self) -> None:
        self._last_pct = None
        self._last_at = None
        self._emitted = 0


class ProgressReporter:
    """把节流后的进度交给一个 sink 回调。

    sink 由调用方决定：worker 侧写 DB + Celery ``update_state``，agent 侧发 SSE。
    Reporter 本身不关心目的地，只保证「速率有界」和「终态必达」。
    """

    __slots__ = ("_sink", "_throttle", "_last")

    def __init__(
        self,
        sink: Callable[[JobProgress], None],
        throttle: ProgressThrottle | None = None,
    ):
        self._sink = sink
        self._throttle = throttle or ProgressThrottle()
        self._last: JobProgress | None = None

    @property
    def throttle(self) -> ProgressThrottle:
        return self._throttle

    @property
    def last(self) -> JobProgress | None:
        return self._last

    def report(
        self,
        progress: int | None = None,
        message: str | None = None,
        *,
        phase: str | None = None,
        current_step: int | None = None,
        total_steps: int | None = None,
        force: bool = False,
    ) -> bool:
        """上报进度。返回是否真的落到了 sink。"""
        snapshot = JobProgress(
            progress=progress,
            message=message,
            phase=phase,
            current_step=current_step,
            total_steps=total_steps,
        ).clamped()

        if not self._throttle.should_emit(snapshot.progress, force=force):
            return False

        self._last = snapshot
        self._sink(snapshot)
        return True

    def flush(self, progress: int | None = None, message: str | None = None, **kwargs) -> bool:
        """无条件上报（终态/阶段边界用）。"""
        return self.report(progress=progress, message=message, force=True, **kwargs)
