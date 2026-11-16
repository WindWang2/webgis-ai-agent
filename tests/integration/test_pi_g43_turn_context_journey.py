"""G43 场景验证车道：真实 Pi bridge + stub LLM + 会话资料策略 + 回执闭环。

与 ``test_pi_g37_session_model_journeys.py`` 同架构：pytest 只做门控与裁决，
独立 driver 子进程承载全部旅程 —— stub LLM 记录**每次供应商请求的完整
最后用户消息**（provider 边界对账，C33）→ uvicorn 线程承载生产 app
（lifespan 拉起真实 vendor/pi v1.5.2 bridge）→ 真实 HTTP 旅程 →
``VERDICT {json}``。

旅程（任务书 §六）：
- J1（C01/C02 供应商边界）：auto 策略 turn → 持久回执 source_lines 含
  2024/2025 两份知识条目（included + 真实 version_token）；stub 实收
  prompt 同时含两条目渲染行。
- J2（C03/C33 排除生效）：PUT 策略排除 2024 知识条目与 2024 记忆事实、
  优先 2025 → 下一 turn 回执 excluded_by_policy/preferred 如实分列；
  stub 实收 prompt **不含** 2024 行、**仍含** 2025 行（宿主新块证据）。
- J3（C31 工具链）：stub 剧本调用 ``webgis_turn_context_receipt`` →
  真实 dispatch 返回的回执出现在 step_result（模型可读当前授权回执）。
- J4（C18 not_sent）：token cap=1 的会话 → 装配后被预算门拒绝 →
  回执 status=not_sent + reason=model_budget_rejected；stub 对该会话
  请求数 == 0（零假发送）。

门控：node + 已构建 vendor；显式 opt-in（PI_JOURNEY_E2E=1）。缺席显式
skip（绝不静默绿）。
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
"""G43 journey driver（独立进程）。输出单行 "VERDICT {json}"。"""
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
from pathlib import Path

REPO_ROOT = Path(sys.argv[1])
WORK = Path(tempfile.mkdtemp(prefix="g43-journey-"))
VERDICT_OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else None

import uuid

RUN = "g43" + uuid.uuid4().hex[:8]

os.environ["PI_AUTOSTART"] = "true"
# 旅程不依赖外部 Redis：单进程内存储（rate limiter / PiTurnRegistry 同
# 回退路径；G37 车道历史上由 dev compose 提供 Redis，这里显式自足）。
os.environ["USE_REDIS"] = "false"
os.environ["LLM_MODEL"] = "g43-a"
os.environ["SESSION_MODEL_TOKEN_CAP_MAX"] = "5000000"
os.environ["SESSION_MODEL_TOKEN_CAP_DEFAULT"] = "0"

# stub 记录完整最后用户消息（provider 边界对账用），按首行 key 索引。
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
    appendFileSync(recFile, JSON.stringify({
      key: base, prompt: lastUser || '', model: parsed.model || '',
    }) + '\n');
    const delayS = Number(step.delay_s || 0);
    if (delayS > 0) await new Promise((r) => setTimeout(r, delayS * 1000));
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    const sse = (o) => res.write(`data: ${JSON.stringify(o)}\n\n`);
    const id = 'c' + Date.now();
    if (step.tool) {
      sse({ id, object: 'chat.completion.chunk', created: 1, model: 'g43-a',
        choices: [{ index: 0, delta: { tool_calls: [{ index: 0, id: 'call_' + Math.random().toString(36).slice(2, 8),
          type: 'function', function: { name: step.tool, arguments: JSON.stringify(step.args || {}) } }] } }] });
      sse({ id, object: 'chat.completion.chunk', created: 1, model: 'g43-a',
        choices: [{ index: 0, delta: {}, finish_reason: 'tool_calls' }],
        usage: { prompt_tokens: 120, completion_tokens: 40 } });
    } else {
      sse({ id, object: 'chat.completion.chunk', created: 1, model: 'g43-a',
        choices: [{ index: 0, delta: { content: step.text || 'done' } }] });
      sse({ id, object: 'chat.completion.chunk', created: 1, model: 'g43-a',
        choices: [{ index: 0, delta: {}, finish_reason: 'stop' }],
        usage: { prompt_tokens: 100, completion_tokens: 30 } });
    }
    res.write('data: [DONE]\n\n'); res.end();
  });
});
srv.listen(0, '127.0.0.1', () => process.stderr.write('STUB_READY ' + srv.address().port + '\n'));
process.on('SIGTERM', () => process.exit(0));
"""

TURN_SCRIPTS = {
    "盘点项目资料": [{"text": "已盘点：本轮含全部可选资料。"}],
    "再次盘点项目资料": [{"text": "已盘点：本轮按用户策略装配。"}],
    "查本轮提交资料": [
        # 生产真实发现链（与 G30 同款）：tool_search 发现 → 激活 → 直调
        {"tool": "tool_search", "args": {"query": "本轮 上下文 回执 资料 用了哪些"}},
        {"tool": "webgis_turn_context_receipt", "args": {}},
        {"text": "回执已读取并转述。"},
    ],
    "预算拒绝探针": [{"text": "never-runs"}],
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
    sys.stdout.flush()  # os._exit 之前必须落盘（捕获管道是块缓冲）
    sys.stderr.flush()
    return code


def _seed_world() -> tuple:
    """org + user(+JWT) + project + 2024/2025 知识条目（carto_fact 权威）。"""
    from app.core.database import SessionLocal
    from app.models.db_model import Organization, User
    from app.models.project import CartoProjectFact, Project
    from app.models.project_knowledge import ProjectKnowledgeEntry

    with SessionLocal() as db:
        org = Organization(id=None, name=f"g43org-{RUN}", slug=f"g43-{RUN}")
        db.add(org)
        db.flush()
        org_id_int = int(org.id)
        org_id_str = str(org.id)
        uid = f"g43-user-{RUN}"
        db.add(User(id=uid, org_id=org_id_int, username=f"g43u{RUN}",
                    email=f"g43-{RUN}@example.com", role="editor",
                    is_active=True))
        db.flush()
        pid = f"g43-proj-{RUN}"
        db.add(Project(id=pid, org_id=org_id_int, owner_id=uid,
                       name="G43 资料项目", status="active"))
        db.flush()
        facts = {}
        for year in ("2024", "2025"):
            fid = f"g43-fact-{year}-{RUN}"
            db.add(CartoProjectFact(
                id=fid, project_id=pid, kind="preference",
                subject=f"{year}数据基线",
                payload={"value": f"{year} 年权威成果集 v1"},
                fingerprint=f"fp-{year}", status="active",
            ))
            db.add(ProjectKnowledgeEntry(
                id=f"g43-pk-{year}-{RUN}", org_id=org_id_str, project_id=pid,
                entity_kind="preference",
                subject=f"{year}成果集",
                authority_store="carto_fact", authority_id=fid,
                version_token=f"fp-{year}",
                summary=f"{year} 年已授权成果索引",
                status="active",
            ))
            facts[year] = {"fact_id": fid,
                           "entry_id": f"g43-pk-{year}-{RUN}"}
        db.commit()
    from app.core.auth import create_access_token

    jwt = create_access_token({"sub": uid, "role": "user"})
    return pid, facts, jwt, org_id_str


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
    os.environ["LLM_API_KEY"] = "stub-g43-key"

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
        verdict["fail"].append("api server did not become ready")
        return _emit_verdict(verdict, 1)

    project_id, facts, jwt, org_id = _seed_world()

    def _req(method, path, body=None, timeout_s=60):
        headers = {"Content-Type": "application/json",
                   "Authorization": "Bearer " + jwt}
        req = urllib.request.Request(
            "http://127.0.0.1:{}{}".format(api_port, path),
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers, method=method,
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8", "replace") or "{}")

    def _stream(message, session_id, timeout_s=180.0):
        payload = {"message": message, "session_id": session_id,
                   "project_id": project_id}
        req = urllib.request.Request(
            "http://127.0.0.1:{}/api/v1/chat/stream".format(api_port),
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + jwt})
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read().decode("utf-8", "replace")
        (WORK / ("sse_" + session_id + ".log")).write_text(body, encoding="utf-8")
        return _parse_sse(body)

    def _stub_recs():
        out = []
        for line in rec.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out

    def _receipt(session_id, turn_id):
        try:
            return _req("GET",
                        "/api/v1/chat/sessions/{}/context-receipts/{}"
                        .format(session_id, turn_id), timeout_s=30)
        except urllib.error.HTTPError as e:
            return {"http_error": e.code}

    def _receipt_event(events):
        for e in events:
            if e["event"] == "turn_context_receipt":
                return e["data"]
        return None

    def _turn_id_of(events):
        for e in events:
            tid = (e["data"] or {}).get("turn_id")
            if tid:
                return tid
        return None

    # ── J1：auto 策略首轮（C01 + 供应商边界） ─────────────────────────
    s1 = f"g43-s1-{RUN}"
    try:
        ev1 = _stream("盘点项目资料", s1)
        j1 = {}
        rcpt_ev = _receipt_event(ev1)
        j1["sse_event"] = rcpt_ev is not None and rcpt_ev.get("status") in (
            "submitted", "assembled")
        turn1 = _turn_id_of(ev1) or (rcpt_ev or {}).get("turn_id", "")
        r1 = _receipt(s1, turn1)
        j1["durable_receipt"] = r1.get("status") == "submitted"
        j1["disposition_started"] = r1.get("disposition") == "started"
        sources1 = {
            s.get("source_id"): s
            for s in (r1.get("payload", {}) or {}).get("sources", [])
        }
        e24 = sources1.get(f"pk:{facts['2024']['entry_id']}")
        e25 = sources1.get(f"pk:{facts['2025']['entry_id']}")
        j1["pk2024_included"] = bool(e24 and e24["decision"] == "included"
                                     and e24.get("version_token") == "fp-2024")
        j1["pk2025_included"] = bool(e25 and e25["decision"] == "included")
        cm24 = sources1.get(f"cm:{facts['2024']['fact_id']}")
        j1["cm2024_included"] = bool(cm24 and cm24["decision"] == "included")
        # 供应商边界：stub 实收 prompt 同时含 2024/2025 渲染行（C33）
        recs1 = [r for r in _stub_recs() if r["key"] == "盘点项目资料"]
        # any()：主调用携带装配块；同 key 的第二请求是标题生成等旁路
        # 调用（只含原始消息）—— 不以它否定边界证据。
        j1["stub_prompt_has_both"] = bool(recs1) and any(
            ("2024成果集" in r["prompt"] and "2025成果集" in r["prompt"])
            for r in recs1)
        verdict["journeys"]["J1"] = j1
        for k, v in j1.items():
            if not v:
                verdict["fail"].append(f"J1.{k}")
    except Exception as exc:  # noqa: BLE001
        verdict["fail"].append(f"J1 exception: {type(exc).__name__}: {exc}")

    # ── J2：排除 2024、优先 2025（C03 + 边界缺席证据） ────────────────
    try:
        j2 = {}
        pol = _req("GET", f"/api/v1/chat/sessions/{s1}/context-policy")
        cur = pol["policy"]["policy_version"]
        put = _req("PUT", f"/api/v1/chat/sessions/{s1}/context-policy", {
            "mode": "explicit",
            "preferred": [f"pk:{facts['2025']['entry_id']}"],
            "excluded": [f"pk:{facts['2024']['entry_id']}",
                         f"cm:{facts['2024']['fact_id']}"],
            "expected_version": cur,
            "project_id": project_id,
        })
        j2["policy_saved"] = (
            put["policy"]["mode"] == "explicit"
            and put["policy"]["policy_version"] > cur)

        ev2 = _stream("再次盘点项目资料", s1)
        rcpt2_ev = _receipt_event(ev2)
        turn2 = _turn_id_of(ev2) or (rcpt2_ev or {}).get("turn_id", "")
        r2 = _receipt(s1, turn2)
        j2["policy_version_frozen"] = (
            r2.get("policy_version") == put["policy"]["policy_version"])
        sources2 = {
            s.get("source_id"): s
            for s in (r2.get("payload", {}) or {}).get("sources", [])
        }
        j2["pk2024_excluded"] = sources2.get(
            f"pk:{facts['2024']['entry_id']}", {}).get(
            "decision") == "excluded_by_policy"
        j2["cm2024_excluded"] = sources2.get(
            f"cm:{facts['2024']['fact_id']}", {}).get(
            "decision") == "excluded_by_policy"
        j2["pk2025_preferred"] = sources2.get(
            f"pk:{facts['2025']['entry_id']}", {}).get("decision") == "preferred"
        # 供应商边界（C33）：新块不含被排除的 2024 行，仍含优先的 2025 行
        recs2 = [r for r in _stub_recs() if r["key"] == "再次盘点项目资料"]
        j2["stub_prompt_excludes_2024"] = bool(recs2) and all(
            "2024成果集" not in r["prompt"] for r in recs2)
        j2["stub_prompt_keeps_2025"] = bool(recs2) and all(
            "2025成果集" in r["prompt"] for r in recs2)
        verdict["journeys"]["J2"] = j2
        for k, v in j2.items():
            if not v:
                verdict["fail"].append(f"J2.{k}")
    except Exception as exc:  # noqa: BLE001
        verdict["fail"].append(f"J2 exception: {type(exc).__name__}: {exc}")

    # ── J3：模型经工具读取回执（C31 真实 dispatch） ───────────────────
    try:
        j3 = {}
        ev3 = _stream("查本轮提交资料", s1)
        step_results = [
            e["data"] for e in ev3
            if e["event"] == "step_result"
        ]
        blob = json.dumps(step_results, ensure_ascii=False)
        tool_calls = [
            (e["data"] or {}).get("name", "")
            for e in ev3 if e["event"] == "tool_call"
        ]
        j3["discovery_then_direct"] = (
            "tool_search" in tool_calls
            and "webgis_turn_context_receipt" in tool_calls)
        j3["tool_step_present"] = (
            "webgis_turn_context_receipt" in blob
            and any(e["event"] == "step_result" for e in ev3))
        j3["tool_result_has_receipt"] = (
            "turn_id" in blob and "sources" in blob)
        # 工具结果携带本会话当前策略下的回执事实（preferred/excluded 可见）
        j3["tool_result_has_policy_state"] = (
            "excluded_by_policy" in blob or "preferred" in blob)
        verdict["journeys"]["J3"] = j3
        for k, v in j3.items():
            if not v:
                verdict["fail"].append(f"J3.{k}")
    except Exception as exc:  # noqa: BLE001
        verdict["fail"].append(f"J3 exception: {type(exc).__name__}: {exc}")

    # ── J5：非流式同语义（C34 权限/提交状态一致 + C04 历史消息回执） ──
    try:
        j5 = {}
        payload = {"message": "再次盘点项目资料", "session_id": s1,
                   "project_id": project_id}
        # 与流式同 key：stub 剧本沿用（非流式是独立会话轮；此处新 key
        # 避免与 J2 的流式请求计数混淆）
        req = urllib.request.Request(
            "http://127.0.0.1:{}/api/v1/chat/completions".format(api_port),
            data=json.dumps(dict(payload, message="再次盘点项目资料")).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + jwt})
        with urllib.request.urlopen(req, timeout=180) as resp:
            ns = json.loads(resp.read().decode("utf-8", "replace") or "{}")
        ns_receipt = ns.get("turn_receipt") or {}
        j5["nonstream_receipt_disclosed"] = (
            ns_receipt.get("status") == "submitted"
            and ns_receipt.get("policy_version")
            == put["policy"]["policy_version"])
        # 同一策略下非流式的源去向与流式一致（2024 排除、2025 保留）
        ns_turn = ns_receipt.get("turn_id", "")
        r5 = _receipt(s1, ns_turn)
        s5 = {x.get("source_id"): x.get("decision")
              for x in (r5.get("payload", {}) or {}).get("sources", [])}
        j5["nonstream_policy_same"] = (
            s5.get(f"pk:{facts['2024']['entry_id']}") == "excluded_by_policy"
            and s5.get(f"pk:{facts['2025']['entry_id']}") == "preferred")
        # C04：历史消息携带持久回执摘要（刷新恢复面）
        detail = _req("GET", f"/api/v1/chat/sessions/{s1}", timeout_s=30)
        msgs = detail.get("messages") or []
        with_receipt = [
            m for m in msgs
            if isinstance(m.get("context_receipt"), dict)
            and m["context_receipt"].get("turn_id")
        ]
        j5["history_message_receipt"] = bool(with_receipt)
        turns_seen = {m["context_receipt"]["turn_id"] for m in with_receipt}
        j5["history_receipts_deduped"] = (
            len(with_receipt) == len(turns_seen))  # C23：一 turn 一摘要
        verdict["journeys"]["J5"] = j5
        for k, v in j5.items():
            if not v:
                verdict["fail"].append(f"J5.{k}")
    except Exception as exc:  # noqa: BLE001
        verdict["fail"].append(f"J5 exception: {type(exc).__name__}: {exc}")

    # ── J4：装配后预算拒绝 → not_sent（C18 零假发送） ─────────────────
    try:
        j4 = {}
        s2 = f"g43-s2-{RUN}"
        # 会话行预置（owner 守卫需要已存在的 conversation；预算拒绝的
        # turn 本身不会创建它）
        from app.core.database import SessionLocal
        from app.models.db_model import Conversation as ConvRow

        with SessionLocal() as db:
            if db.get(ConvRow, s2) is None:
                db.add(ConvRow(id=s2, user_id=f"g43-user-{RUN}", title="t"))
                db.commit()
        _req("PUT", f"/api/v1/chat/sessions/{s2}/model", {
            "mode": "explicit", "model_id": "g43-a",
            "session_token_cap": 1, "expected_revision": 0,
        })
        before = len([r for r in _stub_recs()
                      if r["key"] == "预算拒绝探针"])
        ev4 = _stream("预算拒绝探针", s2)
        after = len([r for r in _stub_recs()
                     if r["key"] == "预算拒绝探针"])
        j4["stub_zero_requests"] = before == after == 0
        errs = [e["data"] for e in ev4 if e["event"] == "task_error"]
        j4["task_error_emitted"] = bool(errs)
        rcpt4_ev = _receipt_event(ev4)
        turn4 = (rcpt4_ev or {}).get("turn_id", "")
        r4 = _receipt(s2, turn4)
        j4["receipt_not_sent"] = r4.get("status") == "not_sent"
        j4["reason_model_budget"] = (
            r4.get("not_sent_reason") == "model_budget_rejected")
        verdict["journeys"]["J4"] = j4
        for k, v in j4.items():
            if not v:
                verdict["fail"].append(f"J4.{k}")
    except Exception as exc:  # noqa: BLE001
        verdict["fail"].append(f"J4 exception: {type(exc).__name__}: {exc}")

    stub.terminate()
    code = _emit_verdict(verdict, 0 if not verdict["fail"] else 1)
    # Pi RPC reader 线程（非守护 executor）会阻止解释器退出 —— verdict
    # 已落盘/打印后硬退出，保证 pytest subprocess.run(timeout=...) 收口。
    os._exit(code)


if __name__ == "__main__":
    main()
'''


def test_g43_turn_context_journeys(tmp_path):
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
    if verdict.get("fail"):
        import re as _re

        relevant = _re.compile(
            r"respawn|Recycl|poll=dead|stale reader|send failed|Deferring|"
            r"Not started|broken|Broken|timeout|unavailable|Traceback",
            _re.IGNORECASE,
        )
        print("---- driver stderr (lifecycle-relevant) ----")
        for line in proc.stderr.splitlines():
            if relevant.search(line):
                print(line[:400])
    if verdict_path.exists():
        verdict_on_disk = json.loads(verdict_path.read_text(encoding="utf-8"))
        assert verdict_on_disk == verdict

    journeys = verdict.get("journeys", {})
    assert not verdict.get("fail"), f"journey failures: {verdict['fail']}"

    j1 = journeys.get("J1", {})
    assert j1["sse_event"] and j1["durable_receipt"]
    assert j1["disposition_started"]
    assert j1["pk2024_included"] and j1["pk2025_included"]
    assert j1["cm2024_included"]
    assert j1["stub_prompt_has_both"]

    j2 = journeys.get("J2", {})
    assert j2["policy_saved"] and j2["policy_version_frozen"]
    assert j2["pk2024_excluded"] and j2["cm2024_excluded"]
    assert j2["pk2025_preferred"]
    assert j2["stub_prompt_excludes_2024"] and j2["stub_prompt_keeps_2025"]

    j3 = journeys.get("J3", {})
    assert j3["discovery_then_direct"]
    assert j3["tool_step_present"] and j3["tool_result_has_receipt"]
    assert j3["tool_result_has_policy_state"]

    j4 = journeys.get("J4", {})
    assert j4["stub_zero_requests"] and j4["task_error_emitted"]
    assert j4["receipt_not_sent"] and j4["reason_model_budget"]

    j5 = journeys.get("J5", {})
    assert j5["nonstream_receipt_disclosed"] and j5["nonstream_policy_same"]
    assert j5["history_message_receipt"] and j5["history_receipts_deduped"]
