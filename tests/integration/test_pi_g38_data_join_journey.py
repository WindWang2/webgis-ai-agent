"""G38 旅程：真实 Pi 子进程 + stub LLM → webgis_data_join_* → 完整派生成果
→ 权威收据/文件 → G18 导出回读。

与 test_pi_g33_thematic_authoring_journey.py 同架构（独立 driver 进程：node
stub LLM → uvicorn 生产 app（真实 vendor/pi bridge）→ HTTP 旅程），聚焦
J15/J25（WP10/WP12）：

J-G38：
  driver 服务端种子（1100 面行政区 FC + 1100 行人口表 + 双侧各 1 未匹配行）
  → 用户「检查两个关联源」turn → webgis_data_join_describe ×2
  → 「建立关联计划并预览诊断」turn → tool_search + plan_upsert + preview
    （全量计数：1100 匹配对/1 左未匹配/1 右未匹配）
  → 「执行关联」turn → webgis_data_join_execute（有界等待 → completed 收据）
  → 「读取第一页结果」turn → webgis_data_join_result（total_rows=1101 全量）
  → 裁决（读权威事实，不读 SSE 文本）：
      DB run 行 completed + 收据计数对账；输出文件 1101 行 + 抽值 oracle；
      渲染 ref 1101 行；G18 真实交付（POST /data-delivery/jobs → 轮询 →
      下载回读）行数/CRS 与收据一致。

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
"""G38 journey driver：stub LLM + uvicorn(app) + 数据关联旅程 → VERDICT json。"""
from __future__ import annotations

import asyncio
import json
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
WORK = Path(tempfile.mkdtemp(prefix="g38-journey-"))

# ── 种子数据（人工可推导 oracle）──────────────────────────────────────
N_MATCHED = 1100          # J15：1000+ 匹配行经 Pi
POP_BASE = 10_000


def _seed_payloads():
    def square(x0, y0, x1, y1):
        return {"type": "Polygon", "coordinates": [
            [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}

    feats = []
    rows = []
    for i in range(N_MATCHED):
        adcode = f"1101{i:04d}"
        x = (i % 40) * 0.02
        y = (i // 40) * 0.02
        feats.append({
            "type": "Feature",
            "properties": {"_wfid": f"wf1-b{i}", "adcode": adcode,
                           "name": f"区{i}"},
            "geometry": square(x, y, x + 0.015, y + 0.015),
        })
        rows.append({"adcode": adcode, "pop": POP_BASE + i, "year": 2020})
    # 双侧各 1 未匹配（计数对账）：左 1 区无人口；右 1 行无边界
    feats.append({
        "type": "Feature",
        "properties": {"_wfid": "wf1-b-unmatched", "adcode": "999999",
                       "name": "无人口区"},
        "geometry": square(5.0, 5.0, 5.015, 5.015),
    })
    rows.append({"adcode": "888888", "pop": 1, "year": 2020})
    fc = {"type": "FeatureCollection", "crs": "EPSG:4326",
          "name": "admin", "features": feats}
    table = {"rows": rows}
    return fc, table


async def _seed_session(session_id: str):
    """服务端种子：用户 + JWT（G18 交付 create 需登录用户）+ FC/rows refs
    （与 G33 顺序纪律一致：server 就绪后 seed）。"""
    from app.core.database import SessionLocal
    from app.models.db_model import User
    from app.services.session_data import session_data_manager

    with SessionLocal() as db:
        if db.get(User, "g38-journey-user") is None:
            db.add(User(id="g38-journey-user", username="g38journey",
                        email="g38journey@example.com", role="editor",
                        is_active=True))
            db.commit()
    from app.core.auth import create_access_token

    fc, table = _seed_payloads()
    left_ref = await session_data_manager.store(session_id, fc, prefix="g38fc")
    right_ref = await session_data_manager.store(session_id, table, prefix="g38rows")
    jwt = create_access_token({"sub": "g38-journey-user", "role": "user"})
    return left_ref, right_ref, jwt


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


def main() -> int:
    sys.path.insert(0, REPO)
    # 隔离 DB/DATA_DIR（显式覆盖 —— 不依赖宿主 pytest 环境继承；
    # journey 的一切事实都应在本 WORK 内可核验）
    os.environ["DATABASE_URL"] = "sqlite:///" + str(WORK / "journey.db")
    os.environ["DATA_DIR"] = str(WORK / "data")
    Path(WORK / "data").mkdir(parents=True, exist_ok=True)

    def _build_turn_scripts(session_id: str, left_ref: str, right_ref: str) -> dict:
        src = lambda ref: {"kind": "session_ref", "session_id": session_id, "ref": ref}
        return {
        "warmup": [{"text": "warmup-ok"}],
        "检查两个关联源": [
            {"tool": "tool_search", "args": {"query": "数据 关联 join 源 检查"}},
            {"tool": "webgis_data_join_describe", "args": {"source": src(left_ref)}},
            {"tool": "webgis_data_join_describe", "args": {"source": src(right_ref)}},
            {"text": "两源已检查。"},
        ],
        "建立关联计划": [
            {"tool": "tool_search", "args": {"query": "关联 计划 字段 映射"}},
            {"tool": "webgis_data_join_plan_upsert", "args": {"plan": {
                "mode": "attribute",
                "title": "人口按区代码富集",
                "left": src(left_ref),
                "right": src(right_ref),
                "keys": [{"left_field": "adcode", "right_field": "adcode"}],
                "keep_side": "left",
                "output_cardinality": "many_to_one",
                "field_map": [
                    {"side": "left", "field": "adcode"},
                    {"side": "left", "field": "name"},
                    {"side": "right", "field": "pop"},
                    {"side": "right", "field": "year"},
                ],
                "output_name": "人口富集区",
            }}},
            {"text": "计划已建立。"},
        ],
        "预览全量诊断": [
            {"tool": "tool_search", "args": {"query": "关联 诊断 预览 基数"}},
            {"tool": "webgis_data_join_preview",
             "args": {"plan_id": "PLAN_ID", "expected_revision": 1}},
            {"text": "诊断完成。"},
        ],
        "执行关联": [
            {"tool": "tool_search", "args": {"query": "关联 执行 job 等待"}},
            {"tool": "webgis_data_join_execute",
             "args": {"plan_id": "PLAN_ID", "expected_revision": 1, "wait_max_s": 90}},
            {"text": "关联已执行。"},
        ],
        "读取第一页结果": [
            {"tool": "tool_search", "args": {"query": "关联 结果 分页 读取"}},
            {"tool": "webgis_data_join_result",
             "args": {"run_id": "RUN_ID", "cursor": 0, "limit": 20}},
            {"text": "结果已读取。"},
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
    os.environ["LLM_API_KEY"] = "stub-g38-key"
    os.environ["LLM_MODEL"] = "stub-model"
    os.environ["PI_AUTOSTART"] = "true"

    api_port = _free_port()
    # Pi 子进程经 HTTP 回调本进程的 tool dispatch 面（G33 同款）
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
    jwt = ""
    RUN = "g38journey"

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

    def api(method: str, path: str, payload=None):
        headers = {"Content-Type": "application/json",
                   "Authorization": "Bearer " + jwt}
        if owner_token:
            headers["X-Session-Token"] = owner_token
        req = urllib.request.Request(
            "http://127.0.0.1:{}{}".format(api_port, path),
            data=json.dumps(payload).encode() if payload is not None else None,
            headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=240) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        session_id = RUN + "-s1"
        left_ref, right_ref, jwt = asyncio.run(_seed_session(session_id))
        verdict["seeded"] = [left_ref, right_ref]

        scripts = _build_turn_scripts(session_id, left_ref, right_ref)

        # 取回 plan_id：先一次 upsert（真实 turn 之外的第一次会失败重试机制
        # 不可靠——改为：第一个真实 turn 完成后从 DB 读 plan_id 注入脚本）。
        (WORK / "journey_script.json").write_text(json.dumps(scripts), encoding="utf-8")

        warm = chat("warmup")
        if "task_error" in warm:
            warm = chat("warmup")
        verdict["journeys"]["warmup_ok"] = "task_complete" in warm

        body1 = chat("检查两个关联源")
        if "task_error" in body1:
            body1 = chat("检查两个关联源")
        (WORK / "sse1.log").write_text(body1, encoding="utf-8")
        _flat1 = body1.replace(" ", "")
        verdict["journeys"]["describe_receipt"] = (
            "webgis_data_join_describe" in body1
            and '"row_count":1101' in _flat1)

        body2 = chat("建立关联计划")
        if "task_error" in body2:
            body2 = chat("建立关联计划")
        (WORK / "sse2.log").write_text(body2, encoding="utf-8")
        verdict["journeys"]["plan_upsert_receipt"] = (
            "webgis_data_join_plan_upsert" in body2 and '"revision":1' in body2.replace(" ", ""))

        # 计划已由工具创建 —— 从权威 DB 读 plan_id 注入后续 turn 脚本
        from app.core.database import SessionLocal
        from app.models.data_join_authoring import DataJoinPlan, DataJoinRun

        plan_id = None
        with SessionLocal() as db:
            plan_row = db.query(DataJoinPlan).filter(
                DataJoinPlan.session_id == session_id).first()
            plan_id = plan_row.id if plan_row else None
        verdict["journeys"]["plan_created_via_tool"] = plan_id is not None
        if plan_id:
            for v in scripts.values():
                for step in v:
                    args = step.get("args")
                    if isinstance(args, dict) and args.get("plan_id") == "PLAN_ID":
                        args["plan_id"] = plan_id
            (WORK / "journey_script.json").write_text(
                json.dumps(scripts), encoding="utf-8")

        body2b = chat("预览全量诊断")
        if "task_error" in body2b:
            body2b = chat("预览全量诊断")
        (WORK / "sse2b.log").write_text(body2b, encoding="utf-8")
        _flat2b = body2b.replace(" ", "")
        verdict["journeys"]["preview_full_counts"] = (
            '"matched_pairs":1100' in _flat2b
            and '"unmatched_left_rows":1' in _flat2b
            and '"unmatched_right_rows":1' in _flat2b
            and '"basis":"full"' in _flat2b)

        body3 = chat("执行关联")
        if "task_error" in body3:
            body3 = chat("执行关联")
        (WORK / "sse3.log").write_text(body3, encoding="utf-8")
        with SessionLocal() as db:
            run_row = db.query(DataJoinRun).filter(
                DataJoinRun.session_id == session_id
            ).order_by(DataJoinRun.created_at.desc()).first()
            run_ok = run_row is not None and run_row.status == "completed"
            receipt = (run_row.receipt or {}) if run_row else {}
            verdict["journeys"]["run_completed_db"] = run_ok
            verdict["journeys"]["receipt_counts"] = (
                receipt.get("output_rows") == 1101
                and (receipt.get("counts") or {}).get("matched_pairs") == 1100)
            run_id = run_row.id if run_row else ""
            output_rows_db = run_row.output_rows if run_row else None

        # 注入 run_id 到结果 turn
        for k, v in scripts.items():
            for step in v:
                if isinstance(step.get("args"), dict) and "run_id" in step["args"]:
                    step["args"]["run_id"] = run_id
        (WORK / "journey_script.json").write_text(json.dumps(scripts), encoding="utf-8")

        body4 = chat("读取第一页结果")
        if "task_error" in body4:
            body4 = chat("读取第一页结果")
        (WORK / "sse4.log").write_text(body4, encoding="utf-8")
        verdict["journeys"]["result_total_rows_full"] = (
            '"total_rows":1101' in body4.replace(" ", ""))

        # ── 权威裁决（读文件/DB，不读 SSE） ──────────────────────────
        from app.services.data_join_authoring import outputs as dj_outputs

        fc = dj_outputs.load_output_content(run_id)["fc"]
        feats = fc.get("features") or []
        verdict["journeys"]["output_file_rows"] = len(feats) == 1101
        # 抽值 oracle：首行 = 区0 → pop 10000；未匹配区 → pop null
        by_code = {f["properties"].get("adcode"): f["properties"]
                   for f in feats}
        first = by_code.get("11010000") or {}
        unmatched = by_code.get("999999") or {}
        verdict["journeys"]["value_oracle"] = (
            first.get("pop") == POP_BASE and unmatched.get("pop") is None
            and unmatched.get("_join_status") == "unmatched")
        verdict["journeys"]["crs_preserved"] = fc.get("crs") == "EPSG:4326"
        # 派生/保留身份：many_to_one 保留左 _wfid
        verdict["journeys"]["identity_preserved"] = (
            first.get("_wfid") == "wf1-b0")

        # 渲染投影（上图入口）
        rr = api("POST",
                 "/api/v1/data-join-authoring/sessions/{}/runs/{}/render-ref"
                 .format(session_id, run_id))
        verdict["journeys"]["render_ref_rows"] = rr.get("rows") == 1101
        render_ref = rr.get("render_ref")

        # ── G18 真实交付链（J25）：渲染 ref → delivery job → 下载回读 ──
        def _delivery_submit(spec):
            req = urllib.request.Request(
                "http://127.0.0.1:{}/api/v1/data-delivery/jobs".format(api_port),
                data=json.dumps({"spec": spec}).encode(),
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + jwt,
                         **({"X-Session-Token": owner_token} if owner_token else {})},
                method="POST")
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                verdict["delivery_submit_error"] = e.read().decode("utf-8")[:600]
                raise

        dl_spec = {
            "version": 1,
            "source": {"kind": "session_ref", "session_id": session_id,
                       "ref": render_ref},
            "format": "geojson",
            "filename": "g38-join-out",
        }
        job = _delivery_submit(dl_spec)
        if isinstance(job, dict) and job.get("losses_required"):
            # geojson 交付的损失确认（如实确认后重提）
            job = _delivery_submit(dl_spec)
        job_id = job.get("job_id") or job.get("id")
        dl_status = None
        for _ in range(120):
            view = api("GET", f"/api/v1/data-delivery/jobs/{job_id}")
            dl_status = view.get("status")
            if dl_status in ("completed", "failed", "cancelled"):
                break
            time.sleep(1.0)
        verdict["journeys"]["delivery_completed"] = dl_status == "completed"
        if dl_status == "completed":
            _dl_headers = {"Authorization": "Bearer " + jwt}
            if owner_token:
                _dl_headers["X-Session-Token"] = owner_token
            req = urllib.request.Request(
                "http://127.0.0.1:{}/api/v1/data-delivery/jobs/{}/download"
                .format(api_port, job_id), headers=_dl_headers)
            with urllib.request.urlopen(req, timeout=120) as resp:
                dl_bytes = resp.read()
            dl_fc = json.loads(dl_bytes.decode("utf-8"))
            dl_feats = dl_fc.get("features") or []
            verdict["journeys"]["delivery_reread_rows"] = len(dl_feats) == 1101
            dl_first = next((f for f in dl_feats
                             if f["properties"].get("adcode") == "11010000"), None)
            verdict["journeys"]["delivery_reread_values"] = (
                dl_first is not None
                and dl_first["properties"].get("pop") == POP_BASE)

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
        shutil.rmtree(WORK, ignore_errors=True)
    else:
        print("WORK_DIR=" + str(WORK), file=sys.stderr)  # 失败保留证据
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


@pytest.mark.timeout(900)
def test_pi_g38_data_join_journey(tmp_path: Path):
    """真实 Pi 子进程旅程：driver 独立进程执行，末行 JSON 为裁决。"""
    driver = tmp_path / "g38_driver.py"
    driver.write_text(_DRIVER_SOURCE, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(driver), str(REPO_ROOT)],
        timeout=880, capture_output=True, text=True,
    )
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    verdict = json.loads(lines[-1]) if lines else {"fail": ["no verdict"]}
    assert proc.returncode == 0, (
        f"journey failed: {verdict.get('fail')} stderr={proc.stderr[-2000:]}")
    gates = {
        "warmup_ok", "describe_receipt", "plan_created_via_tool",
        "preview_full_counts", "run_completed_db", "receipt_counts",
        "result_total_rows_full", "output_file_rows", "value_oracle",
        "crs_preserved", "identity_preserved", "render_ref_rows",
        "delivery_completed", "delivery_reread_rows", "delivery_reread_values",
    }
    for gate in gates:
        assert verdict["journeys"].get(gate) is True, (
            f"gate {gate} not satisfied: {json.dumps(verdict, ensure_ascii=False)}")
