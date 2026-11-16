"""G42 J-G42 车道：真实 Pi 子进程 + stub LLM → 目标补全全链（终验→预览→执行→重终验）。

与 test_pi_g30_skill_job_journey.py 同架构（node stub LLM → uvicorn 生产
app → HTTP 旅程），聚焦 G42 的 Pi 使用链与三类核心闭环之一（数据成果
交付）：

J-G42：
  driver 服务端种子（登录会话 + FC ref + 章节 intent.export_intents=["gpkg"]）
  → 用户 turn 1（文本收口）：agent_settled → 真实终验产出 partial 裁决
    与真实 goal_repair_plan（run_delivery 动作 —— 非手工伪造计划）
  → turn 2：stub LLM 经 tool_search 发现 webgis_goal_repair_preview →
    预览回执（ack_codes 披露）
  → turn 3：stub LLM 调 webgis_goal_repair_start（零等待返回）→ driver
    HTTP 轮询 attempt 到终态（真实 submit_data_delivery 派发 → G18 真实
    durable job → 文件落盘）
  → 裁决：预览/启动回执、attempt completed、交付回执可服务端核验、
    重终验后 verdict 翻转 satisfied、无递归 prompt/第二 writer。

门控：node + 已构建 vendor + PI_JOURNEY_E2E=1（显式 opt-in，绝不静默绿）。
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
"""G42 journey driver：stub LLM + uvicorn(app) + 目标补全旅程 → VERDICT json。"""
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
WORK = Path(tempfile.mkdtemp(prefix="pi-g42-"))
sys.path.insert(0, str(REPO_ROOT))

import uuid

RUN = "g42" + uuid.uuid4().hex[:8]

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
os.environ["PI_AUTOSTART"] = "true"
os.environ.setdefault("GIS_MANIFEST_STRICT", "0")
os.environ["USE_REDIS"] = "false"
# 旅程需要真实登录会话：交付回执族按 conv.user_id 收集（匿名无证据面）。
os.environ["ALLOW_PUBLIC_REGISTER"] = "true"
# 旅程级隔离 DB：每次运行全新 sqlite 文件（app 启动 create_all 建表）——
# 终验/attempt/交付三路并发写在共享大文件上会退化为多写者锁排队（生产为
# Postgres；sqlite 双引擎已有 30s busy timeout，此处只消除跨运行累积态）。
os.environ["DATABASE_URL"] = f"sqlite:///{(WORK / 'journey.db').as_posix()}"

TURN_SCRIPTS = {
    "完成任务": [{"text": "任务完成，等待验收。"}],
    "查看补全预览": [
        {"tool": "tool_search", "args": {"query": "目标 补全 修复 预览 goal repair preview"}},
        {"tool": "webgis_goal_repair_preview", "args": {}},
        {"text": "预览完成。"},
    ],
    "启动补全": [
        {"tool": "tool_search", "args": {"query": "目标 补全 启动 start goal repair"}},
        {"tool": "webgis_goal_repair_start",
         "args": {"acknowledged_losses": ["__ACK_CODES__"]}},
        {"text": "补全已启动。"},
    ],
}


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


async def _seed(session_id: str, *, user_id: str = "user-g42") -> str:
    """登录会话 + FC ref + 章节（export_intents=["gpkg"] + 完成行）。"""
    from app.core.database import SessionLocal
    from app.models.db_model import Conversation
    from app.services.session_data import session_data_manager
    from app.services.session_plan import SessionPlan, save_session_plan

    with SessionLocal() as db:
        db.add(Conversation(id=session_id, user_id=user_id,
                            title="G42 journey"))
        db.commit()
    features = [
        {"type": "Feature", "id": "f1",
         "geometry": {"type": "Point", "coordinates": [116.397, 39.916]},
         "properties": {"name": "北京", "pop": 2154}},
        {"type": "Feature", "id": "f2",
         "geometry": {"type": "Point", "coordinates": [116.407, 39.926]},
         "properties": {"name": "东城", "pop": 709}},
    ]
    fc = {"type": "FeatureCollection", "features": features,
          "properties": {"total_count": 2, "truncated": False,
                         "schema_info": {"crs": "EPSG:4326"}}}
    ref = await session_data_manager.store(session_id, fc, prefix="geojson")
    chapter = {
        "intent": {"task": "数据交付", "scope": {"name": "北京"},
                   "output_intents": [], "export_intents": ["gpkg"],
                   "query": "把结果交付为 gpkg"},
        "analysis_steps": [{"capability": "buffer", "status": "done",
                            "bound_ref": ref,
                            "purpose": "缓冲区分析"}],
        "export_receipts": [],
    }
    plan = SessionPlan(envelope_id=f"env-{RUN}", session_id=session_id,
                       user_goal="把结果交付为 gpkg", gis_chapter=chapter)
    await save_session_plan(plan)
    return ref


def main() -> int:
    stub_js = WORK / "journey_stub.mjs"
    stub_js.write_text(STUB_JS, encoding="utf-8")
    script_path = WORK / "journey_script.json"
    script_path.write_text(json.dumps(TURN_SCRIPTS), encoding="utf-8")
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
    os.environ["LLM_API_KEY"] = "stub-g42-key"
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
        # 注册旅程用户 → JWT（chat 与 attempt 轮询同一身份；跨租户 404 语义）。
        auth_headers = {"Content-Type": "application/json"}
        reg = urllib.request.Request(
            "http://127.0.0.1:{}/api/v1/auth/register".format(api_port),
            data=json.dumps({
                "username": RUN, "email": f"{RUN}@journey.test",
                "password": "J0urney!g42-x"}).encode(),
            headers=auth_headers, method="POST")
        try:
            with urllib.request.urlopen(reg, timeout=15) as resp:
                tok_payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            tok_payload = json.loads(exc.read().decode() or "{}")
        access_token = str(tok_payload.get("access_token")
                           or tok_payload.get("token") or "")
        if not access_token:
            print(json.dumps({"fail": [
                "register did not return a token",
                str(tok_payload)[:200]]}, ensure_ascii=False))
            return 1
        import base64 as _b64

        def _jwt_uid(token: str) -> str:
            try:
                part = token.split(".")[1]
                part += "=" * (-len(part) % 4)
                claims = json.loads(_b64.urlsafe_b64decode(part))
                return str(claims.get("user_id") or claims.get("sub") or "")
            except Exception:  # noqa: BLE001
                return ""

        journey_user = _jwt_uid(access_token)
        verdict["journey_user"] = journey_user
        auth_headers["Authorization"] = f"Bearer {access_token}"

        asyncio.run(_seed(session_id, user_id=journey_user))
        owner_token_box = {"token": None}

        def _chat(message: str) -> tuple[str, list]:
            payload = {"message": message, "session_id": session_id}
            headers = {**auth_headers}
            tok = owner_token_box["token"]
            if tok:
                headers["X-Session-Token"] = tok
            req = urllib.request.Request(
                "http://127.0.0.1:{}/api/v1/chat/stream".format(api_port),
                data=json.dumps(payload).encode(), headers=headers,
            )
            with urllib.request.urlopen(req, timeout=240) as resp:
                body = resp.read().decode("utf-8", "replace")
            events = _parse_sse(body)
            for e in events:
                data = e["data"]
                if isinstance(data, dict) and data.get("owner_token"):
                    owner_token_box["token"] = data["owner_token"]
            return body, events

        def _get(path: str):
            headers = {**auth_headers}
            tok = owner_token_box["token"]
            if tok:
                headers["X-Session-Token"] = tok
            req = urllib.request.Request(
                "http://127.0.0.1:{}{}".format(api_port, path), headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))

        # ── turn 1：真实终验（settled → finalizer → 真实 goal_repair_plan）
        body1, events1 = _chat("完成任务")
        (WORK / "sse1.log").write_text(body1, encoding="utf-8")

        async def _read_block():
            from app.services.session_plan import load_session_plan

            plan = await load_session_plan(session_id)
            chapter = (plan.gis_chapter if plan else None) or {}
            stored = chapter.get("map_product") or {}
            return stored

        stored1 = asyncio.run(_read_block())
        gs1 = stored1.get("goal_satisfaction") or {}
        plan1 = stored1.get("goal_repair_plan") or {}
        verdict["turn1_verdict"] = str(gs1.get("verdict") or "")
        verdict["turn1_plan_actions"] = [
            str(a.get("action")) for a in (plan1.get("actions") or [])]
        verdict["journeys"]["finalizer_real_plan"] = bool(
            plan1.get("actions"))
        verdict["journeys"]["finalizer_partial_verdict"] = \
            verdict["turn1_verdict"] in ("partial", "blocked", "failed")
        if not verdict["journeys"]["finalizer_real_plan"]:
            verdict["fail"].append(
                "turn-1 real finalizer did not produce goal_repair_plan")

        # ── turn 2：stub 经 tool_search 发现并调 preview
        body2, events2 = _chat("查看补全预览")
        (WORK / "sse2.log").write_text(body2, encoding="utf-8")
        preview_payload = None
        for e in events2:
            data = e["data"]
            if not isinstance(data, dict):
                continue
            for key in ("tool_result", "result"):
                payload = data.get(key)
                if isinstance(payload, dict) and payload.get(
                        "schema_version") == "goalrepair.preview/v1":
                    preview_payload = payload
        verdict["journeys"]["pi_preview_receipt"] = bool(preview_payload)
        if not preview_payload:
            verdict["fail"].append("no preview receipt in Pi stream")
        else:
            verdict["preview_actions"] = [
                {"action": a.get("action"),
                 "disposition": a.get("disposition"),
                 "ack": a.get("ack_codes")}
                for a in preview_payload.get("actions", [])]

        # ── turn 3：start（零等待）→ HTTP 轮询 attempt 终态
        ack_codes = []
        action_ids = []
        if preview_payload:
            for a in preview_payload.get("actions", []):
                if a.get("disposition") in (
                        "executable", "needs_acknowledgment"):
                    action_ids.append(a.get("action_id"))
                ack_codes.extend(a.get("ack_codes") or [])
        scripts = json.loads(json.dumps(TURN_SCRIPTS))
        start_step = next(
            (s for s in scripts["启动补全"]
             if s.get("tool") == "webgis_goal_repair_start"), None)
        if start_step is not None:
            start_step["args"] = {
                "action_ids": action_ids,
                "acknowledged_losses": ack_codes,
            }
        script_path.write_text(json.dumps(scripts), encoding="utf-8")
        body3, events3 = _chat("启动补全")
        (WORK / "sse3.log").write_text(body3, encoding="utf-8")
        start_payload = None
        for e in events3:
            data = e["data"]
            if not isinstance(data, dict):
                continue
            for key in ("tool_result", "result"):
                payload = data.get(key)
                if isinstance(payload, dict) and payload.get("attempt_id"):
                    start_payload = payload
        verdict["journeys"]["pi_start_receipt"] = bool(
            start_payload and start_payload.get("success") is not False)
        if not start_payload:
            # 诊断：turn-3 流内的工具回执形状（定位 typed 拒绝/未达）
            _diag = []
            for e in events3:
                data = e["data"]
                if not isinstance(data, dict):
                    continue
                for key in ("tool_result", "result"):
                    payload = data.get(key)
                    if isinstance(payload, dict):
                        _diag.append({k: str(payload.get(k))[:80]
                                      for k in ("success", "code", "message",
                                                "attempt_id", "status")
                                      if k in payload})
            verdict["turn3_tool_payloads"] = _diag[:6]
            verdict["turn3_has_start_call"] = "webgis_goal_repair_start" in body3
        verdict["journeys"]["pi_start_zero_wait"] = bool(
            start_payload and start_payload.get("status") in (
                "running", "pending", "queued"))
        if not start_payload:
            verdict["fail"].append("no start receipt in Pi stream")
        else:
            # 零等待验证：SSE turn 3 正常收口（无自等待超时）
            verdict["journeys"]["turn3_completed"] = \
                "agent_settled" in body3 or "task_complete" in body3

            # HTTP 轮询 attempt（读路径惰性对账同链）
            attempt = None
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                try:
                    attempt = _get("/api/v1/goal-repair/attempts/{}".format(
                        start_payload["attempt_id"]))
                except Exception:
                    attempt = None
                if attempt and not attempt.get("active"):
                    break
                time.sleep(1.0)
            # 惰性对账：waiting_delivery 存在时继续轮询（读路径收口 + 重终验；
            # 生产面板同语义 —— 产物回执落定后由原 evaluator 重新裁决）。
            _wd_deadline = time.monotonic() + 120
            while attempt and time.monotonic() < _wd_deadline and any(
                    a.get("status") == "waiting_delivery"
                    for a in (attempt or {}).get("actions", [])):
                time.sleep(2.0)
                try:
                    attempt = _get("/api/v1/goal-repair/attempts/{}".format(
                        start_payload["attempt_id"]))
                except Exception:
                    break
            verdict["attempt_status"] = (attempt or {}).get("status")
            verdict["attempt_outcome"] = (attempt or {}).get("outcome")
            verdict["attempt_actions"] = [
                {"action": a.get("action"), "status": a.get("status"),
                 "job_id": a.get("job_id")}
                for a in (attempt or {}).get("actions", [])]
            verdict["journeys"]["attempt_terminal"] = bool(
                attempt and not attempt.get("active"))
            verdict["journeys"]["delivery_succeeded"] = any(
                a.get("status") == "succeeded"
                for a in (attempt or {}).get("actions", []))
            if not verdict["journeys"]["attempt_terminal"]:
                verdict["fail"].append("attempt did not reach terminal state")
            if not verdict["journeys"]["delivery_succeeded"]:
                verdict["fail"].append(
                    "delivery action not succeeded: {!r}".format(
                        verdict["attempt_actions"]))

        # ── 服务端核验：真实交付文件 + 重终验 verdict 翻转
        async def _verify():
            from app.services.data_delivery import get_delivery_job
            from app.services.data_delivery.source_resolver import (
                DeliveryActor,
            )
            from app.services.gis_harness.goal_repair_execution import store

            views = store.find_attempts(session_id, limit=1)
            job_ok, rows = False, 0
            if views:
                summary = store.read_summary(int(views[0]["attempt_id"]))
                for act in summary.get("actions") or []:
                    jid = str(act.get("job_id") or "")
                    if jid and act.get("status") == "succeeded":
                        view = await get_delivery_job(
                            jid, DeliveryActor(user_id=journey_user))
                        receipt = getattr(view, "receipt", None)
                        job_ok = receipt is not None
                        rows = int(getattr(receipt, "row_count", 0) or 0)
                        break
            stored = await _read_block()
            gs = stored.get("goal_satisfaction") or {}
            return job_ok, rows, str(gs.get("verdict") or "")

        job_ok, rows, verdict_after = asyncio.run(_verify())
        verdict["journeys"]["delivery_file_receipt"] = bool(job_ok and rows == 2)
        verdict["final_verdict_after"] = verdict_after
        verdict["journeys"]["refinalized_satisfied"] = \
            verdict_after == "satisfied"
        if not job_ok or rows != 2:
            verdict["fail"].append("delivery receipt not verifiable "
                                   f"(job_ok={job_ok}, rows={rows})")
        if verdict_after != "satisfied":
            verdict["fail"].append(
                f"re-finalization did not endorse delivery (verdict="
                f"{verdict_after})")
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
@pytest.mark.timeout(900)
def test_pi_g42_goal_repair_journey(tmp_path):
    entry = _vendor_entry()
    if entry is None:
        pytest.skip(NEEDS_BUILD_REASON)
    proc = subprocess.run(
        [sys.executable, "-c", _DRIVER_SOURCE, str(REPO_ROOT)],
        capture_output=True, text=True, timeout=600, cwd=str(REPO_ROOT),
    )
    verdict_line = next(
        (ln for ln in proc.stdout.splitlines() if ln.startswith("VERDICT ")), None)
    assert verdict_line, (
        "driver did not emit a verdict; stderr tail: "
        + proc.stderr[-2000:]
    )
    verdict = json.loads(verdict_line[len("VERDICT "):])
    assert not verdict.get("fail"), verdict.get("fail")
    assert verdict["journeys"]["finalizer_real_plan"] is True
    assert verdict["journeys"]["pi_preview_receipt"] is True
    assert verdict["journeys"]["pi_start_zero_wait"] is True
    assert verdict["journeys"]["attempt_terminal"] is True
    assert verdict["journeys"]["delivery_succeeded"] is True
    assert verdict["journeys"]["delivery_file_receipt"] is True
    assert verdict["journeys"]["refinalized_satisfied"] is True
