"""Audit round-2 #810: PiRpcClient 子进程收尸 + 并发 stop 写竞争。

- SIGKILL 路径必须 wait() 收尸（此前每个 lifespan 周期泄一个 defunct node）
- request() 在循环侧校验后、executor 线程执行前被 stop() 清场时，
  应抛 PiRpcError 而非裸 AttributeError（写入用捕获的本地句柄，
  句柄失效分类为 BrokenPipeError → PiRpcError）
"""
import asyncio
from unittest.mock import MagicMock

import pytest

from app.services.chat.pi_rpc_client import PiRpcClient, PiRpcError


class _StubbornProc:
    """terminate 不生效（poll 恒 None 直到 kill）的顽固子进程。"""

    def __init__(self):
        self.terminated = False
        self.killed = False
        self.wait_calls = 0
        self.stdin = MagicMock()

    def terminate(self):
        self.terminated = True

    def poll(self):
        # terminate 后依然存活（模拟卡在 native call 的 node）；kill 后退出
        return None if not self.killed else 0

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.wait_calls += 1


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
