"""ToolDescriptor V3 契约测试（ADR-0103）。

覆盖：
- V3 新字段注册期 roundtrip 与校验门（非法词表值显式失败）；
- capability 派生回填（AlgorithmRegistry 反查）与溯源 capability_source；
- 生效安全层 / 生效幂等性的显式声明优先 + 保守派生语义；
- 指纹契约：V3 字段进入 descriptor_fingerprint（同输入同指纹、变更可区分）。
"""
import pytest

from app.tools.descriptor import (
    CAPABILITY_SOURCES,
    DATA_MUTATION_KINDS,
    LATENCY_CLASSES,
    MAP_MUTATION_KINDS,
    MEMORY_CLASSES,
    REQUIRED_CONTEXT_KINDS,
    SCALE_CLASSES,
    SideEffectClass,
    ToolDescriptor,
    ToolStatus,
    canonical_json,
    descriptor_fingerprint,
    validate_descriptor_fields,
)
from app.tools.registry import ToolRegistry


def _mk_registry() -> ToolRegistry:
    reg = ToolRegistry()

    @reg.tool(name="v3_probe", description="probe tool", tier=2, domains=["probe"])
    def v3_probe(query: str) -> dict:
        """probe."""
        return {"query": query}

    return reg


# ---------------------------------------------------------------------------
# 注册期 roundtrip 与校验
# ---------------------------------------------------------------------------

def test_v3_fields_roundtrip():
    reg = ToolRegistry()
    reg.register(
        name="v3_full", description="full v3 tool",
        func=lambda x: {"x": x}, tier=2, domains=["t"],
        side_effect="artifact_creation",
        input_artifacts=["geojson_fc"], output_semantic_type="map_product",
        required_context=["map_state", "session_plan"],
        map_mutations=["add_layer", "camera"],
        data_mutations=["artifact_write"],
        latency_class="slow", memory_class="heavy", scale_class="large",
        crs_semantics="wgs84", unit_semantics="meters", idempotent=False,
        security_tier=1, required_permission="bridge_secret",
        examples=["绘制成都市 POI 密度图"], anti_examples=["用于精确导航"],
        failure_modes=["timeout", "empty_result"], fallback_tool="v3_probe",
    )
    d = reg.descriptor("v3_full")
    assert d.side_effect is SideEffectClass.ARTIFACT_CREATION
    assert d.input_artifacts == ("geojson_fc",)
    assert d.required_context == ("map_state", "session_plan")
    assert d.map_mutations == ("add_layer", "camera")
    assert d.data_mutations == ("artifact_write",)
    assert d.latency_class == "slow" and d.memory_class == "heavy" and d.scale_class == "large"
    assert d.crs_semantics == "wgs84" and d.unit_semantics == "meters"
    assert d.idempotent is False
    assert d.security_tier == 1 and d.required_permission == "bridge_secret"
    assert d.examples == ("绘制成都市 POI 密度图",)
    assert d.anti_examples == ("用于精确导航",)
    assert d.failure_modes == ("timeout", "empty_result")
    assert d.fallback_tool == "v3_probe"


def test_v3_default_fields_backward_compatible():
    d = _mk_registry().descriptor("v3_probe")
    assert d.input_artifacts == () and d.required_context == ()
    assert d.map_mutations == () and d.data_mutations == ()
    assert d.latency_class == "unknown" and d.memory_class == "unknown"
    assert d.scale_class == "unknown"
    assert d.crs_semantics is None and d.unit_semantics is None
    assert d.idempotent is None
    assert d.security_tier is None and d.required_permission is None
    assert d.examples == () and d.anti_examples == ()
    assert d.failure_modes == () and d.fallback_tool is None


def test_v3_unknown_kwarg_still_rejected():
    reg = ToolRegistry()
    with pytest.raises(ValueError, match="v3_bogus_field"):
        reg.register(name="x", description="", func=lambda: {}, tier=1,
                     v3_bogus_field="nope")


def _valid_base() -> dict:
    return dict(
        name="t", status="stable", deprecation_of=None, side_effect="pure",
        result_size_policy="bounded", summary="",
        capabilities=None, algorithms=None, produced_refs=None,
        accepts_ref_types=None, requires_credentials=None,
        provider_dependencies=None, domains=None,
    )


@pytest.mark.parametrize("field,value,fragment", [
    ("latency_class", "instant", "latency_class"),
    ("memory_class", "unbounded", "memory_class"),
    ("scale_class", "xlarge", "scale_class"),
    ("required_context", ["magic_state"], "required_context"),
    ("map_mutations", ["teleport"], "map_mutations"),
    ("data_mutations", ["teleport"], "data_mutations"),
    ("security_tier", 9, "security_tier"),
    ("idempotent", "yes", "idempotent"),
    ("fallback_tool", "  ", "fallback_tool"),
])
def test_v3_validation_gate_rejects_bad_values(field, value, fragment):
    kwargs = _valid_base()
    kwargs[field] = value
    errors = validate_descriptor_fields(**kwargs)
    assert errors and fragment in errors[0]


def test_v3_vocabularies_consistent():
    assert "unknown" in LATENCY_CLASSES and "unknown" in MEMORY_CLASSES and "unknown" in SCALE_CLASSES
    assert set(REQUIRED_CONTEXT_KINDS) and set(MAP_MUTATION_KINDS) and set(DATA_MUTATION_KINDS)
    assert set(CAPABILITY_SOURCES) == {"none", "declared", "derived:algorithm_registry"}


# ---------------------------------------------------------------------------
# 派生回填与溯源
# ---------------------------------------------------------------------------

def test_capability_derivation_from_algorithm_registry():
    """algorithm registry 已声明的工具（spatial_aggregate → admin_aggregation）
    未显式声明 capabilities 时自动派生并溯源。"""
    from app.tools import init_tools

    reg = ToolRegistry()
    init_tools(reg)
    d = reg.descriptor("spatial_aggregate")
    assert "admin_aggregation" in d.capabilities
    assert d.capability_source == "derived:algorithm_registry"
    # 派生算法 id 非空且与 capability 语义同源
    assert d.algorithms, "derived algorithms should be non-empty for spatial_aggregate"


def test_declared_capabilities_win_over_derivation():
    reg = ToolRegistry()
    reg.register(
        name="spatial_aggregate", description="declared override",
        func=lambda: {}, tier=1,
        capabilities=["custom_capability"],
    )
    d = reg.descriptor("spatial_aggregate")
    assert d.capabilities == ("custom_capability",)
    assert d.capability_source == "declared"


def test_capability_source_none_when_no_source():
    d = _mk_registry().descriptor("v3_probe")
    assert d.capabilities == () and d.capability_source == "none"


# ---------------------------------------------------------------------------
# 生效安全层 / 生效幂等
# ---------------------------------------------------------------------------

def test_effective_security_tier_defaults_to_tier():
    d = _mk_registry().descriptor("v3_probe")  # tier=2, security_tier=None
    assert d.effective_security_tier == 2


def test_effective_idempotent_conservative_derivation():
    pure = ToolDescriptor(name="a", side_effect=SideEffectClass.PURE)
    assert pure.effective_idempotent is True
    mutation = ToolDescriptor(name="b", side_effect=SideEffectClass.STATE_MUTATION)
    assert mutation.effective_idempotent is None  # 不替作者断言
    explicit = ToolDescriptor(name="c", side_effect=SideEffectClass.STATE_MUTATION,
                              idempotent=True)
    assert explicit.effective_idempotent is True  # 显式声明优先
    t3 = ToolDescriptor(name="d", tier=3)
    assert t3.effective_idempotent is None  # destructive 不自动幂等


# ---------------------------------------------------------------------------
# 指纹契约
# ---------------------------------------------------------------------------

def test_v3_fields_enter_descriptor_fingerprint():
    base = ToolDescriptor(name="fp", tier=1)
    changed = ToolDescriptor(name="fp", tier=1, latency_class="fast")
    assert descriptor_fingerprint(base) != descriptor_fingerprint(changed)
    # 同输入同输出（稳定性）
    assert descriptor_fingerprint(base) == descriptor_fingerprint(ToolDescriptor(name="fp", tier=1))


def test_contract_payload_contains_v3_fields():
    payload = ToolDescriptor(name="cp", tier=1, map_mutations=("camera",)).contract_payload()
    assert payload["map_mutations"] == ["camera"]
    assert payload["capability_source"] == "none"
    assert payload["latency_class"] == "unknown"
    # canonical JSON 确定性不受影响
    assert canonical_json(payload) == canonical_json(dict(sorted(payload.items())))


def test_planned_status_unchanged_by_v3():
    reg = ToolRegistry()
    reg.register(name="planned_x", description="", func=lambda: {}, tier=1,
                 status="planned")
    d = reg.descriptor("planned_x")
    assert d.status is ToolStatus.PLANNED and not d.model_visible
