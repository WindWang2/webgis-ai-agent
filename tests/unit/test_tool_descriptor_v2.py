"""ADR-0101 Wave 1: ToolDescriptor V2 契约与指纹测试。

锁定语义：
- ToolRegistry 仍是唯一注册中心；descriptor 只是派生投影；
- 注册期校验门（非法 status/side_effect/deprecation 组合、未知 kwarg）显式失败；
- schema_fingerprint 对描述变更不敏感、对入参 schema 变更敏感；
- registry_fingerprint 顺序无关、注册/更新失效；
- PLANNED 工具不可执行；tier-3 强制 destructive 副作用类。
"""
import pytest

from app.tools.descriptor import (
    SideEffectClass,
    ToolDescriptor,
    ToolStatus,
    canonical_json,
    manifest_fingerprint,
    schema_fingerprint,
    validate_descriptor_fields,
)
from app.tools.registry import ToolRegistry


def _register(reg, name="t1", description="desc", func=None, **kwargs):
    if func is None:
        def func(x: int, note: str = "n") -> dict:
            return {"x": x}
    reg.register(name=name, description=description, func=func, **kwargs)
    return func


# ---------------------------------------------------------------------------
# 描述符派生默认
# ---------------------------------------------------------------------------

def test_descriptor_defaults_derived():
    reg = ToolRegistry()
    _register(reg)
    d = reg.descriptor("t1")
    assert isinstance(d, ToolDescriptor)
    assert d.name == "t1"
    assert d.description == "desc"
    assert d.status is ToolStatus.STABLE
    assert d.tier == 1
    assert d.side_effect is SideEffectClass.UNCLASSIFIED
    assert d.destructive_level == 0
    assert d.requires_confirmation is False
    assert d.model_visible is True
    assert d.executable is True
    assert d.required_fields == ("x",)
    assert d.tool_id == "t1@1.0#cv1"


def test_descriptor_cannot_be_second_registry():
    """描述符必须派生自 registry：未注册工具不可凭空构造。"""
    reg = ToolRegistry()
    with pytest.raises(KeyError):
        reg.descriptor("ghost")


def test_explicit_descriptor_fields_roundtrip():
    reg = ToolRegistry()
    _register(
        reg, name="buffer_analysis",
        tier=2, domains=["spatial"],
        side_effect="deterministic_compute",
        capabilities=["cap.buffer"],
        algorithms=["alg.buffer"],
        output_semantic_type="geojson_fc",
        produced_refs=["data"],
        accepts_ref_types=["data"],
        network=False,
        deterministic=True,
        result_size_policy="ref_offload",
        summary="缓冲区分析",
    )
    d = reg.descriptor("buffer_analysis")
    assert d.side_effect is SideEffectClass.DETERMINISTIC_COMPUTE
    assert d.capabilities == ("cap.buffer",)
    assert d.algorithms == ("alg.buffer",)
    assert d.output_semantic_type == "geojson_fc"
    assert d.produced_refs == ("data",)
    assert d.accepts_ref_types == ("data",)
    assert d.network is False
    assert d.deterministic is True
    assert d.result_size_policy == "ref_offload"
    assert d.summary == "缓冲区分析"
    assert d.retry_safe is True
    assert d.cacheable is True
    assert d.replay_safe is True


def test_tier3_forces_destructive_side_effect():
    reg = ToolRegistry()
    _register(reg, tier=3, side_effect="pure")
    d = reg.descriptor("t1")
    assert d.side_effect is SideEffectClass.DESTRUCTIVE
    assert d.destructive_level == 3
    assert d.requires_confirmation is True
    assert d.retry_safe is False
    assert d.cacheable is False
    assert d.replay_safe is False


# ---------------------------------------------------------------------------
# 注册期校验门
# ---------------------------------------------------------------------------

def test_unknown_register_kwarg_rejected():
    reg = ToolRegistry()
    with pytest.raises(ValueError, match="未知的描述符字段"):
        _register(reg, side_effekt="pure")  # 拼写错误必须显式失败


@pytest.mark.parametrize("bad_status", ["retired", "Hidden", ""])
def test_invalid_status_rejected(bad_status):
    errs = validate_descriptor_fields(
        name="t", status=bad_status, deprecation_of=None, side_effect="pure",
        result_size_policy="unknown", summary="",
        capabilities=None, algorithms=None, produced_refs=None,
        accepts_ref_types=None, requires_credentials=None,
        provider_dependencies=None, domains=None,
    )
    assert errs and "status" in errs[0]


def test_deprecated_requires_target():
    errs = validate_descriptor_fields(
        name="old_tool", status="deprecated", deprecation_of=None,
        side_effect="pure", result_size_policy="unknown", summary="",
        capabilities=None, algorithms=None, produced_refs=None,
        accepts_ref_types=None, requires_credentials=None,
        provider_dependencies=None, domains=None,
    )
    assert errs and "deprecation_of" in errs[0]


def test_deprecation_target_requires_deprecated_status():
    errs = validate_descriptor_fields(
        name="old_tool", status="stable", deprecation_of="new_tool",
        side_effect="pure", result_size_policy="unknown", summary="",
        capabilities=None, algorithms=None, produced_refs=None,
        accepts_ref_types=None, requires_credentials=None,
        provider_dependencies=None, domains=None,
    )
    assert errs


def test_self_deprecation_rejected():
    errs = validate_descriptor_fields(
        name="old_tool", status="deprecated", deprecation_of="old_tool",
        side_effect="pure", result_size_policy="unknown", summary="",
        capabilities=None, algorithms=None, produced_refs=None,
        accepts_ref_types=None, requires_credentials=None,
        provider_dependencies=None, domains=None,
    )
    assert errs and "自身" in errs[-1]


def test_duplicate_capability_list_rejected():
    errs = validate_descriptor_fields(
        name="t", status="stable", deprecation_of=None, side_effect="pure",
        result_size_policy="unknown", summary="",
        capabilities=["a", "a"], algorithms=None, produced_refs=None,
        accepts_ref_types=None, requires_credentials=None,
        provider_dependencies=None, domains=None,
    )
    assert errs and "重复" in errs[0]


def test_registration_failure_via_gate():
    reg = ToolRegistry()
    with pytest.raises(ValueError, match="deprecation_of"):
        _register(reg, status="deprecated")
    assert "t1" not in reg.list_tools()


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------

def test_hidden_and_planned_not_model_visible():
    reg = ToolRegistry()
    _register(reg, name="hidden_tool", status="hidden")
    _register(reg, name="planned_tool", status="planned")
    assert reg.descriptor("hidden_tool").model_visible is False
    assert reg.descriptor("planned_tool").model_visible is False
    assert reg.descriptor("hidden_tool").executable is True
    assert reg.descriptor("planned_tool").executable is False


@pytest.mark.asyncio
async def test_planned_tool_dispatch_refused():
    from app.tools._utils import std_error_response  # noqa: F401

    reg = ToolRegistry()
    _register(reg, name="planned_tool", status="planned")
    result = await reg.dispatch("planned_tool", {})
    assert result.get("code") == "TOOL_NOT_EXECUTABLE"


def test_deprecated_alias_points_to_canonical_single_implementation():
    """deprecated 工具是 canonical 之上的状态标注，不产生第二实现。"""
    from app.tools.registry import _TOOL_NAME_ALIASES

    # 别名走既有 _TOOL_NAME_ALIASES（生产中导入期固定不变）；registry.aliases_for
    # 投影到描述符。别名先于 registry 构建（= 启动期事实顺序）。
    _TOOL_NAME_ALIASES["ct_alias"] = "canonical_tool"
    try:
        reg = ToolRegistry()
        _register(reg, name="canonical_tool", side_effect="pure")
        assert reg.descriptor("canonical_tool").deprecation_of is None
        assert reg.resolve_name("ct_alias") == "canonical_tool"
        assert reg.descriptor("canonical_tool").aliases == ("ct_alias",)
    finally:
        _TOOL_NAME_ALIASES.pop("ct_alias", None)


# ---------------------------------------------------------------------------
# 指纹
# ---------------------------------------------------------------------------

def test_canonical_json_deterministic_key_order():
    a = canonical_json({"b": 1, "a": {"d": 2, "c": 3}})
    b = canonical_json({"a": {"c": 3, "d": 2}, "b": 1})
    assert a == b


def test_schema_fingerprint_ignores_description_change():
    s1 = {"type": "function", "function": {"name": "t", "description": "旧描述",
                                           "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}}}
    s2 = {"type": "function", "function": {"name": "t", "description": "全新描述文案，完全不同",
                                           "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}}}
    assert schema_fingerprint(s1) == schema_fingerprint(s2)


def test_schema_fingerprint_detects_input_schema_change():
    s1 = {"type": "function", "function": {"name": "t", "description": "d",
                                           "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}}}
    s2 = {"type": "function", "function": {"name": "t", "description": "d",
                                           "parameters": {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}}}
    assert schema_fingerprint(s1) != schema_fingerprint(s2)


def test_schema_vs_descriptor_fingerprint_distinction():
    reg = ToolRegistry()
    _register(reg)
    fp_schema = reg.schema_fingerprint("t1")
    fp_desc = reg.descriptor_fingerprint("t1")
    assert fp_schema and fp_desc and fp_schema != fp_desc

    # 描述 change：schema 指纹不变，描述符指纹变
    _register(reg, description="全新描述")
    assert reg.schema_fingerprint("t1") == fp_schema
    assert reg.descriptor_fingerprint("t1") != fp_desc

    # 入参 schema change：两者都变
    fp_desc2 = reg.descriptor_fingerprint("t1")

    class NewModel:
        pass

    from pydantic import BaseModel, Field

    class T1Args(BaseModel):
        x: int
        note: str = Field(default="n", min_length=1)

    reg.update_args_model("t1", T1Args)
    assert reg.schema_fingerprint("t1") != fp_schema
    assert reg.descriptor_fingerprint("t1") != fp_desc2


def test_fingerprints_deterministic_across_registry_instances():
    def build(order):
        reg = ToolRegistry()
        for name in order:
            _register(reg, name=name, tier=2, domains=["x"])
        return reg

    r1 = build(["a", "b", "c"])
    r2 = build(["c", "b", "a"])
    assert r1.registry_fingerprint() == r2.registry_fingerprint()
    assert r1.fingerprints() == r2.fingerprints()


def test_registry_fingerprint_invalidated_by_register_and_update():
    reg = ToolRegistry()
    _register(reg, name="a")
    fp1 = reg.registry_fingerprint()
    _register(reg, name="b")
    fp2 = reg.registry_fingerprint()
    assert fp1 != fp2

    from pydantic import BaseModel

    class BArgs(BaseModel):
        q: str

    reg.update_args_model("b", BArgs)
    assert reg.registry_fingerprint() != fp2


def test_manifest_fingerprint_order_independent_and_content_sensitive():
    entries1 = [("a", "fp-a"), ("b", "fp-b")]
    entries2 = [("b", "fp-b"), ("a", "fp-a")]
    assert manifest_fingerprint(entries1) == manifest_fingerprint(entries2)
    assert manifest_fingerprint(entries1) != manifest_fingerprint([("a", "fp-a")])
    assert manifest_fingerprint(entries1) != manifest_fingerprint([("a", "fp-other"), ("b", "fp-b")])


def test_fingerprints_accessor_shape():
    reg = ToolRegistry()
    _register(reg, name="a")
    _register(reg, name="b", tier=2)
    fps = reg.fingerprints()
    assert set(fps) == {"a", "b"}
    for schema_fp, desc_fp in fps.values():
        assert isinstance(schema_fp, str) and len(schema_fp) == 16
        assert isinstance(desc_fp, str) and len(desc_fp) == 16


# ---------------------------------------------------------------------------
# 元数据向后兼容
# ---------------------------------------------------------------------------

def test_metadata_backward_compat_fields():
    reg = ToolRegistry()
    _register(reg, tier=2, domains=["gis"], cost="heavy")
    meta = reg.metadata("t1")
    assert meta["tier"] == 2
    assert meta["domains"] == ["gis"]
    assert meta["cost"] == "heavy"
    assert meta["status"] == "stable"
    assert meta["side_effect"] == "unclassified"
    # 未知工具兜底不受新字段影响
    fallback = reg.metadata("nope")
    assert fallback["tier"] == 1 and "status" not in fallback


def test_descriptors_snapshot_covers_all_tools():
    reg = ToolRegistry()
    _register(reg, name="a")
    _register(reg, name="b", tier=2)
    ds = reg.descriptors()
    assert set(ds) == {"a", "b"}
    assert all(isinstance(d, ToolDescriptor) for d in ds.values())
