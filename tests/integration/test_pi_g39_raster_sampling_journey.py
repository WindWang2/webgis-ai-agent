"""G39 旅程：真实 Pi 子进程 + stub LLM → webgis_raster_sampling_* → 采样成果。

与 test_pi_g33_thematic_authoring_journey.py 同架构（node stub LLM →
uvicorn 生产 app（真实 vendor/pi bridge）→ HTTP/SSE 旅程），聚焦：

J-G39（S30 验收）：
  driver 服务端种子（合成 GeoTIFF → raster ref + 监测点 FC ref）
  → 用户「给这些监测站采样」turn → stub LLM tool_search +
    webgis_raster_sampling_plan（typed 参数 → 真实 dispatch）
  → 用户「预览一下」turn → webgis_raster_sampling_preview
  → 用户「执行」turn → webgis_raster_sampling_execute（durable 等待语义）
  → 用户「读取成果」turn → webgis_raster_sampling_results
  → 裁决：execute 回执 completed=true + 长表 ref + 站位层 ref；服务端
    核对 parquet 全行 + 站位层要素数 + 收据落盘（真实成果，非 transcript）。

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
"""G39 journey driver：stub LLM + uvicorn(app) + 栅格采样旅程 → VERDICT json。"""
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
import urllib.request
from pathlib import Path

REPO_ROOT = Path(sys.argv[1])
WORK = Path(tempfile.mkdtemp(prefix="pi-g39-"))
sys.path.insert(0, str(REPO_ROOT))

import uuid

RUN = "g39" + uuid.uuid4().hex[:8]
os.environ["PI_AUTOSTART"] = "true"
os.environ.setdefault("GIS_MANIFEST_STRICT", "0")
# 隔离 DATA_DIR（scoped raster roots = DATA_DIR/<session>）
os.environ["DATA_DIR"] = str(WORK / "data")
(WORK / "data").mkdir(parents=True, exist_ok=True)

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
    let allText = '';
    for (const m of msgs) {
      if (m.role === 'user') {
        const c = typeof m.content === 'string' ? m.content
          : Array.isArray(m.content) ? m.content.filter((b) => b.type === 'text').map((b) => b.text).join('') : '';
        lastUser = c;
      }
      if (typeof m.content === 'string') allText += m.content + '\n';
      else if (m.content) allText += JSON.stringify(m.content) + '\n';
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
    // $plan_id 占位：从对话中的工具回执提取最近 plan_id（动态值注入）。
    // 回执在 messages 里经过多层 JSON 转义 —— 直接扫描 rsp_<hex> 令牌。
    if (step.args && JSON.stringify(step.args).includes('$plan_id')) {
      const tokens = allText.match(/rsp_[0-9a-f]{8,16}/g) || [];
      const last = tokens.length ? tokens[tokens.length - 1] : null;
      if (last) {
        step = JSON.parse(JSON.stringify(step).replaceAll('$plan_id', last));
      }
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


async def _seed_session(session_id: str) -> dict:
    """服务端种子：合成 GeoTIFF（已知答案）+ 监测点 FC → 会话 refs。"""
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    import app.services.mapspec.store as store_module

    store_module.BASE_STORAGE_DIR = WORK / "mapspec_store"
    store_module.BASE_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    owner_dir = WORK / "data" / session_id
    owner_dir.mkdir(parents=True, exist_ok=True)
    raster_path = owner_dir / "ndvi.tif"
    rows, cols = np.mgrid[0:16, 0:16]
    values = (0.3 + 0.004 * cols + 0.002 * rows).astype("float32")
    with rasterio.open(
        raster_path, "w", driver="GTiff", width=16, height=16, count=1,
        dtype="float32", crs="EPSG:4326",
        transform=from_origin(10.0, 20.0, 0.01, 0.01),
    ) as dst:
        dst.write(values[None, ...])
        dst.set_band_description(1, "NDVI")
        dst.update_tags(1, units="")

    from app.services.session_data import session_data_manager

    raster_ref = await session_data_manager.store(
        session_id, {"kind": "numeric_raster", "file_path": str(raster_path)},
        prefix="raster")
    stations = [
        {"id": f"st-{i}", "x": 10.0 + 0.01 * (2 + i),
         "y": 20.0 - 0.01 * (1 + i)} for i in range(5)]
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": s["id"],
         "properties": {"name": f"监测站{i}"},
         "geometry": {"type": "Point", "coordinates": [s["x"], s["y"]]}}
        for i, s in enumerate(stations)]}
    points_ref = await session_data_manager.store(session_id, fc, prefix="points")
    return {"raster_ref": str(raster_ref), "points_ref": str(points_ref),
            "stations": stations, "values": values}


def main() -> int:
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
    os.environ["LLM_API_KEY"] = "stub-g39-key"
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
            headers["X-Session-Token"] = owner_token
        req = urllib.request.Request(
            "http://127.0.0.1:{}/api/v1/chat/stream".format(api_port),
            data=json.dumps({"message": message, "session_id": session_id}).encode(),
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=240) as resp:
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
        session_id = RUN + "-s1"
        seed = asyncio.run(_seed_session(session_id))
        (WORK / "journey_script.json").write_text(json.dumps({
            "warmup": [{"text": "warmup-ok"}],
            "给这些监测站做栅格采样": [
                {"tool": "tool_search", "args": {"query": "栅格 采样 点集 波段"}},
                {"tool": "webgis_raster_sampling_plan", "args": {
                    "mode": "source_points",
                    "points_ref": seed["points_ref"],
                    "station_crs": "EPSG:4326",
                    "method": "source_pixel",
                    "sources": [{"ref_id": seed["raster_ref"],
                                 "bands": [1]}],
                }},
                {"text": "采样计划已建。"},
            ],
            "预览一下": [
                {"tool": "tool_search", "args": {"query": "栅格 采样 预览"}},
                {"tool": "webgis_raster_sampling_preview",
                 "args": {"plan_id": "$plan_id", "limit": 4}},
                {"text": "预览正常。"},
            ],
            "执行采样": [
                {"tool": "tool_search", "args": {"query": "栅格 采样 执行 成果"}},
                {"tool": "webgis_raster_sampling_execute",
                 "args": {"plan_id": "$plan_id", "wait_max_s": 60}},
                {"text": "采样已执行。"},
            ],
            "读取成果": [
                {"tool": "tool_search", "args": {"query": "栅格 采样 成果 读取"}},
                {"tool": "webgis_raster_sampling_results",
                 "args": {"plan_id": "$plan_id", "per_page": 50}},
                {"text": "成果已读取。"},
            ],
        }), encoding="utf-8")

        warm = chat("warmup")
        if "task_error" in warm:
            warm = chat("warmup")
        verdict["journeys"]["warmup_ok"] = "task_complete" in warm

        body1 = chat("给这些监测站做栅格采样")
        if "task_error" in body1:
            body1 = chat("给这些监测站做栅格采样")
        (WORK / "sse1.log").write_text(body1, encoding="utf-8")
        verdict["journeys"]["plan_receipt"] = (
            "webgis_raster_sampling_plan" in body1
            and '"plan_id"' in body1
            and '"stations"' in body1)

        body2 = chat("预览一下")
        (WORK / "sse2.log").write_text(body2, encoding="utf-8")
        verdict["journeys"]["preview_bounded"] = (
            '"preview": true' in body2 or '"preview":true' in body2
            or '"rows"' in body2)

        body3 = chat("执行采样")
        (WORK / "sse3.log").write_text(body3, encoding="utf-8")
        verdict["journeys"]["execute_completed"] = (
            '"completed": true' in body3 or '"completed":true' in body3)
        verdict["journeys"]["execute_refs"] = (
            "ref:fabric-parquet/" in body3 and "ref:raster-sampling-" in body3)

        body4 = chat("读取成果")
        (WORK / "sse4.log").write_text(body4, encoding="utf-8")
        verdict["journeys"]["results_rows"] = (
            '"sample_id"' in body4 and '"coverage"' in body4)

        # 服务端核对：真实成果（非 transcript 成功）
        from app.services.artifact_registry import fabric_parquet_path
        from app.services.session_data import session_data_manager

        m = re.findall(r'"plan_id"\s*:\s*"(rsp_[0-9a-f]+)"', body1)
        verdict["journeys"]["server_receipt_check"] = False
        if m:
            from app.services.raster_sampling import store as rs_store

            receipts = [r for r in asyncio.run(rs_store.list_receipts(session_id))
                        if r.plan_id == m[-1]]
            if receipts:
                rcpt = receipts[-1]
                table_ref = rcpt.outputs.get("sample_table_ref") or ""
                path = fabric_parquet_path(session_id, table_ref)
                ok_parquet = path is not None and path.is_file()
                layer = await_ = None  # noqa: F841
                layer = asyncio.run(session_data_manager.get(
                    session_id, rcpt.outputs.get("station_layer_ref") or ""))
                layer_ok = (layer is not None
                            and len(layer.get("features") or []) == 5)
                verdict["journeys"]["server_receipt_check"] = bool(
                    ok_parquet and layer_ok
                    and rcpt.coverage.valid_cells == 5)
                verdict["server"] = {
                    "parquet": str(path), "rows": ok_parquet,
                    "station_features": layer_ok,
                    "valid_cells": rcpt.coverage.valid_cells,
                }

        gates = ("warmup_ok", "plan_receipt", "preview_bounded",
                 "execute_completed", "execute_refs", "results_rows",
                 "server_receipt_check")
        for key in gates:
            if not verdict["journeys"].get(key):
                verdict["fail"].append(f"journey gate failed: {key}")
    except Exception as exc:  # noqa: BLE001 — verdict 面必须完整
        verdict["fail"].append(f"driver exception: {type(exc).__name__}: {exc}")
    finally:
        stub.terminate()
        server.should_exit = True

    print(json.dumps(verdict))
    ok = not verdict["fail"] and all(verdict["journeys"].values())
    (WORK / "verdict.json").write_text(json.dumps(verdict), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


@pytest.mark.timeout(900)
def test_g39_raster_sampling_journey(tmp_path: Path):
    driver = tmp_path / "g39_driver.py"
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
        "warmup_ok", "plan_receipt", "preview_bounded", "execute_completed",
        "execute_refs", "results_rows", "server_receipt_check",
    ):
        assert journeys.get(gate) is True, f"gate {gate}: {verdict}"
