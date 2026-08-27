"""Pi native GIS surface: live schemas + resolveCall kinds."""
from pathlib import Path

from app.services.chat.pi_native_surface import (
    EXECUTE_PROXY_NAME,
    NATIVE_TOOL_NAMES,
    STATUS_TOOL,
    native_tools_for_pi,
    resolve_pi_tool_call,
    write_native_tools_file,
)


def test_status_extra_keys_reject():
    resolved = resolve_pi_tool_call(
        STATUS_TOOL,
        {"city": "成都市", "topic": "小学分布", "scope": "全市"},
    )
    assert resolved.kind == "reject"
    assert "city" in resolved.error


def test_empty_status_is_native():
    resolved = resolve_pi_tool_call(STATUS_TOOL, {})
    assert resolved.kind == "native"
    assert resolved.name == STATUS_TOOL


def test_execute_wrapping_intent_rejects():
    resolved = resolve_pi_tool_call(
        EXECUTE_PROXY_NAME,
        {"toolName": "webgis_map_intent", "arguments": {"query": "成都市小学分布情况"}},
    )
    assert resolved.kind == "reject"
    assert "webgis_map_intent" in resolved.error


def test_execute_heatmap_unwraps():
    resolved = resolve_pi_tool_call(
        EXECUTE_PROXY_NAME,
        {"toolName": "heatmap_data", "arguments": {"render_type": "native"}},
    )
    assert resolved.kind == "execute"
    assert resolved.name == "heatmap_data"
    assert resolved.arguments["render_type"] == "native"


def test_unknown_bare_name_rejects_on_model_surface():
    resolved = resolve_pi_tool_call(
        "heatmap_data", {}, allow_passthrough=False,
    )
    assert resolved.kind == "reject"


def test_unknown_bare_name_rejects_by_default():
    resolved = resolve_pi_tool_call("heatmap_data", {})
    assert resolved.kind == "reject"
    assert "list_available_tools" in resolved.error
    assert "webgis_execute" in resolved.error


def test_unknown_bare_name_passthrough_is_explicit_opt_in():
    resolved = resolve_pi_tool_call(
        "pi_test_echo", {"msg": "x"}, allow_passthrough=True,
    )
    assert resolved.kind == "passthrough"
    assert resolved.name == "pi_test_echo"


def test_status_null_valued_extra_keys_reject():
    """Key-sensitive fail-closed: `{"city": null}` is still a hallucination."""
    resolved = resolve_pi_tool_call(STATUS_TOOL, {"city": None, "topic": ""})
    assert resolved.kind == "reject"
    assert "city" in resolved.error
    assert "topic" in resolved.error


def test_native_dump_uses_live_registry_schemas():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    init_tools(registry)
    dumped = {item["name"]: item for item in native_tools_for_pi(registry)}
    assert set(dumped) == set(NATIVE_TOOL_NAMES)

    status = dumped[STATUS_TOOL]["parameters"]
    assert status["properties"] == {} or "city" not in status["properties"]
    assert status.get("additionalProperties") is False

    intent = dumped["webgis_map_intent"]["parameters"]
    assert "query" in intent["properties"]
    assert "query" in intent.get("required", [])
    assert "session_id" not in intent["properties"]


def test_write_native_tools_file(tmp_path: Path):
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    init_tools(registry)
    path = write_native_tools_file(registry, tmp_path / "native-tools.json")
    text = path.read_text(encoding="utf-8")
    for name in NATIVE_TOOL_NAMES:
        assert name in text
