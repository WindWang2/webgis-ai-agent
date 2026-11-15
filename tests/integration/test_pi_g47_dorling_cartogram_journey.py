"""G47 真实 Pi 旅程（WP11）：固定 pin 子进程 → 生产扩展 → 生产 dispatch → 本域服务。

与 G29–G41 同款架构：pytest 只做门控与裁决；driver 是独立 Python 子进程（避免
conftest 预导入污染 app 单例）。链：

    stub LLM SSE（node）
      → uvicorn(app.main:app)（生产 lifespan 拉起 **vendored 固定 pin** 的 Pi RPC 子进程）
      → /api/v1/chat/stream 生产 HTTP 链
      → tool_search deferred 激活
      → webgis_dorling_cartogram_{inspect,plan,run,status,apply,export}
      → ToolDispatchService / ToolRegistry 生产 dispatch
      → cartogram_authoring.service（强版本冻结 → durable job → 独立 oracle → CAS）
      → 单笔 UpsertLayerIntent（checkpoint/undo）

只有 LLM 响应是 stub；RPC / dispatch / 作业 / 写入 / 渲染主链都不 mock。
另外用**生产 HTTP 路由**再取一次导出字节并验证跨会话 404 —— ID 不是权限。

门控：PI_JOURNEY_E2E=1 + node + vendored Pi entry 已构建（显式 skip，绝不
silent-green）。
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
"""G47 journey driver：stub LLM + uvicorn(app) + Dorling 作者旅程 → VERDICT json。"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPO_ROOT = Path(sys.argv[1])
WORK = Path(tempfile.mkdtemp(prefix="pi-g47-"))
sys.path.insert(0, str(REPO_ROOT))

RUN = "g47" + uuid.uuid4().hex[:8]
LAYER_ID = "g47-layer"
os.environ["PI_AUTOSTART"] = "true"
os.environ.setdefault("GIS_MANIFEST_STRICT", "0")

PLAN = {
    "schema_version": 1,
    "mode": "dorling_circle",
    "quantity": {"field": "pop", "unit": "人", "meaning": "count",
                 "aggregation": "sum"},
    "area_scale": {"max_radius": 36.0, "extent": 1000.0},
    "title": "分区人口（Dorling）",
}

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


def _features():
    feats = []
    for i in range(12):
        lon = 104.0 + (i % 4) * 0.25
        lat = 30.4 + (i // 4) * 0.25
        feats.append({
            "type": "Feature", "id": f"u{i}",
            "geometry": {"type": "Polygon", "coordinates": [[
                [lon, lat], [lon + 0.25, lat],
                [lon + 0.25, lat + 0.25], [lon, lat + 0.25], [lon, lat]]]},
            "properties": {"name": f"U{i}", "pop": 100 * (i + 1)},
        })
    return {"type": "FeatureCollection", "features": feats}


async def _seed_session(session_id: str) -> dict:
    import app.services.mapspec.store as store_module
    store_module.BASE_STORAGE_DIR = WORK / "mapspec_store"
    store_module.BASE_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    from app.services.session_data import session_data_manager as sdm
    ref = await sdm.store(session_id, {"kind": "geojson", "geojson": _features()},
                          prefix="result")
    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.intents import UpsertLayerIntent
    result = await apply_gis_mutation(
        session_id,
        UpsertLayerIntent(layer={
            "id": LAYER_ID, "type": "fill", "source": f"{LAYER_ID}-source",
            "paint": {"color": "#c2410c"},
            "data_binding": {"field": "pop", "field_type": "number"},
        }, source_data={"ref_id": str(ref)}),
        origin="agent", actor="journey:seed")
    assert not result.is_error, result.error_msg

    from app.services.cartogram_authoring import (
        inspect_layer, parse_plan, plan_layout,
    )
    ins = await inspect_layer(session_id, LAYER_ID, trusted_dispatch=True)
    anchor = {k: ins["source_anchor"][k] for k in
              ("ref_id", "content_revision", "content_fingerprint")}
    plan = parse_plan({**PLAN, "layer_id": LAYER_ID, "source_anchor": anchor})
    prev = await plan_layout(session_id, LAYER_ID, plan, trusted_dispatch=True)
    return {
        "rev": ins["revision"], "base_ref": str(ref), "anchor": anchor,
        "plan_digest": prev["plan_digest"],
        "candidate_digest": prev["candidate_digest"],
        "positive_units": prev["units_for_layout"]["with_positive_value"],
        "adjacency_edges": prev["geometry"]["adjacency_edges"],
        "coefficient": prev["area_scale"]["coefficient"],
    }


async def _job_row(session_id: str):
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.models.db_model import AnalysisTask
    with SessionLocal() as db:
        rows = db.execute(
            select(AnalysisTask)
            .where(AnalysisTask.session_id == session_id,
                   AnalysisTask.task_type == "cartogram_layout")
            .order_by(AnalysisTask.id.desc())
        ).scalars().all()
    return [{
        "id": int(r.id), "status": str(r.status), "kind": getattr(r, "kind", None),
        "result_ref": getattr(r, "result_ref", None),
        "summary": getattr(r, "result_summary", None),
    } for r in rows]


async def _layer(session_id: str):
    from app.services.mapspec_store import mapspec_store
    spec = await mapspec_store.get_mapspec(session_id) or {}
    for layer in spec.get("layers") or []:
        if layer.get("id") == LAYER_ID:
            return layer, spec
    return None, spec


def main() -> int:
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
    os.environ["LLM_API_KEY"] = "stub-g47-key"
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

    verdict: dict = {"journeys": {}, "fail": [], "observations": {}}
    owner_token = None

    # Pi 子进程身份：固定 pin + 已构建 entry（不是"随系统装的另一个 Pi"）
    try:
        from app.services.chat.pi_compat import pi_vendor_identity
        ident = pi_vendor_identity()
        verdict["observations"]["pi"] = {
            "expected_pin": ident.expected_pin,
            "pin_source": ident.expected_pin_source,
            "entry": str(ident.entry_path or ""),
            "entry_kind": ident.entry_kind,
            "node": ident.node_version,
        }
    except Exception as exc:  # noqa: BLE001
        verdict["fail"].append(f"pi identity unavailable: {exc}")

    def chat(message: str) -> str:
        nonlocal owner_token
        headers = {"Content-Type": "application/json"}
        if owner_token:
            headers["X-Session-Token"] = owner_token
        req = urllib.request.Request(
            f"http://127.0.0.1:{api_port}/api/v1/chat/stream",
            data=json.dumps({"message": message, "session_id": session_id}).encode(),
            headers=headers)
        with urllib.request.urlopen(req, timeout=300) as resp:
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

    def http_get(path: str, token: str):
        headers = {"X-Session-Token": token} if token else {}
        req = urllib.request.Request(f"http://127.0.0.1:{api_port}{path}",
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers or {}), exc.read()

    try:
        session_id = RUN + "-s1"
        seed = asyncio.run(_seed_session(session_id))
        verdict["observations"]["seed"] = seed
        scripts = {
            "warmup": [{"text": "warmup-ok"}],
            "这个层能做面积cartogram吗": [
                {"tool": "tool_search",
                 "args": {"query": "面积 cartogram dorling 圆 检查 字段"}},
                {"tool": "webgis_dorling_cartogram_inspect",
                 "args": {"layer_id": LAYER_ID}},
                {"text": "已查看源版本、字段与支持矩阵。"},
            ],
            "预检人口字段的dorling计划": [
                {"tool": "tool_search",
                 "args": {"query": "cartogram 计划 预检 面积系数 参考圆"}},
                {"tool": "webgis_dorling_cartogram_plan",
                 "args": {"layer_id": LAYER_ID, "plan": PLAN}},
                {"text": "预检通过，面积系数与参考圆已返回。"},
            ],
            "开始求解这个布局": [
                {"tool": "tool_search",
                 "args": {"query": "cartogram 求解 作业 布局"}},
                {"tool": "webgis_dorling_cartogram_run",
                 "args": {"layer_id": LAYER_ID, "plan": PLAN,
                          "source_anchor": seed["anchor"]}},
                {"text": "已提交布局作业。"},
            ],
        }
        (WORK / "journey_script.json").write_text(json.dumps(scripts), encoding="utf-8")

        warm = chat("warmup")
        if "task_error" in warm:
            warm = chat("warmup")
        verdict["journeys"]["warmup_ok"] = "task_complete" in warm
        (WORK / "sse_warm.log").write_text(warm, encoding="utf-8")

        s1 = chat("这个层能做面积cartogram吗")
        (WORK / "sse_inspect.log").write_text(s1, encoding="utf-8")
        verdict["journeys"]["inspect_via_pi"] = (
            "webgis_dorling_cartogram_inspect" in s1 and "source_anchor" in s1
            and "dorling_circle" in s1)
        verdict["observations"]["inspect_has_generic_unsupported"] = (
            '"generic_cartogram": false' in s1
            or '"generic_cartogram":false' in s1)

        s2 = chat("预检人口字段的dorling计划")
        (WORK / "sse_plan.log").write_text(s2, encoding="utf-8")
        verdict["journeys"]["plan_via_pi"] = (
            "webgis_dorling_cartogram_plan" in s2
            and seed["plan_digest"] in s2
            and seed["candidate_digest"] in s2)
        verdict["journeys"]["plan_shows_area_coefficient"] = (
            "shared_coefficient" in s2 and "reference_circles" in s2)
        # 指纹一致性：调**生产 registry 中注册的同一工具**逐字比对
        from app.tools.registry import ToolRegistry
        from app.tools.cartogram_tools import register_cartogram_tools
        reg = ToolRegistry()
        register_cartogram_tools(reg)
        direct = asyncio.run(reg._tools["webgis_dorling_cartogram_plan"](
            layer_id=LAYER_ID, plan=PLAN, session_id=session_id))
        verdict["journeys"]["plan_digest_via_registry_tool"] = bool(
            direct.get("success") is True
            and direct.get("plan_digest") == seed["plan_digest"]
            and direct.get("candidate_digest") == seed["candidate_digest"]
            and direct.get("units_for_layout", {}).get("with_positive_value")
            == seed["positive_units"])

        s3 = chat("开始求解这个布局")
        (WORK / "sse_run.log").write_text(s3, encoding="utf-8")
        verdict["journeys"]["run_via_pi"] = (
            "webgis_dorling_cartogram_run" in s3 and "job_id" in s3)

        rows = asyncio.run(_job_row(session_id))
        verdict["observations"]["job_rows"] = rows
        job = rows[0] if rows else None
        if job is None:
            raise RuntimeError("no cartogram_layout job row was created")
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            rows = asyncio.run(_job_row(session_id))
            job = rows[0] if rows else None
            if job and job["status"] in ("completed", "failed", "cancelled", "stale"):
                break
            time.sleep(0.2)
        verdict["journeys"]["job_completed"] = bool(
            job and job["status"] == "completed" and job["kind"] == "cartogram_layout")
        layout_ref = (job or {}).get("result_ref") or ""
        verdict["journeys"]["job_produced_layout_ref"] = layout_ref.startswith(
            "ref:g47layout-")
        summary = (job or {}).get("summary") or {}
        verdict["journeys"]["job_summary_publication_ready"] = bool(
            summary.get("publication_ready") is True
            and (summary.get("area") or {}).get("max_rel_error", 1) < 0.01
            and summary.get("circle_count") == seed["positive_units"])

        # 布局 artifact 独立回读（不读服务返回的摘要）
        from app.services.session_data import session_data_manager as sdm
        layout = asyncio.run(sdm.get(session_id, layout_ref)) if layout_ref else {}
        circles = (layout or {}).get("circles") or []
        verdict["journeys"]["layout_artifact_is_diagram_space"] = bool(
            layout.get("coordinate_space") == "diagram"
            and layout.get("is_geographic") is False
            and layout.get("units") == "diagram_unit")
        base = sorted(circles, key=lambda c: c["value"])
        area_ok = True
        if len(base) >= 2:
            ratio_v = base[-1]["value"] / base[0]["value"]
            ratio_a = (base[-1]["radius"] / base[0]["radius"]) ** 2
            area_ok = abs(ratio_a - ratio_v) / ratio_v < 5e-3
        verdict["journeys"]["layout_area_ratio_real"] = bool(
            len(circles) == seed["positive_units"] and area_ok)
        extent = float(((layout or {}).get("extent") or {}).get("width") or 0.0)
        verdict["journeys"]["layout_circles_are_not_latlon"] = bool(
            extent > 0 and circles and all(
                0.0 <= float(c["x"]) <= extent + 1e-6
                and 0.0 <= float(c["y"]) <= extent + 1e-6
                for c in circles))

        # 后续 turn 需要 job_id / layout_ref / revision → 重写脚本
        scripts.update({
            "看看算好了吗": [
                {"tool": "tool_search", "args": {"query": "cartogram 作业 状态 轮询"}},
                {"tool": "webgis_dorling_cartogram_status",
                 "args": {"job_id": job["id"]}},
                {"text": "作业已完成。"},
            ],
            "把布局应用到图层": [
                {"tool": "tool_search", "args": {"query": "cartogram 应用 提交 MapSpec"}},
                {"tool": "webgis_dorling_cartogram_apply",
                 "args": {"layer_id": LAYER_ID, "layout_ref": layout_ref,
                          "expected_revision": seed["rev"]}},
                {"text": "布局已作为参考成果应用到层。"},
            ],
            "导出svg交付": [
                {"tool": "tool_search", "args": {"query": "cartogram 导出 svg png pdf"}},
                {"tool": "webgis_dorling_cartogram_export",
                 "args": {"layout_ref": layout_ref, "format": "svg"}},
                {"text": "已生成 SVG。"},
            ],
        })
        (WORK / "journey_script.json").write_text(json.dumps(scripts), encoding="utf-8")

        s4 = chat("看看算好了吗")
        (WORK / "sse_status.log").write_text(s4, encoding="utf-8")
        verdict["journeys"]["status_via_pi"] = (
            "webgis_dorling_cartogram_status" in s4 and layout_ref in s4
            and "publication_ready" in s4)

        s5 = chat("把布局应用到图层")
        (WORK / "sse_apply.log").write_text(s5, encoding="utf-8")
        verdict["journeys"]["apply_via_pi"] = bool(
            "webgis_dorling_cartogram_apply" in s5
            and ('"committed": true' in s5 or '"committed":true' in s5))

        layer, spec = asyncio.run(_layer(session_id))
        block = (layer or {}).get("cartogram_authoring") or {}
        verdict["journeys"]["mapspec_block_committed"] = bool(
            block.get("mode") == "dorling_circle"
            and block.get("layout_ref") == layout_ref
            and block.get("base_source_ref") == seed["base_ref"]
            and block.get("coordinate_space") == "diagram"
            and block.get("decided_by") == "agent"
            and block.get("diagnostic") is False
            and block.get("publication_ready") is True)
        verdict["journeys"]["apply_did_not_rewrite_style"] = bool(
            (layer or {}).get("paint") == {"color": "#c2410c"}
            and (layer or {}).get("data_binding", {}).get("field") == "pop"
            and block.get("previous") is None)
        verdict["journeys"]["apply_is_one_mutation_with_undo"] = bool(
            "can_undo" in s5 and block.get("layout_ref"))

        s6 = chat("导出svg交付")
        (WORK / "sse_export.log").write_text(s6, encoding="utf-8")
        verdict["journeys"]["export_via_pi"] = (
            "webgis_dorling_cartogram_export" in s6 and "sha256" in s6)
        # 独立复算：服务层同一布局的 SVG 字节哈希必须与 Pi 回执一致
        from app.services.cartogram_authoring import export_layout
        data, media, filename = asyncio.run(
            export_layout(session_id, layout_ref, "svg"))
        import hashlib
        digest = hashlib.sha256(data).hexdigest()
        verdict["journeys"]["export_bytes_match_pi_receipt"] = bool(
            digest in s6 and data.startswith(b"<svg")
            and data.count(b"data-feature-id") == len(circles))

        # 生产 HTTP 路由：受权可下载、跨会话 404（ID 不是权限）
        if owner_token:
            path = (f"/api/v1/cartogram/sessions/{session_id}/layouts/"
                    f"{layout_ref}/export?format=svg")
            status, headers, body = http_get(path, owner_token)
            verdict["journeys"]["http_export_authorized"] = bool(
                status == 200
                and headers.get("content-type", "").startswith("image/svg+xml")
                and body.startswith(b"<svg")
                and "dorling-cartogram-" in headers.get("content-disposition", ""))
            foreign = f"/api/v1/cartogram/sessions/{RUN}-nope/layouts/{layout_ref}/export?format=svg"
            status2, _h2, _b2 = http_get(foreign, owner_token)
            verdict["journeys"]["http_foreign_session_denied"] = status2 == 404
            meta = f"/api/v1/cartogram/sessions/{session_id}/layouts/{layout_ref}"
            status3, _h3, body3 = http_get(meta, owner_token)
            verdict["journeys"]["http_layout_receipt_bounded"] = bool(
                status3 == 200 and b"circles_sample" in body3
                and b'"circles":' not in body3)
        else:
            verdict["fail"].append("no owner_token captured from SSE")
            verdict["journeys"]["http_export_authorized"] = False
            verdict["journeys"]["http_foreign_session_denied"] = False
            verdict["journeys"]["http_layout_receipt_bounded"] = False

        # 源数据没被写回布局坐标
        payload = asyncio.run(sdm.get(session_id, seed["base_ref"]))
        feats = (payload or {}).get("geojson", {}).get("features") or []
        verdict["journeys"]["source_not_written_back"] = bool(
            len(feats) == 12
            and all("x" not in f["properties"] and "radius" not in f["properties"]
                    for f in feats))

        required = (
            "warmup_ok", "inspect_via_pi", "plan_via_pi",
            "plan_shows_area_coefficient", "plan_digest_via_registry_tool",
            "run_via_pi", "job_completed", "job_produced_layout_ref",
            "job_summary_publication_ready", "layout_artifact_is_diagram_space",
            "layout_area_ratio_real", "layout_circles_are_not_latlon",
            "status_via_pi", "apply_via_pi", "mapspec_block_committed",
            "apply_did_not_rewrite_style", "apply_is_one_mutation_with_undo",
            "export_via_pi", "export_bytes_match_pi_receipt",
            "http_export_authorized", "http_foreign_session_denied",
            "http_layout_receipt_bounded", "source_not_written_back",
        )
        for key in required:
            if not verdict["journeys"].get(key):
                verdict["fail"].append(f"journey gate failed: {key}")
    except Exception as exc:  # noqa: BLE001 — verdict 面必须完整
        import traceback
        verdict["fail"].append(f"driver exception: {type(exc).__name__}: {exc}")
        verdict["observations"]["traceback"] = traceback.format_exc()[-2500:]
    finally:
        stub.terminate()
        server.should_exit = True

    print(json.dumps(verdict, ensure_ascii=False))
    ok = not verdict["fail"] and all(verdict["journeys"].values())
    (WORK / "verdict.json").write_text(json.dumps(verdict, ensure_ascii=False),
                                       encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


@pytest.mark.timeout(1200)
def test_g47_dorling_cartogram_pi_journey(tmp_path):
    driver = tmp_path / "g47_pi_journey_driver.py"
    driver.write_text(_DRIVER_SOURCE, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(driver), str(REPO_ROOT)],
        capture_output=True, text=True, timeout=1200,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        cwd=str(REPO_ROOT),
    )
    verdict = None
    for line in reversed(proc.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                verdict = json.loads(line)
                break
            except ValueError:
                continue
    assert verdict is not None, (
        f"journey driver produced no verdict (rc={proc.returncode})\n"
        f"stdout tail: {proc.stdout[-3000:]}\nstderr tail: {proc.stderr[-3000:]}")
    print("G47_PI_JOURNEY_VERDICT " + json.dumps(
        {"fail": verdict.get("fail"), "journeys": verdict.get("journeys"),
         "observations": verdict.get("observations")},
        ensure_ascii=False, sort_keys=True))
    assert proc.returncode == 0, (
        f"journey failed: {verdict.get('fail')}\n"
        f"traceback: {(verdict.get('observations') or {}).get('traceback')}\n"
        f"stderr tail: {proc.stderr[-2000:]}")
    for gate, ok in verdict["journeys"].items():
        assert ok is True, f"Pi journey gate {gate} failed: {verdict['fail']}"
