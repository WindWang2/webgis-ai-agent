"""Pi native GIS surface: live schemas + resolveCall kinds."""
import json
from pathlib import Path

import pytest

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


def test_write_native_tools_file_replaces_atomically(tmp_path, monkeypatch):
    """#1044: the dump must land via tmp + os.replace so a crash mid-write can
    never expose a torn native-tools.json to the extension reader (which
    would degrade that spawn to a native-less GeoAgent)."""
    import os

    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    init_tools(registry)

    replaces: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy_replace(src, dst):
        replaces.append((str(src), str(dst)))
        # The payload must already be complete at rename time — the rename
        # only ever publishes a full document.
        staged = json.loads(Path(src).read_text(encoding="utf-8"))
        assert {item["name"] for item in staged} == set(NATIVE_TOOL_NAMES)
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy_replace)

    path = write_native_tools_file(registry, tmp_path / "native-tools.json")

    assert [dst for _, dst in replaces] == [str(path)]
    assert Path(replaces[0][0]).parent == path.parent, "tmp must share the target directory"
    assert path.name in replaces[0][0], "tmp must be distinguishable from the dump"
    assert json.loads(path.read_text(encoding="utf-8"))[0]["name"] == NATIVE_TOOL_NAMES[0]
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name], "no tmp residue"


def test_native_dump_raises_on_missing_registry_name():
    """A native name absent from the registry must fail the dump, not
    silently stub an empty schema (spec story 35: live registry only)."""
    class _PartialRegistry:
        def get_schemas_subset(self, names):
            return []  # nothing registered — every native name is missing

    with pytest.raises(ValueError, match="missing from live registry"):
        native_tools_for_pi(_PartialRegistry())


def test_status_session_id_is_not_an_extra():
    """The sole passthrough key: an extension-injected session_id keeps a
    status call legal — a regression dropping the exemption would reject the
    extension's real callbacks."""
    resolved = resolve_pi_tool_call(STATUS_TOOL, {"session_id": "sess-1"})
    assert resolved.kind == "native"
    assert resolved.name == STATUS_TOOL


def test_execute_missing_tool_name_rejects():
    assert resolve_pi_tool_call(EXECUTE_PROXY_NAME, {}).kind == "reject"
    assert resolve_pi_tool_call(EXECUTE_PROXY_NAME, {"toolName": ""}).kind == "reject"
