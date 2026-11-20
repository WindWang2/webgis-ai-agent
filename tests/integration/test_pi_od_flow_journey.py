"""G21 directed OD flow journey: real Pi + deterministic local LLM stub."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
pytestmark = [
    pytest.mark.heavy,
    pytest.mark.skipif(shutil.which("node") is None, reason="G21 Pi journey needs node"),
    pytest.mark.skipif(os.getenv("PI_JOURNEY_E2E") != "1", reason="opt-in real Pi journey"),
]


def _vendor_entry() -> Path | None:
    from app.services.chat.pi_compat import pi_vendor_identity

    ident = pi_vendor_identity()
    return Path(ident.entry_path) if ident.entry_built else None


_DRIVER = r'''#!/usr/bin/env python3
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

ROOT = Path(sys.argv[1])
WORK = Path(tempfile.mkdtemp(prefix="g21-pi-od-"))
sys.path.insert(0, str(ROOT))
RUN = "g21" + uuid.uuid4().hex[:8]
os.environ["PI_AUTOSTART"] = "true"

import logging
logging.getLogger("app.services.chat.pi_rpc_client").setLevel(logging.DEBUG)

STUB = r"""
import http from 'node:http';
import { appendFileSync } from 'node:fs';
const scripts = JSON.parse(await import('node:fs').then(fs => fs.readFileSync(process.env.G21_SCRIPT, 'utf8')));
const counters = {};
const rec = process.env.G21_REC;
function productRef(value) {
  if (value == null) return null;
  if (typeof value === 'string') {
    const match = value.match(/ref:flow-product-[a-z0-9-]+/);
    if (match) return match[0];
    try { return productRef(JSON.parse(value)); } catch { return null; }
  }
  if (Array.isArray(value)) {
    for (const item of value) { const found = productRef(item); if (found) return found; }
    return null;
  }
  if (typeof value === 'object') {
    for (const item of Object.values(value)) { const found = productRef(item); if (found) return found; }
  }
  return null;
}
const server = http.createServer((req, res) => {
  let body = ''; req.on('data', c => body += c);
  req.on('end', async () => {
    const parsed = JSON.parse(body || '{}');
    const messages = parsed.messages || [];
    let user = null;
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const m = messages[i];
      if (m.role !== 'user') continue;
      user = typeof m.content === 'string' ? m.content
        : Array.isArray(m.content) ? m.content.filter(b => b.type === 'text').map(b => b.text).join('') : '';
      break;
    }
    const key = (user || '').split('\n')[0].trim();
    appendFileSync(rec, JSON.stringify({ key,
      active: ((user || '').match(/\[WEBGIS_ACTIVE_TOOLS:(\[[^\r\n]*?\])\]/) || [])[1] || null,
      tools: (parsed.tools || []).map(t => t.function?.name || t.name) }) + '\n');
    const steps = scripts[key];
    counters[key] = (counters[key] || 0) + 1;
    const step = steps ? steps[Math.min(counters[key] - 1, steps.length - 1)] : { text: 'ok' };
    const args = { ...(step.args || {}) };
    if (step.product_ref) {
      args.product_ref = productRef(messages) || 'missing-product-ref';
    }
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    const sse = o => res.write(`data: ${JSON.stringify(o)}\n\n`);
    const id = 'c' + Date.now();
    if (step.tool) {
      sse({ id, object: 'chat.completion.chunk', created: 1, model: 'stub',
        choices: [{ index: 0, delta: { tool_calls: [{ index: 0, id: 'call_' + Math.random().toString(36).slice(2, 8),
          type: 'function', function: { name: step.tool, arguments: JSON.stringify(args) } }] } }] });
      sse({ id, object: 'chat.completion.chunk', created: 1, model: 'stub', choices: [{ index: 0, delta: {}, finish_reason: 'tool_calls' }] });
    } else {
      sse({ id, object: 'chat.completion.chunk', created: 1, model: 'stub', choices: [{ index: 0, delta: { content: step.text || 'done' } }] });
      sse({ id, object: 'chat.completion.chunk', created: 1, model: 'stub', choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] });
    }
    res.write('data: [DONE]\n\n'); res.end();
  });
});
server.listen(0, '127.0.0.1', () => process.stderr.write('STUB_READY ' + server.address().port + '\n'));
process.on('SIGTERM', () => process.exit(0));
"""

SCRIPTS = {
    "warmup": [{"text": "warmup-ok"}],
    "把这份成都区县通勤表画成流图；两向分别显示": [{
        "tool": "od_flow_edges",
        "args": {
            "od_table_ref": "chengdu-od", "operation": "apply",
            "flow": {"weight_field": "trips", "weight_unit": "trips", "direction_mode": "directed", "geometry": "curve", "top_n": 20},
        },
    }, {"text": "created-directed-flow"}],
    "点选武侯区查看流入和流出": [{
        "tool": "od_flow_edges", "product_ref": True,
        "args": {"operation": "query", "query_kind": "node", "object_id": "武侯区", "focus_direction": "both"},
    }, {"text": "focused-node"}],
    "改看净流并导出": [{
        "tool": "od_flow_edges",
        "args": {
            "od_table_ref": "chengdu-od", "operation": "apply",
            "flow": {"weight_field": "trips", "weight_unit": "trips", "direction_mode": "net", "geometry": "curve", "top_n": 20},
        },
    }, {"text": "applied-net-flow"}],
    "编译当前流图": [{"tool": "webgis_compile_maplibre", "args": {}}, {"text": "compiled-current-flow"}],
}


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    sock.close()
    return port


def port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def parse_sse(text):
    events = []
    for block in text.split("\n\n"):
        event = data = None
        for line in block.splitlines():
            if line.startswith("event: "): event = line[7:].strip()
            elif line.startswith("data: "): data = line[6:]
        if event and data and data != "[DONE]":
            try: events.append({"event": event, "data": json.loads(data)})
            except ValueError: events.append({"event": event, "data": {"raw": data}})
    return events


def main():
    stub_path = WORK / "stub.mjs"
    stub_path.write_text(STUB, encoding="utf-8")
    script_path = WORK / "scripts.json"
    script_path.write_text(json.dumps(SCRIPTS), encoding="utf-8")
    rec = WORK / "rec.jsonl"
    rec.write_text("")
    stub_err = WORK / "stub.err"
    with open(stub_err, "w") as handle:
        stub = subprocess.Popen(["node", str(stub_path)], stdout=subprocess.DEVNULL, stderr=handle,
                                env={**os.environ, "G21_SCRIPT": str(script_path), "G21_REC": str(rec)})
    port = None
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        for line in stub_err.read_text().splitlines():
            if "STUB_READY" in line:
                port = int(line.split()[1])
        if port: break
        time.sleep(0.1)
    if not port:
        print("VERDICT " + json.dumps({"fail": ["stub did not start"]}))
        return 1
    os.environ["LLM_BASE_URL"] = f"http://127.0.0.1:{port}/v1"
    os.environ["LLM_API_KEY"] = "g21-stub"
    os.environ["LLM_MODEL"] = "g21-stub-model"
    api_port = free_port()
    os.environ["WEBGIS_API_BASE"] = f"http://127.0.0.1:{api_port}"

    from app.services.session_data import session_data_manager
    sid = RUN + "-od"
    rows = {"type": "od_table", "rows": [
        {"origin_id": "武侯区", "destination_id": "高新区", "origin_lng": 104.04, "origin_lat": 30.57,
         "destination_lng": 104.07, "destination_lat": 30.55, "trips": 8},
        {"origin_id": "高新区", "destination_id": "武侯区", "origin_lng": 104.07, "origin_lat": 30.55,
         "destination_lng": 104.04, "destination_lat": 30.57, "trips": 3},
    ]}
    source_ref = asyncio.run(session_data_manager.store(sid, rows, prefix="bench"))
    asyncio.run(session_data_manager.set_alias(sid, source_ref, "chengdu-od"))

    import uvicorn
    server = uvicorn.Server(uvicorn.Config("app.main:app", host="127.0.0.1", port=api_port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    from app.services.chat.engine_instance import try_get_chat_engine
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline and not (server.started and port_open(api_port) and try_get_chat_engine() is not None):
        time.sleep(0.5)
    verdict = {"fail": [], "steps": {}}
    print("G21_WORK=" + str(WORK), flush=True)
    from app.agent_pi_bridge import get_tool_registry
    registry = get_tool_registry()
    verdict["flow_registration"] = {
        "registered": "od_flow_edges" in registry.list_tools(),
        "tool_count": len(registry.list_tools()),
    }
    try:
        verdict["flow_registration"]["descriptor"] = registry.descriptor("od_flow_edges").model_dump(mode="json")
    except Exception as exc:
        verdict["flow_registration"]["descriptor_error"] = repr(exc)
    if not (server.started and port_open(api_port) and try_get_chat_engine() is not None):
        verdict["fail"].append("app not ready")
        print("VERDICT " + json.dumps(verdict, ensure_ascii=False))
        return 1

    def stream(message, owner=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{api_port}/api/v1/chat/stream",
            data=json.dumps({"message": message, "session_id": sid}).encode(),
            headers={"Content-Type": "application/json", "X-Session-Token": owner or ""},
        )
        with urllib.request.urlopen(req, timeout=150) as response:
            body = response.read().decode("utf-8", "replace")
        (WORK / (message[:8] + ".log")).write_text(body, encoding="utf-8")
        events = parse_sse(body)
        errors = [{"tool": e["data"].get("tool"), "error": e["data"].get("error")}
                  for e in events if e["event"] == "step_error"]
        if errors:
            raise AssertionError(f"Pi tool failed; refusing an unbounded retry loop: {errors}")
        return events

    def owner_token(events):
        for event in events:
            token = event["data"].get("owner_token")
            if token: return token
        return None

    try:
        warm = stream("warmup")
        owner = owner_token(warm)
        first = stream("把这份成都区县通勤表画成流图；两向分别显示", owner)
        owner = owner or owner_token(first)
        first_results = [e["data"] for e in first if e["event"] == "step_result" and e["data"].get("tool") == "od_flow_edges"]
        verdict["steps"]["create"] = {
            "ok": bool(first_results and first_results[-1]["result"].get("success")),
            "layers": len(first_results[-1]["result"].get("mapspec", {}).get("layers", [])) if first_results else 0,
            "owner": bool(owner),
        }
        second = stream("点选武侯区查看流入和流出", owner)
        query_results = [e["data"] for e in second if e["event"] == "step_result" and e["data"].get("tool") == "od_flow_edges"]
        verdict["steps"]["focus"] = {
            "outbound": query_results[-1]["result"].get("node", {}).get("outbound") if query_results else None,
            "inbound": query_results[-1]["result"].get("node", {}).get("inbound") if query_results else None,
            "matched": query_results[-1]["result"].get("edges", {}).get("matched_count") if query_results else None,
        }
        third = stream("改看净流并导出", owner)
        net_results = [e["data"] for e in third if e["event"] == "step_result" and e["data"].get("tool") == "od_flow_edges"]
        verdict["steps"]["net"] = {
            "magnitude": net_results[-1]["result"].get("stats", {}).get("net", {}).get("displayed_magnitude") if net_results else None,
            "retained": net_results[-1]["result"].get("stats", {}).get("gross", {}).get("retained") if net_results else None,
            "product_ref": net_results[-1]["result"].get("product_ref") if net_results else None,
        }
        fourth = stream("编译当前流图", owner)
        compile_results = [e["data"] for e in fourth if e["event"] == "step_result"]
        verdict["steps"]["compile"] = {
            "ok": any(e["result"].get("success") or e["result"].get("is_compiled") for e in compile_results),
        }
        all_events = first + second + third + fourth
        verdict["tools"] = sorted({e["data"].get("tool") for e in all_events if e["event"] in ("step_result", "step_error")})
        verdict["step_errors"] = [e["data"] for e in all_events if e["event"] == "step_error"]
        verdict["done"] = all_events[-1]["event"] == "done"
        if verdict["steps"]["net"]["product_ref"]:
            req = urllib.request.Request(
                f"http://127.0.0.1:{api_port}/api/v1/chat/sessions/{sid}/flow-product/query",
                data=json.dumps({"product_ref": verdict["steps"]["net"]["product_ref"], "kind": "product"}).encode(),
                headers={"Content-Type": "application/json", "X-Session-Token": owner or ""},
            )
            with urllib.request.urlopen(req, timeout=30) as response:
                restored = json.loads(response.read())
            verdict["restored"] = restored.get("stats")
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        stub.terminate()
        try: stub.wait(timeout=3)
        except subprocess.TimeoutExpired: stub.kill()
        asyncio.run(session_data_manager.clear_session(sid))
    print("VERDICT " + json.dumps(verdict, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def test_g21_directed_od_flow_real_pi_journey():
    if _vendor_entry() is None:
        pytest.skip("vendored Pi RPC entry not built")
    with tempfile.TemporaryDirectory(prefix="g21-pi-runner-") as td:
        driver = Path(td) / "driver.py"
        driver.write_text(_DRIVER, encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k not in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")}
        result = subprocess.run(
            [sys.executable, str(driver), str(REPO_ROOT)],
            cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=520,
        )
    lines = [line for line in result.stdout.splitlines() if line.startswith("VERDICT ")]
    assert lines, f"no verdict: {result.stdout[-500:]} {result.stderr[-800:]}"
    verdict = json.loads(lines[-1][len("VERDICT "):])
    assert not verdict.get("fail"), verdict
    assert verdict["steps"]["create"] == {"ok": True, "layers": 3, "owner": True}
    assert verdict["steps"]["focus"] == {"outbound": 8.0, "inbound": 3.0, "matched": 2}
    assert verdict["steps"]["net"]["magnitude"] == 5.0
    assert verdict["steps"]["net"]["retained"] == 1.0
    assert verdict["steps"]["net"]["product_ref"].startswith("ref:flow-product-")
    assert verdict["steps"]["compile"]["ok"] is True
    assert verdict["restored"]["net"]["displayed_magnitude"] == 5.0
    assert verdict["done"] is True
    assert not verdict["step_errors"], verdict["step_errors"]
    assert "network_od_matrix" not in verdict["tools"]
