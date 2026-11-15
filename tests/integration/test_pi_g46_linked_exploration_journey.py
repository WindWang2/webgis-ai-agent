"""G46 真实 Pi 旅程（E32）：stub LLM + uvicorn(app) + Pi 子进程 → 联合探索。

生产链：Pi 原生工具发现（tool_search）→ dispatch（ToolRegistry 注入可信
session_id）→ linked_exploration.service（同一冻结/求值/CAS 面）→ MapSpec
组件写入。回执经真实 SSE 流回采；oracle 计数独立手算。

门控：node + 已构建 vendor + PI_JOURNEY_E2E=1（显式 opt-in，绝不静默绿）。
重资源阶段：执行前取得同仓统一跨进程锁。
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
"""G46 journey driver：stub LLM + uvicorn(app) + 联合探索旅程 → VERDICT json。"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

REPO = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
WORK = Path(os.environ.get("G46_WORK", "/tmp/g46-journey-work"))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    s.close()
    return port


# ─── 种子数据（oracle 手算见 verdict 断言） ──────────────────────────────
def _fc():
    feats = []
    plan = [("a", 10.0), ("a", 20.0), ("a", 30.0), ("b", 20.0), ("b", 40.0),
            ("c", 50.0)]
    for i, (cat, pop) in enumerate(plan):
        feats.append({
            "type": "Feature",
            "properties": {"_wfid": f"wf-{i}", "cat": cat, "pop": pop},
            "geometry": {"type": "Polygon", "coordinates": [[
                [116.0 + i * 0.02, 39.0], [116.01 + i * 0.02, 39.0],
                [116.01 + i * 0.02, 39.01], [116.0 + i * 0.02, 39.01],
                [116.0 + i * 0.02, 39.0]]]},
        })
    return {"type": "FeatureCollection", "crs": "EPSG:4326", "features": feats}


async def _seed(session_id: str):
    from app.services.session_data import session_data_manager

    ref = await session_data_manager.store(session_id, _fc(), prefix="g46fc")
    return ref


STUB_JS = r"""
import http from 'node:http';
const fsMod = await import('node:fs');
const scriptPath = process.env.JOURNEY_SCRIPT;
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
    let script = {};
    try { script = JSON.parse(fsMod.readFileSync(scriptPath, 'utf8')); } catch {}
    const turns = script[lastUser] || [];
    let idx = 0;
    const next = () => {
      if (idx >= turns.length) { res.end(JSON.stringify({
        choices: [{ message: { role: 'assistant',
          content: 'journey-turn-exhausted（脚本已用尽）' } }] })); return; }
      const step = turns[idx]; idx += 1;
      if (step.text !== undefined) {
        res.end(JSON.stringify({ choices: [{ message: {
          role: 'assistant', content: step.text } }] })); return; }
      const name = step.tool || '';
      const args = step.args || {};
      const call = { id: 'call_' + Math.random().toString(36).slice(2),
        type: 'function', function: { name, arguments: JSON.stringify(args) } };
      res.end(JSON.stringify({ choices: [{ message: {
        role: 'assistant', content: '', tool_calls: [call] } }] })); return; };
    next();
  });
});
const s = srv.listen(0, '127.0.0.1', () => {
  process.stderr.write('STUB_READY ' + s.address().port + '\n');
});
"""


def main() -> int:
    sys.path.insert(0, REPO)
    os.environ["DATABASE_URL"] = "sqlite:///" + str(WORK / "journey.db")
    os.environ["DATA_DIR"] = str(WORK / "data")
    Path(WORK / "data").mkdir(parents=True, exist_ok=True)

    charts = [
        {"chart_id": "ch-cat", "kind": "category", "field": "cat",
         "aggregation": "count", "value_field": None, "title": "类别",
         "bins": 10, "layer_id": None},
        {"chart_id": "ch-rng", "kind": "range", "field": "pop",
         "aggregation": "count", "value_field": None, "title": "数值",
         "bins": 4, "layer_id": None},
    ]
    conditions = [
        {"kind": "category", "condition_id": "cond-ch-cat", "chart_id": "ch-cat",
         "field": "cat", "values": ["a"], "include_null": False},
        {"kind": "range", "condition_id": "cond-ch-rng", "chart_id": "ch-rng",
         "field": "pop", "gte": 20, "lte": 40},
    ]

    def _scripts(session_id: str, ref: str) -> dict:
        return {
            "warmup": [{"text": "warmup-ok"}],
            "检查探索源": [
                {"tool": "tool_search", "args": {"query": "联合 探索 筛选 源 检查"}},
                {"tool": "webgis_exploration_inspect", "args": {"ref": ref}},
                {"text": "源已检查。"},
            ],
            "组合条件预览": [
                {"tool": "tool_search", "args": {"query": "联合 条件 组合 预览 匹配"}},
                {"tool": "webgis_exploration_preview", "args": {
                    "ref": ref, "charts": charts, "conditions": conditions}},
                {"text": "预览完成。"},
            ],
            "保存命名视图": [
                {"tool": "webgis_exploration_view_save", "args": {
                    "title": "类别+区间视图",
                    "plan": {"schema_version": 2,
                             "anchor": {"ref_id": ref, "content_revision": 1,
                                        "content_fingerprint": "PLACEHOLDER"},
                             "charts": charts, "conditions": conditions}}},
                {"text": "视图已保存。"},
            ],
            "重开视图": [
                {"tool": "webgis_exploration_views", "args": {"view_id": "VIEW_ID"}},
                {"text": "视图已打开。"},
            ],
            "应用到地图": [
                {"tool": "webgis_exploration_apply", "args": {"view_id": "VIEW_ID"}},
                {"text": "组件已应用。"},
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
    os.environ["LLM_API_KEY"] = "stub-g46-key"
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
        server.started and try_get_chat_engine() is not None
    ):
        time.sleep(0.5)
    if not (server.started and try_get_chat_engine() is not None):
        stub.terminate()
        print(json.dumps({"fail": ["app did not start"]}))
        return 1

    verdict: dict = {"journeys": {}, "fail": []}
    owner_token = None
    RUN = "g46journey"

    def chat(message: str) -> str:
        nonlocal owner_token
        headers = {"Content-Type": "application/json"}
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

    try:
        session_id = RUN + "-s1"
        ref = asyncio.run(_seed(session_id))
        verdict["seeded"] = [ref]

        # 先用真实锚替换 PLACEHOLDER（经 service.inspect 拿权威锚）
        sys.path.insert(0, REPO)
        from app.services.linked_exploration import service as g46svc

        anchor = asyncio.run(g46svc.inspect_source(
            session_id, ref,
            {"user_id": None, "org_id": None, "owner_token": None,
             "trusted_session_id": session_id}))
        scripts = _scripts(session_id, ref)
        save_turn = scripts["保存命名视图"]
        for step in save_turn:
            blob = step.get("args", {}).get("plan")
            if blob and blob.get("anchor", {}).get("content_fingerprint") == "PLACEHOLDER":
                blob["anchor"] = anchor["anchor"]
        (WORK / "journey_script.json").write_text(
            json.dumps(scripts, ensure_ascii=False), encoding="utf-8")

        warm = chat("warmup")
        if "task_error" in warm:
            warm = chat("warmup")
        verdict["journeys"]["warmup_ok"] = "task_complete" in warm

        b1 = chat("检查探索源")
        if "task_error" in b1:
            b1 = chat("检查探索源")
        verdict["journeys"]["inspect_receipt"] = (
            "webgis_exploration_inspect" in b1
            and '"feature_count":6' in b1.replace(" ", ""))

        b2 = chat("组合条件预览")
        if "task_error" in b2:
            b2 = chat("组合条件预览")
        (WORK / "sse2.log").write_text(b2, encoding="utf-8")
        flat = b2.replace(" ", "")
        # oracle 手算：cat=a 且 pop∈[20,40] → (a,20),(a,30) = 2
        verdict["journeys"]["preview_tool_called"] = (
            "webgis_exploration_preview" in b2)
        verdict["journeys"]["combined_match_count"] = '"match_count":2' in flat
        verdict["journeys"]["facet_denominators"] = (
            '"facet_matched":3' in flat or '"facet_matched": 3' in b2)
        verdict["journeys"]["handle_present"] = '"selection_handle"' in flat

        # view_id 从 DB 读（真实持久化证据）
        from app.core.database import SessionLocal
        from app.models.linked_exploration import ExplorationView

        b3 = chat("保存命名视图")
        if "task_error" in b3:
            b3 = chat("保存命名视图")
        with SessionLocal() as db:
            rows = db.query(ExplorationView).filter(
                ExplorationView.session_id == session_id).all()
            verdict["journeys"]["view_saved_db"] = len(rows) == 1 and (
                rows[0].source_ref == ref)
            view_id = rows[0].id if rows else ""
        scripts["重开视图"] = [
            {"tool": "webgis_exploration_views", "args": {"view_id": view_id}},
            {"text": "视图已打开。"},
        ]
        scripts["应用到地图"] = [
            {"tool": "webgis_exploration_apply", "args": {"view_id": view_id}},
            {"text": "组件已应用。"},
        ]
        (WORK / "journey_script.json").write_text(
            json.dumps(scripts, ensure_ascii=False), encoding="utf-8")

        b4 = chat("重开视图")
        if "task_error" in b4:
            b4 = chat("重开视图")
        verdict["journeys"]["view_reopened_ready"] = (
            '"state":"ready"' in b4.replace(" ", ""))

        b5 = chat("应用到地图")
        if "task_error" in b5:
            b5 = chat("应用到地图")
        verdict["journeys"]["applied_components"] = (
            '"applied_components"' in b5.replace(" ", "")
            and '"count":2' in b5.replace(" ", ""))
        # MapSpec 组件真实落盘
        from app.services.mapspec_store import mapspec_store

        spec = asyncio.run(mapspec_store.get_mapspec(session_id))
        comps = ((spec or {}).get("layout") or {}).get("components") or []
        verdict["journeys"]["mapspec_components_persisted"] = any(
            str(c.get("id", "")).startswith("exploration-chart-") for c in comps
            if isinstance(c, dict))
    except Exception as exc:  # noqa: BLE001
        verdict["fail"].append(f"journey crashed: {exc!r}")
    finally:
        stub.terminate()

    ok = not verdict["fail"] and all(verdict["journeys"].values())
    print("VERDICT " + json.dumps(verdict, ensure_ascii=False))
    if not ok:
        print("WORK_DIR=" + str(WORK), file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


@pytest.mark.timeout(900)
def test_pi_g46_linked_exploration_journey(tmp_path: Path):
    """真实 Pi 子进程旅程：driver 独立进程执行，末行 JSON 为裁决。

    重资源阶段：同仓统一锁内执行（不与兄弟任务并行放大）。
    """
    import fcntl

    lock_dir = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=True,
    ).stdout.strip()
    lock_path = Path(lock_dir) / "webgis-heavy-stage.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = lock_path.open("a+")
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    try:
        driver = tmp_path / "g46_driver.py"
        driver.write_text(_DRIVER_SOURCE, encoding="utf-8")
        env = {**os.environ, "G46_WORK": str(tmp_path / "work")}
        proc = subprocess.run(
            [sys.executable, str(driver), str(REPO_ROOT)],
            timeout=880, capture_output=True, text=True, env=env,
        )
    finally:
        import fcntl as _f

        _f.flock(fh.fileno(), _f.LOCK_UN)
        fh.close()
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    verdict_line = next(
        (ln for ln in reversed(lines) if ln.startswith("VERDICT ")), None)
    assert verdict_line, (
        f"no verdict; stdout tail={lines[-5:] if lines else []}; "
        f"stderr tail={(proc.stderr or '')[-2000:]}")
    verdict = json.loads(verdict_line[len("VERDICT "):])
    assert proc.returncode == 0, (
        f"journey failed: {verdict.get('fail')} stderr={proc.stderr[-2000:]}")
    gates = {
        "warmup_ok", "inspect_receipt", "preview_tool_called",
        "combined_match_count", "facet_denominators", "handle_present",
        "view_saved_db", "view_reopened_ready", "applied_components",
        "mapspec_components_persisted",
    }
    for gate in gates:
        assert verdict["journeys"].get(gate) is True, (
            f"gate {gate} not satisfied: {json.dumps(verdict, ensure_ascii=False)}")
