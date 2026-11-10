"""ADR-0288（W08，#2061）：Pi 侧 LLM 配置真实生效的车道。

问题（issue #2061）：``POST /config/llm`` 写运行时覆盖后返回成功，但 Pi 子
进程的 models.json/settings.json 只在 spawn 时从 **Settings** 生成 —— 新
spawn 仍用旧配置，对话永远看不到更新。

本车道以**两个本地 stub provider**（OS 随机端口）证明闭环：
1. 解析点：``resolve_llm_config``（Settings ⊕ 运行时覆盖）驱动 spawn 写盘；
2. 真实请求证据：stub 记录每个请求的 ``model`` 与鉴权指纹（key 末 4 位），
   换代后**下一个真实 Pi 模型请求**落到新 stub/新模型/新 key —— get_config
   字段变化不算证据；
3. 代漂移回收：``recycle_if_config_stale`` 在无在飞 turn 时换代（新 spawn
   用新代），在飞 turn 延迟（不切半套）。

门控：node + 已构建 vendor（与 vendor contract 车道同纪律，显式 skip）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="model-config lane needs node on PATH"
)


def _needs_build() -> str:
    from app.services.chat.pi_compat import pi_vendor_identity

    if not pi_vendor_identity().entry_built:
        return (
            "vendored Pi RPC entry not built — run `bash scripts/build-vendor-pi.sh` "
            "(explicit skip, never silent-green)"
        )
    return ""


pytestmark = [
    pytest.mark.heavy,
    requires_node,
    pytest.mark.skipif(bool(_needs_build()), reason=_needs_build() or "vendor built"),
]


STUB_JS = r"""
import http from 'node:http';
import fs from 'node:fs';
const port = Number(process.env.STUB_PORT || 0);
const logPath = process.env.STUB_LOG;
const srv = http.createServer((req, res) => {
  if (!req.url.includes('/chat/completions')) {
    res.writeHead(200, {'content-type': 'text/plain'}); res.end('ok'); return;
  }
  let body = ''; req.on('data', (c) => (body += c));
  req.on('end', () => {
    const parsed = JSON.parse(body || '{}');
    const auth = req.headers['authorization'] || '';
    fs.appendFileSync(logPath, JSON.stringify({
      model: parsed.model,
      keyTail: auth.slice(-4),
    }) + '\n');
    res.writeHead(200, {'content-type': 'text/event-stream'});
    const sse = (o) => res.write(`data: ${JSON.stringify(o)}\n\n`);
    const id = 'c1';
    sse({id, object: 'chat.completion.chunk', created: 1, model: parsed.model,
      choices: [{index: 0, delta: {content: 'config-probe-answer'}}]});
    sse({id, object: 'chat.completion.chunk', created: 1, model: parsed.model,
      choices: [{index: 0, delta: {}, finish_reason: 'stop'}]});
    res.write('data: [DONE]\n\n'); res.end();
  });
});
srv.listen(port, '127.0.0.1', () => console.error(`STUB_READY ${srv.address().port}`));
process.on('SIGTERM', () => process.exit(0));
"""


def _boot_stub(tmp: Path, tag: str) -> tuple[int, Path]:
    js = tmp / f"stub_{tag}_{time.monotonic_ns()}.mjs"
    js.write_text(STUB_JS, encoding="utf-8")
    log = tmp / f"stublog_{tag}_{time.monotonic_ns()}.jsonl"
    err_file = tmp / f"stub_err_{tag}_{time.monotonic_ns()}.log"
    env = {**os.environ, "STUB_PORT": "0", "STUB_LOG": str(log)}
    with open(err_file, "w") as fh:
        proc = subprocess.Popen(
            ["node", str(js)], stdout=subprocess.DEVNULL, stderr=fh, env=env
        )
    deadline = time.monotonic() + 10
    port = None
    while time.monotonic() < deadline:
        m = re.search(r"STUB_READY (\d+)", err_file.read_text())
        if m:
            port = int(m.group(1))
            break
        time.sleep(0.1)
    assert port, f"stub {tag} did not announce readiness"
    return port, log


async def test_config_update_reaches_real_pi_requests(tmp_path, monkeypatch):
    """A10：换代后**真实 Pi 模型请求**换到新 stub/新模型/新 key。"""
    from app.services.chat import model_config
    from app.services.chat.pi_rpc_client import PiRpcClient

    model_config.clear_runtime_overrides()
    port_a, log_a = _boot_stub(tmp_path, "a")
    port_b, log_b = _boot_stub(tmp_path, "b")
    key_a, key_b = "sk-test-aaaa", "sk-test-bbbb"

    from app.services.chat.pi_compat import pi_vendor_identity
    entry = Path(pi_vendor_identity().entry_path)

    async def spawn_and_prompt() -> None:
        client = PiRpcClient(cwd=tmp_path)
        monkeypatch.setattr(
            "app.services.chat.pi_rpc_client.PI_AGENT_DIR",
            tmp_path / f"agent-{time.monotonic_ns()}",
        )
        try:
            await client.start()
            # 一次最小 turn：stub 只回一段文本，等 agent_settled。
            proc_out: list[dict] = []
            def _drain() -> None:
                while not client.events.empty():
                    ev = client.events.get_nowait()
                    proc_out.append(ev)
            await client.request("prompt", {"message": "配置探针"})
            deadline = time.monotonic() + 20
            settled = False
            while time.monotonic() < deadline:
                _drain()
                if any(e.get("type") == "agent_settled" for e in proc_out):
                    settled = True
                    break
                await asyncio.sleep(0.1)
            assert settled, f"probe turn did not settle: {[e.get('type') for e in proc_out]}"
        finally:
            await client.stop()

    import asyncio

    # dump_surface_file 在 spawn 内无条件执行（native 面缺失即 abort）——
    # 先装配真实 registry（与生产同源 schema）。
    from app.agent_pi_bridge import set_tool_registry
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    init_tools(reg)
    set_tool_registry(reg)

    try:
        # 世代 A：override 指向 stub A。
        model_config.set_runtime_override(
            base_url=f"http://127.0.0.1:{port_a}/v1",
            model="stub-model-a",
            api_key=key_a,
        )
        await spawn_and_prompt()
        gen_a = model_config.config_generation()

        # 真实请求证据：stub A 收到 model=stub-model-a + key 末4位。
        rows_a = [json.loads(x) for x in log_a.read_text().splitlines() if x.strip()]
        assert rows_a, "stub A saw no requests"
        assert any(r["model"] == "stub-model-a" for r in rows_a)
        assert any(r["keyTail"] == key_a[-4:] for r in rows_a)

        # 世代 B：更新到 stub B → 代际必须漂移。
        model_config.set_runtime_override(
            base_url=f"http://127.0.0.1:{port_b}/v1",
            model="stub-model-b",
            api_key=key_b,
        )
        gen_b = model_config.config_generation()
        assert gen_a != gen_b, "config generation must change across the update"

        # 新 spawn（换代后）→ 真实请求落到 stub B。
        await spawn_and_prompt()
        rows_b = [json.loads(x) for x in log_b.read_text().splitlines() if x.strip()]
        assert rows_b, "stub B saw no requests"
        assert any(r["model"] == "stub-model-b" for r in rows_b)
        assert any(r["keyTail"] == key_b[-4:] for r in rows_b)
        # stub A 没有新请求（换代后不再走旧配置）。
        rows_a_after = [json.loads(x) for x in log_a.read_text().splitlines() if x.strip()]
        assert len(rows_a_after) == len(rows_a), "old provider must receive no new requests"
    finally:
        model_config.clear_runtime_overrides()


async def test_config_generation_stale_and_deferred_recycle(tmp_path, monkeypatch):
    """代漂移检测 + 在飞 turn 延迟换代（不切半套）。"""
    from app.agent_pi_bridge import PiBridge
    from app.services.chat import model_config

    model_config.clear_runtime_overrides()
    import asyncio

    async def _noop(_self):
        return None

    monkeypatch.setattr(PiBridge, "stop", _noop)
    monkeypatch.setattr(PiBridge, "start", _noop)
    bridge = PiBridge.__new__(PiBridge)  # 只测代漂移谓词/回收分支
    bridge._rpc = type("R", (), {})()
    bridge._respawn_lock = asyncio.Lock()
    bridge._lock = asyncio.Lock()
    bridge._active_turn_sid = None
    rpc = bridge._rpc

    # 无代际锚（旧 worker / 解析失败）→ 不漂移（fail-open）
    rpc.spawn_config_generation = ""
    assert bridge.config_generation_stale() is False

    # 有锚 + 当前解析一致 → 不漂移
    from app.services.chat import model_config as mc

    rpc.spawn_config_generation = mc.config_generation()
    assert bridge.config_generation_stale() is False

    # 覆盖变化 → 漂移；无在飞 turn → 回收执行（stop+start 已 no-op 化）
    mc.set_runtime_override(model="another-model-x")
    try:
        assert bridge.config_generation_stale() is True
        assert await bridge.recycle_if_config_stale() is True
        # 回收后（start 重新锚定）：真实代码里 start() 会重锚 —— 这里直接
        # 模拟新锚后不再漂移。
        rpc.spawn_config_generation = mc.config_generation()
        assert bridge.config_generation_stale() is False

        # 在飞 turn → 延迟（回收 False，不杀活 turn）
        mc.set_runtime_override(model="third-model-y")
        bridge._active_turn_sid = "live-session"
        assert bridge.config_generation_stale() is True
        assert await bridge.recycle_if_config_stale() is False
    finally:
        mc.clear_runtime_overrides()
