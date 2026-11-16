"""G45 旅程：真实 Pi 子进程 + stub LLM → webgis_coordop_* → 冻结操作全量派生
重投影 → 权威收据/文件 → WGS84 显示副本 → G18 GPKG 保持 CRS 交付读回。

与 test_pi_g38_data_join_journey.py 同架构（独立 driver 进程：node stub LLM →
uvicorn 生产 app（真实 vendor/pi bridge）→ HTTP 旅程），聚焦 WP11/C32：

J-G45：
  driver 服务端种子（1201 点 WGS84 FC，权威 CRS 证据 + 中文/null/大整数属性）
  → 「检查重投影源」turn → tool_search + webgis_coordop_describe_source
  → 「查询目标 CRS」turn → webgis_coordop_crs_lookup
  → 「列出候选操作」turn → webgis_coordop_list_operations（含环境证据）
  → 「建立坐标操作计划」turn → webgis_coordop_plan_upsert
  → 「锁定最佳操作」turn → webgis_coordop_lock（服务器签发 candidate_id）
  → 「预览重投影」turn → webgis_coordop_preview（before/after 坐标对）
  → 「执行重投影」turn → webgis_coordop_execute(background=false) + status
  → 「读取结果第一页」turn → webgis_coordop_result
  → 裁决（读权威事实，不读 SSE 文本）：
      DB run 行 completed + 收据 1201 行/UTM CRS/操作 manifest；
      输出文件 1201 行 + 解析 oracle 坐标对账；显示 ref 4326 度范围；
      G18 真实 GPKG 交付（保持 CRS、无再次选操作）回读行数/坐标/ID 一致。

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
"""G45 journey driver：stub LLM + uvicorn(app) + 坐标操作旅程 → VERDICT json。"""
from __future__ import annotations

import asyncio
import json
import math
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

REPO = sys.argv[1]
WORK = Path(tempfile.mkdtemp(prefix="g45-journey-"))

N_FEATURES = 1201  # C20：超过预览行数的完整成果


def _utm50n_analytic(lon, lat):
    """独立解析 oracle（Redfearn 4 阶；与生产 pyproj 实现无关）。"""
    a, f = 6378137.0, 1 / 298.257223563
    k0 = 0.9996
    e2 = 2 * f - f * f
    ep2 = e2 / (1 - e2)
    lam = math.radians(lon - 117.0)
    phi = math.radians(lat)
    N = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    T = math.tan(phi) ** 2
    C = ep2 * math.cos(phi) ** 2
    A = math.cos(phi) * lam
    M = a * ((1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * phi
             - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024)
             * math.sin(2 * phi)
             + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024)
             * math.sin(4 * phi)
             - (35 * e2 ** 3 / 3072) * math.sin(6 * phi))
    x = k0 * N * (A + (1 - T + C) * A ** 3 / 6
                  + (5 - 18 * T + T ** 2 + 72 * C - 58 * ep2) * A ** 5 / 120
                  ) + 500000.0
    y = k0 * (M + N * math.tan(phi) * (
        A ** 2 / 2 + (5 - T + 9 * C + 4 * C ** 2) * A ** 4 / 24
        + (61 - 58 * T + T ** 2 + 600 * C - 330 * ep2) * A ** 6 / 720))
    return x, y


def _seed_payload():
    feats = []
    for i in range(N_FEATURES):
        lon = 116.0 + (i % 37) * 0.013
        lat = 39.9 + (i % 23) * 0.007
        feats.append({
            "type": "Feature",
            "properties": {"_wfid": f"g45-{i:05d}",
                           "name": f"要素{i}·中文",
                           "big_int": 98765432101234 + i,
                           "maybe_null": None if i % 3 == 0 else i / 7.0},
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
        })
    # 空几何 + null 几何各 1（C18：独立计数，不静默丢行）→ 变换行 1199
    feats[-2]["geometry"] = None
    feats[-1]["geometry"] = {"type": "Point", "coordinates": []}
    return {"type": "FeatureCollection", "features": feats,
            "properties": {"schema_info": {"crs": "EPSG:4326"}}}


async def _seed_session(session_id: str):
    from app.core.auth import create_access_token
    from app.core.database import SessionLocal
    from app.models.db_model import User
    from app.services.session_data import session_data_manager

    with SessionLocal() as db:
        if db.get(User, "g45-journey-user") is None:
            db.add(User(id="g45-journey-user", username="g45journey",
                        email="g45journey@example.com", role="editor",
                        is_active=True))
            db.commit()
    fc = _seed_payload()
    ref = await session_data_manager.store(session_id, fc, prefix="g45fc")
    jwt = create_access_token({"sub": "g45-journey-user", "role": "user"})
    return ref, jwt


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
    port = s.sockname[1] if False else s.getsockname()[1]
    s.close()
    return port


def main() -> int:
    sys.path.insert(0, REPO)
    os.environ["DATABASE_URL"] = "sqlite:///" + str(WORK / "journey.db")
    os.environ["DATA_DIR"] = str(WORK / "data")
    Path(WORK / "data").mkdir(parents=True, exist_ok=True)

    stub_js = WORK / "journey_stub.mjs"
    stub_js.write_text(STUB_JS, encoding="utf-8")
    (WORK / "journey_script.json").write_text("{}", encoding="utf-8")

    with open(WORK / "stub_err.log", "w") as fh:
        stub = subprocess.Popen(
            ["node", str(stub_js)], stdout=subprocess.DEVNULL, stderr=fh,
            env={**os.environ,
                 "JOURNEY_SCRIPT": str(WORK / "journey_script.json")})
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
    os.environ["LLM_API_KEY"] = "stub-g45-key"
    os.environ["LLM_MODEL"] = "stub-model"
    os.environ["PI_AUTOSTART"] = "true"

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
            server.started and try_get_chat_engine() is not None):
        time.sleep(0.5)
    if not (server.started and try_get_chat_engine() is not None):
        stub.terminate()
        print(json.dumps({"fail": ["app did not start"]}))
        return 1

    verdict: dict = {"journeys": {}, "fail": []}
    owner_token = None
    jwt = ""
    RUN = "g45journey"

    def chat(message: str) -> str:
        nonlocal owner_token
        headers = {"Content-Type": "application/json",
                   "Authorization": "Bearer " + jwt}
        if owner_token:
            headers["X-Session-Token"] = owner_token
        req = urllib.request.Request(
            "http://127.0.0.1:{}/api/v1/chat/stream".format(api_port),
            data=json.dumps({"message": message,
                             "session_id": RUN + "-s1"}).encode(),
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

    def api(method: str, path: str, payload=None):
        headers = {"Content-Type": "application/json",
                   "Authorization": "Bearer " + jwt}
        if owner_token:
            headers["X-Session-Token"] = owner_token
        req = urllib.request.Request(
            "http://127.0.0.1:{}{}".format(api_port, path),
            data=json.dumps(payload).encode() if payload is not None else None,
            headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        session_id = RUN + "-s1"
        src_ref, jwt = asyncio.run(_seed_session(session_id))
        verdict["seeded"] = [src_ref]

        # driver 侧预计算（脚本注入用）：候选 id 与期望 revision 由权威服务
        # 计算后写入 stub 脚本；工具调用本身仍走真实 Pi → dispatch 链。
        from app.services.coordinate_operations import candidates as cand

        listing = cand.list_operations("EPSG:4326", "EPSG:32650", None)
        avail = [c for c in listing["candidates"]
                 if c["availability"] == "available"]
        candidate_id = avail[0]["candidate_id"]
        verdict["journeys"]["candidates_available"] = len(avail) >= 1

        def src():
            return {"kind": "session_ref", "session_id": session_id,
                    "ref": src_ref}

        scripts = {
            "warmup": [{"text": "warmup-ok"}],
            "检查重投影源": [
                {"tool": "tool_search",
                 "args": {"query": "坐标 操作 重投影 CRS 源"}},
                {"tool": "webgis_coordop_describe_source",
                 "args": {"source": src()}},
                {"text": "源已检查。"},
            ],
            "查询目标CRS": [
                {"tool": "tool_search",
                 "args": {"query": "CRS 查询 轴序 单位 维度"}},
                {"tool": "webgis_coordop_crs_lookup",
                 "args": {"query": "EPSG:32650"}},
                {"text": "目标 CRS 已确认。"},
            ],
            "列出候选操作": [
                {"tool": "tool_search",
                 "args": {"query": "坐标 操作 候选 操作 锁定"}},
                {"tool": "webgis_coordop_list_operations",
                 "args": {"source": src(), "target_crs": "EPSG:32650"}},
                {"text": "候选已列出。"},
            ],
            "建立坐标操作计划": [
                {"tool": "tool_search",
                 "args": {"query": "坐标 操作 计划 plan 建立 草稿"}},
                {"tool": "webgis_coordop_plan_upsert", "args": {"plan": {
                    "title": "北京点集 → UTM 50N",
                    "source": src(),
                    "target_crs": "EPSG:32650",
                    "z_policy": "preserve_unconverted",
                    "empty_geometry_policy": "keep",
                    "out_of_area_policy": "report",
                }}},
                {"text": "计划已建立。"},
            ],
            "锁定最佳操作": [
                {"tool": "tool_search",
                 "args": {"query": "坐标 操作 锁定 candidate 冻结"}},
                {"tool": "webgis_coordop_lock", "args": {
                    "plan_id": "PLAN_ID", "candidate_id": candidate_id,
                    "expected_revision": 1}},
                {"text": "操作已锁定。"},
            ],
            "预览重投影": [
                {"tool": "tool_search",
                 "args": {"query": "坐标 操作 预览 样本 精度"}},
                {"tool": "webgis_coordop_preview", "args": {
                    "plan_id": "PLAN_ID", "expected_revision": "REV2",
                    "sample_rows": 10}},
                {"text": "预览完成。"},
            ],
            "执行重投影": [
                {"tool": "tool_search",
                 "args": {"query": "坐标 操作 执行 重投影 等待"}},
                {"tool": "webgis_coordop_execute", "args": {
                    "plan_id": "PLAN_ID", "expected_revision": "REV2",
                    "background": False}},
                {"text": "执行完成。"},
            ],
            "读取结果第一页": [
                {"tool": "tool_search",
                 "args": {"query": "坐标 操作 结果 分页"}},
                {"tool": "webgis_coordop_result", "args": {
                    "run_id": "RUN_ID", "cursor": 0, "limit": 20}},
                {"text": "结果已读取。"},
            ],
        }
        (WORK / "journey_script.json").write_text(
            json.dumps(scripts), encoding="utf-8")

        warm = chat("warmup")
        if "task_error" in warm:
            warm = chat("warmup")
        verdict["journeys"]["warmup_ok"] = "task_complete" in warm

        body1 = chat("检查重投影源")
        if "task_error" in body1:
            body1 = chat("检查重投影源")
        (WORK / "sse1.log").write_text(body1, encoding="utf-8")
        _flat1 = body1.replace(" ", "")
        verdict["journeys"]["describe_receipt"] = (
            "webgis_coordop_describe_source" in body1
            and '"crs":"EPSG:4326"'.replace(" ", "") in _flat1)

        body1b = chat("查询目标CRS")
        if "task_error" in body1b:
            body1b = chat("查询目标CRS")
        verdict["journeys"]["crs_lookup_receipt"] = (
            "EPSG:32650" in body1b and "axis_official" in body1b)

        body1c = chat("列出候选操作")
        if "task_error" in body1c:
            body1c = chat("列出候选操作")
        (WORK / "sse1c.log").write_text(body1c, encoding="utf-8")
        verdict["journeys"]["list_operations_receipt"] = (
            "webgis_coordop_list_operations" in body1c
            and '"environment"'.replace(" ", "") in body1c.replace(" ", ""))

        body2 = chat("建立坐标操作计划")
        if "task_error" in body2:
            body2 = chat("建立坐标操作计划")
        (WORK / "sse2.log").write_text(body2, encoding="utf-8")
        verdict["journeys"]["plan_upsert_receipt"] = (
            "webgis_coordop_plan_upsert" in body2)

        # 从权威 DB 读 plan_id 注入后续脚本
        from app.core.database import SessionLocal
        from app.models.coordinate_operations import (
            CoordinateOperationPlan,
            CoordinateOperationRun,
        )

        plan_id = None
        plan_revision = None
        with SessionLocal() as db:
            row = db.query(CoordinateOperationPlan).filter(
                CoordinateOperationPlan.session_id == session_id).first()
            plan_id = row.id if row else None
            plan_revision = row.revision if row else None
        verdict["journeys"]["plan_created_via_tool"] = plan_id is not None
        if plan_id:
            # 注入真实 plan_id（锁定 turn 之前 —— 锁定调用本身需要它）
            for v in scripts.values():
                for step in v:
                    args = step.get("args")
                    if isinstance(args, dict) \
                            and args.get("plan_id") in ("PLAN_ID", plan_id):
                        args["plan_id"] = plan_id
            (WORK / "journey_script.json").write_text(
                json.dumps(scripts), encoding="utf-8")

        body3 = chat("锁定最佳操作")
        if "task_error" in body3:
            body3 = chat("锁定最佳操作")
        (WORK / "sse3.log").write_text(body3, encoding="utf-8")
        with SessionLocal() as db:
            row = db.query(CoordinateOperationPlan).filter(
                CoordinateOperationPlan.id == plan_id).first()
            locked_ok = (row is not None and row.status == "locked"
                         and row.operation is not None
                         and row.selected_candidate_id == candidate_id)
            locked_revision = row.revision if row else None
        verdict["journeys"]["lock_frozen_in_db"] = locked_ok
        if locked_ok:
            for v in scripts.values():
                for step in v:
                    args = step.get("args")
                    if isinstance(args, dict):
                        if args.get("plan_id") in ("PLAN_ID", plan_id):
                            args["plan_id"] = plan_id
                            if args.get("expected_revision") == "REV2":
                                args["expected_revision"] = locked_revision
            (WORK / "journey_script.json").write_text(
                json.dumps(scripts), encoding="utf-8")

        body4 = chat("预览重投影")
        if "task_error" in body4:
            body4 = chat("预览重投影")
        (WORK / "sse4.log").write_text(body4, encoding="utf-8")
        _flat4 = body4.replace(" ", "")
        verdict["journeys"]["preview_pairs"] = (
            "webgis_coordop_preview" in body4
            and '"before"' in _flat4 and '"after"' in _flat4)

        body5 = chat("执行重投影")
        if "task_error" in body5:
            body5 = chat("执行重投影")
        (WORK / "sse5.log").write_text(body5, encoding="utf-8")
        with SessionLocal() as db:
            run_row = db.query(CoordinateOperationRun).filter(
                CoordinateOperationRun.session_id == session_id
            ).order_by(CoordinateOperationRun.created_at.desc()).first()
            run_ok = run_row is not None and run_row.status == "completed"
            receipt = (run_row.receipt or {}) if run_row else {}
            verdict["journeys"]["run_completed_db"] = run_ok
            verdict["journeys"]["receipt_counts"] = (
                receipt.get("rows_transformed") == 1199
                and receipt.get("rows_total") == 1201
                and receipt.get("null_geometry") == 1
                and receipt.get("empty_geometry") == 1)
            verdict["journeys"]["receipt_crs_op"] = (
                receipt.get("output_crs") == "EPSG:32650"
                and bool((receipt.get("operation") or {})
                         .get("definition_sha256")))
            verdict["journeys"]["display_copy"] = (
                (receipt.get("display") or {}).get("state") == "available")
            run_id = run_row.id if run_row else ""

        for v in scripts.values():
            for step in v:
                if isinstance(step.get("args"), dict) \
                        and "run_id" in step["args"]:
                    step["args"]["run_id"] = run_id
        (WORK / "journey_script.json").write_text(
            json.dumps(scripts), encoding="utf-8")

        body6 = chat("读取结果第一页")
        if "task_error" in body6:
            body6 = chat("读取结果第一页")
        (WORK / "sse6.log").write_text(body6, encoding="utf-8")
        verdict["journeys"]["result_page_full"] = (
            '"total_rows":1201' in body6.replace(" ", ""))

        # ── 权威裁决（读文件/DB，不读 SSE） ──────────────────────────
        from app.services.coordinate_operations import outputs as co_outputs

        fc = co_outputs.load_output_fc(run_id)
        feats = fc.get("features") or []
        verdict["journeys"]["output_file_rows"] = len(feats) == 1201
        f0 = feats[0]
        want = _utm50n_analytic(*f0["geometry"]["coordinates"][:2]
                                if False else (116.0, 39.9))
        x0, y0 = f0["geometry"]["coordinates"][0], f0["geometry"]["coordinates"][1]
        verdict["journeys"]["coordinate_oracle"] = (
            abs(x0 - want[0]) < 0.01 and abs(y0 - want[1]) < 0.01)
        verdict["journeys"]["property_fidelity"] = (
            f0["properties"].get("_wfid") == "g45-00000"
            and f0["properties"].get("name") == "要素0·中文"
            and f0["properties"].get("big_int") == 98765432101234)
        verdict["journeys"]["output_crs_declared"] = (
            fc.get("properties", {}).get("schema_info", {}).get("crs")
            == "EPSG:32650")
        # 空几何保留（不静默丢行）
        verdict["journeys"]["empty_geometry_kept"] = (
            feats[-1]["geometry"] == {"type": "Point", "coordinates": []}
            and feats[-2]["geometry"] is None)

        # 显示副本 ref 载荷核验（4326 度）
        display_ref = (receipt.get("display") or {}).get("display_ref")
        from app.services.session_data import session_data_manager

        dfc = asyncio.run(session_data_manager.get(session_id, display_ref)) \
            if display_ref else None
        d0 = (dfc["features"][0]["geometry"]["coordinates"]
              if dfc else [None, None])
        verdict["journeys"]["display_4326_degrees"] = bool(
            dfc and -180 <= d0[0] <= 180 and -90 <= d0[1] <= 90
            and abs(d0[0] - 116.0) < 1e-9 and abs(d0[1] - 39.9) < 1e-9)

        # ── G18 真实 GPKG 交付（保持 CRS；无再次选操作）→ 回读 ──────
        native_ref = (receipt.get("native_refs") or {}).get(
            "native_fabric_ref") or (receipt.get("native_refs") or {}).get(
                "output_session_ref")
        dl = api("POST", "/api/v1/data-delivery/jobs", {
            "spec": {"source": {
                "kind": "fabric_parquet" if "fabric" in str(native_ref)
                else "session_ref",
                "session_id": session_id, "ref": native_ref},
                "format": "gpkg", "filename": "g45-native"}})
        job_id = dl.get("job_id")
        dl_status = None
        for _ in range(180):
            view = api("GET", f"/api/v1/data-delivery/jobs/{job_id}")
            dl_status = view.get("status")
            if dl_status in ("completed", "failed", "cancelled"):
                break
            time.sleep(1.0)
        verdict["journeys"]["delivery_completed"] = dl_status == "completed"
        if dl_status == "completed":
            req = urllib.request.Request(
                "http://127.0.0.1:{}/api/v1/data-delivery/jobs/{}/download"
                .format(api_port, job_id),
                headers={"Authorization": "Bearer " + jwt})
            with urllib.request.urlopen(req, timeout=120) as resp:
                dl_bytes = resp.read()
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".gpkg",
                                             delete=False) as tf:
                tf.write(dl_bytes)
                gpkg = tf.name
            import pyogrio

            info = pyogrio.read_info(gpkg)
            gdf = pyogrio.read_dataframe(gpkg)
            verdict["journeys"]["gpkg_crs_kept"] = (
                "32650" in str(info["crs"]))
            verdict["journeys"]["gpkg_rows_match"] = len(gdf) == 1201
            verdict["journeys"]["gpkg_coord_identity"] = (
                abs(float(gdf.geometry.iloc[0].x) - x0) < 1e-6
                and str(gdf.iloc[0]["_wfid"]) == "g45-00000")

        for key, ok in verdict["journeys"].items():
            if not ok and key != "warmup_ok":
                verdict["fail"].append(f"journey gate failed: {key}")
    except Exception as exc:  # noqa: BLE001 — verdict 面必须完整
        verdict["fail"].append(f"driver exception: {type(exc).__name__}: {exc}")
    finally:
        stub.terminate()
        server.should_exit = True

    print(json.dumps(verdict))
    ok = not verdict["fail"] and all(verdict["journeys"].values())
    (WORK / "verdict.json").write_text(json.dumps(verdict), encoding="utf-8")
    if ok:
        import shutil as _shutil

        _shutil.rmtree(WORK, ignore_errors=True)
    else:
        print("WORK_DIR=" + str(WORK), file=sys.stderr)  # 失败保留证据
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
'''


def test_pi_g45_coordinate_operations_journey(tmp_path: Path):
    driver = tmp_path / "g45_driver.py"
    driver.write_text(_DRIVER_SOURCE, encoding="utf-8")
    env = {**os.environ,
           "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(driver), str(REPO_ROOT)],
        capture_output=True, text=True, timeout=900, env=env, cwd=str(REPO_ROOT))
    tail = (proc.stdout or "").strip().splitlines()[-1:] or [""]
    try:
        verdict = json.loads(tail[0])
    except ValueError:
        verdict = {"journeys": {}, "fail": [f"no verdict: {tail[0][:300]}",
                                           (proc.stderr or "")[-800:]]}
    assert proc.returncode == 0, json.dumps(verdict, ensure_ascii=False, indent=2)
    assert not verdict.get("fail"), json.dumps(
        verdict, ensure_ascii=False, indent=2)
    journeys = verdict.get("journeys", {})
    for gate in ("describe_receipt", "crs_lookup_receipt",
                 "list_operations_receipt", "plan_created_via_tool",
                 "lock_frozen_in_db", "preview_pairs", "run_completed_db",
                 "receipt_counts", "receipt_crs_op", "display_copy",
                 "result_page_full", "output_file_rows", "coordinate_oracle",
                 "property_fidelity", "output_crs_declared",
                 "empty_geometry_kept", "display_4326_degrees",
                 "delivery_completed", "gpkg_crs_kept", "gpkg_rows_match",
                 "gpkg_coord_identity"):
        assert journeys.get(gate) is True, {
            "gate": gate, "verdict": verdict}
