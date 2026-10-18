"""PiToolSpecV1 ABI 测试（Goal 04, ADR-0224）。

钉住：
- 确定性：同 registry 两次编译 → byte-identical 序列化 + 相同指纹；
- schema projection fail-honest：约束键永不改写；白名单外装饰键删除记
  finding；description 截断记 finding；放不进 per-tool 预算 → 整工具逐出
  （不裁 schema）；
- 指纹纪律：description/schema 变化 → 指纹变化；registry_generation 变化
  → spec 指纹不变（generation 是来源锚，surface 指纹绑定它）；
- dump v3 形状：v2 键保留（扩展兼容）+ abi/registry_generation/
  surface_fingerprint/per-tool spec；
- kill switch：PI_TOOL_ABI=0 → dump v2、registered_surface_names 无
  destructive/budget 逐出（master 行为）；
- 真实 registry 全量 corpus：每个可注册工具都能编译出合法 spec。
"""
import json

import pytest

from app.services.chat.pi_tool_spec import (
    FINDING_ADDITIONAL_PROPERTIES_PINNED,
    FINDING_DESC_TRUNCATED,
    FINDING_KEYWORDS_DROPPED,
    FINDING_PASSTHROUGH_KEY_STRIPPED,
    PI_TOOL_SPEC_ABI_VERSION,
    compile_tool_spec,
    current_registry_generation,
    project_schema_for_pi,
    spec_max_schema_bytes,
    surface_fingerprint,
    tool_abi_enabled,
)


@pytest.fixture(scope="module")
def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    init_tools(reg)
    return reg


@pytest.fixture
def abi_on(monkeypatch):
    monkeypatch.delenv("PI_TOOL_ABI", raising=False)
    monkeypatch.delenv("PI_SPEC_MAX_SCHEMA_BYTES", raising=False)
    assert tool_abi_enabled()


# ---------------------------------------------------------------------------
# 确定性 / 指纹
# ---------------------------------------------------------------------------

def test_compile_is_deterministic(registry, abi_on):
    gen = current_registry_generation()
    a = compile_tool_spec(registry, "query_local_poi", registry_generation=gen)
    b = compile_tool_spec(registry, "query_local_poi", registry_generation=gen)
    assert a.fingerprint() == b.fingerprint()
    assert a.to_dump_dict() == b.to_dump_dict()


def test_fingerprint_excludes_registry_generation(registry, abi_on):
    """generation 是来源锚：spec 契约不变 → spec 指纹不随 generation 抖动。"""
    a = compile_tool_spec(registry, "query_local_poi", registry_generation="g1")
    b = compile_tool_spec(registry, "query_local_poi", registry_generation="g2")
    assert a.fingerprint() == b.fingerprint()


def test_surface_fingerprint_binds_generation_and_order(abi_on):
    from app.services.chat.pi_tool_spec import PiToolSpecV1

    def spec(name):
        return PiToolSpecV1(name=name, description="d", registry_generation="g1")

    s1, s2 = spec("a"), spec("b")
    fp_g1 = surface_fingerprint([s1, s2], registry_generation="g1")
    assert fp_g1 == surface_fingerprint([s1, s2], registry_generation="g1")
    # 顺序敏感（deterministic ordering 是契约）
    assert fp_g1 != surface_fingerprint([s2, s1], registry_generation="g1")
    # generation 参与
    assert fp_g1 != surface_fingerprint([s1, s2], registry_generation="g2")
    # 成员变化参与
    assert fp_g1 != surface_fingerprint([s1], registry_generation="g1")


def test_schema_fingerprint_changes_on_contract_change(registry, abi_on):
    gen = current_registry_generation()
    spec = compile_tool_spec(registry, "query_local_poi", registry_generation=gen)
    other = compile_tool_spec(registry, "get_local_admin_boundary", registry_generation=gen)
    assert spec.schema_fingerprint != other.schema_fingerprint
    assert spec.descriptor_fingerprint != other.descriptor_fingerprint


# ---------------------------------------------------------------------------
# Schema projection fail-honest
# ---------------------------------------------------------------------------

def test_projection_strips_session_id_and_pins_additional_properties():
    schema = {"function": {"name": "t", "parameters": {
        "type": "object",
        "properties": {"session_id": {"type": "string"}, "q": {"type": "string"}},
        "required": ["session_id", "q"],
    }}}
    out, findings = project_schema_for_pi(schema)
    assert "session_id" not in out["properties"]
    assert out["required"] == ["q"]
    assert out["additionalProperties"] is False
    assert findings.get(FINDING_PASSTHROUGH_KEY_STRIPPED) == 1
    assert findings.get(FINDING_ADDITIONAL_PROPERTIES_PINNED) == 1


def test_projection_never_touches_constraint_keywords():
    enum_values = ["a", "b", "c"]
    pattern = "^\\d{4}$"
    schema = {"function": {"name": "t", "parameters": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": enum_values},
            "code": {"type": "string", "pattern": pattern, "minLength": 2, "maxLength": 5},
            "n": {"type": "integer", "minimum": 1, "maximum": 9},
        },
        "required": ["kind"],
        "additionalProperties": False,
    }}}
    out, _findings = project_schema_for_pi(schema)
    props = out["properties"]
    assert props["kind"]["enum"] == enum_values
    assert props["code"]["pattern"] == pattern
    assert props["code"]["minLength"] == 2 and props["code"]["maxLength"] == 5
    assert props["n"]["minimum"] == 1 and props["n"]["maximum"] == 9
    assert out["required"] == ["kind"]


def test_projection_drops_unsupported_decorative_keywords_with_finding():
    schema = {"function": {"name": "t", "parameters": {
        "type": "object",
        "properties": {
            "q": {"type": "string", "x-pydantic-private": {"nested": True}, "writeOnly": True},
        },
    }}}
    out, findings = project_schema_for_pi(schema)
    q = out["properties"]["q"]
    assert "x-pydantic-private" not in q
    assert "writeOnly" not in q
    dropped = findings.get(FINDING_KEYWORDS_DROPPED, {})
    assert dropped.get("x-pydantic-private") == 1
    assert dropped.get("writeOnly") == 1


def test_projection_truncates_long_descriptions_with_finding():
    long_desc = "x" * 2000
    schema = {"function": {"name": "t", "parameters": {
        "type": "object",
        "properties": {"q": {"type": "string", "description": long_desc}},
    }}}
    out, findings = project_schema_for_pi(schema)
    assert len(out["properties"]["q"]["description"]) == 512
    assert findings.get(FINDING_DESC_TRUNCATED) == 1


def test_projection_retains_unions_and_defs_verbatim():
    """union/$defs 原样保留（不展开、不削弱 —— 安全语义不允许投影改写）。"""
    defs = {"Mode": {"enum": ["a", "b"]}}
    schema = {"function": {"name": "t", "parameters": {
        "type": "object",
        "properties": {
            "mode": {"anyOf": [{"$ref": "#/$defs/Mode"}, {"type": "null"}]},
        },
        "$defs": defs,
    }}}
    out, _findings = project_schema_for_pi(schema)
    assert out["properties"]["mode"]["anyOf"] == schema["function"]["parameters"]["properties"]["mode"]["anyOf"]
    assert out["$defs"] == defs


def test_per_tool_budget_excludes_whole_tool_not_schema(registry, abi_on, monkeypatch):
    """fail-honest 预算：超预算 → 整工具逐出 native 面，schema 不被削弱。"""
    monkeypatch.setenv("PI_SPEC_MAX_SCHEMA_BYTES", "10")  # 任何真实 schema 都超
    assert spec_max_schema_bytes() == 10
    from app.services.chat.pi_native_surface import (
        registered_surface_names,
    )

    assert registered_surface_names(registry) == []
    gen = current_registry_generation()
    assert compile_tool_spec(registry, "query_local_poi", registry_generation=gen) is None


# ---------------------------------------------------------------------------
# 安全元数据投影
# ---------------------------------------------------------------------------

def test_spec_carries_safety_metadata_from_descriptor(registry, abi_on):
    gen = current_registry_generation()
    spec = compile_tool_spec(registry, "query_local_poi", registry_generation=gen)
    desc = registry.descriptor("query_local_poi")
    assert spec.tier == int(desc.tier)
    assert spec.effective_security_tier == desc.effective_security_tier
    assert spec.side_effect == desc.side_effect.value
    assert spec.requires_credentials == tuple(sorted(desc.requires_credentials))
    assert spec.tool_id == desc.tool_id
    assert spec.cancellable is True
    assert spec.abi_version == PI_TOOL_SPEC_ABI_VERSION


def test_tier3_tools_never_compile_into_surface(registry, abi_on):
    """tier-3 工具不在注册面（registered_surface_names 排除），spec 编译器
    不为它们开洞 —— 调用方只对注册面名字编译。"""
    from app.services.chat.pi_native_surface import registered_surface_names

    tier3 = [n for n in registry.list_tools()
             if int(registry.descriptor(n).tier) >= 3]
    assert tier3, "测试前置：registry 应有 tier-3 工具"
    surface = set(registered_surface_names(registry))
    assert not (surface & set(tier3))


def test_destructive_low_tier_tool_excluded_from_surface(abi_on):
    """红线：DESTRUCTIVE 副作用类（即便低 tier）不进 native 注册面。"""
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    reg.register(
        "toy_destructive", "destructive toy", lambda: {},
        tier=1, side_effect="destructive",
    )
    reg.register("toy_safe", "safe toy", lambda: {}, tier=1)
    from app.services.chat.pi_native_surface import registered_surface_names

    surface = registered_surface_names(reg)
    assert "toy_destructive" not in surface
    assert "toy_safe" in surface


# ---------------------------------------------------------------------------
# Dump v3 形状 / kill switch
# ---------------------------------------------------------------------------

def test_dump_v3_shape_and_v2_keys(registry, abi_on):
    from app.services.chat.pi_native_surface import (
        EXECUTE_PROXY_NAME,
        NATIVE_TOOL_NAMES,
        pi_surface_for_spawn,
    )

    payload = pi_surface_for_spawn(registry)
    assert payload["version"] == 3
    # v2 键保留（扩展 v2 解析继续工作）
    assert isinstance(payload["tools"], list) and payload["tools"]
    assert payload["default_active"] == list(NATIVE_TOOL_NAMES)
    assert payload["execute_proxy"] == EXECUTE_PROXY_NAME
    # v3 加性字段
    assert payload["abi"]["version"] == PI_TOOL_SPEC_ABI_VERSION
    assert payload["registry_generation"]
    assert payload["surface_fingerprint"]
    with_spec = [t for t in payload["tools"] if "spec" in t]
    assert with_spec, "默认预算下注册面应携带 spec"
    sample = with_spec[0]["spec"]
    assert sample["abi_version"] == PI_TOOL_SPEC_ABI_VERSION
    assert sample["fingerprint"]
    assert "input_schema" in sample and "tier" in sample


def test_dump_v2_when_abi_kill_switch_off(registry, monkeypatch):
    """PI_TOOL_ABI=0 → dump 逐位回到 master v2 行为（无 spec/abi 字段）。"""
    monkeypatch.setenv("PI_TOOL_ABI", "0")
    assert not tool_abi_enabled()
    from app.services.chat.pi_native_surface import pi_surface_for_spawn

    payload = pi_surface_for_spawn(registry)
    assert payload["version"] == 2
    for tool in payload["tools"]:
        assert "spec" not in tool
    assert "registry_generation" not in payload
    assert "surface_fingerprint" not in payload


def test_kill_switch_disables_budget_exclusion(registry, monkeypatch):
    """PI_TOOL_ABI=0 时 per-tool 预算逐出不生效（master 行为）。"""
    from app.services.chat.pi_native_surface import registered_surface_names

    monkeypatch.setenv("PI_TOOL_ABI", "0")
    monkeypatch.setenv("PI_SPEC_MAX_SCHEMA_BYTES", "10")
    assert registered_surface_names(registry)  # 预算不裁


# ---------------------------------------------------------------------------
# 真实 registry corpus
# ---------------------------------------------------------------------------

def test_full_registry_corpus_compiles(registry, abi_on):
    """corpus：全部注册面工具编译成功、序列化确定性、指纹唯一性。"""
    from app.services.chat.pi_native_surface import registered_surface_names

    gen = current_registry_generation()
    fingerprints = set()
    for name in registered_surface_names(registry):
        spec = compile_tool_spec(registry, name, registry_generation=gen)
        assert spec is not None, f"{name} 应可编译"
        # 序列化可 JSON 往返且确定
        dumped = json.dumps(spec.to_dump_dict(), sort_keys=True)
        again = compile_tool_spec(registry, name, registry_generation=gen)
        assert dumped == json.dumps(again.to_dump_dict(), sort_keys=True)
        # schema 投影可序列化且 object 顶层封口
        assert spec.input_schema.get("type") == "object"
        assert spec.input_schema.get("additionalProperties") is False
        assert spec.schema_bytes > 0
        fingerprints.add(spec.fingerprint())
    assert len(fingerprints) >= 100, "356 工具 registry 的注册面应远超 100"


def test_native_front_door_specs_compile(registry, abi_on):
    from app.services.chat.pi_native_surface import NATIVE_TOOL_NAMES

    gen = current_registry_generation()
    for name in NATIVE_TOOL_NAMES:
        spec = compile_tool_spec(registry, name, registry_generation=gen)
        assert spec is not None, f"native 前门 {name} 必须可编译"
        assert spec.tier < 3 and spec.effective_security_tier < 3
