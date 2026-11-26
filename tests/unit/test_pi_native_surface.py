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
# 的 makeJsonSchemaNodeStrict / resolveJsonSchemaStrictSampling）：
#   * **#12049 增量谓词（v1.6.0 新增）**：节点无 `type` 且无 `anyOf` 且无
#     `enum` → 抛错 → 该工具静默降级为非 strict 发送。上游注释明示：裸
#     `enum` 算 typed，裸 `const` **不算**。
#   * **v1.5.2 既有谓词（非本轮增量）**：UNSUPPORTED_STRICT_SCHEMA_KEYS 全量
#     16 键（$ref/$defs/definitions/allOf/oneOf/patternProperties/
#     dependentSchemas/dependencies/unevaluatedProperties/propertyNames/
#     contains/prefixItems/not/if/then/else）；布尔子 schema；空/非数组
#     anyOf；anyOf 结构化变体（type object/array 或带 properties/items）；
#     tuple items（数组）；properties 无 object type；对象节点
#     additionalProperties ≠ false；required 非字符串数组或含未知键。
#     这些工具在 v1.5.2 就已是非 strict，与迁移无关。
#   * **#12067**：Anthropic 非 strict schema 现在显式执行额外参数禁令
#     （dump 已投影 additionalProperties:false，本面满足）。
#
# **接入条件（review 收紧口径，关键事实）**：vendor `registerTool` 的
# `constrainedSampling` 字段可选，未设 = 线上 `strict:false`
# （resolveJsonSchemaStrictSampling 对无 config 的工具返回 undefined →
# `strict: strict ?? false`）。本仓扩展 app/extensions/webgis-tools/index.mjs
# **未设**该字段 → GIS 工具当前全部以非 strict 发送，上述所有拒绝路径
# **尚未被触发**。本车道是「预接入卫生」+ 存量债披露，不是现存缺陷的
# 修复声称；一旦某工具 opt-in `strict:"prefer"`，披露总数即真实 fallout
# 门禁清单（当前实测 252/529 工具，含 #12049 增量与全部既有类）。
#
# 车道做两件事：
#   1. front-door（native 7，常驻声明面）硬闸（完整谓词三桶）—— 当前两例：
#      query_local_poi 的 bbox/polygon 无注解 → #12049 裸节点；
#      webgis_component_update 的 5 个自由对象参数 → anyOf 结构化联合 +
#      additionalProperties:true（v1.5.2 既有类）。故 xfail **strict** 标注：
#      全部修好后 XPASS 会翻红，强制摘除标记（修复时以 reason 清单为准）；
#   2. 长尾披露 —— 全量扫描 v4 spawn dump，按拒绝类分桶输出工具清单与计数
#      （不锁死精确数字，任何 schema 改动不误伤；披露可见即可）。

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


def _is_structured_variant(schema: object) -> bool:
    """上游 isStructuredSchema：object/array 类型或带 properties/items 的变体
    —— anyOf 里的这类变体被 strict 拒绝（对象/数组联合不支持）。"""
    if not isinstance(schema, dict):
        return False
    types = schema.get("type")
    type_list = (
        [types] if isinstance(types, str)
        else types if isinstance(types, list)
        else []
    )
    return (
        "object" in type_list
        or "array" in type_list
        or "properties" in schema
        or "items" in schema
    )


def _strict_reject_nodes(schema: object, path: str = "") -> dict[str, list[str]]:
    """上游 makeJsonSchemaNodeStrict **拒绝路径**的忠实移植（只收集拒绝，
    不移植 nullability 自动改写等变换），按桶返回：

    - ``bare``：#12049 增量谓词 —— 无 type/anyOf/enum 的节点（裸 const 在内；
      布尔子 schema 亦属此类路径上的拒绝，dump 实测 0 个）。
    - ``keyword``：v1.5.2 既有 UNSUPPORTED_STRICT_SCHEMA_KEYS 16 键。
    - ``other``：其余既有拒绝类（v1.5.2 已存在，非本轮增量）：
      ``boolean_schema``/``anyof_empty``/``structured_union``/
      ``tuple_items``/``properties_without_object``/
      ``additional_properties_not_false``/``properties_not_map``/
      ``required_malformed``/``required_unknown``。

    已知理论分歧（dump 实测 0 命中，JSON Schema 生成器不产出此类形态，
    故计数无损）：显式 ``null`` 的 items/anyOf/properties/
    additionalProperties/required 上游会进入分支并多数抛错，本移植用
    ``.get() is not None`` 跳过；``required`` 未知键在 properties 缺席时
    上游按空 properties 判未知，本移植仅在 properties 为 dict 时核对。

    同一节点可命中多桶（如 `{"$ref": ...}` 也无 type）——各桶分别记录，
    披露时按类归位，不合并计数。节点路径形如 ``tool.properties.x.items``。
    """
    findings: dict[str, list[str]] = {"bare": [], "keyword": [], "other": []}
    _collect_strict_rejects(schema, path, findings)
    return findings


def _collect_strict_rejects(schema: object, path: str, out: dict[str, list[str]]) -> None:
    here = path or "<root>"
    if isinstance(schema, bool):
        # 上游：布尔 schema 直接抛（v1.5.2 既有，非 #12049 增量）。
        out["other"].append(f"{here}[boolean_schema]")
        return
    if not isinstance(schema, dict):
        return
    if not any(key in schema for key in _STRICT_DELTA_TYPED_KEYS):
        out["bare"].append(here)
    for key in _STRICT_LEGACY_KEYWORDS:
        if key in schema:
            out["keyword"].append(f"{here}[{key}]")
    any_of = schema.get("anyOf")
    if any_of is not None:
        if not isinstance(any_of, list) or not any_of:
            out["other"].append(f"{here}[anyof_empty]")
        else:
            for i, variant in enumerate(any_of):
                if _is_structured_variant(variant):
                    out["other"].append(f"{here}.anyOf[{i}][structured_union]")
                _collect_strict_rejects(variant, f"{here}.anyOf[{i}]", out)
    items = schema.get("items")
    if items is not None:
        if isinstance(items, list):
            out["other"].append(f"{here}[tuple_items]")
        else:
            _collect_strict_rejects(items, f"{here}.items", out)
    properties = schema.get("properties")
    if properties is not None and schema.get("type") != "object":
        out["other"].append(f"{here}[properties_without_object]")
    if schema.get("type") == "object":
        additional = schema.get("additionalProperties")
        if additional is not None and additional is not False:
            out["other"].append(f"{here}[additional_properties_not_false]")
        if properties is not None and not isinstance(properties, dict):
            out["other"].append(f"{here}[properties_not_map]")
        required = schema.get("required")
        if required is not None:
            if not isinstance(required, list) or any(not isinstance(k, str) for k in required):
                out["other"].append(f"{here}[required_malformed]")
            elif isinstance(properties, dict):
                for key in required:
                    if key not in properties:
                        out["other"].append(f"{here}[required_unknown:{key}]")
    if isinstance(properties, dict):
        for name, sub in properties.items():
            _collect_strict_rejects(sub, f"{here}.properties.{name}", out)
    for key in ("oneOf", "allOf", "prefixItems"):
        branches = schema.get(key)
        if isinstance(branches, list):
            for i, sub in enumerate(branches):
                _collect_strict_rejects(sub, f"{here}.{key}[{i}]", out)
    for key in ("$defs", "definitions"):
        defs = schema.get(key)
        if isinstance(defs, dict):
            for name, sub in defs.items():
                _collect_strict_rejects(sub, f"{here}.{key}.{name}", out)


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
    reason="strict-hostile front-door nodes (pre-engagement hygiene): "
           "query_local_poi bbox/polygon 无 type 注解（#12049 裸节点）；"
           "webgis_component_update 的 placement/style/options/chart/stats "
           "五个自由对象参数为 anyOf[{type:object, additionalProperties:true}]"
           "（v1.5.2 既有 structured_union + additional_properties_not_false）"
           "——修好后 XPASS 翻红 → 摘除本标记",
)
def test_native_frontdoor_schemas_are_openai_strict_clean():
    """front-door（native 7，每个会话常驻声明面）schema 必须 OpenAI strict
    干净（完整谓词三桶皆空：#12049 裸节点 + v1.5.2 既有 keyword + 其余既有
    拒绝类）—— 否则这些工具一旦 opt-in strict 即静默降级（#12049），
    Anthropic 侧还叠加 #12067 的额外参数禁令。当前接入条件下（扩展未设
    constrainedSampling → 线上 strict:false）本闸是预接入卫生，非现存缺陷。

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
        if rejects["bare"] or rejects["keyword"] or rejects["other"]:
            findings[name] = rejects
    assert not findings, (
        f"strict-hostile nodes on the front-door surface: {findings}"
    )


def test_strict_hygiene_longtail_disclosure(capsys):
    """全量 v4 dump 扫描：按上游完整拒绝谓词分桶披露（#12049 增量裸节点 /
    v1.5.2 既有 keyword / 其余既有拒绝类）的工具清单与计数。

    长尾（当前 ~252 工具，含 #12049 增量与全部既有类；其中 additional_
    properties≠false 与结构化 anyOf 联合为主要类）是 registry 存量债 ——
    本测试不锁精确数字（schema 任何改动都不该误伤），只锁扫描器行为与披露
    可见性；修复进展看 front-door xfail 的 XPASS。披露总数即某工具 opt-in
    ``strict:"prefer"`` 后的真实 fallout 门禁清单。
    """
    dumped, _surface = _spawn_dump()
    per_tool: dict[str, dict[str, list[str]]] = {}
    for name, tool in dumped.items():
        rejects = _strict_reject_nodes(tool.get("parameters"))
        if rejects["bare"] or rejects["keyword"] or rejects["other"]:
            per_tool[name] = rejects
    total_bare = sum(len(v["bare"]) for v in per_tool.values())
    total_keyword = sum(len(v["keyword"]) for v in per_tool.values())
    total_other = sum(len(v["other"]) for v in per_tool.values())
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
    other_classes: dict[str, int] = {}
    for rejects in per_tool.values():
        for node in rejects["other"]:
            cls = node.rsplit("[", 1)[-1].rstrip("]").split(":", 1)[0]
            other_classes[cls] = other_classes.get(cls, 0) + 1
    print(f"\n[strict-hygiene] violating tools: {len(per_tool)}/{len(dumped)}; "
          f"#12049 bare nodes: {total_bare}; legacy keyword nodes: {total_keyword}; "
          f"other legacy classes: {total_other} {dict(sorted(other_classes.items()))}")
    print("[strict-hygiene] tools:", ", ".join(sorted(per_tool)[:40]),
          "..." if len(per_tool) > 40 else "")
    captured = capsys.readouterr().out
    assert "violating tools" in captured
    assert "#12049 bare nodes" in captured
    assert "other legacy classes" in captured
