"""G30 J-G30 车道：真实 Pi 子进程 + stub LLM → 技能作业全链（发现→准备→执行）。

与 test_pi_g26_mixed_export_journey.py 同架构（node stub LLM → uvicorn 生产
app → HTTP 旅程），聚焦 G30 的 Pi 使用链：

J-G30：
  driver 服务端种子（点数据 → 会话 ref）
  → 用户 turn 携带 skill_name=validate_spatial_data（chat.py 显式选择解析）
  → stub LLM: tool_search 发现技能作业工具（deferred 面两跳模式）
  → gis_skill_job_prepare（typed 参数+绑定 → prepared_id + 预览）
  → gis_skill_job_start（当前 turn 绑定；有界等待）
  → 裁决：选择披露 notice + prepare 回执 + start 成功回执（refs-only）
    + 报告 ref 服务端可回读 + durable job 行存在（evidence 全覆盖）。

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


# stub 每请求重读脚本文件 —— 种子 ref 运行期生成后写脚本即可（无需预知）。

_DRIVER_SOURCE = r'''#!/usr/bin/env python3
"""G30 journey driver：stub LLM + uvicorn(app) + 技能作业旅程 → VERDICT json。"""
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
WORK = Path(tempfile.mkdtemp(prefix="pi-g30-"))
sys.path.insert(0, str(REPO_ROOT))

import uuid

RUN = "g30" + uuid.uuid4().hex[:8]

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

TURN_SCRIPTS_TEMPLATE = {
    "检查这份数据的质量": [
        {"tool": "tool_search", "args": {"query": "技能 作业 执行 prepare 空间数据验证"}},
        {"tool": "gis_skill_job_prepare", "args": {
            "skill_id": "validate_spatial_data",
            "params": {},
            "bindings": [{"role": "subject", "ref": "__SEEDED_REF__"}],
        }},
        {"tool": "gis_skill_job_start", "args": {"wait_timeout_s": 90}},
        {"text": "数据质量检查完成，报告已生成。"},
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


async def _seed_points_ref(session_id: str) -> str:
    from app.services.session_data import session_data_manager

    features = [
        {"type": "Feature",
         "geometry": {"type": "Point",
                      "coordinates": [104.0 + i * 0.01, 30.6 + i * 0.005]},
         "properties": {"name": f"school{i}", "score": i * 1.5}}
        for i in range(14)
    ]
    fc = {"type": "FeatureCollection", "features": features}
    return await session_data_manager.store(session_id, fc, prefix="geojson")


def main() -> int:
    stub_js = WORK / "journey_stub.mjs"
    stub_js.write_text(STUB_JS, encoding="utf-8")
    script_path = WORK / "journey_script.json"
    script_path.write_text(json.dumps({"placeholder": []}), encoding="utf-8")
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
    os.environ["LLM_API_KEY"] = "stub-g30-key"
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
        seeded_ref = asyncio.run(_seed_points_ref(session_id))
        verdict["seeded_ref"] = seeded_ref
        # 写入含种子 ref 的 turn 脚本（stub 每请求重读）
        scripts = json.loads(json.dumps(TURN_SCRIPTS_TEMPLATE).replace(
            "__SEEDED_REF__", seeded_ref))
        # start 需要 prepare 返回的 prepared_id —— stub 无法引用运行期返回值；
        # 改为 stub 发 start 只带 wait（prepared_id 由 Pi 扩展补齐不可能）——
        # 真实做法：让 stub 第二步后从工具结果中取 prepared_id。stub 不支持
        # 模板引用，改为：prepare 后 start 以“最近 prepared”语义走占位符，
        # 由 gis_skill_job_start 工具支持 prepared_id 缺省（取本会话最近一个
        # prepared）—— 不引入该兜底（一次性消费语义明确）；改用两轮对话：
        # 第一轮 prepare，driver 从 SSE 提取 prepared_id 注入脚本，第二轮
        # start。此处先写第一轮。
        first_turn = list(scripts.keys())[0]
        phase1 = {first_turn: scripts[first_turn][:2] + [{"text": "已准备。"}]}
        script_path.write_text(json.dumps(phase1), encoding="utf-8")

        owner_token_box = {"token": None}

        def _chat(message: str, skill_name: str = "") -> tuple[str, list]:
            payload = {"message": message, "session_id": session_id}
            if skill_name:
                payload["skill_name"] = skill_name
            headers = {"Content-Type": "application/json"}
            tok = owner_token_box["token"]
            if tok:
                headers["X-Session-Token"] = tok  # SEC-08 匿名会话所有权
            req = urllib.request.Request(
                "http://127.0.0.1:{}/api/v1/chat/stream".format(api_port),
                data=json.dumps(payload).encode(), headers=headers,
            )
            with urllib.request.urlopen(req, timeout=180) as resp:
                body = resp.read().decode("utf-8", "replace")
            events = _parse_sse(body)
            for e in events:
                data = e["data"]
                if isinstance(data, dict) and data.get("owner_token"):
                    owner_token_box["token"] = data["owner_token"]
            return body, events

        body1, events1 = _chat("检查这份数据的质量",
                               skill_name="validate_spatial_data")
        (WORK / "sse1.log").write_text(body1, encoding="utf-8")

        # 1) skill_name 显式选择披露（不再是“忽略”文案）
        verdict["owner_token_captured"] = bool(owner_token_box["token"])
        verdict["journeys"]["skill_name_notice"] = (
            "已选择技能 validate_spatial_data" in body1)
        if not verdict["journeys"]["skill_name_notice"]:
            verdict["fail"].append("no explicit skill selection notice in stream")

        # 2) tool_search 发现（技能作业工具在 deferred 面可发现）
        verdict["journeys"]["tool_search_discovery"] = (
            "gis_skill_job_prepare" in body1)

        # 3) prepare 回执：prepared_id
        prepared_id = None
        for e in events1:
            data = e["data"]
            if not isinstance(data, dict):
                continue
            for key in ("tool_result", "result"):
                payload = data.get(key)
                if isinstance(payload, dict) and \
                        str(payload.get("prepared_id", "")).startswith("skp-"):
                    prepared_id = payload["prepared_id"]
        if not prepared_id and '"prepared_id": "skp-' in body1:
            import re as _re
            m = _re.search(r'"prepared_id":\s*"(skp-[0-9a-f]+)"', body1)
            if m:
                prepared_id = m.group(1)
        verdict["journeys"]["prepare_receipt"] = bool(prepared_id)
        if not prepared_id:
            verdict["fail"].append("no prepare receipt (prepared_id) in stream")

        # 第二轮：start（prepared_id 已知）
        if prepared_id:
            # per-turn 动态面每轮重置：start 前同样经 tool_search 激活
            #（ADR-0288 deferred 面两跳模式；与 turn 1 同语义）。
            phase2 = {"启动执行": [
                {"tool": "tool_search", "args": {"query": "技能 作业 启动 执行 start"}},
                {"tool": "gis_skill_job_start", "args": {
                    "prepared_id": prepared_id, "wait_timeout_s": 90}},
                {"text": "执行完成。"},
            ]}
            script_path.write_text(json.dumps(phase2), encoding="utf-8")
            body2, events2 = _chat("启动执行")
            (WORK / "sse2.log").write_text(body2, encoding="utf-8")
            start_ok = None
            for e in events2:
                data = e["data"]
                if not isinstance(data, dict):
                    continue
                for key in ("tool_result", "result"):
                    payload = data.get(key)
                    if isinstance(payload, dict) and \
                            payload.get("success") is True and \
                            payload.get("status") == "completed":
                        start_ok = payload
            if not start_ok and '"status": "completed"' in body2 \
                    and '"result_ref": "ref:skillreport-' in body2:
                start_ok = {"recovered_from_stream": True}
            verdict["journeys"]["start_receipt_completed"] = bool(start_ok)
            if not start_ok:
                verdict["fail"].append("no completed start receipt in stream")

        # 4) 报告可回读（服务端核验：会话存储 + durable 行）
        async def _verify():
            from app.services.gis_harness.skill_jobs.runner import (
                list_skill_job_views,
            )
            from app.services.session_data import session_data_manager

            runs = await list_skill_job_views(session_id=session_id)
            ok_rows = any(
                r.get("skill_id") == "validate_spatial_data"
                and r.get("status") == "completed"
                and str(r.get("result_ref", "")).startswith("ref:skillreport-")
                for r in runs)
            report_ok = False
            if runs:
                ref = next(
                    (r.get("result_ref") for r in runs
                     if str(r.get("result_ref", "")).startswith("ref:skillreport-")),
                    None)
                if ref:
                    report = await session_data_manager.get(session_id, ref)
                    report_ok = (
                        report is not None
                        and report.get("skill_id") == "validate_spatial_data"
                        and "quality_verdict" in report.get(
                            "evidence_covered", []))
            return ok_rows, report_ok

        rows_ok, report_ok = asyncio.run(_verify())
        verdict["journeys"]["durable_job_row"] = rows_ok
        verdict["journeys"]["report_readable"] = report_ok
        if not rows_ok:
            verdict["fail"].append("no completed durable skill_job row")
        if not report_ok:
            verdict["fail"].append("report ref not readable server-side")
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
def test_pi_g30_skill_job_journey(tmp_path):
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
    assert verdict["journeys"]["skill_name_notice"] is True
    assert verdict["journeys"]["prepare_receipt"] is True
    assert verdict["journeys"]["start_receipt_completed"] is True
    assert verdict["journeys"]["durable_job_row"] is True
    assert verdict["journeys"]["report_readable"] is True
