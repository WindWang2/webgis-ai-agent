"""G32 Pi 全链旅程：真实 vendor Pi 子进程 + 生产扩展 + 生产 dispatch +
feature_proposals 服务（任务 §C Pi 使用链验证；P28 的 Pi 面）。

架构（与 test_pi_stub_llm_journeys.py 同车道纪律）：pytest 门控与裁决，
driver 子进程承载 —— stub LLM（node，确定性步骤脚本，每请求重读）→
uvicorn 生产 app（lifespan 拉起真实 Pi bridge）→ 真实 HTTP 旅程 → VERDICT。

旅程：J1 Agent 起草提案（真实 Pi 工具 dispatch → 持久 proposal）→
J2 REST 可见（submitted / agent 作者）→ J3 owner 批准 + merge（agent 无
自批面）→ J4 真实数据逐要素变更。持久根隔离（MAPSPEC_STORAGE_DIR/
DATA_DIR 指向临时目录）。

门控：node + 已构建 vendor + PI_JOURNEY_E2E=1（显式 skip，绝不静默绿）。
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
    shutil.which("node") is None, reason="stub-LLM journeys need node on PATH"
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
    pytest.mark.timeout(600),  # driver 双 boot + 旅程 ≈ 2-4 分钟（全局 180s 不够）
    requires_node,
    pytest.mark.skipif(
        os.getenv("PI_JOURNEY_E2E") != "1",
        reason=(
            "stub-LLM full-stack journeys are opt-in: set PI_JOURNEY_E2E=1 — "
            "explicit skip, never silent-green"
        ),
    ),
]


_DRIVER_SOURCE = r"""
#!/usr/bin/env python3
# G32 journey driver：stub LLM + uvicorn(app) + 旅程 → VERDICT
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPO_ROOT = Path(sys.argv[1])
WORK = Path(tempfile.mkdtemp(prefix="g32-journey-"))
sys.path.insert(0, str(REPO_ROOT))

RUN = "g32j" + uuid.uuid4().hex[:8]
os.environ["PI_AUTOSTART"] = "true"
# 旅程环境无 Redis —— 内存 session store（与单元测试同降级；生产 Redis
# 行为由部署矩阵覆盖）。
os.environ["USE_REDIS"] = "false"

STUB_JS = r'''
import http from 'node:http';
import { appendFileSync, readFileSync } from 'node:fs';
const scriptPath = process.env.JOURNEY_SCRIPT;
const recFile = process.env.JOURNEY_REC;
const counters = {};
const srv = http.createServer((req, res) => {
  let body = ''; req.on('data', (c) => (body += c));
  req.on('end', () => {
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
    appendFileSync(recFile, JSON.stringify({
      key: base,
      tools: (parsed.tools || []).map((t) => t.function?.name || t.name),
    }) + '\n');
    let turnScripts = {};
    try { turnScripts = JSON.parse(readFileSync(scriptPath, 'utf8')); } catch (e) {}
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
'''

SCHOOLS_FC = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "id": "sch-{}".format(i),
         "geometry": {"type": "Point", "coordinates": [116.0 + 0.01 * i, 39.0]},
         "properties": {"name": "学校{}".format(i), "capacity": 500 + i, "tier": "a" if i % 2 else "b"}}
        for i in range(4)
    ],
}


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    s.close()
    return port


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


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


def _emit_verdict(verdict: dict, code: int) -> int:
    print("VERDICT " + json.dumps(verdict, ensure_ascii=False))
    sys.stdout.flush()
    # Pi bridge 的 node 子进程继承 stdout 管道 —— 常规退出会因 fd 未关而
    # 挂住父进程的 communicate；结论已打印即强制退出（跳过 atexit 清理）。
    os._exit(code)
    return code


def main() -> int:
    stub_js = WORK / "journey_stub.mjs"
    stub_js.write_text(STUB_JS, encoding="utf-8")
    (WORK / "journey_script.json").write_text("{}", encoding="utf-8")
    rec = WORK / "journey_rec.jsonl"
    rec.write_text("", encoding="utf-8")
    stub_err = WORK / "stub_err.log"

    with open(stub_err, "w") as fh:
        stub = subprocess.Popen(
            ["node", str(stub_js)], stdout=subprocess.DEVNULL, stderr=fh,
            env={**os.environ,
                 "JOURNEY_SCRIPT": str(WORK / "journey_script.json"),
                 "JOURNEY_REC": str(rec)},
        )
    stub_port = None
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not stub_port:
        for line in stub_err.read_text().splitlines():
            if "STUB_READY" in line:
                stub_port = int(line.split()[1])
        time.sleep(0.1)
    if not stub_port:
        print(json.dumps({"fail": ["stub LLM never became ready"]}))
        stub.terminate()
        return 1

    os.environ["LLM_BASE_URL"] = "http://127.0.0.1:{}/v1".format(stub_port)
    os.environ["LLM_API_KEY"] = "stub-g32-key"
    os.environ["LLM_MODEL"] = "stub-model"
    # 隔离持久根（proposals.json 走 MAPSPEC_STORAGE_DIR；BlobStore/DATA_DIR
    # 同步隔离 —— 不污染开发目录）。
    os.environ["MAPSPEC_STORAGE_DIR"] = str(WORK / "storage")
    os.environ["DATA_DIR"] = str(WORK / "data")

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
        server.started and _port_open(api_port) and try_get_chat_engine() is not None
    ):
        time.sleep(0.5)
    ready = server.started and _port_open(api_port) and try_get_chat_engine() is not None
    verdict = {"journeys": {}, "fail": []}
    if not ready:
        verdict["fail"].append("app never became chat-ready under stub LLM")
        stub.terminate()
        return _emit_verdict(verdict, 1)

    sid = RUN + "-s1"
    owner_token = None

    def _post_stream_raw(message, session_id, timeout_s=120.0, token=None):
        payload = {"message": message, "session_id": session_id}
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Session-Token"] = token
        req = urllib.request.Request(
            "http://127.0.0.1:{}/api/v1/chat/stream".format(api_port),
            data=json.dumps(payload).encode(),
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read().decode("utf-8", "replace")
        (WORK / ("sse_" + session_id + ".log")).write_text(body, encoding="utf-8")
        return _parse_sse(body)

    # warmup（拉起 Pi bridge 会话 + 取 owner_token —— require_owned_session
    # 需要真实 Conversation 归属）。
    for _attempt in range(3):
        try:
            warm = _post_stream_raw("warmup", sid, timeout_s=60)
            for e in warm:
                tok = e["data"].get("owner_token")
                if tok:
                    owner_token = tok
                    break
            if owner_token and any(e["event"] in ("task_start", "session") for e in warm):
                break
            time.sleep(4)
        except Exception as exc:  # noqa: BLE001
            verdict["warmup_error"] = str(exc)[:200]
            time.sleep(4)
    if not owner_token:
        verdict["fail"].append("warmup never produced an owner_token (session not created)")
        stub.terminate()
        return _emit_verdict(verdict, 1)

    # 播种数据集（同进程真实 session store）。
    from app.services.session_data import session_data_manager

    ref_id = asyncio.run(session_data_manager.store(sid, SCHOOLS_FC, prefix="geojson"))
    verdict["seed_ref"] = ref_id

    # 旅程脚本（stub 每请求重读 —— ref_id 播种后写入即可）。
    turn_scripts = {
        # 两段式（ADR-0288 发现闭环）：deferred 工具先 tool_search 发现并入
        # 声明面，随后同 turn 直调 —— 与 A5 J8 旅程同纪律。
        "为学校数据起草容量修正提案": [
            {"tool": "tool_search", "args": {"query": "vector_change_proposal_create persistent proposal draft"}},
            {"tool": "vector_change_proposal_create", "args": {
                "ref_id": ref_id,
                "title": "容量修正（Agent 起草）",
                "submit": True,
                "ops": [
                    {"op": "set_fields", "wfids": ["sch-0"], "fields": {"capacity": 777}},
                    {"op": "set_fields", "wfids": ["sch-1"], "fields": {"capacity": 888}},
                ],
            }},
            {"text": "提案已起草并提交待审；请用户在提案面板审阅批准。"},
        ],
    }
    (WORK / "journey_script.json").write_text(json.dumps(turn_scripts), encoding="utf-8")

    def _rest(method, path, body=None):
        url = "http://127.0.0.1:{}{}".format(api_port, path)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json",
                                              "X-Session-Token": owner_token})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8", "replace") or "{}")

    # ── J-G32-1: Agent 起草（真实 Pi 子进程工具 dispatch）────────────────
    try:
        ev = _post_stream_raw("为学校数据起草容量修正提案", sid, token=owner_token)
    except Exception as exc:  # noqa: BLE001
        verdict["fail"].append("J1 request error: " + str(exc)[:200])
        stub.terminate()
        return _emit_verdict(verdict, 1)
    step_results = [
        e["data"] for e in ev
        if e["event"] == "step_result" and e["data"].get("tool") == "vector_change_proposal_create"
    ]
    search_results = [
        e["data"] for e in ev
        if e["event"] == "step_result" and e["data"].get("tool") == "tool_search"
    ]
    verdict["journeys"]["J0_search"] = {
        "found": bool(search_results),
        "result_preview": json.dumps(search_results[0].get("result") if search_results else None)[:600],
    }
    result_obj = step_results[0].get("result") if step_results else None
    verdict["journeys"]["J1_create"] = {
        "events": [e["event"] for e in ev][:20],
        "tool_success": isinstance(result_obj, dict) and result_obj.get("success") is True,
        "proposal_id": result_obj.get("proposal_id") if isinstance(result_obj, dict) else None,
        "status_field": result_obj.get("status") if isinstance(result_obj, dict) else None,
        "step_errors": [
            {"tool": e["data"].get("tool"), "error": str(e["data"].get("error"))[:500]}
            for e in ev if e["event"] == "step_error"
        ],
    }
    if not verdict["journeys"]["J1_create"]["tool_success"]:
        verdict["fail"].append("J1: vector_change_proposal_create did not succeed via Pi dispatch")
        stub.terminate()
        return _emit_verdict(verdict, 1)
    pid = verdict["journeys"]["J1_create"]["proposal_id"]

    # ── J-G32-2: REST 可见（持久、submitted、agent 作者）─────────────────
    status, listing = _rest("GET", "/api/v1/chat/sessions/{}/feature-proposals".format(sid))
    found = None
    if status == 200:
        for item in listing.get("items", []):
            if item.get("proposal_id") == pid:
                found = item
    verdict["journeys"]["J2_rest_visible"] = {
        "status": status,
        "found": found is not None,
        "author_agent": bool(found and found.get("author_kind") == "agent"),
        "status_field": found.get("status") if found else None,
    }
    if not found:
        verdict["fail"].append("J2: proposal not visible via REST after Pi draft")
        stub.terminate()
        return _emit_verdict(verdict, 1)

    # ── J-G32-3: 用户批准 + merge（owner REST；agent 无自批面）───────────
    status, _approve = _rest("POST",
        "/api/v1/chat/sessions/{}/feature-proposals/{}/decisions".format(sid, pid),
        body={"action": "approve"})
    merge_status, merged = None, {}
    if status == 200:
        merge_status, merged = _rest("POST",
            "/api/v1/chat/sessions/{}/feature-proposals/{}/merge".format(sid, pid))
    verdict["journeys"]["J3_owner_merge"] = {
        "approve_status": status,
        "merge_status": merge_status,
        "merged": merge_status == 200 and merged.get("status") == "merged",
        "receipt_changed": bool(merged.get("receipt", {}).get("changed_wfids")),
        "origin": merged.get("receipt", {}).get("origin"),
    }

    # ── J-G32-4: 数据真实变更（逐要素核对）──────────────────────────────
    payload_after = asyncio.run(session_data_manager.get(sid, ref_id))
    feats = payload_after.get("geojson", payload_after)["features"]
    caps = {f.get("id"): f["properties"]["capacity"] for f in feats}
    verdict["journeys"]["J4_data"] = {
        "caps": caps,
        "applied": caps.get("sch-0") == 777 and caps.get("sch-1") == 888,
        "untouched": caps.get("sch-2") == 502 and caps.get("sch-3") == 503,
    }
    if not (verdict["journeys"]["J3_owner_merge"]["merged"] and verdict["journeys"]["J4_data"]["applied"]):
        verdict["fail"].append("J3/J4: owner approve+merge did not produce real data change")

    stub.terminate()
    return _emit_verdict(verdict, 0 if not verdict["fail"] else 1)


if __name__ == "__main__":
    sys.exit(main())
"""


_DRIVER_PATH = Path(__file__).parent / "_g32_journey_driver.py"


@pytest.fixture(scope="module")
def journey_verdict():
    entry = _vendor_entry()
    if entry is None:
        pytest.skip(NEEDS_BUILD_REASON)
    _DRIVER_PATH.write_text(_DRIVER_SOURCE, encoding="utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, str(_DRIVER_PATH), str(REPO_ROOT)],
            capture_output=True, text=True, timeout=420,
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        )
        verdict_line = None
        for line in proc.stdout.splitlines():
            if line.startswith("VERDICT "):
                verdict_line = line[len("VERDICT "):]
        if verdict_line is None:
            pytest.fail(
                "driver produced no VERDICT; stderr tail: "
                + proc.stderr[-2000:] + "; stdout tail: " + proc.stdout[-2000:]
            )
        yield json.loads(verdict_line)
    finally:
        _DRIVER_PATH.unlink(missing_ok=True)


class TestG32PiJourney:
    def test_agent_draft_via_real_pi_dispatch(self, journey_verdict):
        j1 = journey_verdict["journeys"]["J1_create"]
        assert j1["tool_success"] is True
        assert j1["proposal_id"]
        assert j1["status_field"] == "submitted"
        assert j1["step_errors"] == []

    def test_proposal_persisted_and_rest_visible(self, journey_verdict):
        j2 = journey_verdict["journeys"]["J2_rest_visible"]
        assert j2["status"] == 200
        assert j2["found"] is True
        assert j2["author_agent"] is True
        assert j2["status_field"] == "submitted"

    def test_owner_approve_merge_updates_real_data(self, journey_verdict):
        j3 = journey_verdict["journeys"]["J3_owner_merge"]
        j4 = journey_verdict["journeys"]["J4_data"]
        assert j3["approve_status"] == 200
        assert j3["merged"] is True
        assert j3["origin"] == "feature_proposal"
        assert j4["applied"] is True
        assert j4["untouched"] is True

    def test_no_failures(self, journey_verdict):
        assert journey_verdict["fail"] == [], journey_verdict["fail"]
