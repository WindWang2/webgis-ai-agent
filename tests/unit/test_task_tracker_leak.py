"""Regression: TaskTracker must not accumulate abandoned running tasks or
session keys across long-lived / disconnected sessions.

A stream that dies mid-turn leaves its tasks in TaskStatus.running forever;
_evict_if_needed only evicts finished tasks, so once > MAX_TOTAL_TASKS running
tasks accumulate the tracker grows unboundedly (unbounded _session_tasks keys
too, since they're never removed). RED on the current code; green after the
oldest-task eviction + empty-session-key cleanup fix.
"""
from app.services.task_tracker import TaskTracker


def _accumulate_abandoned_sessions(tracker, n: int) -> None:
    """Simulate n sessions whose stream died mid-turn, leaving one running task each."""
    for i in range(n):
        tracker.create(f"session-{i}", f"request {i}")


def test_running_tasks_are_bounded_after_disconnect_accumulation():
    tracker = TaskTracker()
    _accumulate_abandoned_sessions(tracker, 600)

    # Generous slack matches the existing eviction margin (+50).
    assert len(tracker._tasks) <= TaskTracker.MAX_TOTAL_TASKS + 50, (
        f"{len(tracker._tasks)} tasks retained after 600 abandoned sessions"
    )
    # Session keys are bounded by the task bound (every key has >= 1 task id).
    assert len(tracker._session_tasks) <= TaskTracker.MAX_TOTAL_TASKS + 50, (
        f"{len(tracker._session_tasks)} session keys retained"
    )


def test_eviction_drops_empty_session_keys():
    tracker = TaskTracker()
    _accumulate_abandoned_sessions(tracker, 600)

    empty = [sid for sid, ids in tracker._session_tasks.items() if not ids]
    assert not empty, f"{len(empty)} empty session keys retained after eviction"


def test_finished_tasks_still_evicted_first():
    """Pre-existing behavior: finished tasks are evicted before running ones."""
    from app.services.task_tracker import TaskStatus

    tracker = TaskTracker()
    for i in range(TaskTracker.MAX_TOTAL_TASKS + 100):
        task = tracker.create(f"session-{i}", f"request {i}")
        task.status = TaskStatus.completed
    _accumulate_abandoned_sessions(tracker, 10)  # a few still-running tasks

    assert len(tracker._tasks) <= TaskTracker.MAX_TOTAL_TASKS + 50
    # The running tasks (last 10 created) must survive; only finished evicted.
    running = [t for t in tracker._tasks.values() if t.status == TaskStatus.running]
    assert len(running) == 10
