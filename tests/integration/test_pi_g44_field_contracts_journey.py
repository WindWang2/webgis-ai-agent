"""G44 真实 Pi 旅程（WP10 / WP12 / F27 / F28）。

架构与 G29–G41 旅程同款：pytest 只做门控与裁决；driver 是独立 Python
子进程（避免 conftest 预导入污染 app 单例）。链：

    stub LLM SSE（node 子进程）
      → uvicorn(app.main:app)（生产 lifespan 拉起固定 pin 的 Pi RPC 子进程）
      → /api/v1/chat/stream 生产 HTTP 链
      → tool_search deferred 激活
      → webgis_field_contract_inspect / webgis_field_contract_draft
      → webgis_vector_feature_edit_preview / _commit（G14 同链）
      → ToolDispatchService / ToolRegistry 生产 dispatch
      → field_contracts.service / enforcement → feature_edit executor
      → 合同 DB + 权威数据 ref + journal（真实写入与回读）

旅程：owner 预激活合同（种子）→ Pi 读合同 → Pi 提出合法属性编辑并真实
提交（policy allow_safe_attribute，G14 语义）→ Pi 非法编辑被服务端阻断
（F27）→ Pi 草拟合同但不改变 active（F28：激活/退役无工具面）。
只有 LLM 响应是 stub；RPC/dispatch/合同校验/写入主链都不 mock。

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
"""G44 journey driver：stub LLM + uvicorn(app) + 合同读写旅程 → VERDICT json。"""
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
WORK = Path(tempfile.mkdtemp(prefix="pi-g44-"))
sys.path.insert(0, str(REPO_ROOT))

import uuid

RUN = "g44" + uuid.uuid4().hex[:8]
os.environ["PI_AUTOSTART"] = "true"
os.environ.setdefault("GIS_MANIFEST_STRICT", "0")
# 独立 sqlite 合同 DB（生产 SessionLocal 路径；create_all 建表）
os.environ["DATABASE_URL"] = f"sqlite:///{WORK / 'g44_journey.db'}"
os.environ["DATA_DIR"] = str(WORK / "data")

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

CONTRACT_DOC = {
    "version": 1,
    "title": "设施资产合同（旅程）",
    "fields": [
        {"field": "asset_no", "type": "string", "required": True,
         "min_length": 3, "max_length": 16},
        {"field": "status", "type": "string",
         "enum": ["planned", "active", "retired"]},
        {"field": "capacity", "type": "number", "min": 0},
        {"field": "retired_reason", "type": "string", "nullable": True},
    ],
    "cross_rules": [
        {"rule_id": "rr", "kind": "conditional_required",
         "when": {"field": "status", "op": "eq", "value": "retired"},
         "require": ["retired_reason"]},
        {"rule_id": "uniq", "kind": "unique", "fields": ["asset_no"]},
    ],
}

FACILITIES = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "id": f"fac-{i}",
         "geometry": {"type": "Point", "coordinates": [116.0 + 0.01 * i, 39.0]},
         "properties": {"asset_no": f"A-{i:03d}",
                        "status": "planned" if i % 2 == 0 else "active",
                        "capacity": 10.0 + i}}
        for i in range(4)
    ],
}


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    s.close()
    return port


async def _seed_session(session_id: str) -> dict:
    """服务端种子：设施 ref + owner 预激活合同 + allow_safe_attribute 政策。"""
    from app.services.session_data import session_data_manager as sdm

    ref = str(await sdm.store(session_id, {"geojson": FACILITIES}, prefix="data"))

    from app.services.field_contracts import service as fc_service

    created = await fc_service.create_contract(
        session_id, ref_id=ref, document=CONTRACT_DOC, actor="owner")
    started = await fc_service.start_assessment(
        session_id, created["contract_id"], actor="owner", background=False)
    assert started["assessment"]["status"] == "completed", started
    activated = await fc_service.activate_contract(
        session_id, created["contract_id"], actor="owner")
    assert activated["activated"]

    # owner 政策：safe 属性编辑自动提交（G14 既有语义；Pi 提交的门是
    # policy + plan.safe —— 合同不放宽也不收紧该权限面）
    from app.services.feature_edit import policy as fe_policy

    await fe_policy.set_policy(session_id, "allow_safe_attribute")
    return {"ref": ref, "contract_id": created["contract_id"],
            "digest": created["normalized_digest"][:16]}


def main() -> int:
    def _build_turn_scripts(seed: dict) -> dict:
        legal_ops = [{
            "op": "set_fields",
            "fields": {"status": "retired", "retired_reason": "journey-eof"},
            "wfids": ["fac-0"],
        }]
        illegal_ops = [{
            "op": "set_fields",
            "fields": {"status": "unknown"},
            "wfids": ["fac-0"],
        }]
        return {
            "warmup": [{"text": "warmup-ok"}],
            "查看这个图层的业务字段约束": [
                {"tool": "tool_search", "args": {"query": "字段 合同 业务 规则 约束"}},
                {"tool": "webgis_field_contract_inspect", "args": {"ref_id": seed["ref"]}},
                {"text": "已读取合同。"},
            ],
            "把零号设施退役并注明原因": [
                {"tool": "tool_search", "args": {"query": "vector_feature_edit_preview vector_feature_edit_commit 要素 属性 编辑 预览 提交 set_fields"}},
                {"tool": "vector_feature_edit_preview", "args": {"ops": legal_ops, "ref_id": seed["ref"]}},
                {"text": "预览通过。"},
            ],
            "提交这个退役编辑": [
                {"tool": "tool_search", "args": {"query": "vector_feature_edit_commit 提交 plan"}},
                {"tool": "vector_feature_edit_commit", "args": {"plan_id": "FROM_PREV"}},
                {"text": "已提交。"},
            ],
            "把状态改成未知值试试": [
                {"tool": "tool_search", "args": {"query": "vector_feature_edit_preview 要素 属性 编辑"}},
                {"tool": "vector_feature_edit_preview",
                 "args": {"ops": illegal_ops, "ref_id": seed["ref"]}},
                {"text": "被合同拒绝。"},
            ],
            "帮这个图层起草一个更严的合同草稿": [
                {"tool": "webgis_field_contract_draft", "args": {
                    "ref_id": seed["ref"],
                    "document": {**CONTRACT_DOC, "title": "更严草稿（Pi 起草）",
                                 "fields": CONTRACT_DOC["fields"] + [
                                     {"field": "note", "type": "string"}]}}},
                {"text": "草稿已保存（未激活）。"},
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
    os.environ["LLM_API_KEY"] = "stub-g44-key"
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
    last_plan_id = None
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
        verdict["seeded"] = seed

        # F28（静态面）：本域工具族只有 inspect/draft/assess/result ——
        # Pi 没有任何激活/退役/放宽政策的工具入口。
        from app.tools.field_contracts_tools import register_field_contracts_tools
        from app.tools.registry import ToolRegistry

        reg = ToolRegistry()
        register_field_contracts_tools(reg)
        names = sorted(reg._tools)
        verdict["observations"]["registered_tools"] = names
        verdict["journeys"]["f28_no_activate_tool_for_pi"] = all(
            ("activate" not in n and "retire" not in n and "policy" not in n)
            for n in names
        ) and "webgis_field_contract_inspect" in names

        scripts = _build_turn_scripts(seed)
        # 提交 turn 的 plan_id 由上一 turn 的工具结果回填 —— stub 脚本
        # 无法读服务端状态，改由 driver 在两 turn 之间改写脚本（见下）。
        (WORK / "journey_script.json").write_text(json.dumps(scripts), encoding="utf-8")

        warm = chat("warmup")
        if "task_error" in warm:
            warm = chat("warmup")
        verdict["journeys"]["warmup_ok"] = "task_complete" in warm
        (WORK / "sse_warm.log").write_text(warm, encoding="utf-8")

        t1 = chat("查看这个图层的业务字段约束")
        (WORK / "sse_inspect.log").write_text(t1, encoding="utf-8")
        verdict["journeys"]["f27_contract_read_via_pi"] = (
            "webgis_field_contract_inspect" in t1
            and seed["digest"] in t1
            and "retired" in t1)

        t2 = chat("把零号设施退役并注明原因")
        (WORK / "sse_legal_preview.log").write_text(t2, encoding="utf-8")
        verdict["journeys"]["f27_legal_preview_via_pi"] = (
            "vector_feature_edit_preview" in t2
            and "violates the active field contract" not in t2)
        # 从 SSE 中解析 plan_id（回填提交 turn）
        import re as _re

        m = _re.search(r'"plan_id":\s*"(fep-[0-9a-f]+)"', t2)
        if m:
            last_plan_id = m.group(1)
            commit_steps = scripts["提交这个退役编辑"]
            for step in commit_steps:
                if step.get("tool") == "vector_feature_edit_commit":
                    step["args"] = {"plan_id": last_plan_id}
            (WORK / "journey_script.json").write_text(
                json.dumps(scripts), encoding="utf-8")
        verdict["journeys"]["f27_plan_id_extracted"] = last_plan_id is not None

        t3 = chat("提交这个退役编辑")
        (WORK / "sse_commit.log").write_text(t3, encoding="utf-8")
        verdict["journeys"]["f27_legal_commit_via_pi"] = (
            "vector_feature_edit_commit" in t3)

        # 权威数据 + journal 回读（数据/journal 裁决，不信 SSE 文案）
        async def _verify_write():
            from app.services.feature_edit import executor as fe_exec
            from app.services.session_data import session_data_manager as sdm

            payload = await sdm.get(session_id, seed["ref"])
            props = payload["geojson"]["features"][0]["properties"]
            journals = await fe_exec._load_journals(session_id)
            journal = journals.get(seed["ref"]) or {}
            entries = journal.get("entries") or []
            return {
                "status": props.get("status"),
                "reason": props.get("retired_reason"),
                "journal_entries": len(entries),
                "head_kind": (entries[-1] or {}).get("kind") if entries else None,
                "origin": (entries[-1] or {}).get("origin") if entries else None,
            }

        w = asyncio.run(_verify_write())
        verdict["observations"]["write_readback"] = w
        verdict["journeys"]["f27_legal_write_real"] = (
            w["status"] == "retired" and w["reason"] == "journey-eof"
            and w["journal_entries"] >= 1 and w["origin"] == "agent")

        t4 = chat("把状态改成未知值试试")
        (WORK / "sse_illegal.log").write_text(t4, encoding="utf-8")
        # 预览工具返回 success=false + contract_violation（服务端裁决）
        verdict["journeys"]["f27_illegal_blocked_server_side"] = (
            "violates the active field contract" in t4
            or "CONTRACT_VIOLATION" in t4)

        async def _verify_no_write():
            from app.services.session_data import session_data_manager as sdm

            payload = await sdm.get(session_id, seed["ref"])
            props = payload["geojson"]["features"][0]["properties"]
            return props.get("status")

        status_after = asyncio.run(_verify_no_write())
        verdict["journeys"]["f27_illegal_no_data_change"] = status_after == "retired"

        t5 = chat("帮这个图层起草一个更严的合同草稿")
        (WORK / "sse_draft.log").write_text(t5, encoding="utf-8")
        verdict["journeys"]["f28_draft_via_pi"] = (
            "webgis_field_contract_draft" in t5)

        async def _verify_active_unchanged():
            from app.services.field_contracts.store import field_contract_store

            active = field_contract_store.get_active_contract(
                session_id, seed["ref"])
            rows = field_contract_store.list_contracts(session_id, seed["ref"])
            return {
                "active_id": (active or {}).get("id"),
                "active_digest": (active or {}).get("normalized_digest"),
                "n_rows": len(rows),
                "draft_rows": [r for r in rows if r.get("status") == "draft"],
            }

        au = asyncio.run(_verify_active_unchanged())
        verdict["observations"]["active_after_draft"] = au
        verdict["journeys"]["f28_active_contract_unchanged"] = (
            au["active_id"] == seed["contract_id"]
            and au["active_digest"] == seed["digest"] + au["active_digest"][16:]
            if au["active_digest"] else False)
        # 修正：完整 digest 比对
        verdict["journeys"]["f28_active_contract_unchanged"] = (
            au["active_id"] == seed["contract_id"]
            and (au["active_digest"] or "").startswith(seed["digest"]))
        verdict["journeys"]["f28_draft_row_exists"] = len(au["draft_rows"]) == 1

        # 重开恢复语义（F32 部分）：合同 DB 冷读在重启后仍可用 —— 同进程
        # 内模拟「重开」= 重新解析 active 合同（真实重启由 routes/DB 层保证）
        from app.services.field_contracts.enforcement import resolve_active_contract

        re_resolved = asyncio.run(resolve_active_contract(session_id, seed["ref"]))
        verdict["journeys"]["f32_re_resolve_after_reopen"] = (
            re_resolved is not None
            and re_resolved.plan_view()["digest"].startswith(seed["digest"]))

        required = (
            "warmup_ok", "f28_no_activate_tool_for_pi", "f27_contract_read_via_pi",
            "f27_legal_preview_via_pi", "f27_plan_id_extracted",
            "f27_legal_commit_via_pi", "f27_legal_write_real",
            "f27_illegal_blocked_server_side", "f27_illegal_no_data_change",
            "f28_draft_via_pi", "f28_active_contract_unchanged",
            "f28_draft_row_exists", "f32_re_resolve_after_reopen",
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
def test_g44_field_contracts_pi_journey(tmp_path):
    driver = tmp_path / "g44_pi_journey_driver.py"
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
    print("G44_PI_JOURNEY_VERDICT " + json.dumps(
        {"fail": verdict.get("fail"), "journeys": verdict.get("journeys"),
         "observations": {
             k: v for k, v in (verdict.get("observations") or {}).items()
             if k != "active_after_draft"}}, sort_keys=True))
    assert proc.returncode == 0, (
        f"journey failed: {verdict.get('fail')}\n"
        f"stderr tail: {proc.stderr[-2000:]}")
    for gate, ok in verdict["journeys"].items():
        assert ok is True, f"Pi journey gate {gate} failed: {verdict['fail']}"
