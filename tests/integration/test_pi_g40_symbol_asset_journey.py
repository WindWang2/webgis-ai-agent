"""G40 旅程：真实 Pi 子进程 + stub LLM → webgis_symbol_* → 权威 MapSpec。

与 test_pi_g33_thematic_authoring_journey.py 同架构（独立 driver 进程：
node stub LLM → uvicorn 生产 app（真实 vendor/pi bridge）→ HTTP 旅程），
聚焦 S29/S30：

J-G40：
  driver 服务端种子（点层 circle + 内联 GeoJSON）
  → 用户「列出可用符号资产」turn → webgis_symbol_asset_list（真实 Pi
    dispatch，deferred 工具经 tool_search 激活）
  → 用户「给点位用内置三角形」turn → webgis_symbol_layer_apply（钉
    revision+digest，expected_revision CAS）
  → 裁决：工具成功回执 + 权威 MapSpec symbol_asset 块 = builtin 三角
    digest pin + 兄弟 paint 保留 + 修订推进
  → 用户「解除符号绑定」turn → webgis_symbol_layer_unbind
  → 裁决：块移除 + paint 恢复 + revision 再推进。

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
    pytest.mark.skipif(_vendor_entry() is None, reason=NEEDS_BUILD_REASON),
]


_DRIVER_SOURCE = r'''#!/usr/bin/env python3
"""G40 journey driver：stub LLM + uvicorn(app) + 符号资产绑定旅程 → VERDICT."""
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
WORK = Path(tempfile.mkdtemp(prefix="pi-g40-"))
sys.path.insert(0, str(REPO_ROOT))

import uuid

RUN = "g40" + uuid.uuid4().hex[:8]
LAYER_ID = "poi-journey"
os.environ["PI_AUTOSTART"] = "true"
os.environ.setdefault("GIS_MANIFEST_STRICT", "0")
# 旅程无本地 Redis（单进程 uvicorn + in-process 会话锁即生产等价面）
os.environ.setdefault("USE_REDIS", "false")

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


async def _seed_session(session_id: str) -> int:
    import app.services.mapspec.store as store_module

    store_module.BASE_STORAGE_DIR = WORK / "mapspec_store"
    store_module.BASE_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    features = []
    for i in range(4):
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [116.0 + i * 0.05, 39.9 + (i % 2) * 0.03]},
            "properties": {"name": f"poi-{i}"},
        })
    fc = {"type": "FeatureCollection", "features": features}

    from app.services.session_data import session_data_manager

    ref = await session_data_manager.store(
        session_id, {"kind": "geojson", "geojson": fc}, prefix="result")

    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.intents import UpsertLayerIntent

    result = await apply_gis_mutation(
        session_id,
        UpsertLayerIntent(layer={
            "id": LAYER_ID, "type": "circle", "source": f"{LAYER_ID}-source",
            "paint": {"color": "#2563eb", "radius": 6},
        }, source_data={"ref_id": str(ref)}),  # type: ignore[arg-type]
        origin="agent", actor="journey:seed",
    )
    assert not result.is_error, result.error_msg
    from app.services.session_data import session_data_manager as sdm

    return int(await sdm.get_state_field(session_id, "_cartographic_mutation_revision") or 0)


async def _read_layer_async(session_id: str):
    from app.services.mapspec_store import mapspec_store

    spec = await mapspec_store.get_mapspec(session_id) or {}
    for layer in spec.get("layers") or []:
        if layer.get("id") == LAYER_ID:
            return layer
    return None


def main() -> int:
    def _build_turn_scripts(rev_seed: int) -> dict:
        return {
        "warmup": [{"text": "warmup-ok"}],
        "列出可用符号资产": [
            {"tool": "tool_search", "args": {"query": "符号 资产 点 图标 纹理"}},
            {"tool": "webgis_symbol_asset_list", "args": {"kind": "point_symbol"}},
            {"text": "内置符号已列出。"},
        ],
        "给点位用内置三角形": [
            {"tool": "tool_search", "args": {"query": "符号 资产 绑定 图层 提交"}},
            {"tool": "webgis_symbol_layer_apply", "args": {
                "layer_id": LAYER_ID,
                "plan": {
                    "asset_id": "cartoasset.builtin.triangle",
                    "revision": 1,
                    "kind": "point_symbol",
                    "params": {"size_px": 18, "anchor": "center",
                               "rotation_deg": 0},
                },
                "expected_revision": rev_seed,
            }},
            {"text": "已绑定三角形。"},
        ],
        "解除符号绑定": [
            {"tool": "tool_search", "args": {"query": "符号 资产 解绑 恢复"}},
            {"tool": "webgis_symbol_layer_unbind", "args": {
                "layer_id": LAYER_ID,
                "expected_revision": rev_seed + 1,
            }},
            {"text": "已解除绑定。"},
        ],
    }

    stub_js = WORK / "journey_stub.mjs"
    stub_js.write_text(STUB_JS, encoding="utf-8")
    (WORK / "journey_script.json").write_text("{}", encoding="utf-8")

    with open(WORK / "stub_err.log", "w") as fh:
        stub = subprocess.Popen(
            ["node", str(stub_js)], stdout=subprocess.DEVNULL, stderr=fh,
            env={**os.environ, "JOURNEY_SCRIPT": str(WORK / "journey_script.json")},
        )
    stub_port = None
    deadline = time.monotonic() + 10
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

    os.environ["LLM_BASE_URL"] = "http://127.0.0.1:{}/v1".format(stub_port)
    os.environ["LLM_API_KEY"] = "stub-g40-key"
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
    owner_token = None
    session_id = RUN + "-s1"

    def chat(message: str) -> str:
        nonlocal owner_token
        headers = {"Content-Type": "application/json"}
        if owner_token:
            headers["X-Session-Token"] = owner_token
        req = urllib.request.Request(
            "http://127.0.0.1:{}/api/v1/chat/stream".format(api_port),
            data=json.dumps({"message": message, "session_id": session_id}).encode(),
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = resp.read().decode("utf-8", "replace")
        for block in body.split("\n\n"):
            for line in block.splitlines():
                if line.startswith("data: ") and '"owner_token"' in line:
                    try:
                        d = json.loads(line[6:])
                        if isinstance(d.get("owner_token"), str):
                            owner_token = d["owner_token"]
                    except ValueError:
                        pass
        return body

    try:
        rev_seed = asyncio.run(_seed_session(session_id))
        verdict["seeded_revision"] = rev_seed
        (WORK / "journey_script.json").write_text(
            json.dumps(_build_turn_scripts(rev_seed)), encoding="utf-8")

        warm = chat("warmup")
        if "task_error" in warm:
            warm = chat("warmup")
        verdict["journeys"]["warmup_ok"] = "task_complete" in warm

        body1 = chat("列出可用符号资产")
        if "task_error" in body1:
            body1 = chat("列出可用符号资产")
        (WORK / "sse1.log").write_text(body1, encoding="utf-8")
        verdict["journeys"]["list_receipt"] = "webgis_symbol_asset_list" in body1

        body2 = chat("给点位用内置三角形")
        if "task_error" in body2:
            body2 = chat("给点位用内置三角形")
        (WORK / "sse2.log").write_text(body2, encoding="utf-8")

        layer = asyncio.run(_read_layer_async(session_id))
        if layer is None:
            verdict["fail"].append("layer missing after apply")
        else:
            binding = layer.get("symbol_asset") or {}
            verdict["journeys"]["binding_committed"] = bool(binding)
            verdict["journeys"]["binding_pins_builtin"] = (
                binding.get("asset_id") == "cartoasset.builtin.triangle"
                and binding.get("revision") == 1)
            from app.lib.cartography.symbol_asset import get_builtin_asset
            tri = get_builtin_asset("cartoasset.builtin.triangle")
            verdict["journeys"]["digest_pin_ok"] = (
                binding.get("content_digest") == tri.content_digest)
            verdict["journeys"]["siblings_kept"] = (
                (layer.get("paint") or {}).get("radius") == 6)
            applied_by = binding.get("applied_by") or {}
            verdict["journeys"]["origin_agent"] = (
                applied_by.get("origin") == "agent")

        body3 = chat("解除符号绑定")
        if "task_error" in body3:
            body3 = chat("解除符号绑定")
        (WORK / "sse3.log").write_text(body3, encoding="utf-8")
        layer3 = asyncio.run(_read_layer_async(session_id))
        verdict["journeys"]["unbound_block_removed"] = bool(
            layer3 is not None and "symbol_asset" not in layer3)
        verdict["journeys"]["unbound_paint_restored"] = bool(
            layer3 is not None
            and (layer3.get("paint") or {}).get("color") == "#2563eb")

        from app.services.session_data import session_data_manager as sdm
        rev_final = int(asyncio.run(sdm.get_state_field(
            session_id, "_cartographic_mutation_revision")) or 0)
        verdict["journeys"]["revision_advanced"] = rev_final >= rev_seed + 2

        for key, ok in verdict["journeys"].items():
            if not ok:
                verdict["fail"].append(key)
        verdict["work_dir"] = str(WORK)
        print(json.dumps(verdict))
        return 0 if not verdict["fail"] else 1
    finally:
        stub.terminate()
        server.should_exit = True
        thread.join(timeout=10)


if __name__ == "__main__":
    sys.exit(main())
'''


def test_pi_g40_symbol_asset_journey(tmp_path: Path):
    driver = tmp_path / "g40_driver.py"
    driver.write_text(_DRIVER_SOURCE, encoding="utf-8")
    env = {**os.environ,
           "DATA_DIR": str(tmp_path / "data"),
           "PYTHONUNBUFFERED": "1"}
    proc = subprocess.run(
        [sys.executable, "-I", str(driver), str(REPO_ROOT)],
        capture_output=True, timeout=600, env=env, cwd=str(tmp_path))
    out = proc.stdout.decode("utf-8", "replace").strip().splitlines()
    verdict = {}
    for line in reversed(out):
        if line.startswith("{"):
            try:
                verdict = json.loads(line)
                break
            except ValueError:
                continue
    assert verdict, (
        "driver verdict missing\nstderr:\n"
        + proc.stderr.decode("utf-8", "replace")[-4000:])
    failures = verdict.get("fail") or []
    assert not failures, f"journey failures: {failures}\nverdict: {verdict}"
    journeys = verdict.get("journeys") or {}
    assert journeys.get("warmup_ok") is True
    assert journeys.get("list_receipt") is True
    assert journeys.get("binding_committed") is True
    assert journeys.get("binding_pins_builtin") is True
    assert journeys.get("digest_pin_ok") is True
    assert journeys.get("siblings_kept") is True
    assert journeys.get("origin_agent") is True
    assert journeys.get("unbound_block_removed") is True
    assert journeys.get("unbound_paint_restored") is True
    assert journeys.get("revision_advanced") is True
