"""#1044 hardening of app/extensions/webgis-tools/index.mjs, node-side.

- postToBridge: 401/409/503 map to model-facing recovery guidance instead of a
  bare status line, and the fetch aborts on a budget aligned with the
  backend turn budget (WEBGIS_BRIDGE_TIMEOUT_MS).
- loadNativeTools: a set-but-unusable WEBGIS_NATIVE_TOOLS_PATH logs loudly
  (stderr) and degrades to webgis_execute-only instead of silence.

Each test runs a fresh node process (module state — env reads, token memo —
is per-evaluation). The node runner is shared with
test_pi_extension_turn_token.py via pi_extension_node.
"""
import json

from pi_extension_node import ext_uri, run_node


def test_post_to_bridge_maps_409_503_401_to_recovery_guidance():
    """"HTTP 409: Conflict" invites blind retries; the mapped text must tell
    the model what 409 (turn gone — stop) vs 503 (transient — bounded retry)
    vs 401 (context rejected — re-establish) means."""
    script = f"""
      import http from "node:http";
      let served = 0;
      const server = http.createServer((req, res) => {{
        served += 1;
        const code = [409, 503, 401][served - 1] || 500;
        res.writeHead(code, {{"Content-Type": "application/json"}});
        res.end();
      }});
      await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
      process.env.WEBGIS_API_BASE = `http://127.0.0.1:${{server.address().port}}`;
      const {{ postToBridge }} = await import({ext_uri()});
      const conflict = await postToBridge("tc-1", "webgis_map_intent", {{}}, "tok.sig");
      const unavailable = await postToBridge("tc-2", "webgis_map_intent", {{}}, "tok.sig");
      const rejected = await postToBridge("tc-3", "webgis_map_intent", {{}}, "tok.sig");
      process.stdout.write(JSON.stringify([conflict, unavailable, rejected]));
      process.exit(0);
    """

    result = run_node(script)
    conflict, unavailable, rejected = json.loads(result.stdout)

    assert conflict["isError"] is True
    assert "no longer active" in conflict["content"][0]["text"]
    assert "stop calling GIS tools" in conflict["content"][0]["text"]
    assert conflict["details"]["error"] == "turn_not_active"
    assert conflict["details"]["status"] == 409

    assert unavailable["isError"] is True
    assert "temporarily unavailable" in unavailable["content"][0]["text"]
    assert "retry the same call once" in unavailable["content"][0]["text"]
    assert unavailable["details"]["error"] == "bridge_unavailable"
    assert unavailable["details"]["status"] == 503

    assert rejected["isError"] is True
    assert "rejected the turn context" in rejected["content"][0]["text"]
    assert "Do not retry" in rejected["content"][0]["text"]
    assert rejected["details"]["error"] == "turn_context_rejected"
    assert rejected["details"]["status"] == 401


def test_post_to_bridge_aborts_on_budget_timeout():
    """A bridge fetch that never answers must abort within the configured
    budget with no-blind-retry guidance, not hang for the whole turn."""
    script = f"""
      import http from "node:http";
      const server = http.createServer(() => {{ /* never respond */ }});
      await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
      process.env.WEBGIS_API_BASE = `http://127.0.0.1:${{server.address().port}}`;
      process.env.WEBGIS_BRIDGE_TIMEOUT_MS = "150";
      const {{ postToBridge }} = await import({ext_uri()});
      const started = Date.now();
      const result = await postToBridge("tc-1", "webgis_map_intent", {{}}, "tok.sig");
      process.stdout.write(JSON.stringify({{ elapsedMs: Date.now() - started, result }}));
      process.exit(0);
    """

    result = run_node(script)
    payload = json.loads(result.stdout)

    assert payload["elapsedMs"] < 5000, "fetch far outlived the 150ms budget"
    text = payload["result"]["content"][0]["text"]
    assert "timed out after 150ms" in text
    assert "do not blind-retry" in text
    assert payload["result"]["isError"] is True


def test_unusable_native_dump_logs_loudly_and_degrades(tmp_path):
    """A truncated dump (the torn-write shape) must reach stderr loudly and
    leave webgis_execute registered — not a silent crippled GeoAgent."""
    bad = tmp_path / "native-tools.json"
    bad.write_text('{"truncated": tru', encoding="utf-8")
    script = f"""
      const registered = [];
      const mod = await import({ext_uri()});
      mod.default({{ registerTool: (t) => registered.push(t.name), on: () => {{}} }});
      process.stdout.write(JSON.stringify(registered));
    """

    result = run_node(script, env={"WEBGIS_NATIVE_TOOLS_PATH": str(bad)})

    assert json.loads(result.stdout) == ["webgis_execute"]
    assert str(bad) in result.stderr
    assert "unusable" in result.stderr
    assert "NOT registered" in result.stderr


def test_non_array_native_dump_is_rejected_loudly(tmp_path):
    """A parseable-but-wrong root (object) is as broken as torn JSON."""
    bad = tmp_path / "native-tools.json"
    bad.write_text(json.dumps({"webgis_map_intent": {}}), encoding="utf-8")
    script = f"""
      const registered = [];
      const mod = await import({ext_uri()});
      mod.default({{ registerTool: (t) => registered.push(t.name), on: () => {{}} }});
      process.stdout.write(JSON.stringify(registered));
    """

    result = run_node(script, env={"WEBGIS_NATIVE_TOOLS_PATH": str(bad)})

    assert json.loads(result.stdout) == ["webgis_execute"]
    assert "expected an array" in result.stderr


def test_valid_native_dump_registers_native_tools(tmp_path):
    """Happy path: the spawn-time dump registers each native tool plus the
    execute proxy."""
    dump = tmp_path / "native-tools.json"
    dump.write_text(json.dumps([
        {
            "name": "webgis_map_intent",
            "label": "Map Intent",
            "description": "map intent",
            "parameters": {"type": "object", "properties": {}},
            "promptSnippet": "first step",
        },
    ]), encoding="utf-8")
    script = f"""
      const registered = [];
      const mod = await import({ext_uri()});
      mod.default({{ registerTool: (t) => registered.push(t.name), on: () => {{}} }});
      process.stdout.write(JSON.stringify(registered));
    """

    result = run_node(script, env={"WEBGIS_NATIVE_TOOLS_PATH": str(dump)})

    assert json.loads(result.stdout) == ["webgis_map_intent", "webgis_execute"]
    assert result.stderr == ""
