"""Audit round-2 #810: PiRpcClient 子进程收尸 + 并发 stop 写竞争。

- SIGKILL 路径必须 wait() 收尸（此前每个 lifespan 周期泄一个 defunct node）
- request() 在循环侧校验后、executor 线程执行前被 stop() 清场时，
  应抛 PiRpcError 而非裸 AttributeError（写入用捕获的本地句柄，
  句柄失效分类为 BrokenPipeError → PiRpcError）
"""
import asyncio
import subprocess
from unittest.mock import MagicMock

import pytest

from app.services.chat.pi_rpc_client import PiRpcClient, PiRpcError


class _StubbornProc:
    """terminate 不生效（poll 恒 None 直到 kill）的顽固子进程。"""

    def __init__(self):
        self.terminated = False
        self.killed = False
        self.wait_calls = 0
        self.stdin = MagicMock(closed=False)

    def terminate(self):
        self.terminated = True

    def poll(self):
        # terminate 后依然存活（模拟卡在 native call 的 node）；kill 后退出
        return None if not self.killed else 0

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        # 真实 Popen.wait(timeout) 契约：进程存活（含 SIGTERM 后仍卡死）时
        # 抛 TimeoutExpired；只有 SIGKILL 后才返回退出码。
        self.wait_calls += 1
        if not self.killed:
            raise subprocess.TimeoutExpired(cmd="pi", timeout=timeout)
        return 0


@pytest.mark.asyncio
async def test_stop_reaps_after_sigkill_810():
    client = PiRpcClient.__new__(PiRpcClient)  # 跳过 __init__（不真启进程）
    proc = _StubbornProc()
    client._process = proc
    client._reader_task = None
    client._stderr_task = None

    await client.stop()

    assert proc.killed, "顽固子进程应走到 SIGKILL"
    assert proc.wait_calls >= 1, "SIGKILL 后必须 wait 收尸（#810 僵尸修复）"
    assert client._process is None


@pytest.mark.asyncio
async def test_stop_fails_inflight_pending_requests_910():
    """P2：stop() 收尾时 reader 的 finally 代际守卫会跳过 _fail_all_pending，
    在飞 RPC 必须由 stop() 自身收口，而不是等满 PI_RPC_TIMEOUT。"""
    client = PiRpcClient.__new__(PiRpcClient)
    proc = _StubbornProc()
    client._process = proc
    client._reader_task = None
    client._stderr_task = None
    fut = asyncio.get_running_loop().create_future()
    client._pending_requests = {"req-inflight": fut}

    await client.stop()

    assert fut.done(), "stop() left an in-flight RPC pending (300s wait)"
    with pytest.raises(PiRpcError):
        fut.result()


@pytest.mark.asyncio
async def test_reader_survives_invalid_utf8_stdout_911():
    """P2：stdout 出现非 UTF-8 字节时 json.loads 抛 UnicodeDecodeError，
    读循环不得崩掉整座桥（须按坏行跳过并继续）。"""
    client = PiRpcClient.__new__(PiRpcClient)

    class _FakeProc:
        stdout = object()

        def poll(self):
            return None

    proc = _FakeProc()
    client._process = proc
    client._pending_requests = {}
    client._process_died = False
    client._process_died_event = asyncio.Event()
    client._event_queue = asyncio.Queue()
    lines = [b"\xff\xfe\xfa", b'{"type":"message_update"}', b""]
    client._readline_bounded = lambda _p: lines.pop(0) if lines else b""

    await client._read_responses(proc)

    assert client._event_queue.qsize() == 1, (
        "invalid UTF-8 line killed the stdout reader (bridge permanently dead)"
    )


@pytest.mark.asyncio
async def test_request_after_stop_yields_pi_rpc_error_810():
    """process 已被 stop() 清掉（循环侧校验即失败）→ PiRpcError。"""
    client = PiRpcClient.__new__(PiRpcClient)
    client._process = None
    client._pending_requests = {}
    client._request_counter = 0

    with pytest.raises(PiRpcError, match="not started"):
        await client.request("prompt", {"sid": "s"})


@pytest.mark.asyncio
async def test_request_dead_stdin_yields_pi_rpc_error_810():
    """捕获的句柄 stdin 已失效（进程垂死）→ BrokenPipeError → PiRpcError
    （非 AttributeError）。捕获语义下 stop() 清 self._process 不再让线程侧
    裸解引用 None。"""
    client = PiRpcClient.__new__(PiRpcClient)
    proc = _StubbornProc()
    proc.stdin = None  # 进程关闭/垂死：句柄在捕获与写入之间失效
    client._process = proc
    client._pending_requests = {}
    client._request_counter = 0

    with pytest.raises(PiRpcError, match="pipe error|stopped concurrently|not started"):
        await client.request("prompt", {"sid": "s"})
    # pending future 已清理，无泄漏
    assert client._pending_requests == {}
