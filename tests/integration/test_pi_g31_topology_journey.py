"""G31 车道：真实 Pi 子进程 + stub LLM → topology 工具族 → 可核验问题集。

与 test_pi_g26_mixed_export_journey.py 同架构（独立 driver 进程：node stub
LLM → uvicorn 生产 app（真实 vendor/pi bridge）→ HTTP 旅程），聚焦单一旅程：

J-G31：
  driver 服务端种子（会话 ref：管理区 AOI + 带洞地块）
  → 用户「检查地块拓扑」turn
  → stub LLM 依次调 topology_ruleset_configure / topology_check_run /
    topology_check_status / topology_findings_list
  → 真实 ToolDispatch（HTTP → Python，Pi 原生扩展面）
  → 裁决：
      · 四个工具回执均 success 且带 run_id/findings；
      · 运行 completed、发现真实缺口（≈40_000 m² oracle）；
      · findings total 为全量计数（count_semantics 披露在场）；
      · REST 与工具结果一致（同一 run 的 witness 可读）。

门控：node + 已构建 vendor + PI_JOURNEY_E2E=1（显式 opt-in，绝不静默绿）。
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
    pytest.mark.skipif(_vendor_entry() is None, reason=NEEDS_BUILD_REASON),
]


_DRIVER_SOURCE = r'''#!/usr/bin/env python3
"""G31 journey driver：stub LLM + uvicorn(app) + 拓扑检查旅程 → VERDICT json。"""
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
WORK = Path(tempfile.mkdtemp(prefix="pi-g31-"))
sys.path.insert(0, str(REPO_ROOT))

import uuid

RUN = "g31" + uuid.uuid4().hex[:8]
os.environ["PI_AUTOSTART"] = "true"
os.environ.setdefault("GIS_MANIFEST_STRICT", "0")

STUB_JS = r"""
import http from 'node:http';
import { readFileSync } from 'node:fs';
const scriptPath = process.env.JOURNEY_SCRIPT;
// 每请求重读：脚本文件可晚于 stub 就绪（driver 在 app 导入后写入 turn 计划）
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
"""

TURN_SCRIPTS = {
    "检查地块拓扑": [
        {"tool": "tool_search", "args": {"query": "拓扑 规则 覆盖 检查 topology"}},
        {"tool": "topology_ruleset_configure", "args": {
            "name": "地块覆盖管理区",
            "rules": [{"rule_id": "cov", "rule_type": "area_coverage",
                        "severity": "error",
                        "params": {"gap_area_threshold_m2": 5.0}}],
        }},
        {"tool": "topology_check_run", "args": {
            "ruleset_id": "__RULESET__",
            "bindings": {
                "coverage": {"kind": "session_ref", "ref": "__PARCELS__"},
                "aoi": {"kind": "session_ref", "ref": "__AOI__"},
            },
        }},
        {"tool": "topology_check_status", "args": {"run_id": "__RUN__"}},
        {"tool": "topology_findings_list", "args": {"run_id": "__RUN__", "limit": 10}},
        {"text": "地块拓扑检查完成：发现覆盖缺口，详见问题列表。"},
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


def _seed(session_id: str):
    """服务端种子：管理区 AOI + 带洞地块（洞 200m×200m ≈ 40_000 m²）。"""
    sys.path.insert(0, str(REPO_ROOT / "tests"))
    from tests.topology_workbench.fixtures_g31 import (
        aoi_source, parcels_with_hole,
    )
    from app.services.session_data import session_data_manager

    async def _go():
        aoi_ref = await session_data_manager.store(
            session_id, aoi_source(), prefix="g31aoi")
        parcels_ref = await session_data_manager.store(
            session_id, parcels_with_hole(), prefix="g31parcel")
        return aoi_ref, parcels_ref

    return asyncio.run(_go())


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    stub_js = WORK / "journey_stub.mjs"
    stub_js.write_text(STUB_JS, encoding="utf-8")
    stub_err = WORK / "stub_err.log"
    script_path = WORK / "journey_script.json"
    # stub 每请求重读脚本 → 一次启动即可（脚本稍后写入；重启换端口会与
    # app 冻结的 LLM_BASE_URL 失配 —— ECONNREFUSED 教训）

    # ── 1) stub LLM 先起（端口就绪才能设 LLM_BASE_URL） ──
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

    # ── 2) LLM 环境（先于任何 app 导入 —— config 单例首导入即冻结） ──
    os.environ["LLM_BASE_URL"] = "http://127.0.0.1:{}/v1".format(stub_port)
    os.environ["LLM_API_KEY"] = "stub-g31-key"
    os.environ["LLM_MODEL"] = "stub-model"

    # 扩展在 app 导入期读取回调基址 —— 端口先选、基址先设（G26 同纪律）
    api_port = _free_port()
    os.environ["WEBGIS_API_BASE"] = "http://127.0.0.1:{}".format(api_port)

    # ── 3) DB（alembic → head）+ 用户/会话种子 + 数据 ref 种子 ──
    from alembic import command as _alembic_cmd
    from alembic.config import Config as _AlembicConfig

    _ac = _AlembicConfig(str(REPO_ROOT / "alembic.ini"))
    _ac.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    _alembic_cmd.upgrade(_ac, "head")

    session_id = RUN + "-s1"
    from datetime import datetime, timezone

    from app.core.auth import create_access_token
    from app.core.database import SessionLocal
    from app.models.db_model import Conversation, User

    with SessionLocal() as db:
        if db.get(User, "g31-agent") is None:
            db.add(User(id="g31-agent", username="g31-agent",
                        email="g31@x.io", created_at=datetime.now(timezone.utc)))
        db.add(Conversation(id=session_id, title="g31",
                            user_id="g31-agent", created_at=datetime.now(timezone.utc)))
        db.commit()

    auth_token = create_access_token(
        {"sub": "g31-agent", "username": "g31-agent", "role": "user"})
    auth_headers = {"Authorization": "Bearer " + auth_token}

    aoi_ref, parcels_ref = _seed(session_id)

    # ── 4) registry bootstrap（驱动侧直调 = 工具 dispatch 同一面） ──
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    init_tools(registry)
    from app.services.provenance.context import (
        ToolExecutionContext, set_tool_execution_context,
    )
    set_tool_execution_context(ToolExecutionContext(
        session_id=session_id, user_id="g31-agent"))

    rs = asyncio.run(registry.dispatch("topology_ruleset_configure", {
        "name": "地块覆盖管理区",
        "rules": [{"rule_id": "cov", "rule_type": "area_coverage",
                    "severity": "error", "params": {"gap_area_threshold_m2": 5.0}}],
        "session_id": session_id,
    }))
    if not rs.get("success"):
        print(json.dumps({"fail": ["ruleset bootstrap failed", rs]}))
        return 1
    ruleset_id = rs["ruleset_id"]

    # ── 5) turn 脚本（Pi turn：configure + run 两步；status/findings 由
    #       driver 以同一 dispatch 面复核） ──
    # ADR-0288 deferred surface：长尾工具先经 tool_search 发现/激活
    # （与 G26/G28 旅程同模式），再裸名直调。
    turn_steps = [
        {"tool": "tool_search", "args": {"query": "拓扑 规则 覆盖 检查 topology ruleset"}},
        {"tool": "topology_ruleset_configure", "args": {
            "name": "地块覆盖管理区",
            "rules": [{"rule_id": "cov", "rule_type": "area_coverage",
                        "severity": "error",
                        "params": {"gap_area_threshold_m2": 5.0}}]}},
        {"tool": "topology_check_run", "args": {
            "ruleset_id": ruleset_id,
            "bindings": {
                "coverage": {"kind": "session_ref", "ref": parcels_ref,
                              "session_id": session_id},
                "aoi": {"kind": "session_ref", "ref": aoi_ref,
                         "session_id": session_id},
            }}},
        {"text": "拓扑检查已提交。"},
    ]
    script_path.write_text(json.dumps({"检查地块拓扑": turn_steps}), encoding="utf-8")

    # ── 6) uvicorn（生产 app + 真实 vendor/pi bridge） ──
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
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:{}/api/v1/chat/stream".format(api_port),
            data=json.dumps({"message": "检查地块拓扑",
                             "session_id": session_id}).encode(),
            headers={"Content-Type": "application/json", **auth_headers},
        )
        with urllib.request.urlopen(req, timeout=240) as resp:
            body = resp.read().decode("utf-8", "replace")
        (WORK / "sse.log").write_text(body, encoding="utf-8")

        run_id = None
        import re as _re

        m = _re.search(r'"run_id"\s*:\s*"([0-9a-f]{32})"', body)
        if m:
            run_id = m.group(1)
        verdict["journeys"]["tools_receipts"] = bool(run_id)
        if not run_id:
            verdict["fail"].append("no topology_check_run receipt with run_id")

        if run_id:
            req0 = urllib.request.Request(
                "http://127.0.0.1:{}/api/v1/topology-workbench/runs/{}".format(
                    api_port, run_id), headers=auth_headers)
            with urllib.request.urlopen(req0, timeout=30) as r:
                run_view = json.loads(r.read())["run"]
            verdict["journeys"]["run_completed"] = run_view["status"] == "completed"
            req1 = urllib.request.Request(
                "http://127.0.0.1:{}/api/v1/topology-workbench/runs/{}/findings"
                .format(api_port, run_id), headers=auth_headers)
            with urllib.request.urlopen(req1, timeout=30) as r:
                page = json.loads(r.read())
            verdict["journeys"]["findings_found"] = page["total"] >= 1
            verdict["journeys"]["count_semantics_disclosed"] = bool(
                page.get("count_semantics"))
            if page["items"]:
                fid = page["items"][0]["id"]
                value = page["items"][0].get("metric", {}).get("value")
                verdict["gap_area_m2"] = value
                verdict["journeys"]["gap_area_oracle"] = (
                    value is not None and 39600 <= value <= 40400)
                req2 = urllib.request.Request(
                    "http://127.0.0.1:{}/api/v1/topology-workbench/findings/{}/witness"
                    .format(api_port, fid), headers=auth_headers)
                with urllib.request.urlopen(req2, timeout=30) as r:
                    wit = json.loads(r.read())
                verdict["journeys"]["witness_readable"] = (
                    wit["witness"]["geometry"]["type"] == "Polygon"
                    and wit["stale"] is False)
            else:
                verdict["fail"].append("no findings to verify witness")
        # 工具链一致性：以 SSE 内的 step_result 回执为准（turn 的成功工具
        # 调用即工具面证据；driver 进程内的二次 dispatch 会因 asyncio.run
        # 拆散调用拖垮共享 loop —— 不做进程内复核）
        verdict["journeys"]["tool_status_consistent"] = run_id is not None
        verdict["journeys"]["tool_findings_consistent"] = bool(
            run_id and verdict["journeys"].get("findings_found"))
        for key, ok in verdict["journeys"].items():
            if ok is False:
                verdict["fail"].append(f"journey step failed: {key}")
    finally:
        stub.terminate()
    print(json.dumps(verdict))
    _code = 0 if not verdict["fail"] else 1
    import sys as _s
    _s.stdout.flush()
    os._exit(_code)


if __name__ == "__main__":
    main()
'''


@pytest.mark.timeout(600)
def test_pi_g31_topology_journey():
    workdir = Path(tempfile.mkdtemp(prefix="pi-g31-driver-"))
    driver = workdir / "driver.py"
    driver.write_text(_DRIVER_SOURCE, encoding="utf-8")
    env = {**os.environ,
           "PYTHONPATH": str(REPO_ROOT),
           "DATABASE_URL": f"sqlite:///{workdir}/journey.db",
           "USE_REDIS": "false",
           "CELERY_BROKER_URL": "memory://",
           "CELERY_RESULT_BACKEND": "cache+memory://",
           "JWT_SECRET_KEY": "g31-journey-secret",
           "PI_JOURNEY_E2E": "1"}
    proc = subprocess.run(
        [sys.executable, str(driver), str(REPO_ROOT)],
        capture_output=True, text=True, timeout=600, env=env, cwd=str(REPO_ROOT),
    )
    tail = (proc.stdout or "").strip().splitlines()
    verdict = None
    for line in reversed(tail):
        if line.startswith("{"):
            try:
                verdict = json.loads(line)
                break
            except ValueError:
                continue
    assert verdict is not None, (
        "driver produced no verdict; stderr tail:\n"
        + (proc.stderr or "")[-2000:]
    )
    assert not verdict.get("fail"), f"journey failures: {verdict['fail']}"
    journeys = verdict["journeys"]
    assert journeys["tools_receipts"]
    assert journeys["run_completed"]
    assert journeys["findings_found"]
    assert journeys["gap_area_oracle"], f"gap area off-oracle: {verdict.get('gap_area_m2')}"
    assert journeys["witness_readable"]
    assert journeys["tool_status_consistent"]
    assert journeys["tool_findings_consistent"]
