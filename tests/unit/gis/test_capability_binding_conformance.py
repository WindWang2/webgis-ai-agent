"""ADR-0204 D1/D2：capability↔tool 绑定声明面 conformance（W1+W2）。

覆盖：
- 纯函数校验器（dangling fatal / unbacked warning / metadata 完整度 /
  输出契约分歧 / 确定性 / 聚合折叠）；
- manifest v4 收敛语义（声明面并入反查图 / 溯源 / 悬空 fatal / strict
  fail-fast / 指纹敏感 → is_stale_plan）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.lib.gis.capability_conformance import (
    CODE_BINDING_UNBACKED,
    CODE_ID_DANGLING,
    CODE_METADATA_INCOMPLETE,
    CODE_OUTPUT_DIVERGENCE,
    SEVERITY_FATAL,
    SEVERITY_WARNING,
    ConformanceIssue,
    aggregate_tool_issues,
    validate_capability_conformance,
)


def _algo(capabilities, tool_candidates):
    return SimpleNamespace(
        capabilities=list(capabilities), tool_candidates=list(tool_candidates))


def _run(algos=(), tools=(), capability_ids=("cap_a", "cap_b")):
    return validate_capability_conformance(
        capability_ids=list(capability_ids),
        algorithms=algos,
        tool_metadata=list(tools),
    )


# ── 纯函数校验器 ──────────────────────────────────────────────────────


def test_dangling_declared_id_is_fatal():
    issues = _run(
        algos=[_algo(["cap_a"], ["tool_derived"])],
        tools=[("ghost_tool", {"capabilities": ["no_such_cap"]})],
    )
    fatal = [i for i in issues if i.code == CODE_ID_DANGLING]
    assert len(fatal) == 1
    assert fatal[0].severity == SEVERITY_FATAL
    assert fatal[0].tool == "ghost_tool"
    assert fatal[0].capability == "no_such_cap"


def test_unbacked_declared_binding_is_warning():
    issues = _run(
        algos=[_algo(["cap_a"], ["tool_derived"])],
        tools=[("declared_only", {"capabilities": ["cap_b"]})],
    )
    unbacked = [i for i in issues if i.code == CODE_BINDING_UNBACKED]
    assert len(unbacked) == 1
    assert unbacked[0].severity == SEVERITY_WARNING
    assert unbacked[0].capability == "cap_b"
    # 派生面覆盖的声明不产生 unbacked
    issues2 = _run(
        algos=[_algo(["cap_a"], ["tool_derived"])],
        tools=[("tool_derived", {"capabilities": ["cap_a"]})],
    )
    assert not [i for i in issues2 if i.code == CODE_BINDING_UNBACKED]


def test_metadata_incomplete_lists_missing_fields():
    issues = _run(
        tools=[("bare_tool", {"capabilities": ["cap_a"]})],
    )
    incomplete = [i for i in issues if i.code == CODE_METADATA_INCOMPLETE]
    assert len(incomplete) == 1
    assert "network" in incomplete[0].detail
    assert "deterministic" in incomplete[0].detail
    assert "side_effect" in incomplete[0].detail
    assert "result_size_policy" in incomplete[0].detail

    # 元数据完整的工具不产生 incomplete
    rich = {
        "capabilities": ["cap_a"], "network": False, "deterministic": True,
        "side_effect": "pure", "result_size_policy": "inline_small",
    }
    issues2 = _run(tools=[("rich_tool", rich)])
    assert not [i for i in issues2 if i.code == CODE_METADATA_INCOMPLETE]


def test_output_contract_divergence_same_capability():
    issues = _run(
        tools=[
            ("tool_geo", {"capabilities": ["cap_a"],
                          "output_semantic_type": "geojson_fc"}),
            ("tool_chart", {"capabilities": ["cap_a"],
                            "output_semantic_type": "chart"}),
        ],
    )
    div = [i for i in issues if i.code == CODE_OUTPUT_DIVERGENCE]
    assert len(div) == 1
    assert "geojson_fc" in div[0].detail and "chart" in div[0].detail
    # 一致输出不产生分歧
    issues2 = _run(
        tools=[
            ("tool_geo", {"capabilities": ["cap_a"],
                          "output_semantic_type": "geojson_fc"}),
            ("tool_geo2", {"capabilities": ["cap_a"],
                           "output_semantic_type": "geojson_fc"}),
        ],
    )
    assert not [i for i in issues2 if i.code == CODE_OUTPUT_DIVERGENCE]


def test_validator_deterministic_order_and_no_declarations_no_findings():
    tools = [
        ("z_tool", {"capabilities": ["nope_1"]}),
        ("a_tool", {"capabilities": ["nope_2"]}),
    ]
    issues = _run(tools=tools, capability_ids=())
    # 排序键 = (code, tool, capability)：同 code 内 tool 字典序
    for code in {i.code for i in issues}:
        tools_in_code = [i.tool for i in issues if i.code == code]
        assert tools_in_code == sorted(tools_in_code)
    assert issues == _run(tools=tools, capability_ids=())
    # 未声明 capability 的工具零发现
    assert _run(tools=[("plain", {"tier": 1})]) == []


def test_aggregate_tool_issues_folds_and_bounds():
    issues = [
        ConformanceIssue(CODE_BINDING_UNBACKED, SEVERITY_WARNING,
                         f"t{i}", "cap_a", "") for i in range(20)
    ]
    folded = aggregate_tool_issues(
        issues, codes=[CODE_BINDING_UNBACKED],
        code="tool_capability_divergence", detail_header="unbacked")
    assert len(folded) == 1
    assert "20 tools" in folded[0].detail and "(+4 more)" in folded[0].detail
    assert aggregate_tool_issues([], codes=[CODE_BINDING_UNBACKED],
                                 code="x", detail_header="h") == []


# ── manifest v4 收敛 ─────────────────────────────────────────────────


@pytest.fixture(scope="module")
def live_manifest():
    from app.lib.gis.runtime_manifest import compile_runtime_manifest

    return compile_runtime_manifest()


def test_manifest_v4_declares_binding_surfaces(live_manifest):
    from app.lib.gis.runtime_manifest import MANIFEST_VERSION

    assert MANIFEST_VERSION == 4
    assert live_manifest.manifest_version == 4
    # 真实 registry：声明面非空且全部入溯源字典
    assert len(live_manifest.declared_capability_bindings) > 0
    for tool, caps in live_manifest.declared_capability_bindings.items():
        assert caps and tool in live_manifest.tools
        assert live_manifest.tools[tool]["capabilities"] == caps


def test_manifest_merges_declared_only_providers(live_manifest):
    # recon 实测：image_segmentation 有 18 个声明面工具（算法链不可达）。
    providers = live_manifest.capability_to_tools.get("image_segmentation", [])
    assert len(providers) > 1, "声明面 provider 必须并入反查图"
    declared_tools = {
        t for t, caps in live_manifest.declared_capability_bindings.items()
        if "image_segmentation" in caps
    }
    assert declared_tools, "实测声明簇缺失（数据回归？）"
    for t in declared_tools:
        assert t in providers


def test_manifest_aggregates_divergence_without_fatal(live_manifest):
    codes = [i.code for i in live_manifest.issues]
    assert codes.count("tool_capability_divergence") == 1, "分歧折叠为单条"
    assert not [c for c in codes if c == CODE_ID_DANGLING], "真实 registry 无悬空"
    assert live_manifest.fatal_issues() == []


def test_declared_binding_visible_and_dangling_fatal(tmp_path):
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    from app.lib.gis.runtime_manifest import (
        compile_runtime_manifest,
        validate_runtime_manifest_strict,
    )

    def _build(dangling: bool) -> ToolRegistry:
        reg = ToolRegistry()
        init_tools(reg)
        reg.register(
            "abi_probe_tool",
            "probe",
            lambda **kw: {"ok": True},
            parameters={"type": "object", "properties": {}, "required": []},
            tier=1,
            capabilities=["cap_does_not_exist"] if dangling else ["poi_query"],
        )
        return reg

    ok_manifest = compile_runtime_manifest(tool_registry=_build(False))
    # 声明（非算法候选链）的 poi_query provider 进入反查图
    assert "abi_probe_tool" in ok_manifest.capability_to_tools.get("poi_query", [])
    assert ok_manifest.declared_capability_bindings.get("abi_probe_tool") == ["poi_query"]

    bad_manifest = compile_runtime_manifest(tool_registry=_build(True))
    fatal_codes = [i.code for i in bad_manifest.fatal_issues()]
    assert CODE_ID_DANGLING in fatal_codes
    # 悬空不入图（诚实缺省）
    assert "cap_does_not_exist" not in bad_manifest.capability_to_tools
    with pytest.raises(RuntimeError, match="capability_id_dangling"):
        validate_runtime_manifest_strict(bad_manifest)


def test_binding_drift_changes_fingerprint_and_stales_plan():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    from app.lib.gis.runtime_manifest import compile_runtime_manifest

    def _build(with_caps: bool) -> ToolRegistry:
        reg = ToolRegistry()
        init_tools(reg)
        reg.register(
            "abi_drift_tool",
            "drift probe",
            lambda **kw: {"ok": True},
            parameters={"type": "object", "properties": {}, "required": []},
            tier=1,
            capabilities=["poi_query"] if with_caps else None,
        )
        return reg

    fp_plain = compile_runtime_manifest(tool_registry=_build(False)).fingerprint
    fp_caps = compile_runtime_manifest(tool_registry=_build(True)).fingerprint
    assert fp_plain != fp_caps, "绑定漂移必须进指纹（ADR-0204 D1）"
    m_caps = compile_runtime_manifest(tool_registry=_build(True))
    assert m_caps.is_stale_plan(fp_plain) is True
    assert m_caps.is_stale_plan(fp_caps) is False
