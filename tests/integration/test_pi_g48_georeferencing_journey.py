"""G48 旅程：真实 Pi 子进程 + stub LLM → webgis_georef_* → 权威文件/GeoTIFF/MapSpec/Tile 回读。

覆盖需求：
- R35: Pi deferred 工具发现 (`tool_search`) → inspect → plan (4+ GCP, WGS84 经纬度转投影 CRS) → preview → execute (GeoTIFF/COG) → status → report → bind_layer；验证有界回执与真实落盘 GeoTIFF/COG + 瓦片可读；
- R36: <3 控制点或退化构型诚实拒绝，不编造 RMS、不产出伪 GeoTIFF。

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
"""G48 journey driver：stub LLM + uvicorn(app) + 控制点配准旅程 → VERDICT json。"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = sys.argv[1]
WORK = Path(tempfile.mkdtemp(prefix="g48-journey-"))


def _make_rgba_fixture() -> bytes:
    import numpy as np
    from PIL import Image

    arr = np.zeros((128, 128, 4), dtype=np.uint8)
    arr[0:64, 0:64] = [240, 20, 20, 255]     # Q1 red
    arr[0:64, 64:128] = [0, 0, 0, 255]       # Q2 opaque pure black
    arr[64:128, 0:64] = [250, 240, 20, 255]  # Q3 yellow
    arr[64:128, 64:128] = [0, 0, 0, 0]       # Q4 transparent
    buf = io.BytesIO()
    Image.fromarray(arr, mode="RGBA").save(buf, format="PNG")
    return buf.getvalue()


def _world_tile_for(x: float, y: float, z: int) -> tuple[int, int]:
    origin = 20037508.342789244
    span = (2.0 * origin) / (1 << z)
    tx = int(math.floor((x + origin) / span))
    ty = int(math.floor((origin - y) / span))
    return tx, ty


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


def _replace_placeholder(scripts: dict, key: str, value: str) -> None:
    for steps in scripts.values():
        for step in steps:
            args = step.get("args")
            if isinstance(args, dict):
                for ak, av in list(args.items()):
                    if av == key:
                        args[ak] = value


def main() -> int:
    sys.path.insert(0, REPO)
    os.environ["DATABASE_URL"] = "sqlite:///" + str(WORK / "journey.db")
    os.environ["DATA_DIR"] = str(WORK / "data")
    Path(WORK / "data").mkdir(parents=True, exist_ok=True)

    stub_js = WORK / "journey_stub.mjs"
    stub_js.write_text(STUB_JS, encoding="utf-8")
    (WORK / "journey_script.json").write_text(json.dumps({"warmup": [{"text": "warmup-ok"}]}), encoding="utf-8")

    with open(WORK / "stub_err.log", "w") as fh:
        stub = subprocess.Popen(
            ["node", str(stub_js)],
            stdout=subprocess.DEVNULL,
            stderr=fh,
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

    os.environ["LLM_BASE_URL"] = f"http://127.0.0.1:{stub_port}/v1"
    os.environ["LLM_API_KEY"] = "stub-g48-key"
    os.environ["LLM_MODEL"] = "stub-model"
    os.environ["PI_AUTOSTART"] = "true"

    api_port = _free_port()
    os.environ["WEBGIS_API_BASE"] = f"http://127.0.0.1:{api_port}"

    import uvicorn

    server = uvicorn.Server(uvicorn.Config("app.main:app", host="127.0.0.1", port=api_port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    from app.services.chat.engine_instance import try_get_chat_engine

    deadline = time.monotonic() + 90
    while time.monotonic() < deadline and not (server.started and try_get_chat_engine() is not None):
        time.sleep(0.5)
    if not (server.started and try_get_chat_engine() is not None):
        stub.terminate()
        print(json.dumps({"fail": ["app did not start"]}))
        return 1

    from pyproj import Transformer
    import rasterio
    import httpx
    from app.core.database import SessionLocal
    from app.models.db_model import User
    from app.core.auth import create_access_token
    from app.services.georeferencing.store import get_plan, list_plans

    with SessionLocal() as db:
        if db.get(User, "g48-journey-user") is None:
            db.add(User(
                id="g48-journey-user",
                username="g48journey",
                email="g48journey@example.com",
                role="editor",
                is_active=True,
            ))
            db.commit()
    jwt = create_access_token({"sub": "g48-journey-user", "role": "user"})

    verdict: dict = {"journeys": {}, "fail": []}
    owner_token = None
    session_id = "g48journey-s1"

    def chat(message: str) -> str:
        nonlocal owner_token
        headers = {"Content-Type": "application/json", "Authorization": "Bearer " + jwt}
        if owner_token:
            headers["X-Session-Token"] = owner_token
        req = urllib.request.Request(
            f"http://127.0.0.1:{api_port}/api/v1/chat/stream",
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
        # 1) Warmup & verify native Pi SSE completion
        warm = chat("warmup")
        if "task_error" in warm:
            warm = chat("warmup")
        verdict["journeys"]["warmup_ok"] = "task_complete" in warm

        # 2) Upload synthetic 128x128 RGBA image via real HTTP endpoint
        png_bytes = _make_rgba_fixture()
        auth_headers = {"Authorization": "Bearer " + jwt, "X-Session-Token": owner_token or ""}
        with httpx.Client(base_url=f"http://127.0.0.1:{api_port}", timeout=60.0) as client:
            up_resp = client.post(
                f"/api/v1/georeferencing/sources/upload?session_id={session_id}",
                headers=auth_headers,
                files={"file": ("scan_128.png", png_bytes, "image/png")},
            )
            assert up_resp.status_code == 200, up_resp.text
            source_id = up_resp.json()["source"]["source_id"]

        # Known 2D affine target in EPSG:3857 expressed in WGS84 lon/lat
        x0, y0, scale_m = 13370000.0, 3530000.0, 20.0
        to_4326 = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
        corners = [
            ("P1", 0.0, 0.0, x0, y0),
            ("P2", 128.0, 0.0, x0 + 128.0 * scale_m, y0),
            ("P3", 128.0, 128.0, x0 + 128.0 * scale_m, y0 - 128.0 * scale_m),
            ("P4", 0.0, 128.0, x0, y0 - 128.0 * scale_m),
        ]
        gcps_4 = []
        for lbl, px, py, mx, my in corners:
            lon, lat = to_4326.transform(mx, my)
            gcps_4.append({
                "label": lbl,
                "px": px,
                "py": py,
                "wgs84_lon": lon,
                "wgs84_lat": lat,
                "role": "fit",
                "enabled": True,
            })

        scripts = {
            "warmup": [{"text": "warmup-ok"}],
            "检查已上传的配准影像源": [
                {"tool": "tool_search", "args": {"query": "webgis_georef_inspect"}},
                {"tool": "webgis_georef_inspect", "args": {"source_id": source_id}},
                {"text": "影像源检查完成。"},
            ],
            "用2个控制点创建计划": [
                {"tool": "tool_search", "args": {"query": "webgis_georef_plan"}},
                {"tool": "webgis_georef_plan", "args": {
                    "action": "create",
                    "source_id": source_id,
                    "name": "Bad 2-GCP Plan",
                    "target_crs": "EPSG:3857",
                    "resampling": "bilinear",
                    "generate_cog": True,
                    "gcps": gcps_4[:2],
                }},
                {"text": "仅收到2个控制点，已标记为控制点不足。"},
            ],
            "尝试执行2控制点计划": [
                {"tool": "tool_search", "args": {"query": "webgis_georef_preview webgis_georef_execute"}},
                {"tool": "webgis_georef_preview", "args": {"plan_id": "BAD_PLAN_ID", "expected_revision": 2}},
                {"tool": "webgis_georef_execute", "args": {
                    "plan_id": "BAD_PLAN_ID",
                    "action": "execute",
                    "expected_revision": 2,
                    "background": False,
                }},
                {"text": "因不足3个有效控制点已拒绝配准执行。"},
            ],
            "创建4控制点配准计划": [
                {"tool": "tool_search", "args": {"query": "webgis_georef_plan"}},
                {"tool": "webgis_georef_plan", "args": {
                    "action": "create",
                    "source_id": source_id,
                    "name": "G48 Pi Valid",
                    "target_crs": "EPSG:3857",
                    "resampling": "bilinear",
                    "target_resolution_m": 20.0,
                    "generate_cog": True,
                    "gcps": gcps_4,
                }},
                {"text": "4点仿射配准计划已保存。"},
            ],
            "预览执行配准并绑定图层": [
                {"tool": "tool_search", "args": {"query": "webgis_georef_preview webgis_georef_execute"}},
                {"tool": "tool_search", "args": {"query": "webgis_georef_status webgis_georef_report"}},
                {"tool": "webgis_georef_preview", "args": {"plan_id": "GOOD_PLAN_ID", "expected_revision": 2}},
                {"tool": "webgis_georef_execute", "args": {
                    "plan_id": "GOOD_PLAN_ID",
                    "action": "execute",
                    "expected_revision": 2,
                    "background": False,
                    "idempotency_key": "pi-g48-exec-1",
                }},
                {"tool": "webgis_georef_status", "args": {"plan_id": "GOOD_PLAN_ID"}},
                {"tool": "webgis_georef_report", "args": {"plan_id": "GOOD_PLAN_ID"}},
                {"tool": "webgis_georef_execute", "args": {
                    "plan_id": "GOOD_PLAN_ID",
                    "action": "bind_layer",
                    "expected_mapspec_revision": 0,
                    "layer_id": "pi-georef-layer",
                    "opacity": 0.9,
                }},
                {"text": "配准 GeoTIFF 与 MapSpec 图层已交付。"},
            ],
        }
        (WORK / "journey_script.json").write_text(json.dumps(scripts), encoding="utf-8")

        # Turn 1: inspect
        b1 = chat("检查已上传的配准影像源")
        (WORK / "sse1.log").write_text(b1, encoding="utf-8")
        verdict["journeys"]["inspect_receipt"] = (
            "webgis_georef_inspect" in b1 and source_id in b1 and '"width":128' in b1.replace(" ", "")
        )

        # Turn 2: <3 GCP plan
        b2 = chat("用2个控制点创建计划")
        (WORK / "sse2.log").write_text(b2, encoding="utf-8")
        bad_pl = next((p for p in list_plans(session_id) if p.name == "Bad 2-GCP Plan"), None)
        verdict["journeys"]["bad_plan_insufficient"] = (
            bad_pl is not None
            and bad_pl.fit_metrics is not None
            and bad_pl.fit_metrics.status == "insufficient"
        )
        if bad_pl:
            _replace_placeholder(scripts, "BAD_PLAN_ID", bad_pl.plan_id)
            (WORK / "journey_script.json").write_text(json.dumps(scripts), encoding="utf-8")

        # Turn 2b: refuse preview + execute on <3 GCP (R36)
        b2b = chat("尝试执行2控制点计划")
        (WORK / "sse2b.log").write_text(b2b, encoding="utf-8")
        bad_pl_after = get_plan(session_id, bad_pl.plan_id) if bad_pl else None
        flat2b = b2b.replace(" ", "")
        verdict["journeys"]["r36_honest_refusal"] = (
            '"preview_available":false' in flat2b
            and "At least 3 enabled fit GCPs are required" in b2b
            and "resolve blockers before execution" in b2b
            and bad_pl_after is not None
            and bad_pl_after.latest_artifact is None
        )

        # Turn 3: 4-GCP plan
        b3 = chat("创建4控制点配准计划")
        (WORK / "sse3.log").write_text(b3, encoding="utf-8")
        good_pl = next((p for p in list_plans(session_id) if p.name == "G48 Pi Valid"), None)
        verdict["journeys"]["good_plan_valid"] = (
            good_pl is not None
            and good_pl.fit_metrics is not None
            and good_pl.fit_metrics.status == "valid"
            and good_pl.fit_metrics.fit_rmse is not None
            and good_pl.fit_metrics.fit_rmse < 1e-2
        )
        if good_pl:
            _replace_placeholder(scripts, "GOOD_PLAN_ID", good_pl.plan_id)
            (WORK / "journey_script.json").write_text(json.dumps(scripts), encoding="utf-8")

        # Turn 4: preview -> execute -> status -> report -> bind_layer
        b4 = chat("预览执行配准并绑定图层")
        (WORK / "sse4.log").write_text(b4, encoding="utf-8")
        flat4 = b4.replace(" ", "")
        verdict["journeys"]["bounded_receipt"] = (
            len(b4) < 80_000
            and '"preview_available":true' in flat4
            and '"cog_verified":true' in flat4
            and '"bound":true' in flat4
            and '"residuals"' in flat4
        )

        # Independent oracle checks on disk + HTTP download + tile render
        final_pl = get_plan(session_id, good_pl.plan_id) if good_pl else None
        art = final_pl.latest_artifact if final_pl else None
        geotiff_ok = False
        download_ok = False
        tile_ok = False
        if art is not None and Path(art.file_path).is_file():
            with rasterio.open(art.file_path) as ds:
                arr = ds.read()
                geotiff_ok = (
                    ds.crs.to_string() == "EPSG:3857"
                    and ds.count == 4
                    and ds.dtypes == ("uint8", "uint8", "uint8", "uint8")
                    and abs(ds.bounds.left - x0) < scale_m * 2.0
                    and abs(ds.bounds.top - y0) < scale_m * 2.0
                    and int(((arr[0] < 15) & (arr[1] < 15) & (arr[2] < 15) & (arr[3] == 255)).sum()) >= 100
                )
            with httpx.Client(base_url=f"http://127.0.0.1:{api_port}", timeout=60.0) as client:
                dl = client.get(
                    f"/api/v1/georeferencing/artifacts/{art.artifact_id}/download?session_id={session_id}",
                    headers=auth_headers,
                )
                download_ok = (
                    dl.status_code == 200
                    and hashlib.sha256(dl.content).hexdigest() == art.sha256
                )
                ms = client.get(f"/api/v1/chat/sessions/{session_id}/map-state", headers=auth_headers)
                if ms.status_code == 200:
                    mapspec = (ms.json().get("map_state") or {}).get("mapspec") or {}
                    layers = mapspec.get("layers") or []
                    layer = next((ly for ly in layers if ly.get("id") == "pi-georef-layer"), None)
                    style_id = (layer or {}).get("raster_style", {}).get("style_id")
                    if style_id:
                        z = 14
                        tx, ty = _world_tile_for(x0 + 32.0 * scale_m, y0 - 32.0 * scale_m, z)
                        tr = client.get(
                            f"/api/v1/raster-authoring/tiles/{art.raster_ref}/{z}/{tx}/{ty}.png"
                            f"?session_id={session_id}&layer_id=pi-georef-layer&style_id={style_id}",
                            headers=auth_headers,
                        )
                        tile_ok = tr.status_code == 200 and tr.content.startswith(b"\x89PNG")

        verdict["journeys"]["geotiff_disk_oracle"] = geotiff_ok
        verdict["journeys"]["download_sha256_oracle"] = download_ok
        verdict["journeys"]["mapspec_bind_and_tile_ok"] = bool(
            art and art.mapspec_layer_id == "pi-georef-layer" and (art.mapspec_revision or 0) >= 1 and tile_ok
        )

        for key, ok in verdict["journeys"].items():
            if not ok:
                verdict["fail"].append(f"journey gate failed: {key}")
    except Exception as exc:  # noqa: BLE001
        verdict["fail"].append(f"driver exception: {type(exc).__name__}: {exc}")
    finally:
        stub.terminate()
        server.should_exit = True

    print(json.dumps(verdict, ensure_ascii=False))
    ok = not verdict["fail"] and all(verdict["journeys"].values())
    (WORK / "verdict.json").write_text(json.dumps(verdict, ensure_ascii=False), encoding="utf-8")
    if ok:
        shutil.rmtree(WORK, ignore_errors=True)
    else:
        print("WORK_DIR=" + str(WORK), file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


@pytest.mark.timeout(900)
def test_pi_g48_georeferencing_journey(tmp_path: Path):
    """真实 Pi 子进程旅程（R35 + R36）：driver 独立进程执行，末行 JSON 为裁决。"""
    driver = tmp_path / "g48_driver.py"
    driver.write_text(_DRIVER_SOURCE, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(driver), str(REPO_ROOT)],
        timeout=880,
        capture_output=True,
        text=True,
    )
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    verdict = json.loads(lines[-1]) if lines else {"fail": ["no verdict"]}
    assert proc.returncode == 0, f"journey failed: {verdict.get('fail')} stderr={proc.stderr[-2500:]}"
    expected_gates = {
        "warmup_ok",
        "inspect_receipt",
        "bad_plan_insufficient",
        "r36_honest_refusal",
        "good_plan_valid",
        "bounded_receipt",
        "geotiff_disk_oracle",
        "download_sha256_oracle",
        "mapspec_bind_and_tile_ok",
    }
    for gate in expected_gates:
        assert verdict["journeys"].get(gate) is True, (
            f"gate {gate} not satisfied: {json.dumps(verdict, ensure_ascii=False)}"
        )
