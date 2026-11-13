"""G34 A34 车道：真实 Pi 子进程 + stub LLM → 纸面作者工具 → 真实落盘。

与 ``test_pi_g26_mixed_export_journey.py`` 同架构（独立 driver 进程：node
stub LLM → uvicorn 生产 app（真实 vendor/pi bridge）→ HTTP 旅程），聚焦
本域唯一旅程：

J-G34：
  服务端种子（真实 MapSpec 写入）
  → 用户「把地图排成 A4 纸面」turn
  → stub LLM 先 ``tool_search`` 发现长尾工具，再调
    ``webgis_print_layout_apply``（initialize + set_paper + set_frame）
  → 真实 ToolDispatch（HTTP → Python，Pi 原生扩展面）
  → 裁决：工具回执 committed + revision 递增；随后经 **UI 用的同一 service**
    （``service.inspect`` / ``service.apply``，REST 路由直接调用它）读回，
    证明 Pi 写的是 UI 读的同一份计划，且第二次写入仍走同一 CAS。

门控：node + 已构建 vendor + ``PI_JOURNEY_E2E=1``（显式 opt-in，绝不静默绿）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="journey needs node on PATH"
)


def _vendor_entry() -> Path | None:
    from app.services.chat.pi_compat import pi_vendor_identity

    ident = pi_vendor_identity()
    return Path(ident.entry_path) if ident.entry_built else None


NEEDS_BUILD_REASON = (
    "vendored Pi RPC entry not built — run `bash scripts/build-vendor-pi.sh` "
    "(explicit skip, never silent-green)"
)

pytestmark = [
    pytest.mark.heavy,
    requires_node,
    pytest.mark.skipif(
        os.getenv("PI_JOURNEY_E2E") != "1",
        reason="journey is opt-in (PI_JOURNEY_E2E=1) — explicit skip, never silent-green",
    ),
]


_DRIVER_SOURCE = r'''#!/usr/bin/env python3
"""G34 journey driver：stub LLM + uvicorn(app) + 纸面作者旅程 → VERDICT json。"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
from pathlib import Path

REPO_ROOT = Path(sys.argv[1])
WORK = Path(tempfile.mkdtemp(prefix="pi-g34-"))
sys.path.insert(0, str(REPO_ROOT))

RUN = "g34j" + uuid.uuid4().hex[:8]
os.environ["PI_AUTOSTART"] = "true"
os.environ.setdefault("GIS_MANIFEST_STRICT", "0")

# stub 每次请求都重读脚本：expected_revision 要等种子写入之后才知道。
STUB_JS = r"""
import http from 'node:http';
import fs from 'node:fs';
const scriptPath = process.env.JOURNEY_SCRIPT;
const counters = {};
const srv = http.createServer((req, res) => {
  let body = ''; req.on('data', (c) => (body += c));
  req.on('end', () => {
    const turnScripts = JSON.parse(fs.readFileSync(scriptPath, 'utf8'));
    const parsed = JSON.parse(body || '{}');
    const msgs = parsed.messages || [];
    let lastUser = null;
    for (let i = msgs.length - 1; i >= 0; i -= 1) {
      const m = msgs[i];
      if (m.role !== 'user') continue;
      const c = typeof m.content === 'string' ? m.content
        : Array.isArray(m.content) ? m.content.filter((b) => b.type === 'text').map((b) => b.text).join('') : '';
      lastUser = c; break;
    }
    const base = (lastUser || '').split('\n')[0].trim();
    const steps = turnScripts[base];
    let step = null;
    if (steps) {
      counters[base] = (counters[base] || 0) + 1;
      step = steps[Math.min(counters[base] - 1, steps.length - 1)];
    } else {
      step = { text: 'ok' };
    }
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    const sse = (o) => res.write(`data: ${JSON.stringify(o)}\n\n`);
    const id = 'c' + Date.now();
    if (step.tool) {
      sse({ id, object: 'chat.completion.chunk', created: 1, model: 'stub',
        choices: [{ index: 0, delta: { tool_calls: [{ index: 0, id: 'call_' + Math.random().toString(36).slice(2, 8),
          type: 'function', function: { name: step.tool, arguments: JSON.stringify(step.args || {}) } }] } }] });
      sse({ id, object: 'chat.completion.chunk', created: 1, model: 'stub',
        choices: [{ index: 0, delta: {}, finish_reason: 'tool_calls' }] });
    } else {
      sse({ id, object: 'chat.completion.chunk', created: 1, model: 'stub',
        choices: [{ index: 0, delta: { content: step.text || 'done' } }] });
      sse({ id, object: 'chat.completion.chunk', created: 1, model: 'stub',
        choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] });
    }
    res.write('data: [DONE]\n\n'); res.end();
  });
});
srv.listen(0, '127.0.0.1', () => process.stderr.write('STUB_READY ' + srv.address().port + '\n'));
process.on('SIGTERM', () => process.exit(0));
"""


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    s.close()
    return port


def _parse_sse(text: str) -> list:
    events = []
    for block in text.split("\n\n"):
        ev, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                ev = line[7:].strip()
            elif line.startswith("data: "):
                data = line[6:]
        if ev and data and data != "[DONE]":
            try:
                events.append({"event": ev, "data": json.loads(data)})
            except ValueError:
                events.append({"event": ev, "data": {"raw": data}})
    return events


async def _seed_session(session_id: str) -> int:
    from app.services.mapspec_store import mapspec_store
    from app.services.print_layout_authoring import service

    await mapspec_store.init_project(
        session_id, view={"center": [105.5, 30.5], "zoom": 6.0,
                          "bounds": [104.0, 29.0, 107.0, 32.0]})
    return int((await service.inspect(session_id))["mapspec_revision"])


def _find_receipt(events: list) -> dict | None:
    for e in events:
        data = e["data"]
        if not isinstance(data, dict):
            continue
        for key in ("tool_result", "result", "step_result"):
            payload = data.get(key)
            if isinstance(payload, dict) and "committed" in payload:
                return payload
    return None


def main() -> int:
    stub_js = WORK / "journey_stub.mjs"
    stub_js.write_text(STUB_JS, encoding="utf-8")
    script_path = WORK / "journey_script.json"
    script_path.write_text("{}", encoding="utf-8")
    stub_err = WORK / "stub_err.log"

    with open(stub_err, "w") as fh:
        stub = subprocess.Popen(
            ["node", str(stub_js)], stdout=subprocess.DEVNULL, stderr=fh,
            env={**os.environ, "JOURNEY_SCRIPT": str(script_path)},
        )
    stub_port = None
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        for line in stub_err.read_text().splitlines():
            if "STUB_READY" in line:
                stub_port = int(line.split()[1])
        if stub_port:
            break
        time.sleep(0.1)
    if not stub_port:
        print(json.dumps({"fail": ["stub LLM did not boot"]}))
        return 1

    os.environ["LLM_BASE_URL"] = "http://127.0.0.1:{}/v1".format(stub_port)
    os.environ["LLM_API_KEY"] = "stub-g34-key"
    os.environ["LLM_MODEL"] = "stub-model"

    api_port = _free_port()
    os.environ["WEBGIS_API_BASE"] = "http://127.0.0.1:{}".format(api_port)

    import uvicorn

    server = uvicorn.Server(uvicorn.Config(
        "app.main:app", host="127.0.0.1", port=api_port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    from app.services.chat.engine_instance import try_get_chat_engine

    deadline = time.monotonic() + 90
    while time.monotonic() < deadline and not (
        server.started and try_get_chat_engine() is not None
    ):
        time.sleep(0.5)
    if not (server.started and try_get_chat_engine() is not None):
        stub.terminate()
        print(json.dumps({"fail": ["app did not start"]}))
        return 1

    verdict: dict = {"journeys": {}, "fail": []}
    session_id = RUN + "-s1"
    try:
        seeded_rev = asyncio.run(_seed_session(session_id))
        verdict["seeded_revision"] = seeded_rev

        script_path.write_text(json.dumps({
            "把地图排成 A4 纸面": [
                {"tool": "tool_search",
                 "args": {"query": "纸面 版式 毫米 纸张 A4 导出"}},
                {"tool": "webgis_print_layout_apply", "args": {
                    "initialize": True,
                    "expected_revision": seeded_rev,
                    "dpi": 300,
                    "operations": [
                        {"op": "set_paper", "profile": "a4_landscape"},
                        {"op": "set_frame", "component_ids": ["title"],
                         "frame": {"x": 15.0, "y": 15.0,
                                   "width": 180.0, "height": 18.0}},
                    ],
                }},
                {"text": "A4 纸面已排好。"},
            ],
        }, ensure_ascii=False), encoding="utf-8")

        req = urllib.request.Request(
            "http://127.0.0.1:{}/api/v1/chat/stream".format(api_port),
            data=json.dumps({"message": "把地图排成 A4 纸面",
                             "session_id": session_id}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = resp.read().decode("utf-8", "replace")
        events = _parse_sse(body)
        (WORK / "sse.log").write_text(body, encoding="utf-8")

        receipt = _find_receipt(events)
        verdict["journeys"]["pi_tool_committed"] = bool(
            receipt and receipt.get("committed") is True)
        if not verdict["journeys"]["pi_tool_committed"]:
            verdict["fail"].append("no committed webgis_print_layout_apply receipt")
            if receipt:
                verdict["receipt"] = receipt
            else:
                verdict["fail"].append("no print-layout receipt found in stream")

        # ── UI 侧读回：与 REST 路由同一个 service / 同一份 store ──────────
        from app.services.print_layout_authoring import service

        ins = asyncio.run(service.inspect(session_id))
        plan = ins.get("plan") or {}
        title = next((c for c in plan.get("components") or []
                      if c.get("id") == "title"), {})
        verdict["journeys"]["ui_sees_pi_write"] = (
            ins.get("status") == "configured"
            and abs(float((title.get("frame") or {}).get("y") or 0) - 15.0) < 1e-6
            and plan.get("paper", {}).get("profile") == "a4_landscape")
        if not verdict["journeys"]["ui_sees_pi_write"]:
            verdict["fail"].append("UI inspect does not see the Pi-authored plan")
        verdict["ui_revision"] = ins.get("mapspec_revision")

        # ── 同一 service 再写一次：CAS 仍然连续（没有死锁）────────────────
        ui_rev = int(ins.get("mapspec_revision") or 0)
        if receipt and receipt.get("mutation_revision") is not None:
            verdict["journeys"]["revision_advanced"] = (
                int(receipt["mutation_revision"]) == seeded_rev + 1
                and ui_rev == int(receipt["mutation_revision"]))
        else:
            verdict["journeys"]["revision_advanced"] = False
        if not verdict["journeys"]["revision_advanced"]:
            verdict["fail"].append(
                "revision chain broken: seeded={} receipt={} ui={}".format(
                    seeded_rev, (receipt or {}).get("mutation_revision"), ui_rev))

        second = asyncio.run(service.apply(session_id, {
            "operations": [{"op": "set_frame", "component_ids": ["title"],
                            "frame": {"x": 18.0, "y": 18.0,
                                      "width": 180.0, "height": 18.0}}],
        }, expected_revision=ui_rev, origin="user", actor="ui:journey"))
        verdict["journeys"]["ui_write_after_pi"] = second.get("committed") is True
        if not verdict["journeys"]["ui_write_after_pi"]:
            verdict["fail"].append(
                "UI-path apply after Pi write failed: {}".format(
                    second.get("code")))

        # ── 预演一页：Pi 的作者决定真的进到绘制面 ────────────────────────
        prev = asyncio.run(service.preview(session_id, dpi=150, include_svg=True))
        svg = prev.get("svg") or ""
        verdict["journeys"]["preview_uses_plan"] = (
            prev.get("success") is True
            and 'data-g34-id="title"' in svg
            and abs(float((prev.get("paper_mm") or {}).get("width") or 0) - 297.0) < 1e-6)
        if not verdict["journeys"]["preview_uses_plan"]:
            verdict["fail"].append("preview did not render the authored frame/paper")
    finally:
        stub.terminate()
        try:
            stub.wait(timeout=3)
        except subprocess.TimeoutExpired:
            stub.kill()
        server.should_exit = True
        thread.join(timeout=10)

    print("VERDICT " + json.dumps(verdict, ensure_ascii=False))
    return 0 if not verdict["fail"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


@pytest.mark.heavy
def test_pi_g34_print_layout_journey(tmp_path):
    entry = _vendor_entry()
    if entry is None:
        pytest.skip(NEEDS_BUILD_REASON)
    proc = subprocess.run(
        [sys.executable, "-c", _DRIVER_SOURCE, str(REPO_ROOT)],
        capture_output=True, text=True, timeout=900, cwd=str(REPO_ROOT),
    )
    verdict_line = next(
        (ln for ln in proc.stdout.splitlines() if ln.startswith("VERDICT ")), None)
    assert verdict_line, (
        "driver did not emit a verdict; stderr tail: "
        + proc.stderr[-3000:]
    )
    verdict = json.loads(verdict_line[len("VERDICT "):])
    assert not verdict.get("fail"), verdict.get("fail")
    assert verdict["journeys"]["pi_tool_committed"] is True
    assert verdict["journeys"]["ui_sees_pi_write"] is True
    assert verdict["journeys"]["revision_advanced"] is True
    assert verdict["journeys"]["ui_write_after_pi"] is True
    assert verdict["journeys"]["preview_uses_plan"] is True
