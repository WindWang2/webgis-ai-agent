"""Pi native GIS surface: live schemas + resolveCall kinds."""
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


# ── WP3 schema 严格模式卫生（上游 #12049/#12067）───────────────────────────
#
# 事实（上游 v1.6.0 行为）：OpenAI strict mode 下，tool schema 里**无 type
# 且非组合器**的裸节点（典型：`"items": {}`、`"properties": {"geojson": {}}`）
# 会让整个工具降级为非 strict 发送（#12049）；Anthropic 非 strict 模式则对
# 非 strict schema 明示禁止额外参数（#12067）。Host 侧生成 dump 时无法逐
# 工具补救 —— 这是 registry 参数注解的存量债。本车道做两件事：
#   1. front-door（native 7 + execute proxy，常驻声明面）硬闸 —— 当前
#      query_local_poi 的 bbox/polygon 两参数无注解 → 裸节点，故 xfail
#      **strict** 标注：修好注解后 XPASS 会翻红，强制摘除标记；
#   2. 长尾计数披露 —— 全量扫描 v4 spawn dump，输出违规工具清单与裸节点
#      计数（不锁死精确数字，任何 schema 改动不误伤；披露可见即可）。

_STRICT_COMBINATORS = frozenset({"$ref", "enum", "const", "oneOf", "anyOf", "allOf"})


def _strict_hostile_nodes(schema: object, path: str = "") -> list[str]:
    """递归收集「无 type 且非 $ref/enum/const/oneOf/anyOf/allOf」的裸节点路径。

    JSON Schema 布尔节点（true/false）与组合器分支自描述，不算裸节点。
    """
    findings: list[str] = []
    if isinstance(schema, bool) or not isinstance(schema, dict):
        return findings
    has_type = isinstance(schema.get("type"), str)
    has_combinator = any(key in schema for key in _STRICT_COMBINATORS)
    if not has_type and not has_combinator:
        findings.append(path or "<root>")
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, sub in properties.items():
            findings.extend(_strict_hostile_nodes(sub, f"{path}.properties.{name}"))
    items = schema.get("items")
    if items is not None:
        findings.extend(_strict_hostile_nodes(items, f"{path}.items"))
    for key in ("oneOf", "anyOf", "allOf", "prefixItems"):
        branches = schema.get(key)
        if isinstance(branches, list):
            for i, sub in enumerate(branches):
                findings.extend(_strict_hostile_nodes(sub, f"{path}.{key}[{i}]"))
    additional = schema.get("additionalProperties")
    if isinstance(additional, dict):
        findings.extend(_strict_hostile_nodes(additional, f"{path}.additionalProperties"))
    for key in ("$defs", "definitions"):
        defs = schema.get(key)
        if isinstance(defs, dict):
            for name, sub in defs.items():
                findings.extend(_strict_hostile_nodes(sub, f"{path}.{key}.{name}"))
    return findings


def _spawn_dump() -> tuple[dict, dict]:
    """生产 dump 生成入口（与 pi_doctor surface 域同一函数）：registry →
    ``pi_surface_for_spawn`` → v4 spawn dump（注册超集 + 执行 proxy）。

    返回 ``(tools_by_name, surface)``：``webgis_execute`` 不是 tools[] 条目
    （扩展自注册），只以顶层 ``execute_proxy`` 名字出现在 dump 里。"""
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    from app.services.chat.pi_native_surface import pi_surface_for_spawn

    registry = ToolRegistry()
    init_tools(registry)
    surface = pi_surface_for_spawn(registry)
    assert surface.get("version", 0) >= 3, "hygiene lane pins the v3/v4 dump"
    return {item["name"]: item for item in surface["tools"]}, surface


@pytest.mark.xfail(
    strict=True,
    reason="#12049 strict 降级：query_local_poi 的 bbox/polygon 参数无 type 注解"
           "（registry 存量债，修注解后 XPASS 翻红 → 摘除本标记）",
)
def test_native_frontdoor_schemas_are_openai_strict_clean():
    """front-door（native 7，每个会话常驻声明面）schema 必须 OpenAI strict
    干净：无「无 type 且非组合器」的裸节点 —— 否则常驻前门全部以非 strict
    发送（#12049），Anthropic 侧还叠加 #12067 的额外参数禁令。

    webgis_execute（proxy）不在 dump tools[]（扩展自注册，schema 静态且已
    全 type 化）——其声明面契约由 vendor contract 车道钉住；这里核对
    ``execute_proxy`` 字段仍在（proxy front door 不丢）。
    """
    dumped, surface = _spawn_dump()
    assert surface.get("execute_proxy") == EXECUTE_PROXY_NAME
    missing = set(NATIVE_TOOL_NAMES) - set(dumped)
    assert not missing, f"front-door tools absent from spawn dump: {sorted(missing)}"
    findings: dict[str, list[str]] = {}
    for name in sorted(NATIVE_TOOL_NAMES):
        nodes = _strict_hostile_nodes(dumped[name].get("parameters"))
        if nodes:
            findings[name] = nodes
    assert not findings, (
        f"strict-hostile bare nodes on the front-door surface: {findings}"
    )


def test_strict_hygiene_longtail_disclosure(capsys):
    """全量 v4 dump 扫描：披露 strict-hostile 裸节点的工具清单与计数。

    长尾（当前 ~134 工具 / ~176 节点，多为 geojson/bbox 类无注解参数）是
    registry 存量债 —— 本测试不锁精确数字（schema 任何改动都不该误伤），
    只锁扫描器行为与披露可见性；修复进展看 front-door xfail 的 XPASS。
    """
    dumped, _surface = _spawn_dump()
    per_tool: dict[str, list[str]] = {}
    for name, tool in dumped.items():
        nodes = _strict_hostile_nodes(tool.get("parameters"))
        if nodes:
            per_tool[name] = nodes
    total_nodes = sum(len(v) for v in per_tool.values())
    # 扫描器自证：确实扫了全量面，且披露可读（stdout 有工具清单头）。
    assert len(dumped) > 400, f"spawn dump unexpectedly small: {len(dumped)}"
    assert "query_local_poi" in per_tool, (
        "known native-set violation (bbox/polygon) vanished — if the annotation "
        "was fixed, remove the front-door xfail marker too"
    )
    print(f"\n[strict-hygiene] violating tools: {len(per_tool)}/{len(dumped)}; "
          f"bare nodes: {total_nodes}")
    print("[strict-hygiene] tools:", ", ".join(sorted(per_tool)[:40]),
          "..." if len(per_tool) > 40 else "")
    captured = capsys.readouterr().out
    assert "violating tools" in captured
