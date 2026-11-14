"""G37 场景验证车道：真实 Pi bridge + 双获准模型 + stub LLM（WP12）。

与 ``test_pi_stub_llm_journeys.py`` 同架构：pytest 只做门控与裁决，独立
driver 子进程承载全部旅程 —— boot stub LLM（node，记录每次请求的
``model``/``usage`` 回包）→ uvicorn 线程承载生产 app（lifespan 拉起真实
vendor/pi v1.5.2 bridge，models.json 投影**双获准模型**）→ 真实 HTTP 旅程
→ ``VERDICT {json}``。

旅程（任务书 §6）：
- J0/M01+M20：s1 继承默认（g37-a）→ PUT 显式 g37-b → 带**真实 GIS 工具
  调用**的 turn —— 原生 get_state 回读与 stub 实收请求均为 g37-b，有
  step_result（真实 dispatch）与 model_status 回执；message_end usage 分
  项入账（assistant 两次调用 = 工具轮 + 终答轮）。
- J1/M02+M03+M05：s2(显式 g37-a)/s3(显式 g37-b)/s4(继承) 并发交错 + s2
  回切 —— 每 turn 的 stub 实收模型与会话 preference 一一对应；不串模型、
  不串身份；s4 恒用管理员默认。
- J2/M30：s5 预设极小 token cap → 首个 turn 在 prompt 字节发出前被拒
  （stub 对该会话请求计数 == 0；task_error error_class=policy_reject；
  回执 status=budget_rejected）。
- J3/M06：driver 停止 Pi worker → 下一 turn lazy respawn → s3 的显式选择
  在新 worker 上重新应用（stub 实收仍 g37-b）；累计用量不归零。
- J4/M23：完整 turn 后携带 Last-Event-ID 重放 → 不发第二次 prompt（stub
  计数不变）、账本不重复计费（totals 不变）。

门控：node + 已构建 vendor；显式 opt-in（PI_JOURNEY_E2E=1）。缺席显式
skip（绝不静默绿）。
"""
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
    requires_node,
    pytest.mark.skipif(
        os.getenv("PI_JOURNEY_E2E") != "1",
        reason=(
            "stub-LLM full-stack journeys are opt-in: set PI_JOURNEY_E2E=1 "
            "to run — explicit skip, never silent-green"
        ),
    ),
]


_DRIVER_SOURCE = r'''#!/usr/bin/env python3
"""G37 journey driver（独立进程）。输出单行 "VERDICT {json}"。"""
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
from pathlib import Path

REPO_ROOT = Path(sys.argv[1])
WORK = Path(tempfile.mkdtemp(prefix="g37-journey-"))
VERDICT_OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else None

import uuid

RUN = "g37" + uuid.uuid4().hex[:8]

os.environ["PI_AUTOSTART"] = "true"
# G37 目录：两个获准文本模型（同一 stub 端点），预算上限可自设。
os.environ["LLM_MODEL"] = "g37-a"
os.environ["SESSION_MODEL_CATALOG_EXTRA"] = "g37-b"
os.environ["SESSION_MODEL_TOKEN_CAP_MAX"] = "5000000"
os.environ["SESSION_MODEL_TOKEN_CAP_DEFAULT"] = "0"

STUB_JS = r"""
import http from 'node:http';
import { appendFileSync } from 'node:fs';
const scriptPath = process.env.JOURNEY_SCRIPT;
const recFile = process.env.JOURNEY_REC;
const turnScripts = JSON.parse(await import('node:fs').then((fs) => fs.readFileSync(scriptPath, 'utf8')));
const counters = {};
const srv = http.createServer((req, res) => {
  let body = ''; req.on('data', (c) => (body += c));
  req.on('end', async () => {
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
    // G37：记录每次真实请求实际携带的 model（+ 首行键 + 工具声明面）。
    appendFileSync(recFile, JSON.stringify({
      key: base, model: parsed.model || '',
      tools: (parsed.tools || []).map((t) => t.function?.name || t.name),
    }) + '\n');
    const delayS = Number(step.delay_s || 0);
    if (delayS > 0) await new Promise((r) => setTimeout(r, delayS * 1000));
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    const sse = (o) => res.write(`data: ${JSON.stringify(o)}\n\n`);
    const id = 'c' + Date.now();
    // 供应商物理响应模型（responseModel）：与请求 model 可区分。
    const responseModel = (parsed.model || 'stub') + '-r9';
    if (step.tool) {
      sse({ id, object: 'chat.completion.chunk', created: 1, model: responseModel,
        choices: [{ index: 0, delta: { tool_calls: [{ index: 0, id: 'call_' + Math.random().toString(36).slice(2, 8),
          type: 'function', function: { name: step.tool, arguments: JSON.stringify(step.args || {}) } }] } }] });
      sse({ id, object: 'chat.completion.chunk', created: 1, model: responseModel,
        choices: [{ index: 0, delta: {}, finish_reason: 'tool_calls' }],
        usage: { prompt_tokens: 120, completion_tokens: 40,
                 prompt_tokens_details: { cached_tokens: 10 },
                 completion_tokens_details: { reasoning_tokens: 6 } } });
    } else {
      sse({ id, object: 'chat.completion.chunk', created: 1, model: responseModel,
        choices: [{ index: 0, delta: { content: step.text || 'done' } }] });
      sse({ id, object: 'chat.completion.chunk', created: 1, model: responseModel,
        choices: [{ index: 0, delta: {}, finish_reason: 'stop' }],
        usage: { prompt_tokens: 100, completion_tokens: 30,
                 prompt_tokens_details: { cached_tokens: 5 },
                 completion_tokens_details: { reasoning_tokens: 4 } } });
    }
    res.write('data: [DONE]\n\n'); res.end();
  });
});
srv.listen(0, '127.0.0.1', () => process.stderr.write('STUB_READY ' + srv.address().port + '\n'));
process.on('SIGTERM', () => process.exit(0));
"""

TURN_SCRIPTS = {
    "warmup": [{"text": "warmup-ok"}],
    "查询成都学校分布": [
        {"tool": "webgis_map_intent", "args": {"query": "成都学校分布"}},
        {"text": "学校分布数据已获取并纳入地图计划。"},
    ],
    "查询成都学校分布专题": [
        {"tool": "webgis_map_intent", "args": {"query": "成都学校分布专题"}},
        {"text": "学校分布专题数据已获取并纳入地图计划。"},
    ],
    "s2-alpha-task": [{"text": "s2-alpha-done"}],
    "s2-alpha-task-again": [{"text": "s2-alpha-again-done"}],
    "s3-beta-task": [{"text": "s3-beta-done"}],
    "s3-beta-task-2": [{"text": "s3-beta-2-done"}],
    "s3-beta-task-after-respawn": [{"text": "s3-beta-respawn-done"}],
    "s4-inherit-task": [{"text": "s4-inherit-done"}],
    "s5-budget-task": [{"text": "s5-should-never-run"}],
    "s7-replay-task": [{"text": "s7-replay-done"}],
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
    if VERDICT_OUT is not None:
        try:
            VERDICT_OUT.write_text(
                json.dumps(verdict, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    print("VERDICT " + json.dumps(verdict, ensure_ascii=False))
    return code


def main() -> int:
    stub_js = WORK / "journey_stub.mjs"
    stub_js.write_text(STUB_JS, encoding="utf-8")
    script_path = WORK / "journey_script.json"
    script_path.write_text(json.dumps(TURN_SCRIPTS), encoding="utf-8")
    rec = WORK / "journey_rec.jsonl"
    rec.write_text("", encoding="utf-8")
    stub_err = WORK / "stub_err.log"

    with open(stub_err, "w") as fh:
        stub = subprocess.Popen(
            ["node", str(stub_js)], stdout=subprocess.DEVNULL, stderr=fh,
            env={**os.environ, "JOURNEY_SCRIPT": str(script_path),
                 "JOURNEY_REC": str(rec)},
        )
    deadline = time.monotonic() + 10
    stub_port = None
    while time.monotonic() < deadline:
        for line in stub_err.read_text().splitlines():
            if "STUB_READY" in line:
                stub_port = int(line.split()[1])
        if stub_port:
            break
        time.sleep(0.1)
    if not stub_port:
        print(json.dumps({"fail": ["stub LLM did not become ready"]}))
        stub.terminate()
        return 1

    os.environ["LLM_BASE_URL"] = "http://127.0.0.1:{}/v1".format(stub_port)
    os.environ["LLM_API_KEY"] = "stub-g37-key"

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

    def _req(method, path, body=None, owner_token=None, timeout_s=60):
        headers = {"Content-Type": "application/json"}
        if owner_token:
            headers["X-Session-Token"] = owner_token
        req = urllib.request.Request(
            "http://127.0.0.1:{}{}".format(api_port, path),
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers, method=method,
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8", "replace") or "{}")

    def _stream(message, session_id, owner_token=None, timeout_s=120.0,
                last_event_id=None):
        payload = {"message": message, "session_id": session_id}
        headers = {"Content-Type": "application/json"}
        if owner_token:
            headers["X-Session-Token"] = owner_token
        if last_event_id is not None:
            headers["Last-Event-ID"] = str(last_event_id)
        req = urllib.request.Request(
            "http://127.0.0.1:{}/api/v1/chat/stream".format(api_port),
            data=json.dumps(payload).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read().decode("utf-8", "replace")
        (WORK / ("sse_" + session_id + ".log")).write_text(body, encoding="utf-8")
        return _parse_sse(body)

    def _owner_token_of(events):
        for e in events:
            tok = e["data"].get("owner_token")
            if tok:
                return tok
        return None

    def _stub_recs():
        out = []
        for line in rec.read_text().splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
        return out

    def _model_of_event(events, name):
        for e in events:
            if e["event"] == name:
                return e["data"]
        return None

    def _fail(journey, msg):
        verdict["fail"].append(f"{journey}: {msg}")

    def _usage_eventually(session_id, owner_token, predicate, tries=40):
        """账本行由 fire-and-forget 任务落盘 —— 轮询到断言条件或超时。"""
        last = None
        for _ in range(tries):
            last = _req("GET",
                        "/api/v1/chat/sessions/{}/usage".format(session_id),
                        owner_token=owner_token)
            try:
                if predicate(last):
                    return last
            except Exception:
                pass
            time.sleep(0.5)
        return last

    try:
        # ── 就绪 warmup ────────────────────────────────────────────
        if not ready:
            _fail("boot", "app/bridge did not become ready")
            return _emit_verdict(verdict, 1)
        warm = None
        for _warm in range(3):
            try:
                warm = _stream("warmup", "{}-warm".format(RUN), timeout_s=60)
                if any(e["event"] == "task_start" for e in warm):
                    break
                time.sleep(5)
            except Exception as exc:
                time.sleep(5)
                if _warm == 2:
                    _fail("boot", "warmup failed: {}".format(str(exc)[:200]))
        if not any(e["event"] == "task_start" for e in (warm or [])):
            _fail("boot", "warmup never reached task_start")

        # ── J0/M01+M20：显式选择 g37-b + 真实工具 + usage 入账 ────
        s1 = "{}-s1".format(RUN)
        ev1 = _stream("查询成都学校分布", s1, timeout_s=120)
        tok1 = _owner_token_of(ev1)
        # 会话已建（首条消息）→ 显式选择 g37-b，再跑**独立键**的完整
        # 工具 turn（stub 脚本按消息首行键控 —— 新键从第 0 步起跑）。
        put1 = _req("PUT", "/api/v1/chat/sessions/{}/model".format(s1),
                    body={
                        "mode": "explicit", "model_id": "g37-b",
                        "expected_revision": 0,
                    }, owner_token=tok1)
        ev1b = _stream("查询成都学校分布专题", s1, owner_token=tok1, timeout_s=120)
        model_status = _model_of_event(ev1b, "model_status") or {}

        def _last_turn_complete(u):
            turns = u.get("recent_turns") or []
            return bool(turns) and turns[-1].get("complete") and \
                turns[-1].get("requested_model") == "g37-b"

        usage1 = _usage_eventually(s1, tok1, _last_turn_complete)
        recs1 = [r for r in _stub_recs() if r["key"] == "查询成都学校分布专题"]
        verdict["journeys"]["J0"] = {
            "put_revision": put1.get("preference", {}).get("revision"),
            "model_status": {
                "status": model_status.get("status"),
                "requested": (model_status.get("requested") or {}).get("model"),
                "effective": (model_status.get("effective") or {}).get("model"),
            },
            "has_step_result": any(e["event"] == "step_result" for e in ev1b),
            "has_task_complete": any(e["event"] == "task_complete" for e in ev1b),
            "stub_models": sorted({r["model"] for r in recs1}),
            "last_turn": {
                k: (usage1.get("recent_turns") or [{}])[-1].get(k)
                for k in ("requested_model", "effective_model", "response_model",
                          "status", "complete")
            } if usage1.get("recent_turns") else {},
            "recent_turn_tokens": (
                (usage1.get("recent_turns") or [{}])[-1].get("tokens")
                if usage1.get("recent_turns") else None
            ),
            "records_meta": (
                (usage1.get("recent_turns") or [{}])[-1].get("records")
                if usage1.get("recent_turns") else None
            ),
            "totals": usage1.get("totals"),
        }
        jt = verdict["journeys"]["J0"]
        if jt["model_status"]["effective"] != "g37-b":
            _fail("J0", "effective model is not g37-b: {}".format(jt["model_status"]))
        if not jt["stub_models"] or "g37-a" in jt["stub_models"]:
            _fail("J0", "stub models wrong after explicit g37-b: {}".format(jt["stub_models"]))
        if not jt["has_step_result"] or not jt["has_task_complete"]:
            _fail("J0", "missing real tool step_result/task_complete in turn")
        lt = jt["last_turn"]
        if lt.get("requested_model") != "g37-b" or lt.get("effective_model") != "g37-b":
            _fail("J0", "receipt requested/effective mismatch: {}".format(lt))
        if lt.get("response_model") != "g37-b-r9":
            _fail("J0", "responseModel not recorded: {}".format(lt))
        if not lt.get("complete"):
            _fail("J0", "usage turn not complete")
        toks = jt["recent_turn_tokens"] or {}
        if not (toks.get("input", 0) > 0 and toks.get("output", 0) > 0
                and toks.get("cacheRead", 0) > 0):
            _fail("J0", "usage dimensions missing: {}".format(toks))
        # 两次模型调用（工具轮 + 终答轮）→ 2 records；reasoning ⊂ output
        rm = jt["records_meta"] or {}
        if rm.get("expected") != 2 or rm.get("written") != 2:
            _fail("J0", "expected 2 usage records (tool turn + final), got {}".format(rm))
        expect_total = toks.get("input", 0) + toks.get("output", 0) \
            + toks.get("cacheRead", 0) + toks.get("cacheWrite", 0)
        if toks.get("total") != expect_total:
            _fail("J0", "total != in+out+cacheRead+cacheWrite: {}".format(toks))

        # ── J1/M02+M03+M05：三会话并发交错 + 同 worker 回切 ───────
        s2 = "{}-s2".format(RUN)
        s3 = "{}-s3".format(RUN)
        s4 = "{}-s4".format(RUN)
        ev2w = _stream("s2-alpha-task", s2, timeout_s=90)
        tok2 = _owner_token_of(ev2w)
        ev3w = _stream("s3-beta-task", s3, timeout_s=90)
        tok3 = _owner_token_of(ev3w)
        ev4w = _stream("s4-inherit-task", s4, timeout_s=90)
        tok4 = _owner_token_of(ev4w)
        _req("PUT", "/api/v1/chat/sessions/{}/model".format(s2),
             body={"mode": "explicit", "model_id": "g37-a", "expected_revision": 0},
             owner_token=tok2)
        _req("PUT", "/api/v1/chat/sessions/{}/model".format(s3),
             body={"mode": "explicit", "model_id": "g37-b", "expected_revision": 0},
             owner_token=tok3)
        ev2 = _stream("s2-alpha-task-again", s2, owner_token=tok2, timeout_s=90)
        ev3 = _stream("s3-beta-task-2", s3, owner_token=tok3, timeout_s=90)
        ev4 = _stream("s4-inherit-task", s4, owner_token=tok4, timeout_s=90)
        # 回切 s2（同一 worker 先后服务 s2→s3→s4→s2，M05）
        ev2b = _stream("s2-alpha-task-again", s2, owner_token=tok2, timeout_s=90)

        def _stub_models_for(key):
            return sorted({r["model"] for r in _stub_recs() if r["key"] == key})

        prefs = {
            sid: _req("GET", "/api/v1/chat/sessions/{}/model".format(sid),
                      owner_token=tok)["preference"]
            for sid, tok in ((s2, tok2), (s3, tok3), (s4, tok4))
        }
        verdict["journeys"]["J1"] = {
            "stub_models": {
                "s2": _stub_models_for("s2-alpha-task-again"),
                "s3": _stub_models_for("s3-beta-task-2"),
                "s4": _stub_models_for("s4-inherit-task"),
            },
            "prefs": prefs,
            "s2_model_status": (_model_of_event(ev2b, "model_status") or {}).get("status"),
        }
        jj = verdict["journeys"]["J1"]
        if jj["stub_models"]["s2"] != ["g37-a"]:
            _fail("J1", "s2 requests not on g37-a: {}".format(jj["stub_models"]["s2"]))
        if jj["stub_models"]["s3"] != ["g37-b"]:
            _fail("J1", "s3 requests not on g37-b: {}".format(jj["stub_models"]["s3"]))
        if jj["stub_models"]["s4"] != ["g37-a"]:
            _fail("J1", "inherit session s4 not on default g37-a: {}".format(jj["stub_models"]["s4"]))
        if jj["prefs"][s2].get("model_id") != "g37-a" or \
                jj["prefs"][s3].get("model_id") != "g37-b":
            _fail("J1", "preferences drifted: {}".format(jj["prefs"]))
        if jj["prefs"][s4].get("mode") != "inherit":
            _fail("J1", "s4 should stay inherit: {}".format(jj["prefs"][s4]))
        if jj["s2_model_status"] not in ("applied", None):
            _fail("J1", "s2 re-entry model application status: {}".format(jj["s2_model_status"]))

        # ── J2/M30：预算拒绝发生在 prompt 字节之前 ─────────────────
        s5 = "{}-s5".format(RUN)
        ev5w = _stream("s5-budget-task", s5, timeout_s=90)
        tok5 = _owner_token_of(ev5w)
        put5 = _req("PUT", "/api/v1/chat/sessions/{}/model".format(s5),
                    body={"mode": "inherit", "session_token_cap": 100,
                          "expected_revision": 0},
                    owner_token=tok5)
        before_count = len([r for r in _stub_recs() if r["key"] == "s5-budget-task"])
        ev5 = _stream("s5-budget-task", s5, owner_token=tok5, timeout_s=60)
        after_count = len([r for r in _stub_recs() if r["key"] == "s5-budget-task"])
        err5 = _model_of_event(ev5, "task_error") or {}
        ms5 = _model_of_event(ev5, "model_status") or {}

        def _budget_row(u):
            turns = u.get("recent_turns") or []
            return bool(turns) and turns[-1].get("status") == "budget_rejected"

        usage5 = _usage_eventually(s5, tok5, _budget_row)
        verdict["journeys"]["J2"] = {
            "cap_set": put5.get("preference", {}).get("session_token_cap"),
            "stub_requests_before": before_count,
            "stub_requests_after": after_count,
            "error_class": err5.get("error_class"),
            "model_status": ms5.get("status"),
            "last_turn_status": (
                (usage5.get("recent_turns") or [{}])[-1].get("status")
                if usage5.get("recent_turns") else None
            ),
        }
        j2 = verdict["journeys"]["J2"]
        if j2["stub_requests_after"] != j2["stub_requests_before"]:
            _fail("J2", "stub saw new requests despite budget rejection: {}".format(j2))
        if j2["error_class"] != "policy_reject" or j2["model_status"] != "budget_rejected":
            _fail("J2", "budget rejection not visible: {}".format(j2))
        if j2["last_turn_status"] != "budget_rejected":
            _fail("J2", "receipt status not budget_rejected: {}".format(j2))

        # ── J3/M06：worker 崩溃 → respawn 后偏好重应用、累计不归零 ──
        usage3_before = _req(
            "GET", "/api/v1/chat/sessions/{}/usage".format(s3), owner_token=tok3)
        total_before = (usage3_before.get("totals") or {}).get("total", 0)
        killed = 0
        try:
            from app.agent_pi_bridge import get_bridge_pool
            pool = get_bridge_pool()
            bridges = list(pool.bridges) if pool else []
            for bridge in bridges:
                proc = getattr(bridge._rpc, "_process", None)
                if proc is not None and proc.poll() is None:
                    proc.kill()  # 真实子进程死亡 → reader 标记 → lazy respawn
                    killed += 1
            # 等死亡被桥**观测到**（reader EOF → process_died）再发 turn ——
            # 否则 kill 与 turn 启动竞速：prompt 落在已死进程上（诚实的
            # transport_error，但不是 M06 要验证的 respawn 路径）。
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if all(
                    getattr(b._rpc, "process_died", True) for b in bridges
                ):
                    break
                time.sleep(0.2)
        except Exception as exc:
            _fail("J3", "worker kill failed: {}".format(str(exc)[:160]))
        if killed == 0:
            _fail("J3", "no live Pi worker found to kill")

        def _respawn_turn_recorded(u):
            turns = u.get("recent_turns") or []
            return bool(turns) and turns[-1].get("requested_model") == "g37-b" and \
                turns[-1].get("tokens", {}).get("total", 0) > 0

        ev3b = _stream("s3-beta-task-after-respawn", s3, owner_token=tok3,
                       timeout_s=150)
        ms3b = _model_of_event(ev3b, "model_status") or {}
        usage3_after = _usage_eventually(s3, tok3, _respawn_turn_recorded, tries=60)
        total_after = (usage3_after.get("totals") or {}).get("total", 0)
        respawn_models = sorted({
            r["model"] for r in _stub_recs()
            if r["key"] == "s3-beta-task-after-respawn"
        })
        verdict["journeys"]["J3"] = {
            "model_status": ms3b.get("status"),
            "effective": (ms3b.get("effective") or {}).get("model"),
            "stub_models": respawn_models,
            "total_before": total_before,
            "total_after": total_after,
            "turn_event_types": [e["event"] for e in ev3b][:10],
            "turn_error": (_model_of_event(ev3b, "task_error") or {}).get("error_class"),
        }
        j3 = verdict["journeys"]["J3"]
        if j3["effective"] != "g37-b" or j3["stub_models"] != ["g37-b"]:
            _fail("J3", "preference not reapplied after respawn: {}".format(j3))
        if not (j3["total_after"] > j3["total_before"] > 0):
            _fail("J3", "cumulative usage reset or did not grow: {}".format(j3))

        # ── J4/M23：Last-Event-ID 重放 ≠ 重发 prompt ≠ 重复计费 ────
        s7 = "{}-s7".format(RUN)
        ev7 = _stream("s7-replay-task", s7, timeout_s=90)
        tok7 = _owner_token_of(ev7)
        usage7_before = _req(
            "GET", "/api/v1/chat/sessions/{}/usage".format(s7), owner_token=tok7)
        recs7_before = len([
            r for r in _stub_recs() if r["key"] == "s7-replay-task"
        ])
        ev7r = _stream("s7-replay-task", s7, owner_token=tok7,
                       last_event_id=1, timeout_s=60)
        usage7_after = _req(
            "GET", "/api/v1/chat/sessions/{}/usage".format(s7), owner_token=tok7)
        recs7_after = len([
            r for r in _stub_recs() if r["key"] == "s7-replay-task"
        ])
        verdict["journeys"]["J4"] = {
            "stub_requests_before": recs7_before,
            "stub_requests_after": recs7_after,
            "total_before": (usage7_before.get("totals") or {}).get("total"),
            "total_after": (usage7_after.get("totals") or {}).get("total"),
            "replay_events": len(ev7r),
            "first_turn_completed": any(e["event"] == "task_complete" for e in ev7),
        }
        j4 = verdict["journeys"]["J4"]
        if not j4["first_turn_completed"]:
            # 非空转守卫：首个 turn 必须真实完成（否则“计数不变”是空洞真）
            _fail("J4", "first s7 turn did not complete: {}".format(
                [e["event"] for e in ev7][:6]))
        if j4["stub_requests_after"] != j4["stub_requests_before"]:
            _fail("J4", "replay sent a second prompt: {}".format(j4))
        if j4["total_after"] != j4["total_before"]:
            _fail("J4", "replay changed usage totals: {}".format(j4))

    except Exception as exc:  # noqa: BLE001 — 任何旅程事故都如实进 verdict
        import traceback
        verdict["fail"].append("driver-exception: " + traceback.format_exc()[-800:])
    finally:
        try:
            stub.terminate()
            stub.wait(timeout=5)
        except Exception:
            stub.kill()
        server.should_exit = True
        thread.join(timeout=15)

    return _emit_verdict(verdict, 0 if not verdict["fail"] else 1)


if __name__ == "__main__":
    sys.exit(main())
'''


def test_g37_session_model_journeys(tmp_path):
    if _vendor_entry() is None:
        pytest.skip(NEEDS_BUILD_REASON)
    verdict_path = tmp_path / "verdict.json"
    proc = subprocess.run(
        [
            sys.executable, "-c", _DRIVER_SOURCE,
            str(REPO_ROOT), str(verdict_path),
        ],
        capture_output=True, text=True, timeout=900,
        cwd=str(REPO_ROOT),
    )
    verdict_line = next(
        (ln for ln in proc.stdout.splitlines() if ln.startswith("VERDICT ")),
        "",
    )
    assert verdict_line, (
        f"driver produced no VERDICT line; rc={proc.returncode}\n"
        f"stdout tail:\n{proc.stdout[-2000:]}\nstderr tail:\n{proc.stderr[-3000:]}"
    )
    verdict = json.loads(verdict_line[len("VERDICT "):])
    # 旅程失败时披露 driver stderr 中与 Pi 生命周期相关的行（定位用）
    if verdict.get("fail"):
        import re as _re

        relevant = _re.compile(
            r"respawn|Recycl|poll=dead|stale reader|send failed|Deferring|"
            r"Not started|broken|Broken|timeout|unavailable",
            _re.IGNORECASE,
        )
        print("---- driver stderr (lifecycle-relevant) ----")
        for line in proc.stderr.splitlines():
            if relevant.search(line):
                print(line[:400])
    if verdict_path.exists():
        verdict_on_disk = json.loads(verdict_path.read_text(encoding="utf-8"))
        assert verdict_on_disk == verdict  # 落盘 verdict 与 stdout 一致

    journeys = verdict.get("journeys", {})
    # 每条旅程的独立断言（driver 内已断言，此处对 verdict 复核 —— pytest
    # 侧是最终裁决面）
    assert not verdict.get("fail"), f"journey failures: {verdict['fail']}"

    j0 = journeys.get("J0", {})
    assert j0["model_status"]["effective"] == "g37-b"
    assert "g37-b" in j0["stub_models"] and "g37-a" not in j0["stub_models"]
    assert j0["last_turn"]["response_model"] == "g37-b-r9"
    assert j0["last_turn"]["complete"] is True
    assert j0["records_meta"]["written"] == 2

    j1 = journeys.get("J1", {})
    assert j1["stub_models"]["s2"] == ["g37-a"]
    assert j1["stub_models"]["s3"] == ["g37-b"]
    assert j1["stub_models"]["s4"] == ["g37-a"]

    j2 = journeys.get("J2", {})
    assert j2["stub_requests_after"] == j2["stub_requests_before"]
    assert j2["error_class"] == "policy_reject"
    assert j2["last_turn_status"] == "budget_rejected"

    j3 = journeys.get("J3", {})
    assert j3["effective"] == "g37-b"
    assert j3["stub_models"] == ["g37-b"]
    assert j3["total_after"] > j3["total_before"] > 0

    j4 = journeys.get("J4", {})
    assert j4["stub_requests_after"] == j4["stub_requests_before"]
    assert j4["total_after"] == j4["total_before"]
