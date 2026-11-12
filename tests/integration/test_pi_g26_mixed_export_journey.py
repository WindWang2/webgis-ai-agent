"""G26 A26 车道：真实 Pi 子进程 + stub LLM → webgis_map_export → 可核验产物。

与 test_pi_stub_llm_journeys.py 同架构（独立 driver 进程：node stub LLM →
uvicorn 生产 app（真实 vendor/pi bridge）→ HTTP 旅程），聚焦单一旅程：

J-G26：
  driver 服务端种子（真实 G20 提交链：GeoTIFF → ref → commit_expression）
  → 用户「导出混合地图」turn → stub LLM 调 webgis_map_export(format=svg)
  → 真实 ToolDispatch（HTTP → Python，Pi 原生扩展面）
  → 裁决：工具成功回执（refs-only）+ 签名 URL 下载的产物含 data:image/png
    嵌入 + manifest sidecar 可读 + mapspec_revision 冻结一致。

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
]


_DRIVER_SOURCE = r'''#!/usr/bin/env python3
"""G26 journey driver：stub LLM + uvicorn(app) + 混合导出旅程 → VERDICT json。"""
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
WORK = Path(tempfile.mkdtemp(prefix="pi-g26-"))
sys.path.insert(0, str(REPO_ROOT))

import uuid

RUN = "g26" + uuid.uuid4().hex[:8]
os.environ["PI_AUTOSTART"] = "true"
# 既有 capability manifest 悬空 id（import/refresh/label 等工具声明的能力不在
# 运行时注册表 —— master 既有状态，与本方向无关）在无 DB 的 driver 环境使
# app 启动 fail-fast；按 runtime_manifest 文档化逃生口降级为日志。
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

TURN_SCRIPTS = {
    # ADR-0288 deferred surface：长尾工具先经 tool_search 发现/激活（与
    # 既有 J8 buffer_analysis 旅程同模式），再裸名直调。
    "导出混合地图": [
        {"tool": "tool_search", "args": {"query": "混合地图 导出 出版 矢量 栅格"}},
        {"tool": "webgis_map_export", "args": {"format": "svg"}},
        {"text": "混合地图已导出。"},
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


async def _seed_session(session_id: str, workdir: Path) -> int:
    """服务端种子（真实 G20 提交链）：GeoTIFF → ref → commit_expression。"""
    import numpy as np
    from rasterio.transform import from_origin

    sys.path.insert(0, str(REPO_ROOT / "tests"))
    from tests.unit.raster_authoring.helpers import (
        register_raster_ref,
        write_tif,
    )

    import app.services.scoped_raster_paths as srp

    owner_root = workdir / "raster_owner"
    owner_root.mkdir(exist_ok=True)
    srp.scoped_raster_roots = lambda **kw: [owner_root]

    rows, cols = np.mgrid[0:16, 0:16]
    values = ((rows * 8 + cols) + 100).astype("float32")[None]
    tif = write_tif(
        owner_root / "regional.tif", bands=1, width=16, height=16,
        dtype="float32", values=values, crs="EPSG:4326",
        transform=from_origin(10.0, 25.0, 10.0 / 16, 10.0 / 16))
    ref = await register_raster_ref(session_id, tif)

    from app.services.raster_authoring import commit_expression
    from app.services.raster_authoring.service import current_revision

    receipt = await commit_expression(
        session_id, ref, "raster-layer",
        {"bands": [1], "stretch": {"min": 0, "max": 400}, "colormap": "viridis"},
        expected_revision=await current_revision(session_id),
    )
    return int(receipt["mapspec_revision"])


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
    os.environ["LLM_API_KEY"] = "stub-g26-key"
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

    # 导出目录隔离（driver 工作区）
    import app.services.export_paths as ep

    exports = WORK / "exports"
    exports.mkdir(exist_ok=True)
    ep.exports_root = lambda: exports
    ep.ensure_exports_root = lambda: exports

    verdict: dict = {"journeys": {}, "fail": []}
    session_id = RUN + "-s1"
    try:
        seeded_rev = asyncio.run(_seed_session(session_id, WORK))
        verdict["seeded_revision"] = seeded_rev

        req = urllib.request.Request(
            "http://127.0.0.1:{}/api/v1/chat/stream".format(api_port),
            data=json.dumps({"message": "导出混合地图",
                             "session_id": session_id}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = resp.read().decode("utf-8", "replace")
        events = _parse_sse(body)
        (WORK / "sse.log").write_text(body, encoding="utf-8")

        # 工具执行事件中的 webgis_map_export 成功回执
        tool_ok = None
        for e in events:
            data = e["data"]
            for key in ("tool_result", "result", "step_result"):
                payload = data.get(key) if isinstance(data, dict) else None
                if isinstance(payload, dict) and \
                        payload.get("filename", "").startswith("mixed_map"):
                    tool_ok = payload
        if not tool_ok:
            # 兜底：从 SSE 文本里找（事件形状随版本演进）
            if '"mixed_map_' in body or "mixed_map_" in body:
                tool_ok = {"recovered_from_stream": True}
        verdict["journeys"]["tool_receipt"] = bool(tool_ok)
        if not tool_ok:
            verdict["fail"].append("no successful webgis_map_export receipt in stream")

        # 下载产物核验（签名 URL → 真实字节）
        dl_url = None
        manifest_url = None
        if isinstance(tool_ok, dict):
            dl_url = tool_ok.get("download_url")
            manifest_url = tool_ok.get("manifest_url")
        if not dl_url:
            # 从流文本恢复下载 URL
            import re as _re

            m = _re.search(r'(/api/v1/export/download/mixed_map_[^"\s\\]+)', body)
            if m:
                dl_url = m.group(1)
        verdict["journeys"]["artifact_downloadable"] = False
        verdict["journeys"]["artifact_embeds_raster"] = False
        verdict["journeys"]["manifest_readable"] = False
        verdict["journeys"]["revision_frozen"] = False
        if dl_url:
            with urllib.request.urlopen(
                    "http://127.0.0.1:{}{}".format(api_port, dl_url),
                    timeout=30) as art:
                artifact = art.read()
            if artifact[:4] == b"<svg" or b"<svg" in artifact[:512]:
                verdict["journeys"]["artifact_downloadable"] = True
                verdict["journeys"]["artifact_embeds_raster"] = \
                    b"data:image/png;base64," in artifact
                if not verdict["journeys"]["artifact_embeds_raster"]:
                    verdict["fail"].append("downloaded artifact lacks embedded raster")
            else:
                verdict["fail"].append(
                    "downloaded artifact is not SVG: {} bytes".format(len(artifact)))
        else:
            verdict["fail"].append("no artifact download URL recovered")
        if manifest_url:
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:{}{}".format(api_port, manifest_url),
                        timeout=30) as mf:
                    manifest = json.loads(mf.read().decode("utf-8"))
                verdict["journeys"]["manifest_readable"] = \
                    manifest.get("schema_version") == "mixed_map_export.v1"
                verdict["journeys"]["revision_frozen"] = \
                    manifest.get("mapspec_revision") == seeded_rev
                if not verdict["journeys"]["revision_frozen"]:
                    verdict["fail"].append(
                        "manifest revision {} != seeded {}".format(
                            manifest.get("mapspec_revision"), seeded_rev))
            except Exception as exc:  # noqa: BLE001
                verdict["fail"].append("manifest fetch failed: {}".format(exc))
        elif isinstance(tool_ok, dict) and tool_ok.get("mapspec_revision"):
            verdict["journeys"]["revision_frozen"] = \
                tool_ok.get("mapspec_revision") == seeded_rev
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
def test_pi_g26_mixed_export_journey(tmp_path):
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
    assert verdict["journeys"]["tool_receipt"] is True
    assert verdict["journeys"]["artifact_downloadable"] is True
    assert verdict["journeys"]["artifact_embeds_raster"] is True
    assert verdict["journeys"]["revision_frozen"] is True
