#!/usr/bin/env python3
"""pi_doctor — vendored Pi runtime diagnostics (ADR-0285 / goal-pi-native-host-vnext).

One command answers: *can this checkout actually run the bundled Pi bridge, and
if not, why?* Reports, per domain, PASS / WARN / FAIL with concrete remediation.

Checks (fail-open = WARN only; fail-closed = FAIL exit code):
  1. vendor      submodule pin vs superproject gitlink, describe/tag, protocol
                 floor (>= v1.5.0)                      → WARN on drift
  2. build       dist/bundle rpc-entry presence (+ node engine >= 22.19)
                 → FAIL when missing (Pi cannot start; bridge silently falls
                 back to ChatEngine — the #400 failure mode this tool kills)
  3. extension   app/extensions/webgis-tools/index.mjs parses (node --check)
                 and registers tools against a minimal dump
                 → FAIL (GIS host without its extension is a coding agent)
  4. surface     native-tools dump shape: v2/v3, execute proxy, native names,
                 fingerprint presence on v3                → FAIL on malformed
  5. rpc         live handshake: spawn the real rpc-entry (get_state), send a
                 scripted prompt against a local stub LLM, verify the event
                 vocabulary and agent_settled termination, then abort
                 → FAIL on protocol drift (skipped when --offline)
  6. env         spawn env contract (bridge secret resolvable, entry args
                 flags present in the built vendor)        → WARN

Usage:
  python scripts/pi_doctor.py            # all checks incl. live RPC probe
  python scripts/pi_doctor.py --offline  # skip the subprocess probe
  python scripts/pi_doctor.py --json     # machine-readable report

Exit codes: 0 = all pass (WARN allowed); 1 = at least one FAIL.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

EXTENSION_PATH = REPO_ROOT / "app" / "extensions" / "webgis-tools" / "index.mjs"

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"


@dataclass
class Report:
    domain: str
    status: str
    detail: str
    remediation: str = ""
    facts: dict = field(default_factory=dict)


def _run(cmd: list[str], cwd: Path | None = None, timeout: float = 20.0) -> tuple[int, str]:
    try:
        res = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout
        )
        return res.returncode, (res.stdout + res.stderr).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)


def _live_registry():
    """A real ToolRegistry when importable in this checkout (None otherwise)."""
    try:
        from app.tools import init_tools
        from app.tools.registry import ToolRegistry

        registry = ToolRegistry()
        init_tools(registry)
        return registry
    except Exception:  # noqa: BLE001 — heavy deps may be absent
        return None


# ── 1. vendor identity ──────────────────────────────────────────────────────

def check_vendor() -> Report:
    from app.services.chat.pi_compat import PI_MIN_VENDOR_TAG, pi_vendor_identity

    ident = pi_vendor_identity()
    facts = {
        "head": ident.head_commit,
        "describe": ident.describe,
        "expected_pin": ident.expected_pin,
        "entry": ident.entry_kind,
        "node": ident.node_version,
    }
    if ident.matches_expected_pin is False:
        return Report(
            "vendor", WARN,
            f"working tree ({ident.head_commit}) drifted off recorded gitlink "
            f"({ident.expected_pin})",
            "git -C vendor/pi checkout <recorded-pin>", facts,
        )
    if ident.meets_floor is False:
        return Report(
            "vendor", FAIL,
            f"Pi {ident.describe} is below the bridge protocol floor {PI_MIN_VENDOR_TAG}",
            "upgrade the submodule pin together with the bridge contract tests", facts,
        )
    if ident.head_commit is None:
        return Report(
            "vendor", WARN,
            "no git metadata (deployed image?); identity limited to filesystem facts",
            "", facts,
        )
    return Report(
        "vendor", PASS,
        f"pin ok ({ident.describe or ident.head_commit}); floor {PI_MIN_VENDOR_TAG}", "", facts,
    )


# ── 2. build artifacts ─────────────────────────────────────────────────────

def check_build() -> Report:
    from app.services.chat.pi_compat import pi_vendor_identity

    ident = pi_vendor_identity()
    if not ident.entry_built:
        return Report(
            "build", FAIL,
            "no built rpc-entry (neither dist/bundle nor dist) — the bridge will "
            "silently fall back to ChatEngine (#400)",
            "bash scripts/build-vendor-pi.sh",
        )
    node_min = (22, 19, 0)
    if ident.node_version:
        m = re.match(r"v(\d+)\.(\d+)\.(\d+)", ident.node_version)
        got = tuple(int(x) for x in m.groups()) if m else (0, 0, 0)
        if got < node_min:
            return Report(
                "build", FAIL,
                f"node {ident.node_version} < engines floor v22.19.0",
                "install node >= 22.19 (vendor/pi engines field)", {"entry": ident.entry_kind},
            )
    entry = ident.entry_kind or "?"
    return Report(
        "build", PASS, f"rpc entry ready ({entry}); node {ident.node_version or 'unknown'}",
        facts={"entry": ident.entry_kind, "entry_path": ident.entry_path},
    )


# ── 3. extension ───────────────────────────────────────────────────────────

def check_extension() -> Report:
    if not EXTENSION_PATH.is_file():
        return Report("extension", FAIL, f"{EXTENSION_PATH} missing", "restore the file")
    if shutil.which("node") is None:
        return Report("extension", SKIP, "node not on PATH", "install node to enable check")
    rc, out = _run(["node", "--check", str(EXTENSION_PATH)])
    # --check works for CommonJS; for ESM use dynamic import instead.
    if rc != 0:
        rc, out = _run([
            "node", "--input-type=module", "--eval",
            f"import({json.dumps(EXTENSION_PATH.as_uri())}).then(() => 0).catch((e) => "
            "{ console.error(String(e)); process.exit(1); })",
        ])
        if rc != 0:
            return Report("extension", FAIL, f"extension import failed: {out[:400]}", "")
    # Load-time contract: registering against a mock pi must not throw and must
    # NOT call setActiveTools during load (v1.5.0 two-phase loading).
    dump = {
        "version": 3,
        "tools": [{
            "name": "webgis_map_intent", "label": "l", "description": "d",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        }],
        "default_active": ["webgis_map_intent"],
        "execute_proxy": "webgis_execute",
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(dump, fh)
        dump_path = fh.name
    script = (
        f"import webgisExt from {json.dumps(EXTENSION_PATH.as_uri())};\n"
        f"process.env.WEBGIS_NATIVE_TOOLS_PATH = {json.dumps(dump_path)};\n"
        "let loadActiveCalls = 0; let registered = 0;\n"
        "const pi = { registerTool: () => { registered += 1; },"
        " setActiveTools: () => { loadActiveCalls += 1; }, on: () => {} };\n"
        "webgisExt(pi);\n"
        "process.stderr.write(JSON.stringify({ loadActiveCalls, registered }));\n"
    )
    rc, out = _run(["node", "--input-type=module", "--eval", script])
    if rc != 0:
        return Report("extension", FAIL, f"extension load failed: {out[:400]}", "")
    try:
        payload = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return Report("extension", FAIL, f"unexpected load output: {out[:200]}", "")
    if payload.get("loadActiveCalls", 0) != 0:
        return Report(
            "extension", FAIL,
            "setActiveTools called during extension load (vendor >=0.99 forbids "
            "actions while loading)",
            "defer the default projection to session_start",
        )
    if payload.get("registered", 0) < 2:  # dump tool + webgis_execute
        return Report("extension", FAIL, "no tools registered (dump unreadable?)", "")
    return Report("extension", PASS, f"loads clean; registers {payload['registered']} tools")


# ── 4. native surface dump ─────────────────────────────────────────────────

def check_surface() -> Report:
    from app.services.chat.pi_native_surface import (
        EXECUTE_PROXY_NAME,
        NATIVE_TOOL_NAMES,
        pi_surface_for_spawn,
    )

    registry = _live_registry()
    if registry is None:
        return Report("surface", SKIP, "ToolRegistry unavailable in this environment", "")
    try:
        surface = pi_surface_for_spawn(registry)
    except Exception as exc:  # noqa: BLE001 — registry unavailable in this env
        return Report("surface", SKIP, f"ToolRegistry unavailable here: {exc}", "")
    tools = surface.get("tools") if isinstance(surface, dict) else None
    if not isinstance(tools, list) or not tools:
        return Report("surface", FAIL, "spawn surface has no tools", "")
    names = {t.get("name") for t in tools if isinstance(t, dict)}
    missing_native = sorted(set(NATIVE_TOOL_NAMES) - names)
    if missing_native:
        return Report(
            "surface", FAIL,
            f"native tools missing from dump: {missing_native}",
            "check ToolRegistry initialization / tier gating",
        )
    v3 = surface.get("abi")
    fp_count = sum(1 for t in tools if isinstance(t, dict) and t.get("spec", {}).get("fingerprint"))
    if v3 is not None and fp_count == 0:
        return Report("surface", FAIL, "v3 dump carries no fingerprints (stale-surface gate blind)", "")
    return Report(
        "surface", PASS,
        f"{len(tools)} tools; native 7/7; proxy={EXECUTE_PROXY_NAME}; "
        f"abi={'v3' if v3 else 'v2'}; fingerprints={fp_count}",
    )


# ── 5. live RPC probe ──────────────────────────────────────────────────────

_STUB_LLM_JS = r"""
// minimal OpenAI-compatible stub: tool call once, then final text
import http from 'node:http';
const port = Number(process.env.DOCTOR_STUB_PORT || 18790);
const calls = { n: 0 };
const srv = http.createServer((req, res) => {
  let body = ''; req.on('data', (c) => (body += c));
  req.on('end', () => {
    calls.n += 1;
    const parsed = JSON.parse(body || '{}');
    const msgs = parsed.messages || [];
    const last = msgs[msgs.length - 1];
    const wantsTool = !(last && last.role === 'tool');
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    const sse = (o) => res.write(`data: ${JSON.stringify(o)}\n\n`);
    const id = `c${calls.n}`;
    if (wantsTool) {
      sse({ id, object: 'chat.completion.chunk', created: 1, model: 'stub',
        choices: [{ index: 0, delta: { tool_calls: [{ index: 0, id: 'call_d', type: 'function',
          function: { name: 'webgis_map_intent', arguments: '{"query":"doctor"}' } }] } }] });
      sse({ id, object: 'chat.completion.chunk', created: 1, model: 'stub',
        choices: [{ index: 0, delta: {}, finish_reason: 'tool_calls' }] });
    } else {
      sse({ id, object: 'chat.completion.chunk', created: 1, model: 'stub',
        choices: [{ index: 0, delta: { content: 'doctor ok' } }] });
      sse({ id, object: 'chat.completion.chunk', created: 1, model: 'stub',
        choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] });
    }
    res.write('data: [DONE]\n\n'); res.end();
  });
});
srv.listen(port, '127.0.0.1', () => console.log('stub up'));
"""

_PROBE_JS = r"""
// drive the real rpc-entry: handshake -> scripted turn -> settled -> abort
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const entry = process.argv[2];
const extPath = process.argv[3];
const dumpPath = process.argv[4];
const stubPort = process.argv[5] || '18790';

const agentDir = mkdtempSync(join(tmpdir(), 'pi-doctor-'));
writeFileSync(join(agentDir, 'models.json'), JSON.stringify({
  providers: { webgis: {
    baseUrl: `http://127.0.0.1:${stubPort}/v1`,
    api: 'openai-completions', apiKey: '$DOCTOR_KEY',
    compat: { supportsDeveloperRole: false, supportsReasoningEffort: false },
    models: [{ id: 'stub-model', name: 'stub-model', reasoning: false }],
  } },
}));
writeFileSync(join(agentDir, 'settings.json'), JSON.stringify({
  defaultProvider: 'webgis', defaultModel: 'stub-model' }));

const child = spawn('node', [entry, '--mode', 'rpc', '--no-session',
  '--no-builtin-tools', '--extension', extPath], {
  env: { ...process.env, PI_CODING_AGENT_DIR: agentDir,
    PI_SESSION_DIR: join(agentDir, 'sessions'), PI_OFFLINE: '1',
    PI_SKIP_VERSION_CHECK: '1', WEBGIS_NATIVE_TOOLS_PATH: dumpPath,
    WEBGIS_API_BASE: 'http://127.0.0.1:1',  // unreachable on purpose: no bridge here
    WEBGIS_BRIDGE_SECRET: 'doctor-secret', DOCTOR_KEY: 'k' },
  stdio: ['pipe', 'pipe', 'pipe'],
});
let out = ''; let err = '';
child.stdout.setEncoding('utf8'); child.stdout.on('data', (d) => (out += d));
child.stderr.setEncoding('utf8'); child.stderr.on('data', (d) => (err += d));
const send = (o) => child.stdin.write(JSON.stringify(o) + '\n');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

await sleep(1200);
send({ id: '1', type: 'get_state' });
await sleep(300);
send({ id: '2', type: 'prompt', sessionId: 'doctor',
  message: 'doctor probe\n\n[WEBGIS_TURN_CONTEXT:doctor.turn-1]' });
const deadline = Date.now() + 25000;
while (Date.now() < deadline) {
  await sleep(150);
  if (out.includes('agent_settled')) break;
}
await sleep(300);
send({ id: '3', type: 'abort' });
await sleep(400);
child.kill('SIGKILL');

const lines = out.split('\n').filter(Boolean).map((l) => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean);
const types = lines.filter((l) => l.type && !l.id).map((l) => l.type);
const responses = lines.filter((l) => l.type === 'response');
const result = {
  handshake: responses.some((r) => r.command === 'get_state' && r.success),
  promptAccepted: responses.some((r) => r.command === 'prompt' && r.success),
  sawToolcallEnd: types.includes('message_update') && out.includes('toolcall_end'),
  sawToolExecution: types.includes('tool_execution_start') && types.includes('tool_execution_end'),
  settled: out.includes('"agent_settled"'),
  abortedFlag: out.includes('"agent_settled","aborted":true') || out.includes('"agent_settled", "aborted": true') || /"type":"agent_settled","aborted":true/.test(out),
  eventVocabulary: [...new Set(types)],
  stderrHead: err.split('\n').slice(0, 3).join(' | ').slice(0, 300),
};
process.stderr.write(JSON.stringify(result));
"""


def check_rpc() -> Report:
    from app.services.chat.pi_compat import pi_vendor_identity
    from app.services.chat.pi_native_surface import pi_surface_for_spawn

    if shutil.which("node") is None:
        return Report("rpc", SKIP, "node not on PATH", "")
    ident = pi_vendor_identity()
    if not ident.entry_built:
        return Report("rpc", SKIP, "rpc entry not built (see build check)", "bash scripts/build-vendor-pi.sh")
    registry = _live_registry()
    if registry is None:
        return Report("rpc", SKIP, "ToolRegistry unavailable in this environment", "")
    surface = pi_surface_for_spawn(registry)

    with tempfile.TemporaryDirectory(prefix="pi-doctor-") as td:
        td_path = Path(td)
        dump_path = td_path / "native-tools.json"
        dump_path.write_text(json.dumps(surface), encoding="utf-8")
        stub_js = td_path / "stub_llm.mjs"
        stub_js.write_text(_STUB_LLM_JS, encoding="utf-8")
        probe_js = td_path / "probe.mjs"
        probe_js.write_text(_PROBE_JS, encoding="utf-8")

        stub = subprocess.Popen(
            ["node", str(stub_js)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**__import__("os").environ, "DOCTOR_STUB_PORT": "18790"},
        )
        try:
            time.sleep(0.6)
            rc, out = _run([
                "node", str(probe_js), str(ident.entry_path), str(EXTENSION_PATH),
                str(dump_path), "18790",
            ], timeout=45.0)
        finally:
            stub.terminate()
        if rc != 0:
            return Report("rpc", FAIL, f"probe crashed: {out[:400]}", "")
        try:
            payload = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return Report("rpc", FAIL, f"unreadable probe output: {out[:200]}", "")
        problems = []
        if not payload.get("handshake"):
            problems.append("get_state handshake failed")
        if not payload.get("promptAccepted"):
            problems.append("prompt not accepted")
        if not payload.get("sawToolExecution"):
            problems.append("no tool_execution_* events (surface/extension broken?)")
        if not payload.get("settled"):
            problems.append("agent_settled never arrived (termination contract broken)")
        if problems:
            return Report(
                "rpc", FAIL, "; ".join(problems),
                "vendor protocol drifted — compare against tests/integration/test_pi_vendor_contract.py",
                payload,
            )
        return Report(
            "rpc", PASS,
            f"handshake/turn/settled ok; events: {','.join(payload['eventVocabulary'][:10])}",
            facts={"events": payload["eventVocabulary"], "stderr": payload.get("stderrHead", "")},
        )


# ── 6. env contract ────────────────────────────────────────────────────────

def check_env() -> Report:
    from app.services.chat.pi_compat import pi_vendor_identity

    ident = pi_vendor_identity()
    notes = []
    secret_ok = False
    try:
        from app.core.bridge_secret import get_bridge_secret
        secret_ok = bool(get_bridge_secret())
    except Exception as exc:  # noqa: BLE001
        notes.append(f"bridge secret unavailable: {exc}")
    if not secret_ok:
        notes.append("bridge secret empty/unresolvable — extension callbacks will 401")
    if ident.entry_kind == "dist":
        notes.append("using non-bundle dist entry: workspace node_modules must stay installed")
    if notes:
        return Report("env", WARN, "; ".join(notes), "")
    return Report("env", PASS, "bridge secret resolvable; entry self-contained")


CHECKS = [
    ("vendor", check_vendor),
    ("build", check_build),
    ("extension", check_extension),
    ("surface", check_surface),
    ("rpc", check_rpc),
    ("env", check_env),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--offline", action="store_true", help="skip the live RPC subprocess probe")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    reports: list[Report] = []
    for name, fn in CHECKS:
        if args.offline and name == "rpc":
            reports.append(Report(name, SKIP, "offline mode"))
            continue
        try:
            reports.append(fn())
        except Exception as exc:  # noqa: BLE001 — doctor must always finish
            reports.append(Report(name, FAIL, f"check crashed: {exc}"))

    if args.json:
        print(json.dumps(
            [{"domain": r.domain, "status": r.status, "detail": r.detail,
              "remediation": r.remediation, "facts": r.facts} for r in reports],
            ensure_ascii=False, indent=2))
    else:
        for r in reports:
            mark = {"PASS": "✔", "WARN": "△", "FAIL": "✘", "SKIP": "–"}[r.status]
            print(f"{mark} {r.domain:<10} {r.status}  {r.detail}")
            if r.remediation:
                print(f"             fix: {r.remediation}")
        print()

    failed = [r for r in reports if r.status == FAIL]
    if failed:
        print(f"pi_doctor: {len(failed)} FAIL — Pi bridge cannot be trusted in this checkout")
        return 1
    print("pi_doctor: no FAIL (WARN allowed) — vendored Pi runtime healthy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
