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
# 事实（上游源码核实，vendor/pi v1.6.0 packages/ai/src/api/constrained-sampling.ts
# 的 makeJsonSchemaNodeStrict）：
#   * **#12049 增量谓词（v1.6.0 新增）**：节点无 `type` 且无 `anyOf` 且无
#     `enum` → 抛错 → 该工具静默降级为非 strict 发送。上游注释明示：裸
#     `enum` 算 typed，裸 `const` **不算**。
#   * **v1.5.2 既有谓词（非本轮增量）**：节点含 `$ref`/`$defs`/`definitions`/
#     `allOf`/`oneOf`/`patternProperties`/`dependentSchemas`/`dependencies`/
#     `unevaluatedProperties`/`propertyNames`/`contains`/`prefixItems`/`not`/
#     `if`/`then`/`else` 任一 keyword → 同样降级。这些工具在 v1.5.2 就已经
#     是非 strict，与迁移无关，但同样是「将来接入 strict 时的存量债」。
#   * **#12067**：Anthropic 非 strict schema 现在显式执行额外参数禁令
#     （dump 已投影 additionalProperties:false，本面满足）。
#
# **接入条件（review 收紧口径，关键事实）**：vendor `registerTool` 的
# `constrainedSampling` 字段可选，未设 = 线上 `strict:false`
# （resolveJsonSchemaStrictSampling 对无 config 的工具返回 undefined →
# `strict: strict ?? false`）。本仓扩展 app/extensions/webgis-tools/index.mjs
# **未设**该字段 → GIS 工具当前全部非 strict 发送，#12049 的降级路径
# **尚未被触发**。本车道是「预接入卫生」+ 存量债披露，不是现存缺陷的
# 修复声称；一旦某工具 opt-in `strict:"prefer"`，两桶披露即变为真实门禁
# 清单。
#
# 车道做两件事：
#   1. front-door（native 7，常驻声明面）硬闸 —— 当前 query_local_poi 的
#      bbox/polygon 两参数无注解 → #12049 裸节点，故 xfail **strict** 标注：
#      修好注解后 XPASS 会翻红，强制摘除标记；
#   2. 长尾两桶计数披露 —— 全量扫描 v4 spawn dump，分别输出 #12049 裸节点
#      与 v1.5.2 既有 keyword 节点的工具清单与计数（不锁死精确数字，任何
#      schema 改动不误伤；披露可见即可）。

#: v1.6.0 #12049 增量谓词的「自愈键」：有任一即不算裸节点（const 不在内 ——
#: 上游注释明示裸 const 不算 typed）。
_STRICT_DELTA_TYPED_KEYS = ("type", "anyOf", "enum")

#: v1.5.2 既有的 strict 不支持 keyword（UNSUPPORTED_STRICT_SCHEMA_KEYS 全集，
#: 上游 constrained-sampling.ts）。
_STRICT_LEGACY_KEYWORDS = (
    "$ref", "$defs", "definitions", "allOf", "oneOf", "patternProperties",
    "dependentSchemas", "dependencies", "unevaluatedProperties", "propertyNames",
    "contains", "prefixItems", "not", "if", "then", "else",
)


def _strict_reject_nodes(schema: object, path: str = "") -> dict[str, list[str]]:
    """递归收集 strict 拒绝节点，返回 ``{"bare": [...], "keyword": [...]}``。

    - ``bare``：#12049 增量谓词 —— 无 type/anyOf/enum 的节点（裸 const 在内）。
    - ``keyword``：v1.5.2 既有谓词 —— 含 UNSUPPORTED_STRICT_SCHEMA_KEYS 的节点。

    同一节点可同时命中两桶（如 `{"$ref": ...}` 也无 type）——两桶分别记录，
    披露时各归其位，不合并计数。JSON Schema 布尔节点（true/false）上游同样
    拒绝（"boolean schemas are unsupported"），计入 bare。
    """
    findings: dict[str, list[str]] = {"bare": [], "keyword": []}
    if isinstance(schema, bool):
        findings["bare"].append(path or "<root>")
        return findings
    if not isinstance(schema, dict):
        return findings
    if not any(key in schema for key in _STRICT_DELTA_TYPED_KEYS):
        findings["bare"].append(path or "<root>")
    for key in _STRICT_LEGACY_KEYWORDS:
        if key in schema:
            findings["keyword"].append(f"{path or '<root>'}[{key}]")
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, sub in properties.items():
            for bucket, nodes in _strict_reject_nodes(sub, f"{path}.properties.{name}").items():
                findings[bucket].extend(nodes)
    items = schema.get("items")
    if items is not None:
        for bucket, nodes in _strict_reject_nodes(items, f"{path}.items").items():
            findings[bucket].extend(nodes)
    for key in ("oneOf", "anyOf", "allOf", "prefixItems"):
        branches = schema.get(key)
        if isinstance(branches, list):
            for i, sub in enumerate(branches):
                for bucket, nodes in _strict_reject_nodes(sub, f"{path}.{key}[{i}]").items():
                    findings[bucket].extend(nodes)
    additional = schema.get("additionalProperties")
    if isinstance(additional, dict):
        for bucket, nodes in _strict_reject_nodes(additional, f"{path}.additionalProperties").items():
            findings[bucket].extend(nodes)
    for key in ("$defs", "definitions"):
        defs = schema.get(key)
        if isinstance(defs, dict):
            for name, sub in defs.items():
                for bucket, nodes in _strict_reject_nodes(sub, f"{path}.{key}.{name}").items():
                    findings[bucket].extend(nodes)
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
    干净（两桶皆空：#12049 裸节点 + v1.5.2 既有 keyword 节点）—— 否则这些
    工具一旦 opt-in strict 即静默降级（#12049），Anthropic 侧还叠加 #12067
    的额外参数禁令。当前接入条件下（扩展未设 constrainedSampling → 线上
    strict:false）本闸是预接入卫生，非现存缺陷。

    webgis_execute（proxy）不在 dump tools[]（扩展自注册，schema 静态且已
    全 type 化）——其声明面契约由 vendor contract 车道钉住；这里核对
    ``execute_proxy`` 字段仍在（proxy front door 不丢）。
    """
    dumped, surface = _spawn_dump()
    assert surface.get("execute_proxy") == EXECUTE_PROXY_NAME
    missing = set(NATIVE_TOOL_NAMES) - set(dumped)
    assert not missing, f"front-door tools absent from spawn dump: {sorted(missing)}"
    findings: dict[str, dict[str, list[str]]] = {}
    for name in sorted(NATIVE_TOOL_NAMES):
        rejects = _strict_reject_nodes(dumped[name].get("parameters"))
        if rejects["bare"] or rejects["keyword"]:
            findings[name] = rejects
    assert not findings, (
        f"strict-hostile nodes on the front-door surface: {findings}"
    )


def test_strict_hygiene_longtail_disclosure(capsys):
    """全量 v4 dump 扫描：两桶披露（#12049 裸节点 / v1.5.2 既有 keyword 节点）
    的工具清单与计数。

    长尾（当前 ~136 工具，多为 geojson/bbox 类无注解参数与 $ref/prefixItems
    结构）是 registry 存量债 —— 本测试不锁精确数字（schema 任何改动都不该
    误伤），只锁扫描器行为与披露可见性；修复进展看 front-door xfail 的 XPASS。
    """
    dumped, _surface = _spawn_dump()
    per_tool: dict[str, dict[str, list[str]]] = {}
    for name, tool in dumped.items():
        rejects = _strict_reject_nodes(tool.get("parameters"))
        if rejects["bare"] or rejects["keyword"]:
            per_tool[name] = rejects
    total_bare = sum(len(v["bare"]) for v in per_tool.values())
    total_keyword = sum(len(v["keyword"]) for v in per_tool.values())
    # 扫描器自证：确实扫了全量面，且披露可读（stdout 有工具清单头）。
    assert len(dumped) > 400, f"spawn dump unexpectedly small: {len(dumped)}"
    assert "query_local_poi" in per_tool, (
        "known native-set violation (bbox/polygon) vanished — if the annotation "
        "was fixed, remove the front-door xfail marker too"
    )
    assert any(rejects["bare"] for rejects in per_tool.values()), (
        "no #12049-delta bare nodes anywhere — the scanner lost its delta "
        "predicate (type/anyOf/enum) or every schema was annotated at once"
    )
    print(f"\n[strict-hygiene] violating tools: {len(per_tool)}/{len(dumped)}; "
          f"#12049 bare nodes: {total_bare}; legacy keyword nodes: {total_keyword}")
    print("[strict-hygiene] tools:", ", ".join(sorted(per_tool)[:40]),
          "..." if len(per_tool) > 40 else "")
    captured = capsys.readouterr().out
    assert "violating tools" in captured
    assert "#12049 bare nodes" in captured
