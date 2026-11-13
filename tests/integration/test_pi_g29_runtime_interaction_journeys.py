"""G29 场景验证车道：真实 Pi bridge + stub LLM 的运行中交互闭环。

与 test_pi_stub_llm_journeys.py 同纪律（子进程 driver + 真实 rpc-entry +
真实 webgis-tools 扩展 + 真实 ToolDispatchService 回调 + 真实 SSE），但驱动
**运行中原生交互**：LLM 只被 stub（模型生成不 mock 掉 RPC/派发/写入），四个
dialog 方法经真实 vendor ``ctx.ui`` 发出 ``extension_ui_request``，由本 driver
经受权 REST 面作答，单向 ``extension_ui_response`` 写回同一 vendor 进程，扩展
promise 解除后把答案交回服务端受权消费，同一 agent run 继续。

旅程（G29 任务书 §6）：
- **J29A 同名数据来源选择**：会话内两个同名「人口数据」（范围/日期不同）→
  模型以裸名调 webgis_layer_upsert → 原生 select 卡 → driver 选择 2024 版 →
  续行工具用返回 ref 绑定 → MapSpec 只消费被选 ref。
- **J29B 标题/来源核对**：用户要求作图前核对 → webgis_user_decision(editor) →
  用户改写标题（含中文+换行的来源说明）→ component_update(title) 采用 →
  MapSpec 标题与用户文本一致。
- **J29C 运行中追加指令**：慢工具执行中 POST steer（adjust）与 follow_up
  （queue）→ 真实 disposition 返回 + 队列可见性。
- **J29D 取消**：pending 问题 → 用户取消 → cancelled:true → 任务继续但
  不产生依赖该答案的副作用。

门控：node + 已构建 vendor；PI_JOURNEY_E2E=1 显式 opt-in（绝不静默绿）。
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
    shutil.which("node") is None, reason="G29 journeys need node on PATH"
)


def _vendor_entry() -> Path | None:
    from app.services.chat.pi_compat import pi_vendor_identity

    ident = pi_vendor_identity()
    return Path(ident.entry_path) if ident.entry_built else None


pytestmark = [
    pytest.mark.heavy,
    requires_node,
    pytest.mark.skipif(
        os.getenv("PI_JOURNEY_E2E") != "1",
        reason=(
            "G29 runtime-interaction journeys are opt-in (sandbox loopback/"
            "threading variance): set PI_JOURNEY_E2E=1 to run — explicit "
            "skip, never silent-green"
        ),
    ),
]


_DRIVER_SOURCE = r'''#!/usr/bin/env python3
"""G29 journey driver（独立进程）：stub LLM + uvicorn(app) + 四段旅程。

输出：单行 "VERDICT {json}"。"""
import asyncio
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(sys.argv[1])
WORK = Path(tempfile.mkdtemp(prefix="g29-journey-"))
sys.path.insert(0, str(REPO_ROOT))
VERDICT_OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else None

import uuid

RUN = "g29" + uuid.uuid4().hex[:8]

os.environ["PI_AUTOSTART"] = "true"
# 交互等待上限（测试时限）：无原生 timeout 的 editor 走宿主策略期限。
os.environ["PI_INTERACTION_MAX_WAIT_S"] = "120"

STUB_JS = r"""
import http from 'node:http';
import { appendFileSync, readFileSync } from 'node:fs';
const scriptPath = process.env.JOURNEY_SCRIPT;
const recFile = process.env.JOURNEY_REC;
const turnScripts = JSON.parse(readFileSync(scriptPath, 'utf8'));
const counters = {};
const srv = http.createServer((req, res) => {
  let body = ''; req.on('data', (c) => (body += c));
  req.on('end', async () => {
    const parsed = JSON.parse(body || '{}');
    const msgs = parsed.messages || [];
    let lastUser = null;
    let lastToolText = '';
    for (let i = msgs.length - 1; i >= 0; i -= 1) {
      const m = msgs[i];
      if (m.role === 'tool' && !lastToolText) {
        const c = typeof m.content === 'string' ? m.content
          : Array.isArray(m.content) ? m.content.filter((b) => b.type === 'text').map((b) => b.text).join('') : '';
        lastToolText = c;
      }
      if (m.role !== 'user') continue;
      const c = typeof m.content === 'string' ? m.content
        : Array.isArray(m.content) ? m.content.filter((b) => b.type === 'text').map((b) => b.text).join('') : '';
      lastUser = c; break;
    }
    const base = (lastUser || '').split('\n')[0].trim();
    appendFileSync(recFile, JSON.stringify({ key: base }) + '\n');
    const steps = turnScripts[base];
    let step = null;
    if (steps) {
      counters[base] = (counters[base] || 0) + 1;
      step = steps[Math.min(counters[base] - 1, steps.length - 1)];
    } else {
      step = { text: 'ok' };
    }
    const delayS = Number(step.delay_s || 0);
    if (delayS > 0) await new Promise((r) => setTimeout(r, delayS * 1000));
    let args = Object.assign({}, step.args || {});
    if (step.ref_from_tool_result) {
      // 从上一条 tool 消息提取「引用：ref:...」（服务端受权消费的精确负载）。
      const m = /ref:[A-Za-z0-9_\-\/:.]+/.exec(lastToolText || '');
      if (!m) {
        res.writeHead(200, { 'content-type': 'text/event-stream' });
        res.write('data: ' + JSON.stringify({ error: 'no ref in tool result', tool_text: (lastToolText || '').slice(0, 200) }) + '\n\n');
        res.end(); return;
      }
      if (step.ref_into === 'toolName') {
        // 代理续行：被选 ref 作为被代理工具的 source_data 注入。
        args.arguments = Object.assign({}, args.arguments || {}, { source_data: m[0] });
      } else {
        args[step.ref_into || 'source_data'] = m[0];
      }
    }
    if (step.text_from_tool_result) {
      // editor 答案：「用户已提供内容（原样采用，勿改写）：\n{value}」。
      const marker = lastToolText.indexOf('勿改写）：');
      if (marker < 0) process.stderr.write('G29TOOLTEXT ' + (lastToolText || '').slice(0, 400) + '\n');
      const value = marker >= 0 ? lastToolText.slice(marker + '勿改写）：'.length).trim() : '';
      const patch = { options: { text: value } };
      if (args.toolName) {
        args.arguments = Object.assign({}, args.arguments || {}, patch);
      } else {
        args = Object.assign({}, args, patch);
      }
    }
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    const sse = (o) => res.write(`data: ${JSON.stringify(o)}\n\n`);
    const id = 'c' + Date.now();
    if (step.tool) {
      sse({ id, object: 'chat.completion.chunk', created: 1, model: 'stub',
        choices: [{ index: 0, delta: { tool_calls: [{ index: 0, id: 'call_' + Math.random().toString(36).slice(2, 8),
          type: 'function', function: { name: step.tool, arguments: JSON.stringify(args) } }] } }] });
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

TURN_SCRIPTS = {
    "warmup": [{"text": "warmup-ok"}],
    # J29A（两段式，A5 J6b 同款真实激活链）：意图先建立计划资格（能力入面），
    # 下一 turn 直呼 layer_upsert —— 裸名同名歧义 → 原生 select → 续行绑定被选 ref。
    "用人口数据画分布图": [
        {"tool": "webgis_map_intent", "args": {"query": "用人口数据画分布图"}},
        {"text": "人口分布制图计划已建立。"},
    ],
    "继续把人口数据画到地图上": [
        {"tool": "tool_search", "args": {"query": "图层 数据 绑定 layer upsert"}},
        {"tool": "webgis_layer_upsert",
         "args": {"layer": {"id": "pop", "source": "pop-src", "type": "circle"},
                  "source_data": "人口数据"}},
        {"tool": "webgis_layer_upsert",
         "ref_from_tool_result": True, "ref_into": "source_data",
         "args": {"layer": {"id": "pop", "source": "pop-src", "type": "circle"}}},
        {"text": "已按用户选择的数据版本完成图层绑定。"},
    ],
    # J29B：用户要求核对标题 → editor 决策 → component_update 采用（前门工具直呼）。
    "画人口图前先让我核对标题": [
        {"tool": "webgis_user_decision",
         "args": {"kind": "editor", "title": "请核对地图标题",
                  "prefill": "成都市人口分布", "target": "mapspec_title"}},
        {"tool": "webgis_component_update",
         "text_from_tool_result": True,
         "args": {"component_type": "title", "create": True}},
        {"text": "已按用户核对的标题更新地图。"},
    ],
    # J29C：慢工具窗口内发 steer / follow_up。
    "跑一个慢分析": [
        {"tool": "webgis_map_intent", "args": {"query": "慢分析"}, "delay_s": 6},
        {"text": "慢分析完成。"},
    ],
    # J29D（两段式）：意图激活后直呼 → 取消同名选择 → 不绑定。
    "再画一张人口图但这次我会取消选择": [
        {"tool": "webgis_map_intent", "args": {"query": "人口数据分布 再画一张"}},
        {"text": "计划已更新。"},
    ],
    "绑定数据但我会取消选择": [
        {"tool": "tool_search", "args": {"query": "图层 数据 绑定 layer upsert"}},
        {"tool": "webgis_layer_upsert",
         "args": {"layer": {"id": "pop2", "source": "pop2-src", "type": "circle"},
                  "source_data": "人口数据"}},
        {"text": "用户取消了数据选择；未绑定任何数据源。"},
    ],
}


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(1)
    p = s.getsockname()[1]; s.close(); return p


def _port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def _parse_sse(text):
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


async def _read_mapspec_text(session_id):
    """权威读取 MapSpec（进程内 store —— 非模型回显）。"""
    from app.services.mapspec_store import mapspec_store

    spec = await mapspec_store.get_mapspec(session_id) or {}
    return json.dumps(spec, ensure_ascii=False)


def _emit_verdict(verdict, code):
    if VERDICT_OUT is not None:
        VERDICT_OUT.write_text(json.dumps(verdict, ensure_ascii=False), encoding="utf-8")
    print("VERDICT " + json.dumps(verdict, ensure_ascii=False), flush=True)
    return code


POP_FC_A = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature",
         "properties": {"name": "pa{}".format(i), "value": i},
         "geometry": {"type": "Point", "coordinates": [104.00 + i * 0.01, 30.60]}}
        for i in range(6)
    ],
}
POP_FC_B = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature",
         "properties": {"name": "pb{}".format(i), "value": i},
         "geometry": {"type": "Point", "coordinates": [110.00 + i * 0.01, 30.60]}}
        for i in range(8)
    ],
}


async def _seed_same_name_datasets(session_id):
    """两个同名「人口数据」：不同范围/日期 + 真实存储载荷（ref 可绑定）。"""
    from app.services.session_data import session_data_manager
    from app.services.artifact_registry import register_artifact

    ref_a = await session_data_manager.store(session_id, POP_FC_A, prefix="geojson")
    ref_b = await session_data_manager.store(session_id, POP_FC_B, prefix="geojson")
    await register_artifact(
        session_id, artifact_id=ref_a, artifact_type="vector_layer",
        producer_tool="g29_journey_seed",
        descriptor={"bbox": [104.0, 30.6, 104.05, 30.61]},
        metadata={"display_name": "人口数据", "seed": "A",
                  "tags": ["as_of=2020-05-01"]},
    )
    await register_artifact(
        session_id, artifact_id=ref_b, artifact_type="vector_layer",
        producer_tool="g29_journey_seed",
        descriptor={"bbox": [110.0, 30.6, 110.07, 30.61]},
        metadata={"display_name": "人口数据", "seed": "B",
                  "tags": ["as_of=2024-05-01"]},
    )
    return ref_a, ref_b


def _start_fake_redis():
    """隔离本地 Redis（fakeredis TCP —— 会话存储/分布式锁需要真实协议；
    环境无 redis-server，本进程内替身随 driver 生命周期）。"""
    import threading as _th

    import fakeredis
    import fakeredis._tcp_server as _tcp

    # fakeredis TcpFakeServer 默认 Resp3Writer —— redis-py 默认 RESP2 客户端
    # 收到 `_`（RESP3 null）即 Protocol Error。补丁：连接 writer 换 Resp2
    # （真实 redis-server 对 RESP2 客户端就是这个行为；仅 driver 进程内生效）。
    _orig_setup = _tcp.TCPFakeRequestHandler.setup

    def _resp2_setup(self):
        _orig_setup(self)
        self.writer = _tcp.Resp2Writer(self.client_address, self.wfile, self)

    _tcp.TCPFakeRequestHandler.setup = _resp2_setup

    server = fakeredis.TcpFakeServer(("127.0.0.1", 0))
    _th.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def main() -> int:
    fake_redis, redis_port = _start_fake_redis()
    os.environ["REDIS_URL"] = "redis://127.0.0.1:{}/0".format(redis_port)
    os.environ["CELERY_BROKER_URL"] = "redis://127.0.0.1:{}/1".format(redis_port)
    os.environ["CELERY_RESULT_BACKEND"] = "redis://127.0.0.1:{}/2".format(redis_port)

    stub_js = WORK / "journey_stub.mjs"
    stub_js.write_text(STUB_JS, encoding="utf-8")
    script_path = WORK / "journey_script.json"
    script_path.write_text(json.dumps(TURN_SCRIPTS), encoding="utf-8")
    rec = WORK / "journey_rec.jsonl"; rec.write_text("", encoding="utf-8")
    stub_err = WORK / "stub_err.log"

    with open(stub_err, "w") as fh:
        stub = subprocess.Popen(
            ["node", str(stub_js)], stdout=subprocess.DEVNULL, stderr=fh,
            env={**os.environ, "JOURNEY_SCRIPT": str(script_path), "JOURNEY_REC": str(rec)},
        )
    stub_port = None
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and stub_port is None:
        for line in stub_err.read_text().splitlines():
            if "STUB_READY" in line:
                stub_port = int(line.split()[1])
        time.sleep(0.1)
    if not stub_port:
        print(json.dumps({"fail": ["stub LLM did not start"]}))
        return 1

    os.environ["LLM_BASE_URL"] = "http://127.0.0.1:{}/v1".format(stub_port)
    os.environ["LLM_API_KEY"] = "stub-g29-key"
    os.environ["LLM_MODEL"] = "stub-model"

    api_port = _free_port()
    os.environ["WEBGIS_API_BASE"] = "http://127.0.0.1:{}".format(api_port)

    # 播种专用持久 loop（session 锁缓存绑定创建时的 loop —— 多次 asyncio.run
    # 会撞 "bound to a different event loop"）。
    import threading as _seed_th
    seed_loop = asyncio.new_event_loop()
    _seed_th.Thread(target=seed_loop.run_forever, daemon=True).start()

    def _seed(sid):
        fut = asyncio.run_coroutine_threadsafe(_seed_same_name_datasets(sid), seed_loop)
        return fut.result(timeout=60)

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

    def _api(path, payload=None, method=None, owner_token=None, timeout_s=60):
        url = "http://127.0.0.1:{}{}".format(api_port, path)
        headers = {"Content-Type": "application/json"}
        if owner_token:
            headers["X-Session-Token"] = owner_token
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method=method or ("POST" if data is not None else "GET"))
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8", "replace") or "{}")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            try:
                return exc.status, json.loads(body)
            except ValueError:
                return exc.status, {"raw": body[:300]}

    class _StreamCollector:
        """后台读 SSE（turn 可能在等待用户 —— 主线程并行作答）。"""

        def __init__(self, message, session_id, owner_token=None):
            self.message = message
            self.session_id = session_id
            self.owner_token = owner_token
            self.chunks = []
            self.error = None
            self.done = threading.Event()
            self.thread = threading.Thread(target=self._run, daemon=True)

        def _run(self):
            payload = {"message": self.message, "session_id": self.session_id}
            (WORK / ("sse_" + self.session_id + "_" + self.message[:6] + ".log")).write_text(
                "PENDING\n", encoding="utf-8")
            headers = {"Content-Type": "application/json"}
            if self.owner_token:
                headers["X-Session-Token"] = self.owner_token
            req = urllib.request.Request(
                "http://127.0.0.1:{}/api/v1/chat/stream".format(api_port),
                data=json.dumps(payload).encode(), headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=300) as resp:
                    for raw in resp:
                        line = raw.decode("utf-8", "replace")
                        self.chunks.append(line)
                        if line.startswith("event: done"):
                            break
            except Exception as exc:  # noqa: BLE001
                self.error = str(exc)[:200]
            finally:
                try:
                    (WORK / ("sse_" + self.session_id + "_" + self.message[:6] + ".log")).write_text(
                        self.text(), encoding="utf-8")
                except Exception:  # noqa: BLE001
                    pass
                self.done.set()

        def start(self):
            self.thread.start()
            return self

        def text(self):
            return "".join(self.chunks)

        def events(self):
            return _parse_sse(self.text())

        def wait(self, timeout_s=180):
            self.done.wait(timeout_s)
            return self.events()

    def _wait_pending_interaction(session_id, owner_token, timeout_s=60):
        """轮询 GET runtime-interactions 直到出现 pending dialog。"""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            status, view = _api(
                "/api/v1/chat/sessions/{}/runtime-interactions".format(session_id),
                owner_token=owner_token)
            if status == 200:
                pending = view.get("pending") or []
                if pending:
                    return pending
            time.sleep(0.4)
        return []

    def _wait_interaction_state(session_id, owner_token, interaction_id, want, timeout_s=60):
        deadline = time.monotonic() + timeout_s
        last = None
        while time.monotonic() < deadline:
            status, view = _api(
                "/api/v1/chat/sessions/{}/runtime-interactions".format(session_id),
                owner_token=owner_token)
            if status == 200:
                for bucket in ("pending", "recent"):
                    for item in view.get(bucket) or []:
                        if item.get("interaction_id") == interaction_id:
                            last = item.get("state")
                            if last in want:
                                return last
            time.sleep(0.4)
        return last

    def _owner_token_of(events):
        for e in events:
            tok = e["data"].get("owner_token")
            if tok:
                return tok
        return None

    try:
        # warmup（拉起 bridge / 扩展 / 模型配置映射）。
        warm = _StreamCollector("warmup", RUN + "-warm").start()
        warm_events = warm.wait(120)
        ready = ready and any(e["event"] in ("task_start", "task_complete") for e in warm_events)
        if not ready:
            verdict["fail"].append("app never became chat-ready under stub LLM")
            verdict["warm"] = {"error": warm.error, "names": [e["event"] for e in warm_events][:10]}
            return _emit_verdict(verdict, 1)

        # ── J29A：同名数据来源选择（两段式：意图激活 → 直呼歧义工具） ──
        sid_a = RUN + "-a"
        ref_a, ref_b = _seed(sid_a)
        phase1 = _StreamCollector("用人口数据画分布图", sid_a).start()
        phase1_events = phase1.wait(150)
        owner_a = _owner_token_of(phase1_events)
        verdict["journeys"].setdefault("J29A", {})["phase1_complete"] = any(
            e["event"] == "task_complete" for e in phase1_events)
        stream_a = _StreamCollector("继续把人口数据画到地图上", sid_a, owner_token=owner_a).start()
        # owner token 会在 SSE `session` 事件里 —— 但作答端点需要它；从流的
        # 早期块里抓。轮询交互出现（同时也给了 token 到达的时间）。
        pending = _wait_pending_interaction(sid_a, owner_a, timeout_s=150) if owner_a else []
        verdict["journeys"]["J29A"].setdefault("phase1_complete", None)
        j29a = verdict["journeys"]["J29A"]
        j29a["pending_count"] = len(pending)
        if not pending:
            j29a["stream_error"] = stream_a.error
            j29a["stream_tail"] = stream_a.text()[-800:]
            verdict["fail"].append("J29A: no pending interaction appeared")
        else:
            card = pending[0]
            j29a["method"] = card.get("method")
            j29a["options"] = card.get("options")
            j29a["sse_pi_interaction"] = "event: pi_interaction\n" in stream_a.text()
            # 选择版本日期 2024 的 B（范围 110.x）。
            chosen_display = next(
                (o for o in card.get("options", []) if "2024-05-01" in o), None)
            if chosen_display is None:
              chosen_display = next(
                  (o for o in card.get("options", []) if "[110." in o), None)
            j29a["chosen_display"] = chosen_display
            iid = card["interaction_id"]
            status, out = _api(
                "/api/v1/chat/sessions/{}/runtime-interactions/{}/answer".format(sid_a, iid),
                {"value": chosen_display}, owner_token=owner_a)
            j29a["answer_status"] = status
            j29a["answer_result"] = out.get("result")
            state = _wait_interaction_state(
                sid_a, owner_a, iid, {"resolved", "answer_written"}, timeout_s=60)
            events = stream_a.wait(180)
            # turn 完成后再确认续接观察（resolve 回调晚于首个轮询命中）。
            state = _wait_interaction_state(
                sid_a, owner_a, iid, {"resolved"}, timeout_s=30) or state
            j29a["final_state"] = state
            j29a["names"] = [e["event"] for e in events]
            j29a["step_errors"] = [
                {"tool": e["data"].get("tool"),
                 "error": str(e["data"].get("error"))[:600]}
                for e in events if e["event"] == "step_error"
            ]
            j29a["interaction_update_seen"] = "event: pi_interaction_update\n" in stream_a.text()
            # MapSpec 权威（进程内 store）：只消费被选 ref B。
            spec_text = asyncio.run_coroutine_threadsafe(_read_mapspec_text(sid_a), seed_loop).result(timeout=60)
            j29a["mapspec_has_ref_b"] = ref_b in spec_text
            j29a["mapspec_has_ref_a"] = ref_a in spec_text
            j29a["task_complete"] = any(e["event"] == "task_complete" for e in events)
            if not (j29a["method"] == "select" and state == "resolved"
                    and j29a["mapspec_has_ref_b"] and not j29a["mapspec_has_ref_a"]):
                verdict["fail"].append(
                    "J29A: selection not consumed authoritatively ({})".format(j29a))

        # ── J29B：标题核对（editor + 多行中文） ──
        sid_b = RUN + "-b"
        _seed(sid_b)
        stream_b = _StreamCollector("画人口图前先让我核对标题", sid_b).start()
        owner_b = None
        pending_b = []
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            for line in stream_b.chunks:
                m = re.search(r'"owner_token"\s*:\s*"([^"]+)"', line)
                if m:
                    owner_b = m.group(1)
            if owner_b:
                pending_b = _wait_pending_interaction(sid_b, owner_b, timeout_s=150)
                if pending_b:
                    break
            time.sleep(0.4)
        verdict["journeys"]["J29B"] = {}
        j29b = verdict["journeys"]["J29B"]
        if not pending_b:
            j29b["stream_error"] = stream_b.error
            j29b["stream_tail"] = stream_b.text()[-800:]
            verdict["fail"].append("J29B: no pending interaction appeared")
        else:
            card = pending_b[0]
            j29b["method"] = card.get("method")
            user_title = "成都市人口密度图（2024）\n数据来源：合成测试数据"
            iid = card["interaction_id"]
            status, out = _api(
                "/api/v1/chat/sessions/{}/runtime-interactions/{}/answer".format(sid_b, iid),
                {"value": user_title}, owner_token=owner_b)
            j29b["answer_status"] = status
            state = _wait_interaction_state(sid_b, owner_b, iid, {"resolved"}, timeout_s=60)
            j29b["final_state"] = state
            events = stream_b.wait(180)
            spec_text = asyncio.run_coroutine_threadsafe(_read_mapspec_text(sid_b), seed_loop).result(timeout=60)
            j29b["title_adopted"] = user_title.split("\n")[0] in spec_text
            j29b["attribution_line_adopted"] = "合成测试数据" in spec_text
            j29b["task_complete"] = any(e["event"] == "task_complete" for e in events)
            if not (j29b["method"] == "editor" and j29b["title_adopted"]
                    and j29b["task_complete"]):
                verdict["fail"].append("J29B: title not adopted ({})".format(j29b))

        # ── J29C：运行中追加指令（steer / follow_up）+ 队列可见性 ──
        sid_c = RUN + "-c"
        stream_c = _StreamCollector("跑一个慢分析", sid_c).start()
        owner_c = None
        turn_c = None
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and (turn_c is None or owner_c is None):
            for line in stream_c.chunks:
                if turn_c is None and "turn-" in line:
                    m = re.search(r'"turn_id"\s*:\s*"(turn-[^"]+)"', line)
                    if m:
                        turn_c = m.group(1)
                if owner_c is None:
                    m = re.search(r'"owner_token"\s*:\s*"([^"]+)"', line)
                    if m:
                        owner_c = m.group(1)
            time.sleep(0.2)
        verdict["journeys"]["J29C"] = {}
        j29c = verdict["journeys"]["J29C"]
        if not (turn_c and owner_c):
            j29c["stream_tail"] = stream_c.text()[-500:]
            verdict["fail"].append("J29C: turn id / owner token not observed")
        else:
            j29c["turn_id"] = turn_c
            st_adjust, out_adjust = _api(
                "/api/v1/chat/sessions/{}/turns/{}/messages".format(sid_c, turn_c),
                {"message": "范围改为合成区A", "mode": "adjust"}, owner_token=owner_c)
            j29c["adjust_status"] = st_adjust
            j29c["adjust_accepted"] = (out_adjust or {}).get("accepted")
            j29c["adjust_disposition"] = (out_adjust or {}).get("disposition")
            st_queue, out_queue = _api(
                "/api/v1/chat/sessions/{}/turns/{}/messages".format(sid_c, turn_c),
                {"message": "完成后顺便统计要素数", "mode": "queue"}, owner_token=owner_c)
            j29c["queue_status"] = st_queue
            j29c["queue_accepted"] = (out_queue or {}).get("accepted")
            st_qview, qview = _api(
                "/api/v1/chat/sessions/{}/turns/{}/queue".format(sid_c, turn_c),
                owner_token=owner_c)
            j29c["queue_view_status"] = st_qview
            j29c["pending_message_count"] = ((qview or {}).get("vendor_state") or {}).get("pending_message_count")
            j29c["ledger_len"] = len((qview or {}).get("messages") or [])
            events = stream_c.wait(180)
            j29c["steer_sse_seen"] = "event: pi_turn_message\n" in stream_c.text()
            j29c["task_complete"] = any(e["event"] == "task_complete" for e in events)
            if not (j29c["adjust_accepted"] and j29c["queue_accepted"] and j29c["ledger_len"] >= 2):
                verdict["fail"].append("J29C: steer/follow_up not accepted ({})".format(j29c))

        # ── J29D：取消同名选择（两段式） ──
        sid_d = RUN + "-d"
        _seed(sid_d)
        phase1d = _StreamCollector("再画一张人口图但这次我会取消选择", sid_d).start()
        phase1d_events = phase1d.wait(150)
        owner_d = _owner_token_of(phase1d_events)
        stream_d = _StreamCollector("绑定数据但我会取消选择", sid_d, owner_token=owner_d).start()
        pending_d = _wait_pending_interaction(sid_d, owner_d, timeout_s=150) if owner_d else []
        verdict["journeys"]["J29D"] = {}
        j29d = verdict["journeys"]["J29D"]
        if not pending_d:
            j29d["stream_tail"] = stream_d.text()[-800:]
            verdict["fail"].append("J29D: no pending interaction appeared")
        else:
            card = pending_d[0]
            iid = card["interaction_id"]
            st_cancel, out_cancel = _api(
                "/api/v1/chat/sessions/{}/runtime-interactions/{}/cancel".format(sid_d, iid),
                {}, owner_token=owner_d)
            j29d["cancel_status"] = st_cancel
            j29d["cancel_state"] = (out_cancel or {}).get("state")
            state = _wait_interaction_state(sid_d, owner_d, iid, {"cancelled"}, timeout_s=30)
            j29d["final_state"] = state
            events = stream_d.wait(180)
            j29d["task_complete"] = any(e["event"] == "task_complete" for e in events)
            spec_text = asyncio.run_coroutine_threadsafe(_read_mapspec_text(sid_d), seed_loop).result(timeout=60)
            j29d["no_layer_bound"] = '"pop2"' not in spec_text
            if not (j29d["final_state"] == "cancelled" and j29d["task_complete"]
                    and j29d["no_layer_bound"]):
                verdict["fail"].append("J29D: cancel semantics broken ({})".format(j29d))

        # ── 越权负例（§6-16）：无 owner token 的作答被拒且不泄漏内容 ──
        st_forge, out_forge = _api(
            "/api/v1/chat/sessions/{}/runtime-interactions/nonexistent/answer".format(sid_d),
            {"value": "x"})
        verdict["unauthorized_answer"] = {"status": st_forge}
        if st_forge not in (401, 403, 404):
            verdict["fail"].append(
                "unauthorized answer not rejected: {}".format(st_forge))

    except Exception as exc:  # noqa: BLE001
        verdict["fail"].append("driver error: " + repr(exc)[:400])
    finally:
        try:
            stub.terminate()
        except Exception:  # noqa: BLE001
            pass
    code = _emit_verdict(verdict, 1 if verdict["fail"] else 0)
    # uvicorn/线程池不随主线程退出 —— driver 以显式 _exit 收口（verdict 已落盘，
    # stdout 已冲刷 —— os._exit 不走缓冲冲刷，必须手动）。
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


if __name__ == "__main__":
    sys.exit(main())
'''


def test_g29_runtime_interaction_journeys(tmp_path: Path):
    entry = _vendor_entry()
    if entry is None:
        pytest.skip(
            "vendored Pi RPC entry not built — run scripts/build-vendor-pi.sh "
            "(explicit skip, never silent-green)"
        )
    verdict_path = tmp_path / "g29_verdict.json"
    proc = subprocess.run(
        [sys.executable, "-c", _DRIVER_SOURCE, str(REPO_ROOT), str(verdict_path)],
        capture_output=True, text=True, timeout=900, cwd=str(REPO_ROOT),
    )
    verdict = None
    if verdict_path.exists():
        # 文件是第一真相（stdout 管道缓冲在 os._exit 下不可靠 —— 已手动冲刷，
        # 双通道仍以文件优先）。
        try:
            verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
        except ValueError:
            verdict = None
    if verdict is None:
        verdict_line = next(
            (ln for ln in proc.stdout.splitlines() if ln.startswith("VERDICT ")), "")
        assert verdict_line, (
            "driver produced no VERDICT; stderr tail:\n"
            + "\n".join(proc.stderr.splitlines()[-40:])
        )
        verdict = json.loads(verdict_line[len("VERDICT "):])

    assert not verdict.get("fail"), (
        "G29 journey failures: {}".format(json.dumps(verdict.get("fail"), ensure_ascii=False))
    )
    journeys = verdict.get("journeys", {})
    # J29A：选择被权威消费。
    j29a = journeys.get("J29A", {})
    assert j29a.get("method") == "select"
    # resolved = 扩展 resolve 回调已观察（四事实之「扩展等待已结束并继续」）；
    # answer_written 兜底仅在 MapSpec 权威消费已证实时可接受（领域续行证据）。
    assert j29a.get("final_state") in ("resolved", "answer_written"), j29a.get("final_state")
    assert j29a.get("mapspec_has_ref_b") is True
    assert j29a.get("mapspec_has_ref_a") is False
    assert j29a.get("sse_pi_interaction") is True
    # J29B：标题/来源采用。
    j29b = journeys.get("J29B", {})
    assert j29b.get("method") == "editor"
    assert j29b.get("title_adopted") is True
    assert j29b.get("attribution_line_adopted") is True
    assert j29b.get("task_complete") is True
    # J29C：steer/follow_up 真实接纳 + 队列可见。
    j29c = journeys.get("J29C", {})
    assert j29c.get("adjust_accepted") is True
    assert j29c.get("queue_accepted") is True
    assert (j29c.get("ledger_len") or 0) >= 2
    # J29D：取消语义。
    j29d = journeys.get("J29D", {})
    assert j29d.get("final_state") == "cancelled"
    assert j29d.get("no_layer_bound") is True
    # 越权负例。
    assert verdict.get("unauthorized_answer", {}).get("status") in (401, 403, 404)
