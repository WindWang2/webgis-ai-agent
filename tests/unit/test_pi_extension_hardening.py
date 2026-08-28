"""Tests for #1044: webgis-tools extension hardening.

Validates:
1. postToBridge 409/503 recovery guidance and fetch timeout.
2. loadNativeTools loud logging and error handling.
3. currentTurnToken scanning past 24 entries and pinned fallback.
4. write_native_tools_file atomic write.
5. NATIVE_TOOL_NAMES contract pinning Python, extension, and registry schema dump.
"""
import json
import re
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


def test_current_turn_token_scans_past_24_entries():
    """Item 3: A tool-heavy turn (>24 entries) must not age out the turn marker."""
    server_token = "valid.turn_token"
    first_entry = f"User query [WEBGIS_TURN_CONTEXT:{server_token}]"
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


def test_current_turn_token_falls_back_to_pinned():
    """Item 3: Pinned token persists if entries are later pruned or empty."""
    server_token = "pinned.turn_token"
    entry = f"[WEBGIS_TURN_CONTEXT:{server_token}]"

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
    assert data["token2"] == server_token


def test_post_to_bridge_409_provides_recovery_guidance():
    """Item 1: 409 Conflict provides guidance to retry or check cartography status."""
    script = f"""
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
    res = _run_node_script(script)
    data = json.loads(res.stdout)
    assert data["isError"] is True
    text = data["content"][0]["text"]
    assert "409" in text
    assert "webgis_cartography_status" in text
    assert "concurrent state mutation" in text


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
    assert res.stdout == "[]"
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
