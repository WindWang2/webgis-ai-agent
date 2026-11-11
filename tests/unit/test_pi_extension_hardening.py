"""Tests for #1044: webgis-tools extension hardening.

Validates:
1. postToBridge 409/503 recovery guidance and fetch timeout.
2. loadNativeTools loud logging and error handling.
3. currentTurnToken scanning past 24 entries and no cross-context fallback.
4. write_native_tools_file atomic write.
5. NATIVE_TOOL_NAMES contract pinning Python, extension, and registry schema dump.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path
import pytest

from app.services.chat.pi_native_surface import (
    NATIVE_TOOL_NAMES,
    native_tools_for_pi,
    write_native_tools_file,
)
from app.tools import init_tools
from app.tools.registry import ToolRegistry


@pytest.fixture
def registry():
    r = ToolRegistry()
    init_tools(r)
    return r


EXTENSION_PATH = (
    Path(__file__).parents[2] / "app" / "extensions" / "webgis-tools" / "index.mjs"
)


def _run_node_script(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


# 未装 node 的机器上这些用例应 skip 而非 error（与
# tests/integration/test_pi_real_llm_e2e.py 的 node 守卫同语义）。
requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="needs node on PATH"
)


@requires_node
def test_current_turn_token_scans_past_24_entries():
    """Item 3: A tool-heavy turn (>24 entries) must not age out the turn marker."""
    server_token = "valid.turn_token"
    first_entry = f"User query\n\n[WEBGIS_TURN_CONTEXT:{server_token}]\n\n(Internal routing context; do not quote or modify this marker.)"
    other_entries = [{"role": "assistant", "content": f"tool_step_{i}"} for i in range(35)]
    all_entries = [first_entry] + other_entries
    assert len(all_entries) == 36

    script = f"""
      import {{ currentTurnToken }} from {json.dumps(EXTENSION_PATH.as_uri())};
      const ctx = {{ sessionManager: {{ getEntries: () => {json.dumps(all_entries)} }} }};
      process.stdout.write(currentTurnToken(ctx));
    """
    res = _run_node_script(script)
    assert res.stdout == server_token


@requires_node
def test_current_turn_token_does_not_reuse_a_pruned_context():
    """Item 3: A pruned or empty context cannot inherit the prior session token."""
    server_token = "pinned.turn_token"
    entry = f"[WEBGIS_TURN_CONTEXT:{server_token}]\n\n(Internal routing context; do not quote or modify this marker.)"

    script = f"""
      import {{ currentTurnToken, _resetPinnedTurnToken }} from {json.dumps(EXTENSION_PATH.as_uri())};
      _resetPinnedTurnToken?.();
      const ctx1 = {{ sessionManager: {{ getEntries: () => [{json.dumps(entry)}] }} }};
      const token1 = currentTurnToken(ctx1);
      const ctxEmpty = {{ sessionManager: {{ getEntries: () => [] }} }};
      const token2 = currentTurnToken(ctxEmpty);
      process.stdout.write(JSON.stringify({{ token1, token2 }}));
    """
    res = _run_node_script(script)
    data = json.loads(res.stdout)
    assert data["token1"] == server_token
    assert data["token2"] == ""


@requires_node
def test_post_to_bridge_409_provides_recovery_guidance():
    """Item 1 (#1042): 409 现在语义是 TURN_CONTEXT_INACTIVE —— 扩展透传结构化
    recovery guidance，并阻止对已过期 turn 的盲目重试。

    旧语义（concurrent mutation → 建议 webgis_cartography_status）已被
    multi-pod active-turn 协调取代：409 的 guidance 由后端 detail.guidance
    携带，扩展只负责 surfacing，不得再建议「重试或查制图状态」。
    """
    script = f"""
      import {{ postToBridge }} from {json.dumps(EXTENSION_PATH.as_uri())};
      globalThis.fetch = async () => ({{
        ok: false,
        status: 409,
        statusText: "Conflict",
        json: async () => ({{
          detail: {{
            error: "Pi turn context is no longer active",
            code: "TURN_CONTEXT_INACTIVE",
            session_id: "s1",
            turn_id: "t1",
            guidance: "Re-synchronize session state before issuing further tools.",
          }},
        }}),
      }});
      const res = await postToBridge("call-1", "webgis_map_intent", {{ query: "test" }}, "tok.123");
      process.stdout.write(JSON.stringify(res));
    """
    res = _run_node_script(script)
    data = json.loads(res.stdout)
    assert data["isError"] is True
    text = data["content"][0]["text"]
    assert "409" in text
    assert "Re-synchronize session state" in text
    assert "webgis_cartography_status" not in text

    # 字符串 detail（非结构化响应）也必须原样透传，不带旧的重试建议。
    script_str_detail = f"""
      import {{ postToBridge }} from {json.dumps(EXTENSION_PATH.as_uri())};
      globalThis.fetch = async () => ({{
        ok: false,
        status: 409,
        statusText: "Conflict",
        json: async () => ({{ detail: "concurrent state mutation" }}),
      }});
      const res = await postToBridge("call-1", "webgis_map_intent", {{ query: "test" }}, "tok.123");
      process.stdout.write(JSON.stringify(res));
    """
    res2 = _run_node_script(script_str_detail)
    data2 = json.loads(res2.stdout)
    assert data2["isError"] is True
    text2 = data2["content"][0]["text"]
    assert "409" in text2
    assert "concurrent state mutation" in text2
    assert "webgis_cartography_status" not in text2


@requires_node
def test_post_to_bridge_503_provides_recovery_guidance():
    """Item 1: 503 Service Unavailable provides guidance about overload/retry."""
    script = f"""
      import {{ postToBridge }} from {json.dumps(EXTENSION_PATH.as_uri())};
      globalThis.fetch = async () => ({{
        ok: false,
        status: 503,
        statusText: "Service Unavailable",
        json: async () => ({{ detail: "backend overloaded" }}),
      }});
      const res = await postToBridge("call-1", "query_local_poi", {{}}, "tok.123");
      process.stdout.write(JSON.stringify(res));
    """
    res = _run_node_script(script)
    data = json.loads(res.stdout)
    assert data["isError"] is True
    text = data["content"][0]["text"]
    assert "503" in text
    assert "overloaded" in text or "temporarily" in text


@requires_node
def test_post_to_bridge_timeout_provides_guidance():
    """Item 1: Timeout aborts fetch and provides timeout recovery guidance."""
    script = f"""
      import {{ postToBridge }} from {json.dumps(EXTENSION_PATH.as_uri())};
      globalThis.fetch = (_url, opts) => new Promise((_resolve, reject) => {{
        opts.signal.addEventListener("abort", () => {{
          const err = new Error("The operation was aborted");
          err.name = "AbortError";
          reject(err);
        }});
      }});
      const res = await postToBridge("call-1", "query_local_poi", {{}}, "tok.123", {{ timeoutMs: 50 }});
      process.stdout.write(JSON.stringify(res));
    """
    res = _run_node_script(script)
    data = json.loads(res.stdout)
    assert data["isError"] is True
    assert data["details"]["error"] == "timeout"
    text = data["content"][0]["text"]
    assert "timed out" in text
    assert "webgis_cartography_status" in text


@requires_node
def test_post_to_bridge_external_signal_cancellation():
    """Item 1: External harness cancellation signal cancels postToBridge."""
    script = f"""
      import {{ postToBridge }} from {json.dumps(EXTENSION_PATH.as_uri())};
      const controller = new AbortController();
      controller.abort();
      globalThis.fetch = (_url, opts) => new Promise((_resolve, reject) => {{
        const err = new Error("The operation was aborted");
        err.name = "AbortError";
        if (opts.signal?.aborted) {{
          return reject(err);
        }}
        opts.signal.addEventListener("abort", () => {{
          reject(err);
        }});
      }});
      const res = await postToBridge("call-1", "query_local_poi", {{}}, "tok.123", {{ signal: controller.signal }});
      process.stdout.write(JSON.stringify(res));
    """
    res = _run_node_script(script)
    data = json.loads(res.stdout)
    assert data["isError"] is True
    assert data["details"]["error"] == "cancelled"
    assert "cancelled" in data["content"][0]["text"]


@requires_node
def test_load_native_tools_logs_on_invalid_json(tmp_path: Path):
    """Item 2: loadNativeTools logs loudly when file is unparseable or not an array."""
    bad_file = tmp_path / "bad-native-tools.json"
    bad_file.write_text("NOT_JSON_DATA", encoding="utf-8")

    script = f"""
      import {{ loadNativeTools }} from {json.dumps(EXTENSION_PATH.as_uri())};
      process.env.WEBGIS_NATIVE_TOOLS_PATH = {json.dumps(str(bad_file))};
      const res = loadNativeTools();
      process.stdout.write(JSON.stringify(res));
    """
    res = _run_node_script(script)
    assert json.loads(res.stdout) == {"tools": [], "defaultActive": []}
    assert "Failed to load native tools" in res.stderr or "WEBGIS_NATIVE_TOOLS_PATH" in res.stderr


def test_write_native_tools_file_atomic(tmp_path: Path):
    """Item 2: write_native_tools_file performs atomic write and leaves no temp files."""
    registry = ToolRegistry()
    init_tools(registry)
    target = tmp_path / "subdir" / "native-tools.json"
    out_path = write_native_tools_file(registry, target)
    assert out_path == target
    assert target.exists()

    # Ensure no leftover temporary files in parent directory
    temp_files = [f for f in target.parent.iterdir() if f.name.startswith(".native-tools.json.tmp-")]
    assert len(temp_files) == 0

    content = json.loads(target.read_text(encoding="utf-8"))
    assert isinstance(content, list)
    assert len(content) == len(NATIVE_TOOL_NAMES)


def test_native_tool_names_contract(registry):
    """Item 4: NATIVE_TOOL_NAMES (Python) and FALLBACK_NATIVE (JS) and registry dump match 1:1."""
    # Extract FALLBACK_NATIVE from index.mjs
    mjs_text = EXTENSION_PATH.read_text(encoding="utf-8")
    m = re.search(r"const\s+FALLBACK_NATIVE\s*=\s*\[([^\]]+)\]", mjs_text)
    assert m, "FALLBACK_NATIVE array not found in index.mjs"
    fallback_names = [x.strip().strip('"').strip("'") for x in m.group(1).split(",") if x.strip()]

    assert fallback_names == list(NATIVE_TOOL_NAMES), (
        f"FALLBACK_NATIVE in index.mjs ({fallback_names}) drifted from "
        f"NATIVE_TOOL_NAMES in pi_native_surface.py ({list(NATIVE_TOOL_NAMES)})"
    )

    dumped_names = [t["name"] for t in native_tools_for_pi(registry)]
    assert dumped_names == list(NATIVE_TOOL_NAMES), (
        f"Registry schema dump ({dumped_names}) drifted from "
        f"NATIVE_TOOL_NAMES ({list(NATIVE_TOOL_NAMES)})"
    )


# ---------------------------------------------------------------------------
# ADR-0224（Goal 04）：dump v3 spec 解析 + per-call fingerprint echo。
# ---------------------------------------------------------------------------

@requires_node
def test_load_native_tools_parses_v3_shape(tmp_path):
    """v3 dump（v2 键 + abi/spec/generation 加性字段）继续以 v2 兼容形状返回。"""
    dump = {
        "version": 3,
        "abi": {"version": 1, "policy": "pi_tool_spec.v1"},
        "registry_generation": "gen123",
        "surface_fingerprint": "surf456",
        "tools": [
            {
                "name": "webgis_map_intent",
                "label": "Map Intent",
                "description": "d",
                "parameters": {"type": "object", "properties": {}},
                "spec": {"name": "webgis_map_intent", "fingerprint": "fp1", "tier": 1},
            },
        ],
        "default_active": ["webgis_map_intent"],
        "execute_proxy": "webgis_execute",
    }
    path = tmp_path / "surface-v3.json"
    path.write_text(json.dumps(dump), encoding="utf-8")
    script = f"""
      import {{ loadNativeTools }} from {json.dumps(EXTENSION_PATH.as_uri())};
      process.env.WEBGIS_NATIVE_TOOLS_PATH = {json.dumps(str(path))};
      const res = loadNativeTools();
      process.stdout.write(JSON.stringify(res));
    """
    res = _run_node_script(script)
    data = json.loads(res.stdout)
    assert [t["name"] for t in data["tools"]] == ["webgis_map_intent"]
    assert data["defaultActive"] == ["webgis_map_intent"]
    # spec 原样可达（echo 源）
    assert data["tools"][0]["spec"]["fingerprint"] == "fp1"


@requires_node
def test_post_to_bridge_includes_tool_fingerprint_when_provided():
    body_check = """
      let lastBody = null;
      globalThis.fetch = async (url, init) => {
        lastBody = JSON.parse(init.body);
        return { ok: true, json: async () => ({
          toolCallId: "c", content: [], details: {}, isError: false,
        }) };
      };
    """
    script = f"""
      import {{ postToBridge }} from {json.dumps(EXTENSION_PATH.as_uri())};
      {body_check}
      await postToBridge("c", "heatmap_data", {{}}, "tok.sig", {{ toolFingerprint: "FP123" }});
      process.stdout.write(lastBody.toolFingerprint);
    """
    res = _run_node_script(script)
    assert res.stdout == "FP123"


# ── vendor v1.5.0 适配（goal-pi-native-host-vnext）：两阶段加载下的默认表面延迟 ──
# Pi ≥0.99 在扩展加载期禁用 action 方法（setActiveTools 抛
# "Extension runtime not initialized"）。默认投影必须延迟到 session_start，
# 且在 session_start 不发射的形态下由首轮 before_agent_start 兜底。

_MINIMAL_DUMP = {
    "version": 3,
    "abi": {"version": 1, "policy": "pi_tool_spec.v1"},
    "registry_generation": "gen-t",
    "surface_fingerprint": "fp-t",
    "tools": [
        {
            "name": "webgis_map_intent",
            "label": "Map Intent",
            "description": "parse intent",
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
            "label": "Status",
            "description": "zero-arg status",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            "spec": {"fingerprint": "fp-status", "version": 1, "policy": "pi_tool_spec.v1"},
        },
    ],
    "default_active": ["webgis_map_intent", "webgis_cartography_status"],
    "execute_proxy": "webgis_execute",
}


def _mock_pi_harness_script(dump_path: Path, events: dict) -> str:
    """Invoke the extension with a mock pi; return a JSON transcript of calls."""
    return f"""
      import webgisExt from {json.dumps(EXTENSION_PATH.as_uri())};
      process.env.WEBGIS_NATIVE_TOOLS_PATH = {json.dumps(str(dump_path))};
      const calls = {{ setActiveTools: [], registered: [], handlers: {{}} }};
      const pi = {{
        registerTool: (t) => calls.registered.push(t.name),
        setActiveTools: (names) => calls.setActiveTools.push(names),
        on: (event, handler) => {{ calls.handlers[event] = handler; }},
      }};
      webgisExt(pi);
      calls.loadPhaseActiveCalls = calls.setActiveTools.length;
      calls.handlers["session_start"]?.();
      calls.afterSessionStartActiveCalls = calls.setActiveTools.length;
      calls.handlers["before_agent_start"]?.({{ systemPrompt: "x", prompt: "hello" }});
      calls.afterFirstTurnActiveCalls = calls.setActiveTools.length;
      process.stderr.write(JSON.stringify(calls));
    """


@requires_node
def test_extension_defers_default_surface_past_load(tmp_path: Path):
    """Pi v1.5.0: no setActiveTools during load; session_start applies default;
    each turn resets the default so prior projections cannot leak."""
    dump = tmp_path / "native-tools.json"
    dump.write_text(json.dumps(_MINIMAL_DUMP), encoding="utf-8")
    res = _run_node_script(_mock_pi_harness_script(dump, {}))
    data = json.loads(res.stderr.strip().splitlines()[-1])
    # 加载期零调用（旧实现在这里直接抛 not-initialized）
    assert data["loadPhaseActiveCalls"] == 0
    # session_start → 默认投影（7 natives + proxy + 发现入口 2，ADR-0288）
    assert data["afterSessionStartActiveCalls"] == 1
    assert len(data["setActiveTools"][0]) == 10
    assert {"tool_search", "codemode"} <= set(data["setActiveTools"][0])
    # Each turn resets authority even after session_start initialized the runtime.
    assert data["afterFirstTurnActiveCalls"] == 2
    assert data["setActiveTools"][1] == data["setActiveTools"][0]


@requires_node
def test_extension_applies_default_lazily_without_session_start(tmp_path: Path):
    """session_start 从未发射（回退旧 vendor 形态）时，首轮 before_agent_start
    兜底应用默认表面。"""
    dump = tmp_path / "native-tools.json"
    dump.write_text(json.dumps(_MINIMAL_DUMP), encoding="utf-8")
    script = f"""
      import webgisExt from {json.dumps(EXTENSION_PATH.as_uri())};
      process.env.WEBGIS_NATIVE_TOOLS_PATH = {json.dumps(str(dump))};
      const active = [];
      const pi = {{
        registerTool: () => {{}},
        setActiveTools: (names) => active.push(names),
        on: (event, handler) => {{ if (event === "before_agent_start") handler({{ systemPrompt: "s", prompt: "hi" }}); }},
      }};
      webgisExt(pi);
      process.stderr.write(JSON.stringify(active));
    """
    res = _run_node_script(script)
    data = json.loads(res.stderr.strip().splitlines()[-1])
    assert len(data) == 1 and len(data[0]) == 10  # 8 + 发现入口 2（ADR-0288）


@requires_node
def test_before_agent_start_replaces_both_vendor_openings(tmp_path: Path):
    """v0.81 与 v1.5.0 两种上游 preamble 都被整段替换为 GIS 身份（非前缀）。"""
    dump = tmp_path / "native-tools.json"
    dump.write_text(json.dumps(_MINIMAL_DUMP), encoding="utf-8")
    openings = [
        "You are an expert coding assistant operating inside pi, a coding agent harness. You help users by reading files, executing commands, editing code, and writing new files.",
        "You are a coding assistant running inside pi, a coding agent harness. Help users inspect files, run commands, and modify code.",
    ]
    script = f"""
      import webgisExt from {json.dumps(EXTENSION_PATH.as_uri())};
      process.env.WEBGIS_NATIVE_TOOLS_PATH = {json.dumps(str(dump))};
      const openings = {json.dumps(openings)};
      const pi = {{
        registerTool: () => {{}},
        setActiveTools: () => {{}},
        on: (event, handler) => {{ pi._h = handler; }},
      }};
      webgisExt(pi);
      const results = [];
      for (const opening of openings) {{
        const out = await pi._h({{ systemPrompt: opening + " Extra guidance.", prompt: "q" }});
        results.push({{ replaced: !out.systemPrompt.includes(opening),
                 headed: out.systemPrompt.startsWith("You are GeoAgent"),
                 extraKept: out.systemPrompt.includes("Extra guidance.") }});
      }}
      process.stderr.write(JSON.stringify(results));
    """
    res = _run_node_script(script)
    for row in json.loads(res.stderr.strip().splitlines()[-1]):
        assert row["replaced"], "coding opening must be replaced, not retained"
        assert row["headed"], "GIS identity must lead the rewritten prompt"
        assert row["extraKept"], "non-opening prompt content must survive"


@requires_node
def test_before_agent_start_prefixes_unknown_opening(tmp_path: Path):
    """未识别的 preamble（未来上游再改写）仍走前缀兜底 —— 行为与原 shim 一致。"""
    dump = tmp_path / "native-tools.json"
    dump.write_text(json.dumps(_MINIMAL_DUMP), encoding="utf-8")
    unknown = "You are a harness of a different vendor entirely."
    script = f"""
      import webgisExt from {json.dumps(EXTENSION_PATH.as_uri())};
      process.env.WEBGIS_NATIVE_TOOLS_PATH = {json.dumps(str(dump))};
      const pi = {{
        registerTool: () => {{}},
        setActiveTools: () => {{}},
        on: (event, handler) => {{ pi._h = handler; }},
      }};
      webgisExt(pi);
      const out = await pi._h({{ systemPrompt: {json.dumps(unknown)}, prompt: "q" }});
      process.stderr.write(out.systemPrompt);
    """
    res = _run_node_script(script)
    text = res.stderr.strip()
    assert text.splitlines()[0].startswith("You are GeoAgent")
    assert unknown in text


@requires_node
def test_before_agent_start_malformed_marker_falls_back_to_default(tmp_path: Path):
    """畸形 [WEBGIS_ACTIVE_TOOLS:...] marker（JSON 解析失败/非数组）必须回落
    默认投影 —— 不得让全注册超集保持 active（review P3-7 回归锁）。"""
    dump = tmp_path / "native-tools.json"
    dump.write_text(json.dumps(_MINIMAL_DUMP), encoding="utf-8")
    malformed = "[WEBGIS_ACTIVE_TOOLS:[not-json]]"
    script = f"""
      import webgisExt from {json.dumps(EXTENSION_PATH.as_uri())};
      process.env.WEBGIS_NATIVE_TOOLS_PATH = {json.dumps(str(dump))};
      const active = [];
      const pi = {{
        registerTool: () => {{}},
        setActiveTools: (names) => active.push(names),
        on: (event, handler) => {{ pi._h = handler; }},
      }};
      webgisExt(pi);
      await pi._h({{ systemPrompt: "s", prompt: {json.dumps(malformed)} }});
      process.stderr.write(JSON.stringify(active));
    """
    res = _run_node_script(script)
    data = json.loads(res.stderr.strip().splitlines()[-1])
    assert len(data) == 1 and len(data[0]) == 10, (
        "malformed marker must apply the default 10-tool projection "
        "(8 + discovery entries, ADR-0288), got "
        f"{data}"
    )
