"""A5 场景验证车道：真实 Pi bridge + 脚本化 stub LLM（ADR-0282）。

架构：pytest 用例本身只做门控与裁决 —— 五段旅程由独立驱动脚本（本文件
内嵌的 ``_DRIVER_SOURCE``）以子进程执行：boot stub LLM（node）→ 以
uvicorn 线程承载生产同构 app（lifespan 拉起真实 vendor/pi v1.5.0 bridge）
→ 对真实 HTTP 端口跑旅程 → 输出机器可判 JSON。子进程隔离使 pytest 的
conftest 预导入不再污染 app 模块单例（此前 pi_bridge 全局被 pytest 侧
导入态复位，见 planning/pi-native-host-vnext/milestone-ledger.md）。

与 test_pi_real_llm_e2e.py 的区别：LLM 边界由本地确定性 stub 承担（无需
真实凭据），其余全真 —— 真实 rpc-entry、真实 webgis-tools 扩展、真实
ToolDispatchService 工具回调（HTTP → Python）、真实 SSE/取消 seam。GIS
证据用内联 FeatureCollection 驱动真实空间计算（与 journey 真车道同纪律：
不调外部 POI、不伪造数据）。

旅程（任务书 A5）：J1 成都小学分布（真实 GIS 工具 dispatch）；J2 换色
不重算；J3 北京跨会话不污染；J5 纯文本不伪装地图成功。J4（取消）不由本
车道驱动 —— 由 test_runtime_chaos_pi.py 与 vendor 契约车道的 abort 用例
确定性覆盖（本 sandbox 跨线程取消窗口不稳，见 driver 内注释与 ledger）。

门控：node + 已构建 vendor；缺失显式 skip（绝不静默绿）。
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
    shutil.which("node") is None, reason="stub-LLM journeys need node on PATH"
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
        reason=(
            "stub-LLM full-stack journeys are opt-in (sandbox loopback/threading "
            "variance): set PI_JOURNEY_E2E=1 to run — explicit skip, never silent-green"
        ),
    ),
]


_DRIVER_SOURCE = r'''#!/usr/bin/env python3
"""A5 journey driver（独立进程）：stub LLM + uvicorn(app) + 五段旅程。

输出：单行 "VERDICT {json}"。driver 采集事实并做旅程内基本断言素材，
最终裁决在 pytest 侧对照 verdict 进行。
"""
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
WORK = Path(tempfile.mkdtemp(prefix="pi-journey-"))
sys.path.insert(0, str(REPO_ROOT))

# 会话库跨 driver 运行持久（sqlite）—— 每次运行用唯一会话名，避免历史
# owner_token 归属把本轮首条消息判成 404（warmup/J* 同理）。
import uuid

RUN = "jr" + uuid.uuid4().hex[:8]

os.environ["USE_NEW_AGENT"] = "true"

STUB_JS = r"""
import http from 'node:http';
import { appendFileSync } from 'node:fs';
const scriptPath = process.env.JOURNEY_SCRIPT;
const recFile = process.env.JOURNEY_REC;
const turnScripts = JSON.parse(await import('node:fs').then((fs) => fs.readFileSync(scriptPath, 'utf8')));
const counters = {};
const srv = http.createServer((req, res) => {
  let body = ''; req.on('data', (c) => (body += c));
  req.on('end', async () => {
    const parsed = JSON.parse(body || '{}');
    const msgs = parsed.messages || [];
    // 找最后一条 USER 消息文本 —— 会话主线（title 生成等旁路调用没有
    // journey turn key，全部以纯文本应答，永不消耗旅程步骤）。
    let lastUser = null;
    for (let i = msgs.length - 1; i >= 0; i -= 1) {
      const m = msgs[i];
      if (m.role !== 'user') continue;
      const c = typeof m.content === 'string' ? m.content
        : Array.isArray(m.content) ? m.content.filter((b) => b.type === 'text').map((b) => b.text).join('') : '';
      lastUser = c; break;
    }
    const base = (lastUser || '').split('\n')[0].trim();
    appendFileSync(recFile, JSON.stringify({ key: base }) + '\n');
    const steps = turnScripts[base];
    let step = null;
    if (steps) {
      counters[base] = (counters[base] || 0) + 1;
      step = steps[Math.min(counters[base] - 1, steps.length - 1)];
    } else {
      step = { text: 'ok' };  // 旁路（title 等）
    }
    const delayS = Number(step.delay_s || 0);
    if (delayS > 0) await new Promise((r) => setTimeout(r, delayS * 1000));
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

INLINE_FC = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"name": "p{}".format(i), "value": (i * 7) % 23},
            "geometry": {
                "type": "Point",
                "coordinates": [104.04 + (i % 4) * 0.05, 30.62 + (i // 4) * 0.05],
            },
        }
        for i in range(12)
    ],
}

# J6 邻近性分析的内联双集合（小而有界，真实空间计算）
INLINE_SCHOOLS_FC = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {"name": "s{}".format(i)},
         "geometry": {"type": "Point", "coordinates": [104.05 + i * 0.01, 30.65]}}
        for i in range(4)
    ],
}

INLINE_METRO_FC = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {"name": "metro-north", "id": "m1"},
         "geometry": {"type": "Point", "coordinates": [104.05, 30.66]}},
        {"type": "Feature", "properties": {"name": "metro-south", "id": "m2"},
         "geometry": {"type": "Point", "coordinates": [104.08, 30.64]}},
    ],
}

# 旅程脚本（按用户消息首行键控；旁路调用如 title 生成不消耗步骤）
TURN_SCRIPTS = {
    "warmup": [{"text": "warmup-ok"}],
    "成都小学分布": [
        {"tool": "webgis_map_intent", "args": {"query": "成都小学分布"}},
        {"tool": "query_local_poi", "args": {"district": "成都市", "subtype": "小学", "limit": 5}},
        {"text": "成都小学分布图已准备。"},
    ],
    "换个颜色": [{"text": "已将图层配色调整为暖色系（本次未重新计算数据）。"}],
    "北京市小学": [{"text": "北京市小学分布需要新的数据查询，本会话与成都会话相互独立。"}],
    "北京小学分布（将被取消）": [
        {"tool": "webgis_map_intent", "args": {"query": "北京小学分布"}, "delay_s": 8},
        {"text": "cancelled-turn-placeholder"},
    ],
    "重新开始": [{"text": "重新开始：这是取消后的新一轮，未携带上一轮结果。"}],
    "这是一条普通问答": [{"text": "这是一条不涉及地图的普通问答。"}],
    # J6（PI-09）：框选后的邻近性分析 —— 长尾工具经 webgis_execute 代理
    # （PI-03 代理车道），nearest_facility 双集合最近邻 + 内联
    # FeatureCollection 驱动真实空间计算（离地铁站距离，米）。
    "框选这块的学校离地铁站多远": [
        {"tool": "webgis_map_intent", "args": {"query": "成都学校到地铁站邻近性"}},
        {"tool": "webgis_execute", "args": {
            "toolName": "nearest_facility",
            "arguments": {
                "source_points": INLINE_SCHOOLS_FC,
                "target_points": INLINE_METRO_FC,
            },
        }},
        {"text": "框选范围内每所学校到最近地铁站的距离（米）已算出。"},
    ],
    # J6b：两段式 —— 意图先建立计划资格（邻近能力入面），下一 turn 裸名执行。
    "分析框选学校到地铁站的最近设施匹配": [
        {"tool": "webgis_map_intent", "args": {"query": "成都学校到地铁站最近设施匹配 邻近性分析"}},
        {"text": "邻近性分析计划已建立（最近设施能力已入面）。"},
    ],
    "执行最近设施匹配计算": [
        {"tool": "nearest_facility", "args": {
            "source_points": INLINE_SCHOOLS_FC,
            "target_points": INLINE_METRO_FC,
        }},
        {"text": "每所学校到最近地铁站的距离（米）已算出。"},
    ],
}


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    s.close()
    return port


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


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


def main() -> int:
    stub_js = WORK / "journey_stub.mjs"
    stub_js.write_text(STUB_JS, encoding="utf-8")
    script_path = WORK / "journey_script.json"
    script_path.write_text(json.dumps(TURN_SCRIPTS), encoding="utf-8")
    rec = WORK / "journey_rec.jsonl"
    rec.write_text("", encoding="utf-8")
    stub_err = WORK / "stub_err.log"

    with open(stub_err, "w") as fh:
        stub = subprocess.Popen(
            ["node", str(stub_js)], stdout=subprocess.DEVNULL, stderr=fh,
            env={**os.environ, "JOURNEY_SCRIPT": str(script_path),
                 "JOURNEY_REC": str(rec)},
        )
    # 本 sandbox 对 node→node 回环有按端口的不稳定过滤：用 node 探针验证
    # （与 vendor 子进程同类进程）；不通则换端口重启 stub，最多 5 次。
    def _node_reaches(port):
        probe = subprocess.run(
            ["node", "-e",
             "fetch('http://127.0.0.1:' + process.argv[1] + '/x')"
             ".then((r) => r.text())"
             ".then(() => process.exit(0))"
             ".catch(() => process.exit(1))", str(port)],
            capture_output=True, text=True, timeout=10)
        return probe.returncode == 0

    stub_port = None
    for _boot in range(5):
        deadline = time.monotonic() + 10
        port_cand = None
        while time.monotonic() < deadline:
            for line in stub_err.read_text().splitlines():
                if "STUB_READY" in line:
                    port_cand = int(line.split()[1])
            if port_cand:
                break
            time.sleep(0.1)
        if port_cand and _node_reaches(port_cand):
            stub_port = port_cand
            break
        if port_cand is None:
            break
        stub.terminate()
        try:
            stub.wait(timeout=3)
        except subprocess.TimeoutExpired:
            stub.kill()
        stub_err.write_text("")
        with open(stub_err, "a") as fh:
            stub = subprocess.Popen(
                ["node", str(stub_js)], stdout=subprocess.DEVNULL, stderr=fh,
                env={**os.environ, "JOURNEY_SCRIPT": str(script_path),
                     "JOURNEY_REC": str(rec)},
            )
    if not stub_port:
        print(json.dumps({"fail": [
            "stub LLM unreachable from a node peer after retries "
            "(sandbox loopback filtering)"]}))
        return 1

    os.environ["LLM_BASE_URL"] = "http://127.0.0.1:{}/v1".format(stub_port)
    os.environ["LLM_API_KEY"] = "stub-journey-key"
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
        server.started and _port_open(api_port) and try_get_chat_engine() is not None
    ):
        time.sleep(0.5)
    ready = server.started and _port_open(api_port) and try_get_chat_engine() is not None
    verdict = {"journeys": {}, "fail": []}

    def _post_stream_raw(message, session_id, timeout_s=120.0, owner_token=None):
        payload = {"message": message, "session_id": session_id}
        headers = {"Content-Type": "application/json"}
        if owner_token:
            headers["X-Session-Token"] = owner_token  # SEC-08 真实前端契约
        req = urllib.request.Request(
            "http://127.0.0.1:{}/api/v1/chat/stream".format(api_port),
            data=json.dumps(payload).encode(),
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read().decode("utf-8", "replace")
        (WORK / ("sse_" + session_id + ".log")).write_text(body, encoding="utf-8")
        return body

    def _post_stream(message, session_id, timeout_s=120.0, owner_token=None):
        return _parse_sse(_post_stream_raw(
            message, session_id, timeout_s=timeout_s, owner_token=owner_token))

    def _owner_token_of(events):
        for e in events:
            tok = e["data"].get("owner_token")
            if tok:
                return tok
        return None

    def _sid(e):
        return e["data"].get("session_id") or e["data"].get("task_id")

    try:
        warm_detail = []
        for _warm_attempt in range(3):
            if not ready:
                break
            try:
                # 每次尝试用全新 session（避免 owner_token 归属 404 干扰诊断）
                warm = _post_stream(
                    "warmup", "{}-warmup-{}".format(RUN, _warm_attempt), timeout_s=60)
                warm_detail.append([e["event"] for e in warm][:6])
                if any(e["event"] in ("task_start", "session") for e in warm):
                    break
                time.sleep(5)
            except Exception as exc:  # noqa: BLE001
                verdict["warmup_error"] = str(exc)[:200]
                time.sleep(5)
        else:
            ready = False
        verdict["warmup_attempts"] = warm_detail
        if not ready:
            verdict["fail"].append(
                "app never became chat-ready under stub LLM (environment)")
            print("VERDICT " + json.dumps(verdict, ensure_ascii=False))
            return 1

        # ── J1: 成都小学分布 ──
        try:
            ev = _post_stream("成都小学分布", RUN + "-cd")
        except Exception as exc:  # noqa: BLE001 — 单旅程失败入 verdict
            verdict["fail"].append("J1 request error: " + str(exc)[:200])
            print("VERDICT " + json.dumps(verdict, ensure_ascii=False))
            return 1
        verdict["journeys"]["J1"] = {
            "names": [e["event"] for e in ev],
            "map_intent_ok": any(
                e["event"] == "step_result"
                and e["data"].get("tool") == "webgis_map_intent"
                and isinstance(e["data"].get("result"), dict)
                and e["data"]["result"].get("success") is True
                for e in ev),
            "foreign_sid": [_sid(e) for e in ev if _sid(e) and _sid(e) != RUN + "-cd"],
            "step_errors": [
                {"tool": e["data"].get("tool"), "error": str(e["data"].get("error"))[:700]}
                for e in ev if e["event"] == "step_error"
            ],
            "owner_token_returned": bool(_owner_token_of(ev)),
        }

        # ── J2: 换色（不重算）──
        calls_before = rec.read_text().count('"key":"换个颜色"')
        cd_owner = _owner_token_of(ev)
        try:
            ev2 = _post_stream("换个颜色", RUN + "-cd", owner_token=cd_owner)
        except Exception as exc:  # noqa: BLE001
            verdict["fail"].append("J2 request error: " + str(exc)[:200])
            print("VERDICT " + json.dumps(verdict, ensure_ascii=False))
            return 1
        calls_after = rec.read_text().count('"key":"换个颜色"')
        verdict["journeys"]["J2"] = {
            "names": [e["event"] for e in ev2],
            "llm_calls": calls_after - calls_before,
        }

        # ── J3: 北京（跨会话隔离）──
        ev3 = _post_stream("北京市小学", RUN + "-bj")
        verdict["journeys"]["J3"] = {
            "names": [e["event"] for e in ev3],
            "foreign_sid": [_sid(e) for e in ev3 if _sid(e) and _sid(e) != RUN + "-bj"],
        }

        # ── J4（取消）：不在此驱动 —— 取消语义已由 test_runtime_chaos_pi.py
        # （16 项确定性）与 test_pi_vendor_contract.py 的 abort 活体用例锁定；
        # 本 sandbox 中跨线程 uvicorn + SSE 取消窗口不稳定（见 ledger），不
        # 引入 flaky 断言。

        # ── J5: 纯文本不伪装地图成功 ──
        ev5 = _post_stream("这是一条普通问答", RUN + "-plain")
        completes = [e["data"] for e in ev5 if e["event"] == "task_complete"]
        verdict["journeys"]["J5"] = {
            "names": [e["event"] for e in ev5],
            "map_product": completes[0].get("map_product") if completes else None,
        }

        # ── J6（PI-09）：wrap-native 双端拒绝 —— 注册面工具不得嵌套进
        #    webgis_execute 代理（PI-03 负面 oracle：拒绝嵌套）。──
        J6_MSG = "框选这块的学校离地铁站多远"
        try:
            ev6 = _post_stream(J6_MSG, RUN + "-cd", owner_token=cd_owner)
            verdict["journeys"]["J6"] = {
                "names": [e["event"] for e in ev6],
                "wrap_rejected": any(
                    e["event"] == "step_error"
                    and "webgis_execute" in str(e["data"].get("tool"))
                    and "do not wrap" in str(e["data"].get("error"))
                    for e in ev6),
            }
        except Exception as exc:  # noqa: BLE001 — 单旅程失败入 verdict
            verdict["fail"].append("J6 request error: " + str(exc)[:200])

        # ── J6b（PI-09）：计划资格两段式 —— 意图建立邻近能力后裸名执行。
        J6B_MSG = "执行最近设施匹配计算"
        body6b = ""
        try:
            _post_stream("分析框选学校到地铁站的最近设施匹配", RUN + "-cd", owner_token=cd_owner)
            body6b = _post_stream_raw(J6B_MSG, RUN + "-cd", owner_token=cd_owner)
            ev6b = _parse_sse(body6b)
            verdict["journeys"]["J6b"] = {
                "names": [e["event"] for e in ev6b],
                "nearest_ok": any(
                    e["event"] == "step_result"
                    and e["data"].get("tool") == "nearest_facility"
                    and isinstance(e["data"].get("result"), dict)
                    and (
                        e["data"]["result"].get("success") is True
                        or e["data"]["result"].get("status") == "ok"
                    )
                    for e in ev6b),
                "proximity_evidence": any(
                    e["event"] == "step_result"
                    and isinstance(e["data"].get("result"), dict)
                    and (
                        "nearest" in str(e["data"]["result"])[:2000]
                        or e["data"]["result"].get("geojson_ref")
                    )
                    for e in ev6b),
                "foreign_sid": [_sid(e) for e in ev6b if _sid(e) and _sid(e) != RUN + "-cd"],
                "step_errors": [
                    {"tool": e["data"].get("tool"), "error": str(e["data"].get("error"))[:300]}
                    for e in ev6b if e["event"] == "step_error"
                ],
            }
        except Exception as exc:  # noqa: BLE001 — 单旅程失败入 verdict
            verdict["fail"].append("J6b request error: " + str(exc)[:200])

        # ── J7（PI-09）：断线重连 resume —— 同消息 + Last-Event-ID 重放，
        #    DUP-1：绝不重发 prompt（零 LLM 调用）。──
        try:
            ids6 = [ln[4:].strip() for ln in body6b.splitlines() if ln.startswith("id: ")]
            last_id = ids6[-1] if ids6 else ""
            rec_before = rec.read_text().count(json.dumps({"key": J6_MSG}, ensure_ascii=False))
            payload = {"message": J6_MSG, "session_id": RUN + "-cd"}
            req = urllib.request.Request(
                "http://127.0.0.1:{}/api/v1/chat/stream".format(api_port),
                data=json.dumps(payload).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Last-Event-ID": last_id,
                    "X-Session-Token": cd_owner or "",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp7:
                body7 = resp7.read().decode("utf-8", "replace")
            (WORK / "sse_resume.log").write_text(body7, encoding="utf-8")
            ev7 = _parse_sse(body7)
            rec_after = rec.read_text().count(json.dumps({"key": J6_MSG}, ensure_ascii=False))
            verdict["journeys"]["J7"] = {
                "names": [e["event"] for e in ev7],
                "llm_calls": rec_after - rec_before,
                "error_event": any(e["event"] == "error" for e in ev7),
                "replayed_sid_consistent": all(
                    (not _sid(e)) or _sid(e) == RUN + "-cd" for e in ev7),
            }
        except Exception as exc:  # noqa: BLE001 — 单旅程失败入 verdict
            verdict["fail"].append("J7 request error: " + str(exc)[:200])
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        stub.terminate()
        try:
            stub.wait(timeout=3)
        except subprocess.TimeoutExpired:
            stub.kill()

    print("VERDICT " + json.dumps(verdict, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _run_driver() -> dict:
    with tempfile.TemporaryDirectory(prefix="pi-journey-runner-") as td:
        driver_path = Path(td) / "journey_driver.py"
        driver_path.write_text(_DRIVER_SOURCE, encoding="utf-8")
        env = {k: v for k, v in os.environ.items()
               if k not in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")}
        res = subprocess.run(
            [sys.executable, str(driver_path), str(REPO_ROOT)],
            capture_output=True, text=True, timeout=560, env=env, cwd=str(REPO_ROOT),
        )
    for line in reversed(res.stdout.splitlines()):
        if line.startswith("VERDICT "):
            return json.loads(line[len("VERDICT "):])
    raise AssertionError(
        "driver produced no verdict: rc={} stdout_tail={} stderr_tail={}".format(
            res.returncode, res.stdout[-500:], res.stderr[-800:])
    )


async def test_a5_journeys_on_real_pi_bridge():
    """五段旅程裁决（driver 采集，pytest 断言）。"""
    if _vendor_entry() is None:
        pytest.skip(NEEDS_BUILD_REASON)
    verdict = _run_driver()
    assert not verdict.get("fail"), verdict.get("fail")
    j = verdict["journeys"]

    # J1：真实 GIS 工具证据 + 事件归属
    assert "task_start" in j["J1"]["names"], j["J1"]
    assert j["J1"]["map_intent_ok"] is True, j["J1"]
    assert "task_complete" in j["J1"]["names"]
    assert j["J1"]["names"][-1] == "done"
    assert not j["J1"]["foreign_sid"], j["J1"]["foreign_sid"]

    # J2：换色恰一次 LLM 调用（无数据重算）
    assert j["J2"]["llm_calls"] == 1, j["J2"]
    assert j["J2"]["names"][-1] == "done"

    # J3：北京会话事件不掺入他席
    assert not j["J3"]["foreign_sid"], j["J3"]["foreign_sid"]
    assert "task_complete" in j["J3"]["names"]

    # J5：无地图变更不伪装完成
    mp = j["J5"]["map_product"]
    assert not (isinstance(mp, dict) and mp.get("status") == "completed"), j["J5"]

    # J6（PI-09）：wrap-native 嵌套被拒（PI-03 负面 oracle）
    assert "J6" in j, verdict.get("fail")
    assert j["J6"]["wrap_rejected"] is True, j["J6"]

    # J6b（PI-09）：两段式计划资格 → nearest_facility 裸名真实计算成功
    assert "J6b" in j, verdict.get("fail")
    assert j["J6b"]["nearest_ok"] is True, j["J6b"]
    assert j["J6b"]["proximity_evidence"] is True, j["J6b"]
    assert "task_complete" in j["J6b"]["names"]
    assert j["J6b"]["names"][-1] == "done"
    assert not j["J6b"]["foreign_sid"], j["J6b"]["foreign_sid"]

    # J7（PI-09）：resume 只重放 —— 零 LLM 调用、会话归属一致
    assert "J7" in j, verdict.get("fail")
    assert j["J7"]["llm_calls"] == 0, j["J7"]
    assert j["J7"]["replayed_sid_consistent"] is True, j["J7"]
    assert not j["J7"]["error_event"], j["J7"]
    assert j["J7"]["names"][-1] == "done", j["J7"]
