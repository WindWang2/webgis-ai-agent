"""G35 WP10/WP12：固定 pin 的真实 Pi 子进程 + 生产扩展 + 生产 dispatch + 本域服务。

J-G35（唯一旅程，显式 opt-in ``PI_JOURNEY_E2E=1``）：
  stub LLM（合法，不 mock 主链）驱动一条 turn 内五次**真实工具调用**：
  capabilities → plan → submit(pending) → status → result
  → 扩展 ``index.mjs`` 经 ``/pi-tools/execute`` 真实回调
  → ``ToolDispatchService`` → ``temporal_media.service``（冻结/计划/提交/查询/成果）
  → worker 真实逐帧渲染 / APNG 编码 / 独立解码 / 原子发布
  → 签名 URL 真实 HTTP 下载并独立解码动画与证据 ZIP。

分层如实记录：Pi 子进程 / 后端 bridge / 领域服务 **同链**；
durable job 的 Celery enqueue 在此旅程以进程内 worker 替代（单测另验），
浏览器 UI 由 vitest 单独验证，**不宣称浏览器同链通过**。

门控：node + 固定 pin 的已构建 vendor + PI_JOURNEY_E2E=1（显式 skip，不静默绿）。
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
requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="journey needs node on PATH")


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

_STUB_JS = r"""
import http from 'node:http';
// 脚本化 LLM：计划版本与 job_id 由 driver 用**同一生产函数**从同一冻结文档
// 预计算（合法 fixture），避免从被上下文预算截断的工具回执里猜动态值。
const BASE = { width: 480, height: 270, dwell_ms: 1000,
  timing_policy: 'equal', missing_display: 'skip', format: 'apng', fps: 0 };
const planRef = process.env.STUB_PLAN_REF;
const jobId = process.env.STUB_JOB_ID;
let reqCount = 0;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const srv = http.createServer((req, res) => {
  let body = ''; req.on('data', (c) => (body += c));
  req.on('end', async () => {
    if (!req.url.includes('/chat/completions')) {
      res.writeHead(200, {'content-type':'text/plain'}); res.end('ok'); return;
    }
    reqCount += 1;
    let step = null;
    if (reqCount === 1) {
      // 生产 deferred 长尾发现路径（与 G26 `webgis_map_export` 同款）：
      // 先 tool_search 激活 G35 工具，再按名直调；不改写 surface dump。
      step = { name: 'tool_search',
               arguments: { query: '时序地图 动画 媒体 交付 计划 提交 状态 成果 下载 temporal media animation' } };
    } else if (reqCount === 2) {
      step = { name: 'webgis_temporal_media_capabilities', arguments: {} };
    } else if (reqCount === 3) {
      step = { name: 'webgis_temporal_media_plan', arguments: BASE };
    } else if (reqCount === 4) {
      step = { name: 'webgis_temporal_media_submit',
               arguments: { ...BASE, plan_ref: planRef } };
    } else if (reqCount === 5) {
      step = { name: 'webgis_temporal_media_status', arguments: { job_id: jobId } };
    } else if (reqCount === 6) {
      await sleep(1500);
      step = { name: 'webgis_temporal_media_result', arguments: { job_id: jobId } };
    }
    res.writeHead(200, {'content-type':'text/event-stream'});
    const sse = (o) => res.write(`data: ${JSON.stringify(o)}\n\n`);
    const id = 'c' + reqCount;
    if (step) {
      sse({ id, object:'chat.completion.chunk', created:1, model:'stub',
        choices:[{index:0, delta:{tool_calls:[{index:0, id:'call_'+reqCount,
          type:'function', function:{name:step.name,
            arguments:JSON.stringify(step.arguments)}}]}, finish_reason:null}] });
      sse({ id, object:'chat.completion.chunk', created:1, model:'stub',
        choices:[{index:0, delta:{}, finish_reason:'tool_calls'}] });
    } else {
      sse({ id, object:'chat.completion.chunk', created:1, model:'stub',
        choices:[{index:0, delta:{content:'年度动画与逐帧证据已生成并核验。'},
          finish_reason:null}] });
      sse({ id, object:'chat.completion.chunk', created:1, model:'stub',
        choices:[{index:0, delta:{}, finish_reason:'stop'}] });
    }
    res.write('data: [DONE]\n\n'); res.end();
  });
});
srv.listen(Number(process.env.STUB_PORT || 0), '127.0.0.1',
  () => console.error('STUB_READY ' + srv.address().port));
process.on('SIGTERM', () => process.exit(0));
"""

_PROBE_JS = r"""
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
const [entry, ext, dump, stubPort, promptFile] = process.argv.slice(2);
const agentDir = mkdtempSync(join(tmpdir(), 'pi-g35-'));
writeFileSync(join(agentDir, 'models.json'), JSON.stringify({
  providers: { webgis: {
    baseUrl: `http://127.0.0.1:${stubPort}/v1`,
    api: 'openai-completions', apiKey: '$STUB_KEY',
    compat: { supportsDeveloperRole: false, supportsReasoningEffort: false },
    models: [{ id: 'stub-model', name: 'stub-model', reasoning: false }],
  } },
}));
writeFileSync(join(agentDir, 'settings.json'), JSON.stringify({
  defaultProvider: 'webgis', defaultModel: 'stub-model' }));
const child = spawn('node', [entry, '--mode', 'rpc', '--no-session',
  '--no-builtin-tools', '--extension', ext], {
  env: { ...process.env, PI_CODING_AGENT_DIR: agentDir,
    PI_SESSION_DIR: join(agentDir, 'sessions'), PI_OFFLINE: '1',
    PI_SKIP_VERSION_CHECK: '1', WEBGIS_NATIVE_TOOLS_PATH: dump,
    WEBGIS_API_BASE: process.env.WEBGIS_API_BASE,
    WEBGIS_BRIDGE_SECRET: process.env.WEBGIS_BRIDGE_SECRET, STUB_KEY: 'k' },
  stdio: ['pipe', 'pipe', 'pipe'],
});
let out = ''; let err = '';
child.stdout.setEncoding('utf8'); child.stdout.on('data', (d) => (out += d));
child.stderr.setEncoding('utf8'); child.stderr.on('data', (d) => (err += d));
const send = (o) => child.stdin.write(JSON.stringify(o) + '\n');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
await sleep(1200);
send({ id: '1', type: 'get_state' });
await sleep(300);
const prompt = readFileSync(promptFile, 'utf8');
send({ id: '2', type: 'prompt', sessionId: 'g35-journey-session', message: prompt });
const deadline = Date.now() + 120000;
while (Date.now() < deadline) {
  await sleep(150);
  if (out.includes('agent_settled')) break;
}
await sleep(400);
child.kill('SIGKILL');
const lines = out.split('\n').filter(Boolean);
const parsed = [];
for (const line of lines) {
  try { parsed.push(JSON.parse(line)); } catch { parsed.push({ unparseable: line.slice(0, 160) }); }
}
process.stderr.write('RESULT=' + JSON.stringify({ lines: parsed,
  stderrHead: err.split('\n').slice(0, 8).join(' | ').slice(0, 600) }) + '\n');
"""

_DRIVER_SOURCE = r'''#!/usr/bin/env python3
"""G35 journey driver：真实 Pi RPC + 生产 app/bridge + stub LLM → VERDICT json。"""
import asyncio
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(sys.argv[1])
STUB_JS_PATH = Path(sys.argv[2])
PROBE_JS_PATH = Path(sys.argv[3])
WORK = Path(tempfile.mkdtemp(prefix="pi-g35-journey-"))
sys.path.insert(0, str(REPO_ROOT))
os.environ["PI_AUTOSTART"] = "false"
os.environ["GIS_MANIFEST_STRICT"] = "0"
os.environ["USE_REDIS"] = "false"
os.environ["WEBGIS_BRIDGE_SECRET"] = "g35-journey-secret"
os.environ["DATABASE_URL"] = "sqlite:///" + str(WORK / "g35.db")
os.environ["DATA_DIR"] = str(WORK / "data")
os.environ["JWT_SECRET_KEY"] = "g35-journey-jwt-secret"
(WORK / "data").mkdir(parents=True, exist_ok=True)


def free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(1)
    port = s.getsockname()[1]; s.close(); return port


def boot_stub(path: Path, extra_env: dict) -> tuple[int, subprocess.Popen]:
    err = WORK / "stub_err.log"
    with err.open("w") as fh:
        proc = subprocess.Popen(["node", str(path)], stdout=subprocess.DEVNULL,
                                stderr=fh,
                                env={**os.environ, "STUB_PORT": "0", **extra_env})
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        for line in err.read_text().splitlines():
            if "STUB_READY" in line:
                return int(line.split()[1]), proc
        time.sleep(0.1)
    proc.kill()
    raise RuntimeError("stub LLM did not boot")


def main() -> int:
    verdict = {"journeys": {}, "fail": [], "layers": {
        "pi_subprocess": True, "production_extension": True,
        "production_dispatch": True, "domain_service": True,
        "browser_same_chain": False,
        "durable_celery_enqueue": "in-process worker in this journey",
    }}
    # 用与生产 preview/submit 完全相同的函数预计算脚本化 LLM 的两个动态值。
    sys.path.insert(0, str(REPO_ROOT / "tests"))
    from tests.unit.temporal_media.conftest import temporal_document
    from app.services.temporal_media.capabilities import probe_capabilities
    from app.services.temporal_media.contracts import MediaPlanArgs
    from app.services.temporal_media.freeze import build_frozen
    from app.services.temporal_media.planning import build_plan
    from app.services.temporal_media.service import _job_id_for, _principal

    session_id = "g35-journey-session"
    doc = temporal_document([2019, 2021, 2024])
    frozen = build_frozen(session_id, doc, 3)
    principal = _principal(None, session_id)
    scripted_args = MediaPlanArgs(
        session_id=session_id, width=480, height=270, dwell_ms=1000,
        timing_policy="equal", missing_display="skip", format="apng", fps=0)
    scripted_plan = build_plan(frozen, scripted_args, principal,
                               probe_capabilities())
    stub_env = {
        "STUB_PLAN_REF": scripted_plan["plan_hash"][:16],
        "STUB_JOB_ID": _job_id_for(principal, scripted_plan["plan_hash"]),
    }
    stub_port, stub = boot_stub(STUB_JS_PATH, stub_env)

    api_port = free_port()
    os.environ["WEBGIS_API_BASE"] = "http://127.0.0.1:{}".format(api_port)
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(
        "app.main:app", host="127.0.0.1", port=api_port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True); thread.start()
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline and not server.started:
        time.sleep(0.3)
    if not server.started:
        stub.terminate()
        print(json.dumps({"fail": ["app did not start"]})); return 1

    # 会话数据种子（测试数据，不是主链 mock）：权威 MapSpec 文档与修订。
    from app.services import mapspec_store

    async def seeded_mapspec(session_id):
        return doc

    mapspec_store.mapspec_store.get_mapspec = seeded_mapspec
    import app.services.raster_authoring.service as ras

    async def seeded_revision(session_id):
        return 3

    ras.current_revision = seeded_revision

    # 生产 surface dump（真实注册表含 G35 工具）；旅程显式激活 G35 工具集。
    from app.services.chat.pi_native_surface import dump_surface_file
    from app.agent_pi_bridge import get_tool_registry, register_active_pi_turn
    registry = get_tool_registry()
    g35 = [n for n in registry.list_tools() if "temporal_media" in n]
    verdict["g35_tools_registered"] = len(g35)
    if len(g35) < 6:
        verdict["fail"].append("G35 tools missing from live registry: %r" % g35)
    dump_path = dump_surface_file(WORK / "native-tools.json")
    # 生产 surface dump **零改写**。G35 与 G26 的 webgis_map_export 一样属于
    # deferred 长尾：扩展注册全部超集工具，可由名称直调 / tool_search 发现；
    # 只有冻结的 native 前门在 default_active。旅程用脚本化 LLM 按名直调，
    # 不靠改写 dump 制造「默认已激活」的假象。
    payload = json.loads(dump_path.read_text(encoding="utf-8"))
    definitions = {t.get("name"): t for t in payload.get("tools") or []}
    verdict["journeys"]["tools_in_production_superset"] = all(
        n in definitions for n in g35)
    verdict["journeys"]["tools_deferred_contract"] = all(
        definitions.get(n, {}).get("exposure") == "deferred" for n in g35)
    verdict["journeys"]["tools_have_schema"] = all(
        bool((definitions.get(n, {}).get("parameters") or {}).get("properties"))
        for n in g35)
    verdict["surface_tools"] = len(payload.get("tools") or [])
    verdict["default_active_count"] = len(payload.get("default_active") or [])

    # 导出目录隔离；后台 worker 在本进程内真实执行 durable job 的任务体。
    import app.services.export_paths as ep
    exports = WORK / "exports"; exports.mkdir(exist_ok=True)
    ep.exports_root = lambda: exports
    ep.ensure_exports_root = lambda: exports
    import app.services.jobs.submit as job_submit
    from app.services.temporal_media.runner import execute_media_job

    def in_process_submit(**kwargs):
        tk = kwargs.get("task_kwargs") or {}
        media_job_id, principal = tk.get("media_job_id"), tk.get("principal")
        if media_job_id and principal:
            execute_media_job(principal, media_job_id)
        return {"job_id": "77", "task_id": "t-77", "idempotent_reuse": False}

    job_submit.submit_durable_job = in_process_submit

    turn_id = "turn-g35-0001"
    from app.services.chat.pi_turn_context import issue_turn_token
    token = issue_turn_token(os.environ["WEBGIS_BRIDGE_SECRET"], session_id, turn_id)
    asyncio.run(register_active_pi_turn(session_id, turn_id))

    prompt = (
        "把已有年度地图做成每年一秒的动画，保留缺期说明，查询并下载\n\n"
        "[WEBGIS_TURN_CONTEXT:{}]\n\n"
        "(Internal routing context; do not quote or modify this marker.)"
    ).format(token)
    (WORK / "prompt.txt").write_text(prompt, encoding="utf-8")

    from app.services.chat.pi_compat import pi_vendor_identity
    ident = pi_vendor_identity()
    entry = ident.entry_path
    ext = str(REPO_ROOT / "app" / "extensions" / "webgis-tools" / "index.mjs")
    verdict["pi_vendor_head"] = ident.head_commit
    verdict["pi_expected_pin"] = ident.expected_pin
    verdict["pi_entry_kind"] = ident.entry_kind
    if ident.matches_expected_pin is False:
        verdict["fail"].append("vendor/pi drifted off recorded pin")
    if entry is None:
        verdict["fail"].append("Pi RPC entry not built")

    try:
        proc = subprocess.run(
            ["node", str(PROBE_JS_PATH), str(entry), ext, str(dump_path),
             str(stub_port), str(WORK / "prompt.txt")],
            capture_output=True, text=True, timeout=180,
            env={**os.environ, "WEBGIS_API_BASE": os.environ["WEBGIS_API_BASE"],
                 "WEBGIS_BRIDGE_SECRET": os.environ["WEBGIS_BRIDGE_SECRET"]},
        )
        result_line = next((ln for ln in proc.stderr.splitlines()
                            if ln.startswith("RESULT=")), None)
        if not result_line:
            verdict["fail"].append("probe emitted no RESULT: " + proc.stderr[-800:])
            print("VERDICT " + json.dumps(verdict)); return 0
        parsed = json.loads(result_line[len("RESULT="):])
        (WORK / "pi_events.json").write_text(json.dumps(parsed["lines"]), encoding="utf-8")
        events = parsed["lines"]
        text = json.dumps(events)
        starts = [e for e in events if isinstance(e, dict)
                  and e.get("type") == "tool_execution_start"]
        ends = [e for e in events if isinstance(e, dict)
                and e.get("type") == "tool_execution_end"]
        names = [str(e.get("toolName") or "") for e in starts]
        verdict["tool_names"] = names
        verdict["journeys"]["deferred_discovery"] = "tool_search" in names
        verdict["journeys"]["capabilities"] = any("capabilities" in n for n in names)
        verdict["journeys"]["plan"] = any(n.endswith("_plan") for n in names)
        verdict["journeys"]["submit"] = any(n.endswith("_submit") for n in names)
        verdict["journeys"]["status"] = any(n.endswith("_status") for n in names)
        verdict["journeys"]["result"] = any(n.endswith("_result") for n in names)
        verdict["journeys"]["dispatch_no_error"] = bool(ends) and all(
            not e.get("isError") for e in ends)
        verdict["journeys"]["pending_not_upgraded"] = "pending" in text
        verdict["journeys"]["plan_version_in_chain"] = "plan_ref" in text
        verdict["journeys"]["artifacts_in_chain"] = "artifacts" in text
        verdict["journeys"]["agent_settled"] = any(
            isinstance(e, dict) and e.get("type") == "agent_settled" for e in events)
        for key, ok in list(verdict["journeys"].items()):
            if not ok:
                verdict["fail"].append("journey check failed: " + key)

        # 独立 HTTP 下载 + 独立解码（真实签名 URL）。
        from app.services.temporal_media.store import list_jobs, load_state
        jobs = list_jobs(principal)
        last = jobs[-1] if jobs else None
        if not last:
            verdict["fail"].append("no persisted media job")
        else:
            state = load_state(principal, last["job_id"])
            verdict["job_status"] = state.get("status")
            artifacts = state.get("artifacts") or {}
            base = "http://127.0.0.1:{}".format(api_port)
            animation = artifacts.get("animation") or {}
            if animation.get("url"):
                with urllib.request.urlopen(base + animation["url"], timeout=30) as resp:
                    data = resp.read()
                from PIL import Image
                with Image.open(io.BytesIO(data)) as im:
                    verdict["journeys"]["animation_download_and_decode"] = (
                        data[:8] == b"\x89PNG\r\n\x1a\n" and im.n_frames == 3)
                    verdict["decoded_frames"] = im.n_frames
            else:
                verdict["journeys"]["animation_download_and_decode"] = False
            evidence = artifacts.get("evidence") or {}
            if evidence.get("url"):
                with urllib.request.urlopen(base + evidence["url"], timeout=30) as resp:
                    zip_bytes = resp.read()
                with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                    manifest = json.loads(zf.read("manifest.json"))
                    verdict["journeys"]["evidence_package_readback"] = (
                        manifest["verification"]["passed"] is True
                        and manifest["container"]["total_duration_ms"] == 3000
                        and len(manifest["observations"]) == 3)
                    verdict["manifest_gaps"] = len(manifest["missing_periods"])
            else:
                verdict["journeys"]["evidence_package_readback"] = False
            for key in ("animation_download_and_decode", "evidence_package_readback"):
                if not verdict["journeys"].get(key):
                    verdict["fail"].append("artifact check failed: " + key)
            if not state.get("verification", {}).get("passed"):
                verdict["fail"].append("domain verification did not pass")
    except Exception as exc:  # noqa: BLE001
        verdict["fail"].append("driver exception: %s: %s" % (type(exc).__name__, exc))
    finally:
        stub.terminate()
        try:
            stub.wait(timeout=3)
        except subprocess.TimeoutExpired:
            stub.kill()
        server.should_exit = True
        thread.join(timeout=10)
    (WORK / "verdict.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    print("VERDICT " + json.dumps(verdict, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def test_pi_g35_temporal_media_journey(tmp_path):
    entry = _vendor_entry()
    if entry is None:
        pytest.skip(NEEDS_BUILD_REASON)
    stub_path = tmp_path / "g35_stub.mjs"
    probe_path = tmp_path / "g35_probe.mjs"
    stub_path.write_text(_STUB_JS, encoding="utf-8")
    probe_path.write_text(_PROBE_JS, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-c", _DRIVER_SOURCE, str(REPO_ROOT),
         str(stub_path), str(probe_path)],
        capture_output=True, text=True, timeout=420, cwd=str(REPO_ROOT),
    )
    verdict_line = next(
        (ln for ln in proc.stdout.splitlines() if ln.startswith("VERDICT ")), None)
    assert verdict_line, (
        "driver did not emit a verdict; stdout tail: " + proc.stdout[-1500:]
        + " stderr tail: " + proc.stderr[-2500:])
    verdict = json.loads(verdict_line[len("VERDICT "):])
    assert not verdict.get("fail"), verdict.get("fail")
    assert verdict["pi_vendor_head"] == verdict["pi_expected_pin"]
    assert verdict["g35_tools_registered"] >= 6
    for key in ("deferred_discovery", "capabilities", "plan", "submit",
                "status", "result", "dispatch_no_error",
                "pending_not_upgraded", "agent_settled",
                "plan_version_in_chain", "artifacts_in_chain",
                "tools_in_production_superset", "tools_deferred_contract",
                "tools_have_schema",
                "animation_download_and_decode", "evidence_package_readback"):
        assert verdict["journeys"].get(key) is True, key
    assert verdict["decoded_frames"] == 3
    assert verdict["manifest_gaps"] == 3
