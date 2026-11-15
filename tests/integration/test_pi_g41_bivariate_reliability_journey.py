"""G41 真实 Pi 旅程（WP11 / B34 / B35）。

架构与 G29–G35 旅程同款：pytest 只做门控与裁决；driver 是独立 Python
子进程（避免 conftest 预导入污染 app 单例）。链：

    stub LLM SSE（node 子进程）
      → uvicorn(app.main:app)（生产 lifespan 拉起固定 pin 的 Pi RPC 子进程）
      → /api/v1/chat/stream 生产 HTTP 链
      → tool_search deferred 激活
      → webgis_bivariate_reliability_{inspect,preview,apply}
      → ToolDispatchService / ToolRegistry 生产 dispatch
      → bivariate_reliability_authoring.service
      → 单笔 UpsertLayerIntent（CAS/checkpoint/undo）
      → 冻结派生展示 ref 与 canonical 图例（有界回执）

只有 LLM 响应是 stub；RPC/dispatch/写入/渲染主链都不 mock。两个核心模式
各跑一遍完整旅程，并独立回读派生 ref、paint、图例与证据块。

门控：PI_JOURNEY_E2E=1 + node 可用 + 固定 pin 的 vendor/pi RPC entry 已构建
（显式 skip，绝不 silent-green）。不等线上 CI。
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
"""G41 journey driver：stub LLM + uvicorn(app) + 双模式作者旅程 → VERDICT json。"""
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
WORK = Path(tempfile.mkdtemp(prefix="pi-g41-"))
sys.path.insert(0, str(REPO_ROOT))

import uuid

RUN = "g41" + uuid.uuid4().hex[:8]
LAYER_ID = "result-journey"
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

BIV_PLAN = {
    "mode": "bivariate_matrix",
    "bivariate": {
        "n": 3,
        "axis_a": {"field": "income", "unit": "元", "method": "quantiles"},
        "axis_b": {"field": "rate", "unit": "%", "method": "equal_interval"},
        "matrix_id": "BiPurpleOrange",
        "boundary_rule": "upper_inclusive",
    },
}
REL_PLAN = {
    "mode": "reliability_dual_channel",
    "reliability": {
        "estimate_field": "estimate", "estimate_unit": "m", "estimate_k": 4,
        "measure": {
            "kind": "standard_error", "value_field": "se", "unit": "m",
            "source": "author_declared",
            "method_note": "旅程声明：仪器给出的逐要素标准误（未经独立验证）",
        },
        "visual": {
            "channel": "opacity", "min_value": 0, "max_value": 2,
            "min_output": 0.25, "max_output": 0.9,
            "unknown_output": 0.5, "buckets": 4, "clip": "clamp",
        },
    },
}


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    s.close()
    return port


async def _seed_session(session_id: str) -> dict:
    """服务端种子：完整 GeoJSON → ref → 真实 UpsertLayerIntent；并预取
    两个模式的确定性 candidate_digest（与 Pi preview 服务同源、同代）。"""
    import app.services.mapspec.store as store_module
    store_module.BASE_STORAGE_DIR = WORK / "mapspec_store"
    store_module.BASE_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    features = []
    for i in range(12):
        base_lon = 116.0 + i * 0.05
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[
                [base_lon, 39.0], [base_lon + 0.04, 39.0],
                [base_lon + 0.04, 39.04], [base_lon, 39.04], [base_lon, 39.0],
            ]]},
            "properties": {
                "income": float(i * 10), "rate": float(i % 3),
                "estimate": float(i),
                "se": 0.0 if i == 0 else float(i % 4) * 0.5,
            },
        })
    fc = {"type": "FeatureCollection", "features": features}

    from app.services.session_data import session_data_manager as sdm
    ref = await sdm.store(session_id, {"kind": "geojson", "geojson": fc},
                          prefix="result")
    from app.lib.cartography.thematic_spec import build_graduated_spec, spec_to_paint
    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.intents import UpsertLayerIntent

    legend = build_graduated_spec(fc, field="income")
    paint, _ = spec_to_paint(legend)
    result = await apply_gis_mutation(
        session_id,
        UpsertLayerIntent(layer={
            "id": LAYER_ID, "type": "fill", "source": f"{LAYER_ID}-source",
            "paint": {"color": paint}, "legend_spec": legend,
            "data_binding": {"field": "income", "field_type": "number"},
        }, source_data={"ref_id": str(ref)}),
        origin="agent", actor="journey:seed")
    assert not result.is_error, result.error_msg

    from app.services.bivariate_reliability_authoring import (
        inspect_layer, parse_plan, preview_expression,
    )
    ins = await inspect_layer(session_id, LAYER_ID, trusted_dispatch=True)
    anchor = {
        "ref_id": ins["source_anchor"]["ref_id"],
        "content_revision": ins["source_anchor"]["content_revision"],
        "content_fingerprint": ins["source_anchor"]["content_fingerprint"],
    }
    biv_plan = parse_plan({**BIV_PLAN, "layer_id": LAYER_ID,
                           "source_anchor": anchor})
    rel_plan = parse_plan({**REL_PLAN, "layer_id": LAYER_ID,
                           "source_anchor": anchor})
    biv_prev = await preview_expression(
        session_id, LAYER_ID, biv_plan, trusted_dispatch=True)
    rel_prev = await preview_expression(
        session_id, LAYER_ID, rel_plan, trusted_dispatch=True)
    rev = int(await sdm.get_state_field(
        session_id, "_cartographic_mutation_revision") or 0)
    return {
        "rev": rev, "base_ref": str(ref),
        "biv_digest": biv_prev["candidate_digest"],
        "rel_digest": rel_prev["candidate_digest"],
        "biv_pairs": biv_prev["summary"]["valid_pairs"],
        "rel_valid": rel_prev["summary"]["valid_measure"],
        "anchor": anchor,
    }


async def _read_layer(session_id: str):
    from app.services.mapspec_store import mapspec_store
    spec = await mapspec_store.get_mapspec(session_id) or {}
    for layer in spec.get("layers") or []:
        if layer.get("id") == LAYER_ID:
            return layer, spec
    return None, spec


def main() -> int:
    def _build_turn_scripts(seed: dict) -> dict:
        rev = seed["rev"]
        return {
            "warmup": [{"text": "warmup-ok"}],
            "查看这个矢量层能做双变量吗": [
                {"tool": "tool_search", "args": {"query": "双变量 可靠性 成对 矩阵 图例 作者"}},
                {"tool": "webgis_bivariate_reliability_inspect",
                 "args": {"layer_id": LAYER_ID}},
                {"text": "已查看源版本与字段。"},
            ],
            "预览收入与比率的双变量矩阵": [
                {"tool": "tool_search", "args": {"query": "双变量 预览 矩阵 联合计数"}},
                {"tool": "webgis_bivariate_reliability_preview",
                 "args": {"layer_id": LAYER_ID, "plan": BIV_PLAN}},
                {"text": "矩阵预览已完成。"},
            ],
            "应用这个双变量矩阵": [
                {"tool": "tool_search", "args": {"query": "双变量 应用 提交 MapSpec 矩阵"}},
                {"tool": "webgis_bivariate_reliability_apply", "args": {
                    "layer_id": LAYER_ID, "plan": BIV_PLAN,
                    "expected_revision": rev,
                    "preview_digest": seed["biv_digest"],
                }},
                {"text": "双变量图已落图。"},
            ],
            "预览估计值与标准误的双通道表达": [
                {"tool": "tool_search", "args": {"query": "可靠性 误差 标准误 预览 双通道"}},
                {"tool": "webgis_bivariate_reliability_preview",
                 "args": {"layer_id": LAYER_ID, "plan": REL_PLAN}},
                {"text": "可靠性预览已完成。"},
            ],
            "应用估计值加误差的双通道图": [
                {"tool": "tool_search", "args": {"query": "可靠性 应用 提交 误差 透明度"}},
                {"tool": "webgis_bivariate_reliability_apply", "args": {
                    "layer_id": LAYER_ID, "plan": REL_PLAN,
                    "expected_revision": rev + 1,
                    "preview_digest": seed["rel_digest"],
                }},
                {"text": "可靠性双通道图已落图。"},
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
    os.environ["LLM_API_KEY"] = "stub-g41-key"
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

    verdict: dict = {"journeys": {}, "fail": []}
    owner_token = None

    def chat(message: str) -> str:
        nonlocal owner_token
        headers = {"Content-Type": "application/json"}
        if owner_token:
            headers["X-Session-Token"] = owner_token
        req = urllib.request.Request(
            f"http://127.0.0.1:{api_port}/api/v1/chat/stream",
            data=json.dumps({"message": message, "session_id": session_id}).encode(),
            headers=headers)
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
        verdict["seeded_revision"] = seed["rev"]
        verdict["base_ref"] = seed["base_ref"]
        (WORK / "journey_script.json").write_text(
            json.dumps(_build_turn_scripts(seed)), encoding="utf-8")

        warm = chat("warmup")
        if "task_error" in warm:
            warm = chat("warmup")
        verdict["journeys"]["warmup_ok"] = "task_complete" in warm
        (WORK / "sse_warm.log").write_text(warm, encoding="utf-8")

        b1 = chat("查看这个矢量层能做双变量吗")
        (WORK / "sse_inspect.log").write_text(b1, encoding="utf-8")
        verdict["journeys"]["bivariate_inspect_via_pi"] = (
            "webgis_bivariate_reliability_inspect" in b1)

        b2 = chat("预览收入与比率的双变量矩阵")
        (WORK / "sse_biv_preview.log").write_text(b2, encoding="utf-8")
        verdict["journeys"]["bivariate_preview_via_pi"] = (
            "webgis_bivariate_reliability_preview" in b2)
        # 指纹一致性：调**生产 ToolRegistry 中注册的同一预览工具函数**，
        # 与 Pi apply 使用的确定性 digest 逐字比对（不靠宽松回退）。
        # 注意：这里是 registry 工具直调，不是 ToolDispatchService.dispatch；
        # 后者已由上面的 Pi 链覆盖，措辞不互相冒充。
        verdict.setdefault("observations", {})["bivariate_digest_in_sse"] = (
            seed["biv_digest"] in b2)
        from app.tools.registry import ToolRegistry
        from app.tools.bivariate_reliability_tools import (
            register_bivariate_reliability_tools,
        )
        reg = ToolRegistry()
        register_bivariate_reliability_tools(reg)
        dispatched = asyncio.run(reg._tools[
            "webgis_bivariate_reliability_preview"](
                layer_id=LAYER_ID, plan=BIV_PLAN, session_id=session_id))
        verdict["journeys"]["bivariate_preview_digest_via_registry_tool"] = (
            dispatched.get("success") is True
            and dispatched.get("candidate_digest") == seed["biv_digest"]
            and seed["biv_pairs"] > 0)

        b3 = chat("应用这个双变量矩阵")
        (WORK / "sse_biv_apply.log").write_text(b3, encoding="utf-8")
        verdict["journeys"]["bivariate_apply_via_pi"] = (
            "webgis_bivariate_reliability_apply" in b3)
        layer_b, spec_b = asyncio.run(_read_layer(session_id))
        if layer_b is None:
            verdict["fail"].append("layer missing after bivariate apply")
        else:
            legend = layer_b.get("legend_spec") or {}
            verdict["journeys"]["bivariate_map_committed"] = (
                legend.get("type") == "bivariate"
                and layer_b.get("paint", {}).get("color", {}).get("method") == "match"
                and layer_b.get("bivariate", {}).get("matrix") == 3)
            cells = legend.get("cells") or []
            counts = legend.get("counts") or {}
            verdict["journeys"]["bivariate_joint_counts_real"] = (
                len(cells) == 9
                and sum(c.get("count", 0) for c in cells) == counts.get("valid_pairs")
                == seed["biv_pairs"])
            ev = layer_b.get("bivariate_reliability_authoring") or {}
            verdict["journeys"]["bivariate_evidence_agent"] = (
                ev.get("decided_by") == "agent"
                and ev.get("mode") == "bivariate_matrix"
                and ev.get("base_source_ref") == seed["base_ref"]
                and ev.get("base_content_revision") == seed["anchor"]["content_revision"])
            derived_ref = (spec_b.get("sources") or {}).get(
                f"{LAYER_ID}-source", {}).get("ref_id")
            verdict["journeys"]["bivariate_derived_ref"] = bool(
                derived_ref and str(derived_ref).startswith("ref:g41display-"))
            if derived_ref:
                from app.services.session_data import session_data_manager as sdm
                payload = asyncio.run(sdm.get(session_id, str(derived_ref)))
                feats = (payload or {}).get("geojson", {}).get("features") or []
                verdict["journeys"]["bivariate_derived_readback"] = (
                    len(feats) == 12
                    and all("__g41_pair_class" in f["properties"] for f in feats)
                    and (payload or {}).get("geojson", {}).get("metadata", {})
                        .get("base_source_ref") == seed["base_ref"])
                base_payload = asyncio.run(sdm.get(session_id, seed["base_ref"]))
                base_feats = (base_payload or {}).get("geojson", {}).get("features") or []
                verdict["journeys"]["bivariate_source_not_written_back"] = (
                    all("__g41_pair_class" not in f["properties"]
                        for f in base_feats))

        r1 = chat("预览估计值与标准误的双通道表达")
        (WORK / "sse_rel_preview.log").write_text(r1, encoding="utf-8")
        verdict["journeys"]["reliability_preview_via_pi"] = (
            "webgis_bivariate_reliability_preview" in r1)

        r2 = chat("应用估计值加误差的双通道图")
        (WORK / "sse_rel_apply.log").write_text(r2, encoding="utf-8")
        verdict["journeys"]["reliability_apply_via_pi"] = (
            "webgis_bivariate_reliability_apply" in r2)
        layer_r, spec_r = asyncio.run(_read_layer(session_id))
        if layer_r is None:
            verdict["fail"].append("layer missing after reliability apply")
        else:
            legend = layer_r.get("legend_spec") or {}
            paint = layer_r.get("paint") or {}
            verdict["journeys"]["reliability_map_committed"] = (
                legend.get("type") == "reliability_dual_channel"
                and paint.get("color", {}).get("method") == "step"
                and paint.get("opacity", {}).get("method") == "match"
                and "bivariate" not in layer_r)
            measure = legend.get("measure") or {}
            verdict["journeys"]["reliability_no_fabricated_confidence"] = (
                measure.get("kind") == "standard_error"
                and measure.get("author_declared") is True
                and measure.get("level") is None
                and "平方单位" not in str(measure.get("unit_note")))
            verdict["journeys"]["reliability_missing_bucket"] = any(
                b.get("key") == "unknown" for b in (legend.get("buckets") or []))
            ev = layer_r.get("bivariate_reliability_authoring") or {}
            verdict["journeys"]["reliability_evidence_agent"] = (
                ev.get("decided_by") == "agent"
                and ev.get("mode") == "reliability_dual_channel"
                and ev.get("base_source_ref") == seed["base_ref"])
            derived_ref = (spec_r.get("sources") or {}).get(
                f"{LAYER_ID}-source", {}).get("ref_id")
            if derived_ref and owner_token:
                from app.services.session_data import session_data_manager as sdm
                shared = asyncio.run(sdm.get_ref_data_shared(
                    session_id, str(derived_ref), owner_token=owner_token))
                verdict["journeys"]["reliability_authorized_download_readback"] = bool(
                    getattr(shared, "success", False)
                    and isinstance(getattr(shared, "data", None), dict))
            else:
                verdict["journeys"]["reliability_authorized_download_readback"] = False

        required = (
            "warmup_ok", "bivariate_inspect_via_pi", "bivariate_preview_via_pi",
            "bivariate_preview_digest_via_registry_tool", "bivariate_apply_via_pi",
            "bivariate_map_committed", "bivariate_joint_counts_real",
            "bivariate_evidence_agent", "bivariate_derived_ref",
            "bivariate_derived_readback", "bivariate_source_not_written_back",
            "reliability_preview_via_pi", "reliability_apply_via_pi",
            "reliability_map_committed", "reliability_no_fabricated_confidence",
            "reliability_missing_bucket", "reliability_evidence_agent",
            "reliability_authorized_download_readback",
        )
        for key in required:
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
def test_g41_bivariate_reliability_pi_journey(tmp_path):
    driver = tmp_path / "g41_pi_journey_driver.py"
    driver.write_text(_DRIVER_SOURCE, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(driver), str(REPO_ROOT)],
        capture_output=True, text=True, timeout=900,
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
    # 证据化输出：每个 gate 的真实取值随 pytest -s 落在测试日志里。
    print("G41_PI_JOURNEY_VERDICT " + json.dumps(
        {"fail": verdict.get("fail"), "journeys": verdict.get("journeys"),
         "observations": verdict.get("observations")}, sort_keys=True))
    assert proc.returncode == 0, (
        f"journey failed: {verdict.get('fail')}\n"
        f"stderr tail: {proc.stderr[-2000:]}")
    for gate, ok in verdict["journeys"].items():
        assert ok is True, f"Pi journey gate {gate} failed: {verdict['fail']}"
