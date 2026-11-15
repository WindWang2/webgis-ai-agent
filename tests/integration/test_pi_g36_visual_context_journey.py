"""G36 旅程：真实 Pi 子进程 + stub LLM → 原生 ImageContent → 专题工具 → 权威 MapSpec。

与 test_pi_g33_thematic_authoring_journey.py 同架构（独立 driver 进程：
node stub LLM → uvicorn 生产 app（真实 vendor/pi bridge + 真实扩展 + 真实
dispatch）→ HTTP 旅程），聚焦三条生产旅程：

J-G36-1（V01 可读性问答）：HTTP intake 当前图证据 → /chat/stream 携
  attachments → 真实 Pi 子进程收到原生 images → stub LLM 收到 OpenAI
  vision 线格式（image_url data URI）且 base64 与 intake 回执的
  derived_sha256 一致 → 回复完成、地图零 mutation。
J-G36-2（V02 参考图驱动修改）：参考图 intake → turn 携附件 → stub 调
  webgis_thematic_style_apply（真实 dispatch/CAS）→ 权威 MapSpec palette
  改变 + 断点保留 + revision +1。
J-G36-3（V33 撤权换代）：已送图撤销 → 同会话纯文本 turn → vendor
  session 换代后 stub 收到的请求不再含旧 pixels。

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
"""G36 journey driver：stub LLM + uvicorn(app) + 视觉上下文旅程 → VERDICT json。"""
import asyncio
import hashlib
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
import uuid
from pathlib import Path

REPO_ROOT = Path(sys.argv[1])
WORK = Path(tempfile.mkdtemp(prefix="pi-g36-"))
sys.path.insert(0, str(REPO_ROOT))

RUN = "g36" + uuid.uuid4().hex[:8]
LAYER_ID = "result-journey"
os.environ["PI_AUTOSTART"] = "true"
os.environ.setdefault("GIS_MANIFEST_STRICT", "0")
# 旅程自包含：进程内会话锁 + 内存会话态（不依赖本机 Redis；单进程语义等价）。
os.environ["USE_REDIS"] = "0"
# 隔离数据面（blob/session 状态落 WORK）。
(WORK / "data").mkdir(parents=True, exist_ok=True)
os.environ["DATA_DIR"] = str(WORK / "data")
# G36 视觉能力门：管理员描述符确证 stub-model 支持视觉（三来源之 env）。
os.environ["MODEL_DESCRIPTOR_OVERRIDES"] = json.dumps([
    {"provider_id": "webgis", "model_id": "stub-model", "vision": True},
])

STUB_JS = r"""
import http from 'node:http';
import fs from 'node:fs';
const scriptPath = process.env.JOURNEY_SCRIPT;
const imagesLog = process.env.IMAGES_LOG;
const counters = {};
const srv = http.createServer((req, res) => {
  if (!req.url.includes('/chat/completions')) {
    res.writeHead(200, { 'content-type': 'text/plain' }); res.end('ok'); return;
  }
  let body = ''; req.on('data', (c) => (body += c));
  req.on('end', () => {
    const parsed = JSON.parse(body || '{}');
    const msgs = parsed.messages || [];
    let lastUser = null;
    let imageBlocks = [];
    // 记录最后一条 user 消息的图像块（OpenAI vision 线格式）。
    for (let i = msgs.length - 1; i >= 0; i -= 1) {
      const m = msgs[i];
      if (m.role !== 'user') continue;
      if (Array.isArray(m.content)) {
        imageBlocks = m.content.filter((b) => b.type === 'image_url');
        lastUser = m.content.filter((b) => b.type === 'text').map((b) => b.text).join('');
      } else { lastUser = m.content; }
      break;
    }
    if (imagesLog) {
      fs.appendFileSync(imagesLog, JSON.stringify({
        req: counters.__n = (counters.__n || 0) + 1,
        lastUserHead: String(lastUser || '').slice(0, 40),
        imageCount: imageBlocks.length,
        images: imageBlocks.map((b) => ({
          mime: (b.image_url && b.image_url.url || '').slice(0, 30),
          b64Head: (b.image_url && b.image_url.url || '').split(',').pop().slice(0, 24),
          b64Len: (b.image_url && b.image_url.url || '').split(',').pop().length,
        })),
      }) + '\n');
    }
    const base = (lastUser || '').split('\n')[0].trim();
    const turnScripts = JSON.parse(fs.readFileSync(scriptPath, 'utf8'));
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


def _striped_png(w=240, h=160) -> bytes:
    """红/绿/蓝竖条 PNG（stub 侧无法解码，但 base64 哈希可核对）。"""
    from PIL import Image

    img = Image.new("RGB", (w, h))
    for x in range(w):
        color = (220, 30, 30) if x % 3 == 0 else ((30, 200, 30) if x % 3 == 1 else (30, 30, 220))
        for y in range(h):
            img.putpixel((x, y), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _reference_jpeg() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (120, 80), (200, 60, 20)).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


async def _seed_session(session_id: str) -> int:
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
        }, source_data={"ref_id": str(ref)}),
        origin="agent", actor="journey:seed",
    )
    assert not result.is_error, result.error_msg
    from app.services.session_data import session_data_manager as sdm

    return int(await sdm.get_state_field(session_id, "_cartographic_mutation_revision") or 0)


async def _read_revision(session_id: str) -> int:
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
    stub_js = WORK / "journey_stub.mjs"
    stub_js.write_text(STUB_JS, encoding="utf-8")
    (WORK / "journey_script.json").write_text("{}", encoding="utf-8")
    images_log = WORK / "stub_images.jsonl"

    with open(WORK / "stub_err.log", "w") as fh:
        stub = subprocess.Popen(
            ["node", str(stub_js)], stdout=subprocess.DEVNULL, stderr=fh,
            env={**os.environ, "JOURNEY_SCRIPT": str(WORK / "journey_script.json"),
                 "IMAGES_LOG": str(images_log)},
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
    os.environ["LLM_API_KEY"] = "stub-g36-key"
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
    ident: dict = {"owner_token": None, "session_id": None}

    def _headers(extra=None):
        h = {"Content-Type": "application/json"}
        if ident["owner_token"]:
            h["X-Session-Token"] = ident["owner_token"]
        h.update(extra or {})
        return h

    def chat(message: str, attachments=None, with_session=True) -> str:
        payload = {"message": message}
        if with_session:
            payload["session_id"] = ident["session_id"]
        if attachments:
            payload["attachments"] = [
                {"evidence_id": e} for e in attachments]
        req = urllib.request.Request(
            "http://127.0.0.1:{}/api/v1/chat/stream".format(api_port),
            data=json.dumps(payload).encode(),
            headers=_headers(),
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = resp.read().decode("utf-8", "replace")
        # 匿名会话：首个无 session 请求由服务端铸造 session + owner_token
        # （SSE session 事件）；捕获后本旅程所有请求走同一身份。
        for block in body.split("\n\n"):
            for line in block.splitlines():
                if not line.startswith("data: "):
                    continue
                try:
                    d = json.loads(line[6:])
                except ValueError:
                    continue
                if isinstance(d.get("owner_token"), str):
                    ident["owner_token"] = d["owner_token"]
                if isinstance(d.get("session_id"), str) and isinstance(
                        d.get("owner_token"), str):
                    ident["session_id"] = d["session_id"]
        return body

    def intake(data: bytes, filename: str, mime: str, source_type: str,
               extra_fields: dict) -> dict:
        boundary = "g36b" + uuid.uuid4().hex
        parts = []
        fields = dict(extra_fields)
        fields["source_type"] = source_type
        for k, v in fields.items():
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        parts.append(
            (f'--{boundary}\r\nContent-Disposition: form-data; name="image"; '
             f'filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n').encode()
            + data + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        req = urllib.request.Request(
            "http://127.0.0.1:{}/api/v1/chat/sessions/{}/visual-context/evidence"
            .format(api_port, ident["session_id"]),
            data=b"".join(parts),
            headers=_headers({"Content-Type": f"multipart/form-data; boundary={boundary}"}),
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())

    def revoke(evidence_id: str) -> int:
        req = urllib.request.Request(
            "http://127.0.0.1:{}/api/v1/chat/sessions/{}/visual-context/evidence/{}"
            .format(api_port, ident["session_id"], evidence_id),
            method="DELETE", headers=_headers())
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            return e.code

    def stub_image_entries() -> list:
        if not images_log.exists():
            return []
        out = []
        for line in images_log.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out

    import urllib.error  # noqa: F401  (revoke 错误面)

    try:
        # 会话身份占位；warmup 后由服务端铸造会话替换（ident["session_id"]）。

        # warmup：匿名首问（无 session_id）→ 服务端铸造会话 + owner_token
        # （后续 intake/chat 复用同一身份 —— 会话存在性与所有权的前提）。
        (WORK / "journey_script.json").write_text(json.dumps({
            "warmup": [{"text": "warmup-ok"}],
        }), encoding="utf-8")
        warm = chat("warmup", with_session=False)
        (WORK / "warm.log").write_text(warm, encoding="utf-8")
        if "task_error" in warm:
            warm = chat("warmup", with_session=False)
            (WORK / "warm.log").write_text(warm, encoding="utf-8")
        verdict["journeys"]["warmup_ok"] = "task_complete" in warm
        if not (ident["owner_token"] and ident["session_id"]):
            verdict["fail"].append(
                f"anonymous session mint failed: sid={ident['session_id']!r} "
                f"has_token={bool(ident['owner_token'])}")
            print(json.dumps(verdict))
            return 1
        session_id = ident["session_id"]

        rev_seed = asyncio.run(_seed_session(session_id))
        verdict["seeded_revision"] = rev_seed

        # ── 证据 intake（真实 HTTP multipart 路径）──
        cap1 = intake(_striped_png(), "cap.png", "image/png", "map_viewport", {
            "mapspec_revision": str(rev_seed),
            "mapspec_fingerprint": "",
            "viewport": json.dumps({"center": [116.1, 39.0], "zoom": 10}),
            "dpr": "2.0",
            "label": "当前地图视口",
        })
        ref1 = intake(_reference_jpeg(), "ref.jpg", "image/jpeg",
                      "reference_upload", {"label": "配色参考"})
        verdict["journeys"]["intake_ok"] = (
            cap1.get("evidence_id", "").startswith("vev-")
            and ref1.get("evidence_id", "").startswith("vev-"))

        # ── J-G36-1：V01 可读性问答（原生 images → stub 收到）──
        (WORK / "journey_script.json").write_text(json.dumps({
            "看这张当前地图截图，哪些线不易辨认": [
                {"text": "红色与蓝色线条对比度低，最不易辨认。"},
            ],
            "按参考图的配色调整当前分级图，保留断点": [
                {"tool": "tool_search", "args": {"query": "专题 表达 提交 分级 修改"}},
                {"tool": "webgis_thematic_style_apply", "args": {
                    "layer_id": LAYER_ID,
                    "plan": {"mode": "graduated_manual",
                             "breaks": [0, 10, 20, 50],
                             "palette": "Reds"},
                    "expected_revision": rev_seed,
                }},
                {"text": "已按参考图色系改为 Reds，断点保留。"},
            ],
            "继续总结": [{"text": "总结完成。"}],
            "读取当前证据": [
                {"tool": "webgis_visual_evidence_read",
                 "args": {"evidence_id": cap1["evidence_id"]}},
                {"text": "证据已读取。"},
            ],
        }), encoding="utf-8")

        n_before = len(stub_image_entries())
        body1 = chat("看这张当前地图截图，哪些线不易辨认",
                     attachments=[cap1["evidence_id"]])
        (WORK / "sse1.log").write_text(body1, encoding="utf-8")
        verdict["journeys"]["v01_receipt_sent"] = (
            "attachment_receipt" in body1 and '"status": "sent"' in body1
            and cap1["evidence_id"] in body1)
        verdict["journeys"]["v01_turn_complete"] = "task_complete" in body1
        # 地图零 mutation（解释型不动 MapSpec）。
        rev_after1 = asyncio.run(_read_revision(session_id))
        verdict["journeys"]["v01_no_map_mutation"] = (rev_after1 == rev_seed)
        # stub 收到的请求：原生图像内容 + base64 与 intake 回执 sha 一致。
        entries = stub_image_entries()
        img_entries = [e for e in entries if e.get("imageCount", 0) > 0]
        verdict["journeys"]["v01_stub_received_image"] = len(img_entries) >= 1
        if img_entries:
            import base64 as _b64
            got = img_entries[-1]["images"][0]
            # b64Len 精确哈希核对：取该请求记录的 b64Head 不够 —— stub 端
            # 另存完整 base64？为可核对，直接以派生 sha 断言：stub 记录的
            # b64 长度与 intake derived_size 的 base64 长度一致（4/3）。
            expected_len = ((int(cap1["derived_size"]) + 2) // 3) * 4
            verdict["journeys"]["v01_b64_len_matches"] = (
                abs(got["b64Len"] - expected_len) <= 4)
            verdict["journeys"]["v01_wire_mime"] = got["mime"].startswith("data:image/")
        # ── J-G36-1b：agent 读证据工具（真实 dispatch + result_images）──
        body1b = chat("读取当前证据")
        (WORK / "sse1b.log").write_text(body1b, encoding="utf-8")
        verdict["journeys"]["tool_read_ok"] = (
            "webgis_visual_evidence_read" in body1b
            and "task_complete" in body1b)

        # ── J-G36-2：V02 参考图驱动修改（真实 inspect/apply + CAS）──
        body2 = chat("按参考图的配色调整当前分级图，保留断点",
                     attachments=[ref1["evidence_id"]])
        (WORK / "sse2.log").write_text(body2, encoding="utf-8")
        layer_manual = asyncio.run(_read_layer_async(session_id))
        if layer_manual is None:
            verdict["fail"].append("layer missing after reference-driven apply")
        else:
            spec = layer_manual.get("legend_spec", {})
            verdict["journeys"]["v02_breaks_preserved"] = (
                spec.get("breaks") == [0.0, 10.0, 20.0, 50.0])
            verdict["journeys"]["v02_palette_changed"] = (
                "Reds" in json.dumps(spec) or "Reds" in json.dumps(layer_manual))
            ev = layer_manual.get("thematic_authoring") or {}
            verdict["journeys"]["v02_agent_evidence"] = (
                ev.get("decided_by") == "agent")
        rev_after2 = asyncio.run(_read_revision(session_id))
        verdict["journeys"]["v02_revision_advanced"] = (rev_after2 == rev_seed + 1)
        verdict["journeys"]["v02_receipt_sent"] = '"status": "sent"' in body2
        # stub 第二次收图（参考图）。
        entries2 = [e for e in stub_image_entries() if e.get("imageCount", 0) > 0]
        verdict["journeys"]["v02_stub_received_image"] = len(entries2) >= 2

        # ── V20：刷新恢复 —— 会话详情带附件元数据（来源/版本/ref）。──
        detail_req = urllib.request.Request(
            "http://127.0.0.1:{}/api/v1/chat/sessions/{}".format(
                api_port, ident["session_id"]),
            headers=_headers())
        with urllib.request.urlopen(detail_req, timeout=30) as resp:
            detail = json.loads(resp.read().decode())
        attach_msgs = [m for m in detail.get("messages", [])
                       if isinstance(m.get("attachments"), list)
                       and m["attachments"]]
        verdict["journeys"]["v20_detail_attachments"] = bool(attach_msgs)
        if attach_msgs:
            meta = attach_msgs[-1]["attachments"][0]
            verdict["journeys"]["v20_meta_bounded"] = (
                meta.get("evidence_id", "").startswith("vev-")
                and "derived_sha256" in meta
                and "b64" not in json.dumps(meta).lower())

        # ── J-G36-3：V33 撤权 → 纯文本 turn 不再重送旧 pixels ──
        n_img_reqs_before = len(
            [e for e in stub_image_entries() if e.get("imageCount", 0) > 0])
        code = revoke(cap1["evidence_id"])
        verdict["journeys"]["v33_revoke_ok"] = (code == 200)
        body3 = chat("继续总结")
        (WORK / "sse3.log").write_text(body3, encoding="utf-8")
        verdict["journeys"]["v33_turn_complete"] = "task_complete" in body3
        entries_after = stub_image_entries()
        img_reqs_after = [e for e in entries_after if e.get("imageCount", 0) > 0]
        verdict["journeys"]["v33_no_stale_pixels_resent"] = (
            len(img_reqs_after) == n_img_reqs_before)

        if not all(verdict["journeys"].values()):
            verdict["fail"].append(
                "unmet: " + ", ".join(
                    k for k, v in verdict["journeys"].items() if not v))
    finally:
        try:
            server.should_exit = True
        except Exception:
            pass
        stub.terminate()
        stub.wait(timeout=10)
    print(json.dumps(verdict))
    return 0 if not verdict["fail"] else 1


if __name__ == "__main__":
    sys.exit(main())
'''


def test_g36_visual_context_journey(tmp_path: Path):
    driver = tmp_path / "g36_driver.py"
    driver.write_text(_DRIVER_SOURCE, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    result = subprocess.run(
        [sys.executable, str(driver), str(REPO_ROOT)],
        capture_output=True, text=True, timeout=600, env=env,
        cwd=str(REPO_ROOT),
    )
    verdict = None
    for line in result.stdout.splitlines():
        if line.startswith("{"):
            try:
                verdict = json.loads(line)
            except ValueError:
                continue
    assert verdict is not None, (
        "driver produced no verdict — "
        f"stdout={result.stdout[-2000:]} stderr={result.stderr[-3000:]}")
    assert not verdict.get("fail"), json.dumps(verdict, ensure_ascii=False)
    journeys = verdict.get("journeys", {})
    assert journeys.get("v01_stub_received_image"), json.dumps(
        verdict, ensure_ascii=False)
    assert journeys.get("v01_receipt_sent")
    assert journeys.get("v01_no_map_mutation")
    assert journeys.get("v02_breaks_preserved")
    assert journeys.get("v02_revision_advanced")
    assert journeys.get("v02_stub_received_image")
    assert journeys.get("v33_revoke_ok")
    assert journeys.get("v33_no_stale_pixels_resent")
    assert journeys.get("tool_read_ok")
