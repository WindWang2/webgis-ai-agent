"""取消传播原语（ADR-0052）。

重点验证「取消不只是 UI 状态」：token 在线程间可见、经 contextvar 穿进同步代码、
长循环在 checkpoint 处**提前**退出（跑掉的 chunk 数远小于总数）。
"""
import asyncio
import threading
import time

import pytest

from app.services.jobs.cancellation import (
    CancellationRegistry,
    CancellationToken,
    OperationCancelled,
    cancellable,
    checkpoint,
    current_token,
    is_cancelled,
    use_token,
)


# ── token 基本语义 ──────────────────────────────────────────────────


def test_cancel_is_idempotent():
    """重复取消返回 False 且不重复触发回调（规范 §5：double cancel 幂等）。"""
    token = CancellationToken("job-1")
    fired: list[str] = []
    token.add_callback(lambda t: fired.append(t.reason or ""))

    assert token.cancel("first") is True
    assert token.cancel("second") is False
    assert token.cancelled is True
    assert token.reason == "first"  # 首个原因胜出，不被后续覆盖
    assert fired == ["first"]


def test_raise_if_cancelled():
    token = CancellationToken("job-2")
    token.raise_if_cancelled()  # 未取消时是 no-op
    token.cancel("stop")
    with pytest.raises(OperationCancelled) as exc:
        token.raise_if_cancelled()
    assert exc.value.job_id == "job-2"


def test_add_callback_after_cancel_fires_immediately():
    token = CancellationToken()
    token.cancel()
    fired: list[bool] = []
    token.add_callback(lambda _t: fired.append(True))
    assert fired == [True]


def test_callback_exception_does_not_break_cancel():
    """一个坏回调不能让取消半途而废 —— 否则取消会变得不可靠。"""
    token = CancellationToken()
    fired: list[str] = []

    def bad(_t):
        raise RuntimeError("boom")

    token.add_callback(bad)
    token.add_callback(lambda _t: fired.append("ok"))
    assert token.cancel() is True
    assert token.cancelled is True
    assert fired == ["ok"]


def test_link_cascades_to_child():
    """agent task 取消要级联到它派生的 durable job。"""
    parent = CancellationToken("agent")
    child = CancellationToken("job")
    parent.link(child)
    assert child.cancelled is False
    parent.cancel("user cancelled")
    assert child.cancelled is True
    assert "user cancelled" in (child.reason or "")


def test_token_is_visible_across_threads():
    """token 用 threading.Event 承载 —— 工作线程里的 GIS 代码必须能看到取消。"""
    token = CancellationToken()
    observed = threading.Event()

    def worker():
        for _ in range(2000):
            if token.cancelled:
                observed.set()
                return
            time.sleep(0.001)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    token.cancel()
    assert observed.wait(timeout=5.0) is True
    t.join(timeout=5.0)


# ── asyncio 抢占 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wait_returns_immediately_when_already_cancelled():
    token = CancellationToken()
    token.cancel()
    await asyncio.wait_for(token.wait(), timeout=1.0)


@pytest.mark.asyncio
async def test_wait_wakes_up_on_cancel():
    """执行引擎靠这个把取消变成抢占：无需等在飞工具自然结束。"""
    token = CancellationToken()
    waiter = asyncio.create_task(token.wait())
    await asyncio.sleep(0)
    assert not waiter.done()
    token.cancel()
    await asyncio.wait_for(waiter, timeout=1.0)


@pytest.mark.asyncio
async def test_wait_wakes_up_when_cancelled_from_another_thread():
    """真实场景：cancel 来自 API 请求线程，等待者在事件循环里。"""
    token = CancellationToken()
    waiter = asyncio.create_task(token.wait())
    await asyncio.sleep(0)
    threading.Thread(target=token.cancel, daemon=True).start()
    await asyncio.wait_for(waiter, timeout=5.0)


@pytest.mark.asyncio
async def test_waiters_are_cleaned_up_after_wait():
    """等待者用完必须摘掉，否则每个工具 wave 泄漏一个 asyncio.Event。"""
    token = CancellationToken()
    task = asyncio.create_task(token.wait())
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert token._waiters == []  # noqa: SLF001 —— 泄漏断言必须看内部状态


# ── contextvar 传播 + checkpoint ────────────────────────────────────


def test_checkpoint_is_noop_without_token():
    """没有 token 时 checkpoint 必须零副作用 —— 普通请求路径也会经过这些循环。"""
    assert current_token() is None
    assert is_cancelled() is False
    checkpoint()


def test_use_token_binds_and_restores():
    token = CancellationToken()
    assert current_token() is None
    with use_token(token):
        assert current_token() is token
    assert current_token() is None


def test_checkpoint_raises_inside_bound_context():
    token = CancellationToken("job-9")
    with use_token(token):
        checkpoint()
        token.cancel("stop")
        assert is_cancelled() is True
        with pytest.raises(OperationCancelled):
            checkpoint()


@pytest.mark.asyncio
async def test_token_propagates_into_to_thread():
    """关键机制：asyncio.to_thread 复制 context，所以同步 GIS 工具无需改签名。

    registry 的同步工具执行路径正是 to_thread —— 这条断言保证取消能到达那里。
    """
    token = CancellationToken()
    token.cancel("stop")

    def sync_gis_work():
        checkpoint()  # 应当在工作线程里抛出
        return "should not reach"

    with use_token(token):
        with pytest.raises(OperationCancelled):
            await asyncio.to_thread(sync_gis_work)


@pytest.mark.asyncio
async def test_sibling_asyncio_tasks_have_independent_tokens():
    """并行工具 wave 里，取消一个工具不应影响另一个（context 是每 task 复制的）。"""
    token_a = CancellationToken("a")
    token_b = CancellationToken("b")
    results: dict[str, str] = {}

    async def run(name: str, token: CancellationToken):
        with use_token(token):
            await asyncio.sleep(0.01)
            try:
                checkpoint()
                results[name] = "ok"
            except OperationCancelled:
                results[name] = "cancelled"

    token_a.cancel()
    await asyncio.gather(run("a", token_a), run("b", token_b))
    assert results == {"a": "cancelled", "b": "ok"}


# ── 取消延迟：真正停止计算而不是跑完 ────────────────────────────────


def test_cancellable_loop_stops_early():
    """规范 §13：100 个 chunk，第 5 个后取消 → 实际执行应 ≤ 7 而不是 100。"""
    token = CancellationToken()
    executed: list[int] = []

    with use_token(token):
        with pytest.raises(OperationCancelled):
            for i in cancellable(range(100)):
                executed.append(i)
                if i == 5:
                    token.cancel("user cancelled")

    assert len(executed) <= 7, f"取消后仍执行了 {len(executed)} 个 chunk"
    assert len(executed) >= 6  # 第 5 个必须已完成，取消不应回滚已完成工作


def test_cancellable_every_n_bounds_overshoot():
    """every=N 时超跑量必须 ≤ N —— 检查点稀疏化不能让取消失控。"""
    token = CancellationToken()
    executed: list[int] = []
    every = 10

    with use_token(token):
        with pytest.raises(OperationCancelled):
            for i in cancellable(range(1000), every=every):
                executed.append(i)
                if i == 15:
                    token.cancel()

    assert len(executed) <= 16 + every


def test_cancellable_completes_normally_without_cancel():
    token = CancellationToken()
    with use_token(token):
        assert list(cancellable(range(50))) == list(range(50))


def test_cancellable_normalizes_bad_every():
    token = CancellationToken()
    with use_token(token):
        assert list(cancellable(range(5), every=0)) == list(range(5))


# ── registry ────────────────────────────────────────────────────────


def test_registry_register_is_idempotent():
    reg = CancellationRegistry()
    first = reg.register("job-1")
    second = reg.register("job-1")
    assert first is second


def test_registry_cancel_unknown_key_returns_false():
    """API 重启后 registry 为空 —— 取消不能因此报错（持久事实在 DB）。"""
    reg = CancellationRegistry()
    assert reg.cancel("never-seen") is False
    assert reg.is_cancelled("never-seen") is False


def test_registry_cancel_and_discard():
    reg = CancellationRegistry()
    token = reg.register("job-2")
    assert reg.cancel("job-2") is True
    assert token.cancelled is True
    assert reg.is_cancelled("job-2") is True
    reg.discard("job-2")
    assert reg.get("job-2") is None


def test_registry_is_lru_bounded():
    """长寿命 API 进程不能让 registry 无限增长。"""
    reg = CancellationRegistry(max_entries=8)
    for i in range(40):
        reg.register(f"job-{i}")
    assert len(reg) == 8
    assert reg.get("job-0") is None      # 最旧的被淘汰
    assert reg.get("job-39") is not None  # 最新的仍在


def test_registry_accepts_int_keys():
    """durable job id 是整数 —— registry 必须按字符串统一。"""
    reg = CancellationRegistry()
    token = reg.register(42)
    assert reg.get("42") is token
    assert reg.cancel(42) is True
