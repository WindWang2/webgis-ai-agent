"""G33 旅程：真实 Pi 子进程 + stub LLM → webgis_thematic_style_* → 权威 MapSpec。

与 test_pi_g26_mixed_export_journey.py 同架构（独立 driver 进程：node stub
LLM → uvicorn 生产 app（真实 vendor/pi bridge）→ HTTP 旅程），聚焦单一旅程：

J-G33（验收 20/16 一部分 + WP09）：
  driver 服务端种子（GeoJSON → ref → 真实 UpsertLayerIntent 专题层）
  → 用户「查看这个专题层」turn → stub LLM 调 webgis_thematic_style_inspect
  → 用户「改成 0/10/20/50」turn → webgis_thematic_style_apply（人工断点，
    expected_revision CAS）
  → 用户「检查图例」turn → inspect 回读
  → 用户「恢复自动」turn → apply(mode=revert_auto)
  → 裁决：工具成功回执 + 权威 MapSpec 断点为 [0,10,20,50] + paint/图例同源
    （integrity oracle）+ revert 后回到自动裁决 + 证据块 decided_by=agent。

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
"""G33 journey driver：stub LLM + uvicorn(app) + 专题作者旅程 → VERDICT json。"""
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
WORK = Path(tempfile.mkdtemp(prefix="pi-g33-"))
sys.path.insert(0, str(REPO_ROOT))

import uuid

RUN = "g33" + uuid.uuid4().hex[:8]
LAYER_ID = "result-journey"
os.environ["PI_AUTOSTART"] = "true"
os.environ.setdefault("GIS_MANIFEST_STRICT", "0")

STUB_JS = r"""
import http from 'node:http';
const scriptPath = process.env.JOURNEY_SCRIPT;
const turnScripts = JSON.parse(await import('node:fs').then((fs) => fs.readFileSync(scriptPath, 'utf8')));
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


async def _seed_session(session_id: str) -> int:
    """服务端种子：GeoJSON → ref → 真实 UpsertLayerIntent 专题层。"""
    import app.services.mapspec.store as store_module

    store_module.BASE_STORAGE_DIR = WORK / "mapspec_store"
    store_module.BASE_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    values = [0, 3, 7, 12, 18, 25, 33, 47, 60, 88]
    features = []
    for i, v in enumerate(values):
        base_lon = 116.0 + i * 0.05
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[
                [base_lon, 39.0], [base_lon + 0.04, 39.0],
                [base_lon + 0.04, 39.04], [base_lon, 39.04], [base_lon, 39.0],
            ]]},
            "properties": {"pop": float(v)},
        })
    fc = {"type": "FeatureCollection", "features": features}

    from app.services.session_data import session_data_manager

    ref = await session_data_manager.store(
        session_id, {"kind": "geojson", "geojson": fc}, prefix="result")

    from app.lib.cartography.thematic_spec import build_graduated_spec, spec_to_paint
    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.intents import UpsertLayerIntent

    legend = build_graduated_spec(fc, field="pop")
    paint, _warn = spec_to_paint(legend)
    result = await apply_gis_mutation(
        session_id,
        UpsertLayerIntent(layer={
            "id": LAYER_ID, "type": "fill", "source": f"{LAYER_ID}-source",
            "paint": {"color": paint}, "legend_spec": legend,
            "data_binding": {"field": "pop", "field_type": "number"},
        }, source_data={"ref_id": str(ref)}),  # type: ignore[arg-type]
        origin="agent", actor="journey:seed",
    )
    assert not result.is_error, result.error_msg
    from app.services.session_data import session_data_manager as sdm

    return int(await sdm.get_state_field(session_id, "_cartographic_mutation_revision") or 0)


def _read_layer(session_id: str):
    from app.services.mapspec_store import mapspec_store

    spec = mapspec_store.get_mapspec_sync(session_id) if hasattr(
        mapspec_store, "get_mapspec_sync") else None
    if spec is None:
        import asyncio

        spec = asyncio.run(mapspec_store.get_mapspec(session_id)) or {}
    for layer in spec.get("layers") or []:
        if layer.get("id") == LAYER_ID:
            return layer
    return None


async def _read_layer_async(session_id: str):
    from app.services.mapspec_store import mapspec_store

    spec = await mapspec_store.get_mapspec(session_id) or {}
    for layer in spec.get("layers") or []:
        if layer.get("id") == LAYER_ID:
            return layer
    return None


def main() -> int:
    # ── 先 seed + 写真实 turn 脚本，再启动 stub（stub 在模块加载时一次性
    #    读脚本——G26 同款契约；占位文件会让全部旅程退化为 "ok" 文本）。──
    session_id = RUN + "-s1"
    rev_seed = asyncio.run(_seed_session(session_id))
    turn_scripts = {
        "查看这个专题层": [
            {"tool": "tool_search", "args": {"query": "专题 分级 断点 图例 表达"}},
            {"tool": "webgis_thematic_style_inspect",
             "args": {"layer_id": LAYER_ID}},
            {"text": "当前分级已查看。"},
        ],
        "改成 0 10 20 50 四段": [
            {"tool": "webgis_thematic_style_apply", "args": {
                "layer_id": LAYER_ID,
                "plan": {"mode": "graduated_manual",
                         "breaks": [0, 10, 20, 50],
                         "palette": "Blues"},
                "expected_revision": rev_seed,
            }},
            {"text": "已改为人工四段。"},
        ],
        "检查图例": [
            {"tool": "webgis_thematic_style_inspect",
             "args": {"layer_id": LAYER_ID}},
            {"text": "图例已核对。"},
        ],
        "恢复自动分级": [
            {"tool": "webgis_thematic_style_apply", "args": {
                "layer_id": LAYER_ID,
                "plan": {"mode": "revert_auto"},
                "expected_revision": rev_seed + 1,
            }},
            {"text": "已恢复自动。"},
        ],
    }

    stub_js = WORK / "journey_stub.mjs"
    stub_js.write_text(STUB_JS, encoding="utf-8")
    (WORK / "journey_script.json").write_text(
        json.dumps(turn_scripts), encoding="utf-8")

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
    os.environ["LLM_API_KEY"] = "stub-g33-key"
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

    def chat(message: str) -> str:
        nonlocal owner_token
        headers = {"Content-Type": "application/json"}
        if owner_token:
            headers["X-Session-Token"] = owner_token  # SEC-08 真实前端契约
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
        verdict["seeded_revision"] = rev_seed

        body1 = chat("查看这个专题层")
        (WORK / "sse1.log").write_text(body1, encoding="utf-8")
        verdict["journeys"]["inspect_receipt"] = (
            "webgis_thematic_style_inspect" in body1)

        body2 = chat("改成 0 10 20 50 四段")
        (WORK / "sse2.log").write_text(body2, encoding="utf-8")

        layer_manual = asyncio.run(_read_layer_async(session_id))
        if layer_manual is None:
            verdict["fail"].append("layer missing after manual apply")
        else:
            breaks = layer_manual.get("legend_spec", {}).get("breaks")
            verdict["journeys"]["manual_breaks_committed"] = (
                breaks == [0.0, 10.0, 20.0, 50.0])
            if not verdict["journeys"]["manual_breaks_committed"]:
                verdict["fail"].append(f"breaks not manual: {breaks!r}")
            ev = layer_manual.get("thematic_authoring") or {}
            verdict["journeys"]["evidence_agent"] = (
                ev.get("decided_by") == "agent"
                and ev.get("mode") == "graduated_manual")
            from app.services.thematic_authoring.integrity import (
                assert_expression_consistent,
            )
            problems = assert_expression_consistent(layer_manual)
            verdict["journeys"]["integrity_clean"] = not problems
            if problems:
                verdict["fail"].append(f"integrity: {problems}")

        body3 = chat("检查图例")
        (WORK / "sse3.log").write_text(body3, encoding="utf-8")
        layer_check = asyncio.run(_read_layer_async(session_id))
        if layer_check is not None:
            spec = layer_check.get("legend_spec") or {}
            verdict["journeys"]["legend_reads_manual"] = (
                spec.get("breaks") == [0.0, 10.0, 20.0, 50.0]
                and len(spec.get("labels") or []) == 3)

        body4 = chat("恢复自动分级")
        (WORK / "sse4.log").write_text(body4, encoding="utf-8")
        layer_revert = asyncio.run(_read_layer_async(session_id))
        if layer_revert is not None:
            spec = layer_revert.get("legend_spec") or {}
            verdict["journeys"]["reverted_to_auto"] = (
                spec.get("method") != "manual"
                and spec.get("breaks") != [0.0, 10.0, 20.0, 50.0])
            ev = layer_revert.get("thematic_authoring") or {}
            verdict["journeys"]["revert_evidence"] = ev.get("reverted") is True

        for key, ok in verdict["journeys"].items():
            if key in ("inspect_receipt", "manual_breaks_committed", "evidence_agent",
                       "integrity_clean", "legend_reads_manual", "reverted_to_auto",
                       "revert_evidence") and not ok:
                verdict["fail"].append(f"journey gate failed: {key}")
    except Exception as exc:  # noqa: BLE001 — verdict 面必须完整
        verdict["fail"].append(f"driver exception: {type(exc).__name__}: {exc}")
    finally:
        stub.terminate()
        server.should_exit = True  # 防 uvicorn 线程拖住进程（泄漏教训）

    print(json.dumps(verdict))
    ok = not verdict["fail"] and all(verdict["journeys"].values())
    (WORK / "verdict.json").write_text(json.dumps(verdict), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


@pytest.mark.timeout(900)
def test_g33_thematic_authoring_journey(tmp_path: Path):
    driver = tmp_path / "g33_driver.py"
    driver.write_text(_DRIVER_SOURCE, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    result = subprocess.run(
        [sys.executable, str(driver), str(REPO_ROOT)],
        capture_output=True, text=True, timeout=900, env=env, cwd=str(REPO_ROOT),
    )
    tail = (result.stdout or "").strip().splitlines()
    verdict = {}
    for line in reversed(tail):
        if line.startswith("{"):
            try:
                verdict = json.loads(line)
                break
            except ValueError:
                continue
    assert result.returncode == 0, (
        f"journey failed\nverdict={verdict}\nstderr tail:\n"
        f"{(result.stderr or '')[-3000:]}"
    )
    journeys = verdict.get("journeys", {})
    for gate in (
        "inspect_receipt", "manual_breaks_committed", "evidence_agent",
        "integrity_clean", "legend_reads_manual", "reverted_to_auto",
        "revert_evidence",
    ):
        assert journeys.get(gate) is True, f"gate {gate}: {verdict}"
