"""pi-schema 参数校验/强制转换 golden 活体比对（ADR-0285）。

对**真实 v1.5.0 rpc-entry** 逐例执行 golden corpus：stub LLM 按调用序号发
出各 case 的工具实参；打点扩展记录 execute() 实际收到的（归一化后）实参；
校验拒绝则从 tool_execution_end 的错误文本取证。单进程单 turn 串行跑完
全部 case。

门控：需要 node + 已构建 vendor（与 test_pi_vendor_contract.py 同纪律）。
未构建时只做 golden 结构自检并显式 skip 活体比对。

发现漂移时的处置：**先核实上游行为再改 golden** —— golden 的每次变更都
必须引用上游 commit/文档（防欺骗红线：不为了绿灯硬改期望）。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PATH = REPO_ROOT / "tests" / "fixtures" / "pi_schema_coercion_golden.json"

pytest.importorskip("app.services.chat.pi_compat", reason="app package importable")

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="coercion golden lane needs node on PATH"
)


def _vendor_entry() -> Path | None:
    from app.services.chat.pi_compat import pi_vendor_identity

    ident = pi_vendor_identity()
    return Path(ident.entry_path) if ident.entry_built else None


pytestmark = [pytest.mark.heavy, requires_node]


# 打点扩展：注册 golden schema 的工具；把 execute() 收到的实参逐条落到
# EXEC_FILE（JSONL）。校验拒绝不会到这里 —— 由 stdout 事件取证。
COERCION_EXT = r"""
import { appendFileSync, writeFileSync } from 'node:fs';
const SCHEMA = JSON.parse(process.env.COERCION_SCHEMA);
export default function coercion(pi) {
  pi.registerTool({
    name: 'coerce_probe', label: 'coerce', description: 'probe',
    parameters: SCHEMA,
    async execute(toolCallId, params) {
      appendFileSync(process.env.EXEC_FILE, JSON.stringify(params) + '\n');
      return { content: [{ type: 'text', text: 'ok' }], details: {}, isError: false };
    },
  });
}
"""

# 单进程驱动：stub 按调用序号取 case 实参；跑完最后一个 case 后给终文。
CASE_STUB = r"""
import http from 'node:fs';
"""

DRIVER = r"""
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import http from 'node:http';

const entry = process.argv[2];
const extPath = process.argv[3];
const schemaPath = process.argv[4];
const casesPath = process.argv[5];
const execFile = process.argv[6];
const stubErrFile = process.argv[7];

const cases = JSON.parse(readFileSync(casesPath, 'utf8'));
const schema = JSON.parse(readFileSync(schemaPath, 'utf8'));

let llmCall = 0;
const stub = http.createServer((req, res) => {
  let body = ''; req.on('data', (c) => (body += c));
  req.on('end', () => {
    llmCall += 1;
    const parsed = JSON.parse(body || '{}');
    const msgs = parsed.messages || [];
    const last = msgs[msgs.length - 1];
    const lastIsTool = !!(last && (last.role === 'tool' || last.role === 'toolResult'));
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    const sse = (o) => res.write(`data: ${JSON.stringify(o)}\n\n`);
    const id = 'c' + llmCall;
    if (lastIsTool && llmCall - 1 >= cases.length) {
      sse({ id, object: 'chat.completion.chunk', created: 1, model: 'stub',
        choices: [{ index: 0, delta: { content: 'all cases consumed' } }] });
      sse({ id, object: 'chat.completion.chunk', created: 1, model: 'stub',
        choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] });
      res.write('data: [DONE]\n\n'); res.end();
      return;
    }
    const idx = llmCall - 1;
    const c = cases[idx] ?? cases[cases.length - 1];
    sse({ id, object: 'chat.completion.chunk', created: 1, model: 'stub',
      choices: [{ index: 0, delta: { tool_calls: [{ index: 0, id: 'call_' + idx,
        type: 'function',
        function: { name: 'coerce_probe', arguments: JSON.stringify(c.model_args) } }] } }] });
    sse({ id, object: 'chat.completion.chunk', created: 1, model: 'stub',
      choices: [{ index: 0, delta: {}, finish_reason: 'tool_calls' }] });
    res.write('data: [DONE]\n\n'); res.end();
  });
});
const srv = stub;
await new Promise((resolve) => srv.listen(0, '127.0.0.1', () => {
  writeFileSync(stubErrFile, 'STUB_READY ' + srv.address().port + '\n');
  resolve();
}));

const agentDir = mkdtempSync(join(tmpdir(), 'pi-coerce-'));
writeFileSync(join(agentDir, 'models.json'), JSON.stringify({
  providers: { webgis: {
    baseUrl: `http://127.0.0.1:${srv.address().port}/v1`,
    api: 'openai-completions', apiKey: '$K',
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
    PI_SKIP_VERSION_CHECK: '1', COERCION_SCHEMA: JSON.stringify(schema),
    EXEC_FILE: execFile, K: 'k' },
  stdio: ['pipe', 'pipe', 'pipe'],
});
let out = ''; let err = '';
child.stdout.setEncoding('utf8'); child.stdout.on('data', (d) => (out += d));
child.stderr.setEncoding('utf8'); child.stderr.on('data', (d) => (err += d));
const send = (o) => child.stdin.write(JSON.stringify(o) + '\n');
await new Promise((r) => setTimeout(r, 1200));
send({ id: '1', type: 'prompt', sessionId: 'coerce',
  message: 'run all coercion cases' });
const deadline = Date.now() + 90000;
while (Date.now() < deadline) {
  await new Promise((r) => setTimeout(r, 200));
  const toolEnds = (out.match(/"tool_execution_end"/g) || []).length;
  if (toolEnds >= cases.length && out.includes('agent_settled')) break;
  if (out.includes('agent_settled')) break;
}
await new Promise((r) => setTimeout(r, 400));
child.kill('SIGKILL'); srv.close();
const lines = out.split('\n').filter(Boolean).map((line) => { try { return JSON.parse(line); } catch { return null; } }).filter(Boolean);
const toolEnds = lines.filter((line) => line.type === 'tool_execution_end');
writeFileSync(process.argv[8], JSON.stringify(toolEnds));
process.stderr.write('DONE llm=' + llmCall + ' toolEnds=' + toolEnds.length + ' err=' + err.slice(0, 200) + '\n');
"""


def _golden() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def test_golden_structure_selfcheck():
    """无 vendor 也可运行的契约自检：schema/case/expect 结构完备，政策文本
    在档（活体比对 skip 时仍保证 golden 本身可机读、无歧义字段）。"""
    g = _golden()
    assert set(g) >= {"$meta", "tool_schema", "cases"}
    meta = g["$meta"]
    for key in ("purpose", "vendor_verified", "evidence", "policy"):
        assert meta.get(key), f"$meta.{key} missing"
    assert g["tool_schema"]["type"] == "object"
    props = g["tool_schema"]["properties"]
    ids = set()
    for case in g["cases"]:
        cid = case["id"]
        assert cid not in ids, f"duplicate case id {cid}"
        ids.add(cid)
        assert "model_args" in case and "expect" in case
        expect = case["expect"]
        assert expect.get("outcome") in ("executed", "rejected")
        if expect["outcome"] == "executed":
            assert "received" in expect or "received_contains" in expect
        else:
            assert expect.get("error_contains")
        # model_args 的键必须在 schema 属性内（unknown-field-rejected 除外，
        # 它的越权键正是被测对象）
        if cid != "unknown-field-rejected":
            for k in case["model_args"]:
                assert k in props, f"case {cid}: arg {k} not in schema"


@pytest.mark.skipif(_vendor_entry() is None, reason=(
    "vendored Pi RPC entry not built — run `bash scripts/build-vendor-pi.sh` "
    "(live coercion comparison skipped explicitly, never silent-green)"))
async def test_coercion_matrix_matches_golden(tmp_path: Path):
    g = _golden()
    cases = g["cases"]
    schema = g["tool_schema"]

    ext = tmp_path / "coerce_ext.mjs"
    ext.write_text(COERCION_EXT, encoding="utf-8")
    driver = tmp_path / "driver.mjs"
    driver.write_text(DRIVER, encoding="utf-8")
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps(cases), encoding="utf-8")
    exec_file = tmp_path / "exec.jsonl"
    exec_file.write_text("", encoding="utf-8")
    tool_ends_path = tmp_path / "tool_ends.json"
    stub_err = tmp_path / "stub_err.log"
    stub_err.write_text("", encoding="utf-8")

    import subprocess

    res = subprocess.run(
        ["node", str(driver), str(_vendor_entry()), str(ext), str(schema_path),
         str(cases_path), str(exec_file), str(stub_err), str(tool_ends_path)],
        capture_output=True, text=True, timeout=180,
        env={**__import__("os").environ, "WEBGIS_API_BASE": "http://127.0.0.1:1"},
    )
    assert "DONE" in res.stderr, f"driver failed: {res.stderr[:600]}"

    executed_rows = [
        json.loads(line) for line in exec_file.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    tool_ends = json.loads(tool_ends_path.read_text(encoding="utf-8"))
    error_texts = [
        str((te.get("result") or {}).get("content") or [{}])[0:400]
        if isinstance((te.get("result") or {}).get("content"), list)
        else str((te.get("result") or {}).get("content"))[:400]
        for te in tool_ends
    ]

    # executed 顺序 == golden 中 outcome=executed 的 case 顺序
    expected_exec = [c for c in cases if c["expect"]["outcome"] == "executed"]
    assert len(executed_rows) == len(expected_exec), (
        f"executed count drift: golden={len(expected_exec)} live={len(executed_rows)}; "
        f"tool_ends={len(tool_ends)} stderr={res.stderr[:300]}"
    )
    for case, received in zip(expected_exec, executed_rows):
        exp = case["expect"]
        if "received" in exp:
            assert received == exp["received"], (
                f"{case['id']}: received drift\n  golden={exp['received']}\n  live   ={received}"
            )
        else:  # received_contains
            for k, v in exp["received_contains"].items():
                assert received.get(k) == v, (
                    f"{case['id']}.{k}: drift golden={v!r} live={received.get(k)!r}"
                )

    # rejected cases：错误文本必须含期望关键词（按 golden case 顺序对齐
    # tool_ends —— rejected 的 execute 不触发，无行可对）
    assert len(tool_ends) == len(cases), (
        f"tool_execution_end count {len(tool_ends)} != cases {len(cases)} "
        f"(did the stub script terminate early? stderr={res.stderr[:300]})"
    )
    for case, text in zip(cases, error_texts):
        exp = case["expect"]
        if exp["outcome"] == "rejected":
            assert exp["error_contains"] in text, (
                f"{case['id']}: rejection text missing {exp['error_contains']!r}: {text[:200]}"
            )
        else:
            assert "Validation failed" not in text, (
                f"{case['id']}: expected executed but vendor rejected: {text[:200]}"
            )
