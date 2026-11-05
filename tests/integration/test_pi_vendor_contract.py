"""Pi vendor RPC 协议契约测试（ADR-0282 / goal-pi-native-host-vnext）。

针对**真实 vendored rpc-entry**（非 mock）锁定上游 wire 契约：分帧、信封、
事件词表、agent_settled 唯一终判、abort 信号、U+2028 分帧安全、内建编码
工具不可见、persona 注入。LLM 边界由本地确定性 stub 承担（OpenAI 兼容流
式）—— 无需真实凭据即可在本地/CI 复现（区别于 test_pi_real_llm_e2e.py
的 cost-gated 车道）。

门控纪律：需要 node + 已构建的 vendor dist（scripts/build-vendor-pi.sh）。
缺失时显式 skip 并给出修复命令 —— 绝不静默绿；也绝不把 skip 当作通过。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
EXTENSION_PATH = REPO_ROOT / "app" / "extensions" / "webgis-tools" / "index.mjs"

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="vendor contract lane needs node on PATH"
)

BUILTIN_CODING_TOOLS = {"bash", "read", "write", "edit", "grep", "find", "ls", "powershell"}

# Minimal deterministic v3 surface dump — independent of the Python registry so
# this lane pins the *vendor protocol*, not tool inventory (that has its own
# unit lanes). Two native tools + the execute proxy mirror production shapes.
CONTRACT_DUMP = {
    "version": 3,
    "abi": {"version": 1, "policy": "pi_tool_spec.v1"},
    "registry_generation": "gen-contract",
    "surface_fingerprint": "fp-contract",
    "tools": [
        {
            "name": "webgis_map_intent",
            "label": "Map Intent",
            "description": "Parse user intent for a map task",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            "spec": {"fingerprint": "fp-map-intent", "version": 1, "policy": "pi_tool_spec.v1"},
        },
        {
            "name": "webgis_cartography_status",
            "label": "Cartography Status",
            "description": "Zero-arg status read",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            "spec": {"fingerprint": "fp-status", "version": 1, "policy": "pi_tool_spec.v1"},
        },
    ],
    "default_active": ["webgis_map_intent", "webgis_cartography_status"],
    "execute_proxy": "webgis_execute",
}

STUB_LLM_JS = r"""
import http from 'node:http';
const port = Number(process.env.STUB_PORT || 0);
const mode = process.env.STUB_MODE || 'tool-then-text';
const requests = [];
const srv = http.createServer((req, res) => {
  let body = ''; req.on('data', (c) => (body += c));
  req.on('end', async () => {
    requests.push(body);
    const parsed = JSON.parse(body || '{}');
    const msgs = parsed.messages || [];
    const last = msgs[msgs.length - 1];
    const wantsTool = mode === 'tool-then-text' && !(last && last.role === 'tool');
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    const sse = (o) => res.write(`data: ${JSON.stringify(o)}\n\n`);
    const id = 'c' + requests.length;
    if (wantsTool) {
      sse({ id, object: 'chat.completion.chunk', created: 1, model: 'stub',
        choices: [{ index: 0, delta: { tool_calls: [{ index: 0, id: 'call_1',
          type: 'function',
          function: { name: process.env.STUB_TOOL || 'webgis_map_intent',
                       arguments: process.env.STUB_TOOL_ARGS || '{"query":"成都小学分布"}' } }] } }] });
      sse({ id, object: 'chat.completion.chunk', created: 1, model: 'stub',
        choices: [{ index: 0, delta: {}, finish_reason: 'tool_calls' }] });
    } else {
      const delayS = Number(process.env.STUB_DELAY_SECOND_S || 0);
      if (delayS > 0) { await new Promise((r) => setTimeout(r, delayS * 1000)); }
      sse({ id, object: 'chat.completion.chunk', created: 1, model: 'stub',
        choices: [{ index: 0, delta: { content: process.env.STUB_TEXT || 'final answer text' } }] });
      sse({ id, object: 'chat.completion.chunk', created: 1, model: 'stub',
        choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] });
    }
    res.write('data: [DONE]\n\n'); res.end();
  });
});
// slow mode: the tool call itself is emitted late so abort lands mid-turn
if (mode === 'slow-tool') {
  // handled by the tool extension, not here
}
srv.listen(port, '127.0.0.1', () => console.error(`STUB_READY ${srv.address().port}`));
process.on('SIGTERM', () => process.exit(0));
"""

PROBE_JS = r"""
// Drive the real rpc-entry: handshake -> scripted turn(s) -> (optional abort)
// -> collect every stdout line as JSON. Usage:
//   node probe.mjs <entry> <ext> <dump> <stubPort> <promptFile> [--abort-after-tool-start]
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const [entry, ext, dump, stubPort, promptFile] = process.argv.slice(2);
const abortAfterTool = process.argv.includes('--abort-after-tool-start');
const slowTool = process.argv.includes('--slow-tool');

const agentDir = mkdtempSync(join(tmpdir(), 'pi-contract-'));
writeFileSync(join(agentDir, 'models.json'), JSON.stringify({
  providers: { webgis: {
    baseUrl: `http://127.0.0.1:${stubPort}/v1`,
    api: 'openai-completions', apiKey: '$STUB_KEY',
    compat: { supportsDeveloperRole: false, supportsReasoningEffort: false },
    models: [{ id: 'stub-model', name: 'stub-model', reasoning: false }],
  } },
}));
writeFileSync(join(agentDir, 'settings.json'), JSON.stringify({
  defaultProvider: 'webgis', defaultModel: 'stub-model' }));
// slow tool variant: swap the dump's tool for a sleeping one via env is not
// possible (dump is static) — instead the slow behavior lives in the stub
// LLM's second response delay. For abort tests we abort after the FIRST
// tool_execution_start regardless.

const child = spawn('node', [entry, '--mode', 'rpc', '--no-session',
  '--no-builtin-tools', '--extension', ext], {
  env: { ...process.env, PI_CODING_AGENT_DIR: agentDir,
    PI_SESSION_DIR: join(agentDir, 'sessions'), PI_OFFLINE: '1',
    PI_SKIP_VERSION_CHECK: '1', WEBGIS_NATIVE_TOOLS_PATH: dump,
    WEBGIS_API_BASE: process.env.WEBGIS_API_BASE || 'http://127.0.0.1:9',
    WEBGIS_BRIDGE_SECRET: 'contract-secret', STUB_KEY: 'k' },
  stdio: ['pipe', 'pipe', 'pipe'],
});
let out = ''; let err = '';
child.stdout.setEncoding('utf8'); child.stdout.on('data', (d) => (out += d));
child.stderr.setEncoding('utf8'); child.stderr.on('data', (d) => (err += d));
const send = (o) => child.stdin.write(JSON.stringify(o) + '\n');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

await sleep(1200);
send({ id: '1', type: 'get_state' });
await sleep(250);
const prompt = readFileSync(promptFile, 'utf8');
send({ id: '2', type: 'prompt', sessionId: 'contract-session', message: prompt });

let aborted = false;
if (abortAfterTool) {
  const dl = Date.now() + 20000;
  while (Date.now() < dl) {
    await sleep(80);
    if (out.includes('tool_execution_start')) break;
  }
  await sleep(300);
  send({ id: '3', type: 'abort' });
  aborted = true;
}

const deadline = Date.now() + 30000;
while (Date.now() < deadline) {
  await sleep(120);
  if (out.includes('agent_settled')) break;
}
await sleep(400);
child.kill('SIGKILL');

const lines = out.split('\n').filter(Boolean);
const parsed = [];
for (const line of lines) { try { parsed.push(JSON.parse(line)); } catch { parsed.push({ unparseable: line.slice(0, 120) }); } }
process.stderr.write('RESULT=' + JSON.stringify({
  lines: parsed, stderrHead: err.split('\n').slice(0, 5).join(' | ').slice(0, 400), aborted,
}) + '\n');
"""


def _vendor_entry() -> Path | None:
    from app.services.chat.pi_compat import pi_vendor_identity

    ident = pi_vendor_identity()
    return Path(ident.entry_path) if ident.entry_built else None


def _needs_build() -> str:
    if _vendor_entry() is None:
        return (
            "vendored Pi RPC entry not built — run `bash scripts/build-vendor-pi.sh` "
            "(explicit skip, never silent-green)"
        )
    return ""


pytestmark = [
    pytest.mark.heavy,
    requires_node,
    pytest.mark.skipif(bool(_needs_build()), reason=_needs_build() or "vendor built"),
]


class _VendorHarness:
    """One stub LLM process; one rpc-entry probe per turn."""

    def __init__(self, stub_port: int, tmp: Path, mode: str = "tool-then-text",
                 tool: str | None = None, tool_args: str | None = None,
                 stub_text: str | None = None, delay_second_s: float = 0):
        self.stub_port = stub_port
        self.tmp = tmp
        self.stub_js = tmp / f"stub_{time.monotonic_ns()}.mjs"
        self.stub_js.write_text(STUB_LLM_JS, encoding="utf-8")
        self.probe_js = tmp / "probe.mjs"
        self.probe_js.write_text(PROBE_JS, encoding="utf-8")
        self.dump = tmp / "native-tools.json"
        self.dump.write_text(json.dumps(CONTRACT_DUMP), encoding="utf-8")
        env = {**os.environ, "STUB_PORT": str(stub_port), "STUB_MODE": mode}
        if tool:
            env["STUB_TOOL"] = tool
        if tool_args:
            env["STUB_TOOL_ARGS"] = tool_args
        if stub_text:
            env["STUB_TEXT"] = stub_text
        if delay_second_s:
            env["STUB_DELAY_SECOND_S"] = str(delay_second_s)
        self.stub_env = env
        self.stub_proc: subprocess.Popen | None = None

    def start_stub(self) -> None:
        """Boot the stub on an OS-assigned loopback port (STUB_PORT=0) and
        self-verify the port is actually connectable from another process —
        some CI sandboxes filter loopback by port range, and a silently
        unreachable stub would surface as misleading vendor retry events."""
        self.stub_port = _boot_stub(self.stub_js, self.stub_env, self.tmp)
        self.stub_env["STUB_PORT"] = str(self.stub_port)
        _assert_loopback_reachable(self.stub_port)

    def stop_stub(self) -> None:
        if self.stub_proc:
            self.stub_proc.terminate()
            self.stub_proc.wait(timeout=3)
            self.stub_proc = None
        while _STUB_PROCS:
            proc = _STUB_PROCS.pop()
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()

    def run_turn(self, prompt: str, *, abort_after_tool: bool = False,
                 api_base: str = "http://127.0.0.1:9") -> dict:
        prompt_file = self.tmp / f"prompt_{time.monotonic_ns()}.txt"
        prompt_file.write_text(prompt, encoding="utf-8")
        args = [
            "node", str(self.probe_js), str(_vendor_entry()), str(EXTENSION_PATH),
            str(self.dump), str(self.stub_port), str(prompt_file),
        ]
        if abort_after_tool:
            args.append("--abort-after-tool-start")
        env = {**self.stub_env, "WEBGIS_API_BASE": api_base}
        res = subprocess.run(
            args, capture_output=True, text=True, timeout=90, env=env,
        )
        payload = None
        for line in reversed(res.stderr.splitlines()):
            if line.startswith("RESULT="):
                payload = json.loads(line[len("RESULT="):])
                break
        assert payload is not None, f"probe produced no RESULT: rc={res.returncode} err={res.stderr[:600]}"
        return payload


def _boot_stub(js_path: Path, env: dict, tmp: Path) -> int:
    """Boot a stub whose stderr announces ``STUB_READY <port>``; poll a
    stderr FILE (no pipe-buffering games) and return the assigned port."""
    err_file = tmp / f"stub_err_{time.monotonic_ns()}.log"
    with open(err_file, "w") as fh:
        proc = subprocess.Popen(
            ["node", str(js_path)],
            stdout=subprocess.DEVNULL, stderr=fh, env=env,
        )
    deadline = time.monotonic() + 10
    port = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"stub crashed at boot: {err_file.read_text()[:400]}")
        text = err_file.read_text()
        m = re.search(r"STUB_READY (\d+)", text)
        if m:
            port = int(m.group(1))
            break
        time.sleep(0.1)
    assert port, f"stub did not announce readiness: {err_file.read_text()[:400]}"
    _STUB_PROCS.append(proc)
    return port


_STUB_PROCS: list[subprocess.Popen] = []


def _assert_loopback_reachable(port: int) -> None:
    import urllib.request

    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2)
    except urllib.error.HTTPError:
        pass  # any HTTP answer proves connectability
    except Exception as exc:  # noqa: BLE001
        pytest.skip(
            f"loopback port {port} not connectable in this environment ({exc}); "
            "vendor child would see retries — environment limitation, not product"
        )


@pytest.fixture()
def make_harness(tmp_path: Path):
    """Factory: options (delay_second_s, tool, ...) must reach the STUB's boot
    env — they are read by the stub process itself, so they cannot be injected
    after boot."""
    made: list[_VendorHarness] = []

    def _make(**kwargs) -> _VendorHarness:
        h = _VendorHarness(0, tmp_path, **kwargs)  # 0 = OS-assigned port
        h.start_stub()
        made.append(h)
        return h

    try:
        yield _make
    finally:
        for h in made:
            h.stop_stub()


@pytest.fixture()
def harness(make_harness):
    return make_harness()


def _events(result: dict) -> list[dict]:
    return [row for row in result["lines"] if isinstance(row, dict) and row.get("type") and "id" not in row]


def _responses(result: dict) -> list[dict]:
    return [row for row in result["lines"] if isinstance(row, dict) and row.get("type") == "response"]


TURN_PROMPT = (
    "成都小学分布\n\n"
    "[WEBGIS_TURN_CONTEXT:contract-session.turn-0001]\n"
    "(Internal routing context; do not quote or modify this marker.)"
)


async def test_handshake_and_prompt_acceptance(harness):
    result = harness.run_turn(TURN_PROMPT)
    responses = _responses(result)
    state = next((r for r in responses if r.get("command") == "get_state"), None)
    assert state is not None and state.get("success") is True
    assert state["data"]["model"]["provider"] == "webgis"
    prompt_resp = next((r for r in responses if r.get("command") == "prompt"), None)
    assert prompt_resp is not None and prompt_resp.get("success") is True
    disposition = (prompt_resp.get("data") or {}).get("disposition")
    assert disposition in ("started", "queued"), f"unexpected disposition {disposition!r}"


async def test_full_turn_event_sequence_and_sole_terminal(harness):
    """Wire contract: the vocabulary the bridge/mapper rely on, in order, with
    agent_settled appearing exactly once as the sole terminator."""
    result = harness.run_turn(TURN_PROMPT)
    events = _events(result)
    types = [e["type"] for e in events]

    # tool-call streaming deltas (mapper consumes assistantMessageEvent only)
    updates = [e for e in events if e["type"] == "message_update"]
    kinds = {u.get("assistantMessageEvent", {}).get("type") for u in updates}
    assert "toolcall_start" in kinds and "toolcall_end" in kinds
    toolcall_end = next(
        u["assistantMessageEvent"]["toolCall"] for u in updates
        if u.get("assistantMessageEvent", {}).get("type") == "toolcall_end"
    )
    assert toolcall_end["name"] == "webgis_map_intent"

    # tool execution events carry the fields the mapper/step events need
    assert "tool_execution_start" in types and "tool_execution_end" in types
    tstart = next(e for e in events if e["type"] == "tool_execution_start")
    tend = next(e for e in events if e["type"] == "tool_execution_end")
    assert tstart["toolCallId"] == tend["toolCallId"] == toolcall_end["id"]
    assert tstart["toolName"] == "webgis_map_intent"
    # callback target is a dead port → the extension surfaces an error result
    assert tend["isError"] is True

    # agent_end is NOT terminal; carries the authoritative messages list
    aend = next(e for e in events if e["type"] == "agent_end")
    assert aend.get("willRetry") is False
    assert isinstance(aend.get("messages"), list) and aend["messages"]

    # agent_settled: exactly once, after agent_end, sole terminator
    assert types.count("agent_settled") == 1
    assert types.index("agent_end") < types.index("agent_settled")

    # events never carry an id (response/event discrimination contract)
    assert all("id" not in e for e in events)


async def test_abort_mid_tool_settles_aborted(make_harness):
    """Abort lands while the second provider response is deliberately slow —
    the vendor must cancel the in-flight request, surface the extension's
    cancelled tool outcome, and settle exactly once with aborted:true."""
    harness = make_harness(delay_second_s=4)
    result = harness.run_turn(TURN_PROMPT, abort_after_tool=True)
    events = _events(result)
    settled = next(e for e in events if e["type"] == "agent_settled")
    assert settled.get("aborted") is True
    assert [e["type"] for e in events].count("agent_settled") == 1


async def test_unicode_line_separators_do_not_break_framing(harness):
    """U+2028/U+2029 inside the prompt must not be treated as record
    separators by the vendor's JSONL writer (strict LF-only framing)."""
    prompt = (
        "分析line1line2 成都小学分布\n\n"
        "[WEBGIS_TURN_CONTEXT:contract-session.turn-u2028]"
    )
    result = harness.run_turn(prompt)
    types = [e["type"] for e in _events(result)]
    assert "agent_settled" in types
    assert not any(isinstance(row, dict) and "unparseable" in row for row in result["lines"])


async def test_no_builtin_coding_tools_reachable(harness):
    """GIS sessions must not see bash/read/write/edit — exposure AND execution
    both fail-closed: the tool never appears in the model-visible declarations."""
    result = harness.run_turn(TURN_PROMPT)
    declared: set[str] = set()
    for e in _events(result):
        if e["type"] == "message_start" and e.get("message", {}).get("role") == "system":
            for t in e["message"].get("toolsAdded", []):
                declared.add(t.get("name"))
    assert declared, "expected tool declarations on the system message"
    leaked = declared & BUILTIN_CODING_TOOLS
    assert not leaked, f"builtin coding tools leaked into GIS session: {sorted(leaked)}"
    assert "webgis_execute" in declared  # proxy front door present


async def test_persona_injected_and_turn_token_recovered(harness):
    """The model-visible system prompt is GeoAgent (coding persona replaced),
    and the model-visible tool list contains only the GIS surface — verified
    end-to-end by asserting on what the stub LLM actually received."""
    import os

    rec_js = harness.tmp / "rec_stub.mjs"
    rec_js.write_text(
        "import { appendFileSync } from 'node:fs';\n" + STUB_LLM_JS.replace(
            "requests.push(body);",
            "requests.push(body); appendFileSync(process.env.REC_FILE, body.slice(0, 24000) + '\\n');",
        ),
        encoding="utf-8",
    )
    rec_file = harness.tmp / "rec.jsonl"
    rec_file.write_text("", encoding="utf-8")
    rec_port = _boot_stub(
        rec_js, {**os.environ, "STUB_PORT": "0", "STUB_MODE": "tool-then-text",
                 "REC_FILE": str(rec_file)}, harness.tmp,
    )
    _assert_loopback_reachable(rec_port)

    prompt_file = harness.tmp / "persona_prompt.txt"
    prompt_file.write_text(TURN_PROMPT, encoding="utf-8")
    res = subprocess.run(
        ["node", str(harness.probe_js), str(_vendor_entry()), str(EXTENSION_PATH),
         str(harness.dump), str(rec_port), str(prompt_file)],
        capture_output=True, text=True, timeout=90,
        env={**os.environ, "WEBGIS_API_BASE": "http://127.0.0.1:9"},
    )
    assert '"agent_settled"' in res.stderr, f"turn did not settle: {res.stderr[:400]}"
    rows = [json.loads(line) for line in rec_file.read_text(encoding="utf-8").splitlines() if line]
    assert rows, "recording stub captured no requests"
    first = rows[0]
    system = ""
    tool_names: set[str] = set()
    msgs = first.get("messages") or []
    for m in msgs:
        if m.get("role") == "system":
            system = str(m.get("content") or "")
            break
    if not system:
        system = str(first.get("system") or "")  # provider-specific top-level field
    for t in first.get("tools") or []:
        fn = t.get("function") or {}
        tool_names.add(fn.get("name") or t.get("name") or "")
    assert system.startswith("You are GeoAgent"), (
        f"persona must lead the system prompt; head={system[:160]!r}"
    )
    assert "coding assistant" not in system[:120], "vendor coding persona must be replaced"
    assert "webgis_map_intent" in tool_names
    assert not (tool_names & BUILTIN_CODING_TOOLS)
