"""G03 引擎面级测试——8 面 × 裁决词表的红→绿覆盖（合成 registry）。

每个 reason code 至少一条正例/反例；判定语义与既有校验器
（capability_conformance / pi_native_surface 逐出语义）同源同判。
"""
from types import SimpleNamespace
from typing import Literal

import pytest
from pydantic import BaseModel, Field

from app.lib.gis.tool_capability_conformance import (
    CONFORMANCE_FACES,
    FACE_DISPATCH,
    FACE_PI_NATIVE,
    FACE_PLANNER,
    FACE_REGISTRATION,
    FACE_RESULT,
    FACE_SCIENCE,
    FACE_SEARCH,
    STATUS_FAIL,
    STATUS_NOT_EVALUATED,
    STATUS_PASS,
    STATUS_WARNING,
    ConformanceFacts,
    _audit_dispatch,
    _audit_planner,
    _audit_science,
    audit_tool_conformance,
)
from app.lib.gis.parameter_contracts import ParameterContract, ParameterSpec
from app.tools.registry import ToolRegistry


class _BufferArgs(BaseModel):
    distance: float = Field(..., ge=0.0001)
    unit: Literal["m", "km"] = "m"


def _noop(**_: object) -> dict:
    return {}


def _register_good(registry: ToolRegistry) -> None:
    """面面完备的工具：8 面应全 pass（除构造性 N/A 面）。"""
    registry.register(
        "good_tool", "good", func=_noop, args_model=_BufferArgs,
        tier=1, domains=["analysis"],
        side_effect="pure", network=False, deterministic=True,
        result_size_policy="bounded", output_semantic_type="geojson_fc",
        produced_refs=("data",),
    )


def _facts(
    registry: ToolRegistry,
    *,
    capability_ids: tuple = ("cap_a",),
    algorithms: tuple = (),
    contracts: dict | None = None,
    native_names: tuple = (),
    domain_keywords: dict | None = None,
    spec_compiler=None,
) -> ConformanceFacts:
    return ConformanceFacts(
        registry=registry,
        capability_ids=capability_ids,
        algorithms=algorithms,
        parameter_contracts=contracts or {},
        native_tool_names=native_names,
        domain_keywords=domain_keywords if domain_keywords is not None
        else {"analysis": ["分析"], "raster": ["栅格"]},
        spec_compiler=spec_compiler,
    )


# ── registration 面 ──────────────────────────────────────────────

def test_registration_complete_passes():
    r = ToolRegistry()
    _register_good(r)
    v = audit_tool_conformance("good_tool", _facts(r)).face(FACE_REGISTRATION)
    assert (v.status, v.reason) == (STATUS_PASS, "descriptor_complete")


def test_registration_unclassified_side_effect_warns():
    r = ToolRegistry()
    r.register("bare", "bare", func=_noop)  # side_effect 默认 unclassified
    v = audit_tool_conformance("bare", _facts(r)).face(FACE_REGISTRATION)
    assert (v.status, v.reason) == (STATUS_WARNING, "metadata_incomplete")
    assert "side_effect" in v.detail


def test_registration_dangling_fallback_tool_fails():
    r = ToolRegistry()
    _register_good(r)
    r.register(
        "with_bad_fallback", "x", func=_noop, fallback_tool="ghost_tool",
        side_effect="pure", network=False, deterministic=True,
        result_size_policy="bounded")
    v = audit_tool_conformance("with_bad_fallback", _facts(r)).face(FACE_REGISTRATION)
    assert (v.status, v.reason) == (STATUS_FAIL, "fallback_tool_dangling")
    assert "ghost_tool" in v.detail


def test_registration_live_fallback_tool_passes():
    r = ToolRegistry()
    _register_good(r)
    r.register(
        "with_fallback", "x", func=_noop, fallback_tool="good_tool",
        side_effect="pure", network=False, deterministic=True,
        result_size_policy="bounded", output_semantic_type="object")
    v = audit_tool_conformance("with_fallback", _facts(r)).face(FACE_REGISTRATION)
    assert v.status == STATUS_PASS


def test_registration_dangling_deprecation_target_warns():
    r = ToolRegistry()
    r.register(
        "old_tool", "x", func=_noop, status="deprecated",
        deprecation_of="ghost_canonical",
        side_effect="pure", network=False, deterministic=True,
        result_size_policy="bounded")
    v = audit_tool_conformance("old_tool", _facts(r)).face(FACE_REGISTRATION)
    assert (v.status, v.reason) == (STATUS_WARNING, "metadata_incomplete")
    assert "deprecation_of_dangling" in v.detail


# ── search 面 ────────────────────────────────────────────────────

def test_search_tier1_always_visible():
    r = ToolRegistry()
    _register_good(r)
    v = audit_tool_conformance("good_tool", _facts(r)).face(FACE_SEARCH)
    assert (v.status, v.reason) == (STATUS_PASS, "always_visible")


def test_search_tier2_covered_domain_passes():
    r = ToolRegistry()
    r.register("ndvi", "n", func=_noop, tier=2, domains=["raster"])
    v = audit_tool_conformance("ndvi", _facts(r)).face(FACE_SEARCH)
    assert (v.status, v.reason) == (STATUS_PASS, "domain_covered")


def test_search_tier2_without_domains_warns():
    r = ToolRegistry()
    r.register("orphan_t2", "o", func=_noop, tier=2)
    v = audit_tool_conformance("orphan_t2", _facts(r)).face(FACE_SEARCH)
    assert (v.status, v.reason) == (STATUS_WARNING, "tier2_no_domains")


def test_search_tier2_uncovered_domain_warns():
    r = ToolRegistry()
    r.register("weird_domain", "w", func=_noop, tier=2, domains=["no_keywords_domain"])
    v = audit_tool_conformance("weird_domain", _facts(r)).face(FACE_SEARCH)
    assert (v.status, v.reason) == (STATUS_WARNING, "domain_keywords_missing")
    assert "no_keywords_domain" in v.detail


def test_search_tier3_not_evaluated():
    r = ToolRegistry()
    r.register("danger", "d", func=_noop, tier=3, side_effect="destructive")
    v = audit_tool_conformance("danger", _facts(r)).face(FACE_SEARCH)
    assert (v.status, v.reason) == (STATUS_NOT_EVALUATED, "tier3_explicit_only")


# ── planner 面 ───────────────────────────────────────────────────

def _algo(cap: str, tools: tuple, contract: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        id=f"algo_{cap}", capabilities=(cap,), tool_candidates=tools,
        parameter_contract_ref=contract)


def test_planner_dangling_capability_fails():
    r = ToolRegistry()
    _register_good(r)
    # good_tool 未声明 capabilities —— 用直接审计器喂 declared 悬空分支
    d = SimpleNamespace(name="t", capabilities=("ghost_cap",))
    v = _audit_planner(d, _facts(r), {})
    assert (v.status, v.reason) == (STATUS_FAIL, "capability_id_dangling")


def test_planner_algorithm_backed_passes():
    r = ToolRegistry()
    r.register(
        "backed", "b", func=_noop, capabilities=["cap_a"],
        side_effect="pure", network=False, deterministic=True,
        result_size_policy="bounded")
    facts = _facts(r, algorithms=(_algo("cap_a", ("backed",)),))
    v = audit_tool_conformance("backed", facts).face(FACE_PLANNER)
    assert (v.status, v.reason) == (STATUS_PASS, "algorithm_backed")


def test_planner_declared_unbacked_warns():
    r = ToolRegistry()
    r.register(
        "orchestrator", "o", func=_noop, capabilities=["cap_a"],
        side_effect="pure", network=False, deterministic=True,
        result_size_policy="bounded")
    v = audit_tool_conformance("orchestrator", _facts(r)).face(FACE_PLANNER)
    assert (v.status, v.reason) == (STATUS_WARNING, "binding_unbacked")


def test_planner_no_binding_not_evaluated():
    r = ToolRegistry()
    _register_good(r)
    v = audit_tool_conformance("good_tool", _facts(r)).face(FACE_PLANNER)
    assert (v.status, v.reason) == (STATUS_NOT_EVALUATED, "no_capability_binding")


def test_planner_derived_source_passes():
    d = SimpleNamespace(name="t", capabilities=(), capability_source="derived:algorithm_registry")
    v = _audit_planner(d, _facts(ToolRegistry()), {})
    assert (v.status, v.reason) == (STATUS_PASS, "derived_from_algorithm_registry")


# ── pi_native 面 ─────────────────────────────────────────────────

def _spec_stub(fp: str = "ab12", size: int = 100) -> SimpleNamespace:
    return SimpleNamespace(schema_fingerprint=fp, schema_bytes=size)


def test_pi_native_not_frontdoor_not_evaluated():
    r = ToolRegistry()
    _register_good(r)
    v = audit_tool_conformance("good_tool", _facts(r)).face(FACE_PI_NATIVE)
    assert (v.status, v.reason) == (STATUS_NOT_EVALUATED, "not_native_frontdoor")


def test_pi_native_spec_compiles_passes():
    r = ToolRegistry()
    _register_good(r)
    facts = _facts(r, native_names=("good_tool",), spec_compiler=lambda reg, n: _spec_stub())
    v = audit_tool_conformance("good_tool", facts).face(FACE_PI_NATIVE)
    assert (v.status, v.reason) == (STATUS_PASS, "spec_compiled")


def test_pi_native_over_budget_fails():
    r = ToolRegistry()
    _register_good(r)
    facts = _facts(r, native_names=("good_tool",), spec_compiler=lambda reg, n: None)
    v = audit_tool_conformance("good_tool", facts).face(FACE_PI_NATIVE)
    assert (v.status, v.reason) == (STATUS_FAIL, "spec_over_budget")


def test_pi_native_without_schema_fails():
    # 真实 registry 总能从签名推导 schema——「无 schema」分支用鸭子类型
    # registry 直接构造（编译器同款防御路径）。
    fake = SimpleNamespace(
        get_schemas_subset=lambda names: [],
        tool_names=lambda: ["schemaless"],
        descriptor=lambda n: SimpleNamespace(
            name=n, tier=1, status="stable", side_effect="pure",
            requires_credentials=(), capabilities=(), algorithms=(),
            tool_id="schemaless@1.0#cv1"),
    )
    facts = _facts(fake, native_names=("schemaless",))  # type: ignore[arg-type]
    v = audit_tool_conformance("schemaless", facts).face(FACE_PI_NATIVE)
    assert (v.status, v.reason) == (STATUS_FAIL, "no_live_schema")


# ── proxy 面 ─────────────────────────────────────────────────────

@pytest.mark.parametrize("status,expected,reason", [
    ("stable", STATUS_PASS, "proxy_reachable"),
    ("experimental", STATUS_PASS, "proxy_reachable"),
    ("hidden", STATUS_WARNING, "hidden_from_model_surface"),
    ("external_unavailable", STATUS_WARNING, "external_unavailable"),
    ("planned", STATUS_FAIL, "not_executable"),
])
def test_proxy_face_by_status(status, expected, reason):
    r = ToolRegistry()
    r.register("s_tool", "s", func=_noop, status=status)
    v = audit_tool_conformance("s_tool", _facts(r)).face("proxy")
    assert (v.status, v.reason) == (expected, reason)


def test_proxy_deprecated_with_canonical_passes():
    r = ToolRegistry()
    _register_good(r)
    r.register(
        "old_alias", "o", func=_noop, status="deprecated", deprecation_of="good_tool")
    v = audit_tool_conformance("old_alias", _facts(r)).face("proxy")
    assert (v.status, v.reason) == (STATUS_PASS, "proxy_reachable")


# ── dispatch 面 ──────────────────────────────────────────────────

def test_dispatch_clean_passes_with_disclosure():
    d = SimpleNamespace(requires_credentials=("amap_key",), required_permission="admin", tier=2)
    v = _audit_dispatch(d)
    assert (v.status, v.reason) == (STATUS_PASS, "gate_facts_coherent")
    assert "amap_key" in v.detail and "permission=admin" in v.detail


def test_dispatch_blank_credential_id_fails():
    d = SimpleNamespace(requires_credentials=("  ",), required_permission="", tier=1)
    v = _audit_dispatch(d)
    assert (v.status, v.reason) == (STATUS_FAIL, "credential_id_invalid")


def test_dispatch_tier3_notes_confirmation():
    d = SimpleNamespace(requires_credentials=(), required_permission="", tier=3)
    v = _audit_dispatch(d)
    assert v.status == STATUS_PASS
    assert "tier3" in v.detail


# ── result 面 ────────────────────────────────────────────────────

def _result_descriptor(**kw) -> SimpleNamespace:
    base = dict(result_size_policy="bounded", produced_refs=("data",),
                output_semantic_type="geojson_fc")
    base.update(kw)
    return SimpleNamespace(**base)


def test_result_complete_passes():
    from app.lib.gis.tool_capability_conformance import _audit_result
    v = _audit_result(_result_descriptor())
    assert (v.status, v.reason) == (STATUS_PASS, "io_contract_declared")


def test_result_unknown_policy_warns():
    from app.lib.gis.tool_capability_conformance import _audit_result
    v = _audit_result(_result_descriptor(result_size_policy="unknown"))
    assert (v.status, v.reason) == (STATUS_WARNING, "result_size_unknown")


def test_result_ref_offload_without_refs_warns():
    from app.lib.gis.tool_capability_conformance import _audit_result
    v = _audit_result(_result_descriptor(result_size_policy="ref_offload", produced_refs=()))
    assert (v.status, v.reason) == (STATUS_WARNING, "ref_offload_without_ref_contract")


def test_result_missing_output_type_warns():
    from app.lib.gis.tool_capability_conformance import _audit_result
    v = _audit_result(_result_descriptor(output_semantic_type=""))
    assert (v.status, v.reason) == (STATUS_WARNING, "output_type_missing")


# ── science 面 ───────────────────────────────────────────────────

def _contract(cid: str, params: list) -> ParameterContract:
    return ParameterContract(id=cid, parameters=params)


_ALGO_WITH_CONTRACT = SimpleNamespace(
    id="algo_x", capabilities=("cap_a",), tool_candidates=("sci_tool",),
    parameter_contract_ref="ctest")


def _sci_registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register(
        "sci_tool", "s", func=_noop, args_model=_BufferArgs,
        algorithms=["algo_x"], capabilities=["cap_a"],
        side_effect="pure", network=False, deterministic=True,
        result_size_policy="bounded")
    return r


def test_science_no_contract_not_evaluated():
    r = ToolRegistry()
    _register_good(r)
    v = audit_tool_conformance("good_tool", _facts(r)).face(FACE_SCIENCE)
    assert (v.status, v.reason) == (STATUS_NOT_EVALUATED, "no_parameter_contract")


def test_science_aligned_passes():
    contract = _contract("ctest", [
        ParameterSpec(name="distance", type="number", required=True, minimum=0.0001),
        ParameterSpec(name="unit", type="enum", default="m", enum_values=["m", "km"]),
    ])
    facts = _facts(_sci_registry(), algorithms=(_ALGO_WITH_CONTRACT,),
                   contracts={"ctest": contract})
    v = audit_tool_conformance("sci_tool", facts).face(FACE_SCIENCE)
    assert (v.status, v.reason) == (STATUS_PASS, "contract_aligned")


def test_science_required_divergence_warns():
    # 契约:distance 可选带默认;schema:required —— 两侧分叉
    contract = _contract("ctest", [
        ParameterSpec(name="distance", type="number", default=10.0),
        ParameterSpec(name="unit", type="enum", default="m", enum_values=["m", "km"]),
    ])
    facts = _facts(_sci_registry(), algorithms=(_ALGO_WITH_CONTRACT,),
                   contracts={"ctest": contract})
    v = audit_tool_conformance("sci_tool", facts).face(FACE_SCIENCE)
    assert (v.status, v.reason) == (STATUS_WARNING, "contract_divergence")
    assert "distance" in v.detail and "required" in v.detail


def test_science_enum_divergence_warns():
    contract = _contract("ctest", [
        ParameterSpec(name="distance", type="number", required=True),
        ParameterSpec(name="unit", type="enum", default="m", enum_values=["m", "km", "mi"]),
    ])
    facts = _facts(_sci_registry(), algorithms=(_ALGO_WITH_CONTRACT,),
                   contracts={"ctest": contract})
    v = audit_tool_conformance("sci_tool", facts).face(FACE_SCIENCE)
    assert v.reason == "contract_divergence" and "enum" in v.detail


def test_science_range_divergence_warns():
    contract = _contract("ctest", [
        ParameterSpec(name="distance", type="number", required=True, minimum=1.0),
        ParameterSpec(name="unit", type="enum", default="m", enum_values=["m", "km"]),
    ])
    facts = _facts(_sci_registry(), algorithms=(_ALGO_WITH_CONTRACT,),
                   contracts={"ctest": contract})
    v = audit_tool_conformance("sci_tool", facts).face(FACE_SCIENCE)
    assert "minimum" in v.detail


def test_science_absent_param_warns():
    contract = _contract("ctest", [
        ParameterSpec(name="distance", type="number", required=True),
        ParameterSpec(name="unit", type="enum", default="m", enum_values=["m", "km"]),
        ParameterSpec(name="not_in_schema", type="number", default=1.0),
    ])
    facts = _facts(_sci_registry(), algorithms=(_ALGO_WITH_CONTRACT,),
                   contracts={"ctest": contract})
    v = audit_tool_conformance("sci_tool", facts).face(FACE_SCIENCE)
    assert "not_in_schema" in v.detail


def test_science_missing_contract_registration_warns():
    facts = _facts(_sci_registry(), algorithms=(_ALGO_WITH_CONTRACT,), contracts={})
    v = audit_tool_conformance("sci_tool", facts).face(FACE_SCIENCE)
    assert v.reason == "contract_divergence" and "ctest" in v.detail


def test_science_direct_without_schema():
    d = SimpleNamespace(name="no_schema_tool", algorithms=("algo_x",))
    fake = SimpleNamespace(get_schemas_subset=lambda names: [])
    facts = ConformanceFacts(
        registry=fake, parameter_contracts={"ctest": _contract("ctest", [])},
        algorithms=(_ALGO_WITH_CONTRACT,))
    v = _audit_science(d, facts, {"algo_x": _ALGO_WITH_CONTRACT})
    assert (v.status, v.reason) == (STATUS_NOT_EVALUATED, "no_args_schema")


# ── overall 聚合 ─────────────────────────────────────────────────

def test_overall_fail_beats_warning():
    r = ToolRegistry()
    r.register("mixed", "m", func=_noop, status="planned")  # proxy fail + metadata warn
    report = audit_tool_conformance("mixed", _facts(r))
    assert report.overall == STATUS_FAIL


def test_overall_all_not_evaluated():
    # 无 domains 声明、无 capabilities、非 native、tier1 → 各面 pass/not_evaluated
    r = ToolRegistry()
    r.register(
        "io_only", "i", func=_noop,
        side_effect="pure", network=False, deterministic=True,
        result_size_policy="bounded", output_semantic_type="object")
    report = audit_tool_conformance("io_only", _facts(r))
    # planner/science = not_evaluated;其余面 pass → overall pass
    assert report.overall == STATUS_PASS
    statuses = {v.face: v.status for v in report.faces}
    assert statuses[FACE_PLANNER] == STATUS_NOT_EVALUATED
    assert statuses[FACE_SCIENCE] == STATUS_NOT_EVALUATED


def test_report_covers_all_eight_faces_sorted():
    r = ToolRegistry()
    _register_good(r)
    report = audit_tool_conformance("good_tool", _facts(r))
    assert tuple(v.face for v in report.faces) == tuple(sorted(CONFORMANCE_FACES))
    assert set(CONFORMANCE_FACES) == {
        FACE_REGISTRATION, FACE_SEARCH, FACE_PLANNER, FACE_PI_NATIVE,
        "proxy", FACE_DISPATCH, FACE_RESULT, FACE_SCIENCE}


def test_audit_unregistered_tool_raises_keyerror():
    r = ToolRegistry()
    with pytest.raises(KeyError):
        audit_tool_conformance("ghost", _facts(r))
