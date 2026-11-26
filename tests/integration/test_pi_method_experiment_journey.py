"""方法实验真实 Pi 旅程（02 任务 WP6 / 验收旅程 6）。

架构与 G29–G41 旅程同款：pytest 只做门控与裁决；driver 是独立 Python
子进程（避免 conftest 预导入污染 app 单例）。链：

    stub LLM SSE（node 子进程）
      → uvicorn(app.main:app)（生产 lifespan 拉起固定 pin 的 Pi RPC 子进程）
      → /api/v1/chat/stream 生产 HTTP 链
      → tool_search deferred 激活
      → webgis_method_experiment_{inspect,plan,run,compare,adopt}
      → ToolDispatchService / ToolRegistry 生产 dispatch（ref 解引用）
      → method_experiment.service（与普通 UI REST 完全同一实现）
      → 真实插值评价原语（interpolation_block_cv）→ CV 指标 → 推荐 → 采纳回执

只有 LLM 响应是 stub；RPC/dispatch/评价计算/持久化主链都不 mock。
**同链确定性证明**：seed 阶段先经服务层直接编译出 experiment_id（内容
hash），Pi plan 回合产出的 id 必须与之逐字一致（同一输入同一计划）。

门控：PI_JOURNEY_E2E=1 + node 可用 + 固定 pin 的 vendor/pi RPC entry
已构建（显式 skip，绝不 silent-green）。不等线上 CI。
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
    shutil.which("node") is None, reason="journey needs node on PATH")


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
"""方法实验 journey driver：stub LLM + uvicorn(app) + 五工具链 → VERDICT json。"""
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
WORK = Path(tempfile.mkdtemp(prefix="pi-mexp-"))
sys.path.insert(0, str(REPO_ROOT))

import uuid

RUN = "mexp" + uuid.uuid4().hex[:8]
os.environ["PI_AUTOSTART"] = "true"
os.environ.setdefault("GIS_MANIFEST_STRICT", "0")

STUB_JS = r"""
import http from 'node:http';
const fsMod = await import('node:fs');
const scriptPath = process.env.JOURNEY_SCRIPT;
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
    const turnScripts = JSON.parse(fsMod.readFileSync(scriptPath, 'utf8'));
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


def _make_fc(n=40, seed=12):
    import random
    rng = random.Random(seed)
    feats = []
    for _ in range(n):
        lon = 104.0 + rng.uniform(-0.03, 0.03)
        lat = 30.6 + rng.uniform(-0.03, 0.03)
        price = rng.gauss(100, 5)
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
            "properties": {"price": round(price, 4)},
        })
    return {"type": "FeatureCollection", "features": feats}


def main() -> int:
    session_id = RUN + "-s1"

    # ── seed：点要素 → ref；服务层直接预编译计划（确定性 id）─────────────
    async def _seed():
        from app.services.session_data import session_data_manager as sdm
        ref = await sdm.store(session_id, _make_fc(), prefix="geojson")
        from app.services.gis_harness.method_experiment.plan import (
            compile_experiment_plan,
        )
        plan = compile_experiment_plan(
            family_id="interpolation", input_ref=str(ref), value_field="price",
            candidate_ids=["idw", "nearest_neighbor"], max_trials=4)
        return str(ref), plan

    input_ref, plan = asyncio.run(_seed())
    experiment_id = plan.experiment_id
    n_trials = len(plan.trials)

    def _build_turn_scripts() -> dict:
        return {
            "warmup": [{"text": "warmup-ok"}],
            "看看插值方法比较的能力面": [
                {"tool": "tool_search",
                 "args": {"query": "方法实验 方法卡 插值 比较 选型"}},
                {"tool": "webgis_method_experiment_inspect",
                 "args": {"family_id": "interpolation"}},
                {"text": "方法卡已检视。"},
            ],
            "编译插值实验计划": [
                {"tool": "webgis_method_experiment_plan",
                 "args": {"family_id": "interpolation", "input_ref": input_ref,
                          "value_field": "price",
                          "candidate_ids": ["idw", "nearest_neighbor"],
                          "max_trials": 4, "session_id": session_id}},
                {"text": "计划已编译。"},
            ],
            "运行这个实验": [
                {"tool": "webgis_method_experiment_run",
                 "args": {"experiment_id": experiment_id,
                          "session_id": session_id}},
                {"text": "实验已运行。"},
            ],
            "比较实验结果": [
                {"tool": "webgis_method_experiment_compare",
                 "args": {"experiment_id": experiment_id,
                          "session_id": session_id}},
                {"text": "比较已完成。"},
            ],
            "采纳第一个候选": [
                {"tool": "webgis_method_experiment_adopt",
                 "args": {"experiment_id": experiment_id, "trial_id": "t01",
                          "reason": "比较证据支持", "session_id": session_id}},
                {"text": "采纳已记录。"},
            ],
        }

    stub_js = WORK / "journey_stub.mjs"
    stub_js.write_text(STUB_JS, encoding="utf-8")
    (WORK / "journey_script.json").write_text("{}", encoding="utf-8")
    with open(WORK / "stub_err.log", "w") as fh:
        stub = subprocess.Popen(
            ["node", str(stub_js)], stdout=subprocess.DEVNULL, stderr=fh,
            env={**os.environ, "JOURNEY_SCRIPT": str(WORK / "journey_script.json")})
    stub_port = None
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        for line in (WORK / "stub_err.log").read_text().splitlines():
            if "STUB_READY" in line:
                stub_port = int(line.split()[1])
        if stub_port:
            break
        time.sleep(0.1)
    if not stub_port:
        print(json.dumps({"fail": ["stub LLM did not boot"]}))
        return 1

    os.environ["LLM_BASE_URL"] = f"http://127.0.0.1:{stub_port}/v1"
    os.environ["LLM_API_KEY"] = "stub-mexp-key"
    os.environ["LLM_MODEL"] = "stub-model"
    api_port = _free_port()
    os.environ["WEBGIS_API_BASE"] = f"http://127.0.0.1:{api_port}"

    import uvicorn
    server = uvicorn.Server(uvicorn.Config(
        "app.main:app", host="127.0.0.1", port=api_port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    from app.services.chat.engine_instance import try_get_chat_engine
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline and not (
            server.started and try_get_chat_engine() is not None):
        time.sleep(0.5)
    if not (server.started and try_get_chat_engine() is not None):
        stub.terminate()
        print(json.dumps({"fail": ["app did not start"]}))
        return 1

    verdict: dict = {"journeys": {}, "fail": [],
                     "experiment_id": experiment_id}

    def chat(message: str) -> str:
        req = urllib.request.Request(
            f"http://127.0.0.1:{api_port}/api/v1/chat/stream",
            data=json.dumps({"message": message, "session_id": session_id}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            body = resp.read().decode("utf-8", "replace")
        return body

    try:
        (WORK / "journey_script.json").write_text(
            json.dumps(_build_turn_scripts()), encoding="utf-8")

        warm = chat("warmup")
        if "task_error" in warm:
            warm = chat("warmup")
        verdict["journeys"]["warmup_ok"] = "task_complete" in warm
        (WORK / "sse_warm.log").write_text(warm, encoding="utf-8")

        b1 = chat("看看插值方法比较的能力面")
        (WORK / "sse_inspect.log").write_text(b1, encoding="utf-8")
        verdict["journeys"]["inspect_via_pi"] = (
            "webgis_method_experiment_inspect" in b1)

        b2 = chat("编译插值实验计划")
        (WORK / "sse_plan.log").write_text(b2, encoding="utf-8")
        verdict["journeys"]["plan_via_pi"] = (
            "webgis_method_experiment_plan" in b2)
        # 同链确定性：Pi plan 产出的 experiment_id 与服务层预编译逐字一致
        verdict["journeys"]["plan_id_same_chain"] = experiment_id in b2

        b3 = chat("运行这个实验")
        (WORK / "sse_run.log").write_text(b3, encoding="utf-8")
        verdict["journeys"]["run_via_pi"] = (
            "webgis_method_experiment_run" in b3)
        verdict["journeys"]["run_completed_all_trials"] = (
            '"completed"' in b3 and b3.count('"completed"') >= n_trials)

        b4 = chat("比较实验结果")
        (WORK / "sse_compare.log").write_text(b4, encoding="utf-8")
        verdict["journeys"]["compare_via_pi"] = (
            "webgis_method_experiment_compare" in b4)
        verdict["journeys"]["recommendation_evidence"] = (
            '"recommended"' in b4 and "cv_rmse" in b4)

        b5 = chat("采纳第一个候选")
        (WORK / "sse_adopt.log").write_text(b5, encoding="utf-8")
        verdict["journeys"]["adopt_via_pi"] = (
            "webgis_method_experiment_adopt" in b5)
        verdict["journeys"]["adopt_receipt_recorded"] = (
            '"adoption"' in b5 or '"t01"' in b5)

        # 独立回读：adoption receipt 真实持久化（session store 单键）
        async def _readback():
            from app.services.gis_harness.method_experiment.service import (
                load_state,
            )
            state = await load_state(session_id, experiment_id)
            if state is None or state.adoption is None:
                return False
            return (state.adoption.trial_id == "t01"
                    and state.adoption.metric_snapshot
                    and state.status in ("completed", "partial"))
        verdict["journeys"]["adoption_readback"] = asyncio.run(_readback())

    except Exception as exc:  # noqa: BLE001 — driver 失败要带消息返回
        verdict["fail"].append(f"{type(exc).__name__}: {exc}")
    finally:
        stub.terminate()

    if not verdict["journeys"].get("warmup_ok"):
        verdict["fail"].append("warmup failed")
    for key in ("inspect_via_pi", "plan_via_pi", "plan_id_same_chain",
                "run_via_pi", "run_completed_all_trials", "compare_via_pi",
                "recommendation_evidence", "adopt_via_pi",
                "adoption_readback"):
        if not verdict["journeys"].get(key):
            verdict["fail"].append(f"journey step failed: {key}")

    print(json.dumps(verdict, ensure_ascii=False))
    return 1 if verdict["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
'''


def test_pi_method_experiment_journey(tmp_path: Path):
    """门控 + 裁决：driver 子进程退出码与 VERDICT json 决定。"""
    driver = tmp_path / "driver.py"
    driver.write_text(_DRIVER_SOURCE, encoding="utf-8")
    venv_bin = Path(sys.executable)
    proc = subprocess.run(
        [str(venv_bin), str(driver), str(REPO_ROOT)],
        capture_output=True, text=True, timeout=600,
        cwd=str(REPO_ROOT))
    tail = (proc.stdout or "")[-2000:]
    assert proc.returncode == 0, (
        f"journey driver failed:\nstdout tail: {tail}\n"
        f"stderr tail: {(proc.stderr or '')[-1500:]}")
    verdict = json.loads(proc.stdout.strip().splitlines()[-1])
    assert not verdict.get("fail"), verdict
    assert verdict["journeys"].get("plan_id_same_chain") is True
