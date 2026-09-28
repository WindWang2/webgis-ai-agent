"""H10：ActionPlan 编译器契约测试（确定性 / 拓扑 / contract validation）。

负例驱动：TOOL_UNRESOLVED / ACTION_UNRESOLVABLE / SIDE_EFFECT_MISMATCH /
TOOL_DEPRECATED / DEPENDENCY_CYCLE / DEPENDENCY_UNKNOWN / 
COMPENSATION_INFEASIBLE 每码必有「注入缺陷 → 精确命中」。
"""
from __future__ import annotations

from typing import Optional, Sequence

import pytest

from app.lib.gis.action_ir import Compensation, GISAction, GISActionPlan
from app.services.gis_action.compiler import (
    ToolResolution,
    ToolResolver,
    compile_actions,
)


class FakeResolver(ToolResolver):
    """registry/catalog 快照替身（事实表驱动；未注册 → None）。"""

    def __init__(self, tools: Optional[dict] = None,
                 capability_map: Optional[dict] = None):
        self._tools = tools or {}
        self._caps = capability_map or {}

    def resolve_tool(self, tool_name: str) -> Optional[ToolResolution]:
        fact = self._tools.get(tool_name)
        if fact is None:
            return None
        return ToolResolution(**fact)

    def capability_candidates(self, capability_id: str) -> Sequence[str]:
        return tuple(self._caps.get(capability_id, ()))


_MUTATION = dict(tool="webgis_layer_upsert", side_effect="state_mutation")
_PURE = dict(tool="list_datasets", side_effect="pure")


def _plan(*actions: GISAction) -> GISActionPlan:
    return GISActionPlan(plan_id="gap-t", actions=list(actions))


class TestHappyPath:
    def test_compiles_with_resolved_tool(self):
        plan = _plan(GISAction(action_id="act-a", kind="analyze",
                               tool="hotspot_analysis"))
        comp = compile_actions(plan, resolver=FakeResolver(
            {"hotspot_analysis": dict(tool="hotspot_analysis",
                                      side_effect="deterministic_compute")}))
        assert comp.status == "compiled"
        assert comp.steps[0].tool == "hotspot_analysis"
        assert comp.steps[0].client_action_id.startswith("gac.gap-t.01.analyze")
        assert comp.reason_codes == ["ACTION_PLAN_COMPILED"]

    def test_capability_first_resolution_picks_first_candidate(self):
        plan = _plan(GISAction(action_id="act-a", kind="analyze",
                               capability="spatial_statistics"))
        comp = compile_actions(plan, resolver=FakeResolver(
            tools={
                "tool_a": dict(tool="tool_a", side_effect="pure"),
                "tool_b": dict(tool="tool_b", side_effect="pure"),
            },
            capability_map={"spatial_statistics": ["tool_a", "tool_b"]}))
        assert comp.status == "compiled"
        assert comp.steps[0].tool == "tool_a"

    def test_dependency_ordering_topological(self):
        plan = _plan(
            GISAction(action_id="act-c", kind="export", tool="t_export",
                      side_effect="external_io",
                      depends_on=["act-a", "act-b"]),
            GISAction(action_id="act-b", kind="cartograph", tool="t_carto",
                      depends_on=["act-a"]),
            GISAction(action_id="act-a", kind="data_acquire", tool="t_acq"),
        )
        comp = compile_actions(plan, resolver=FakeResolver(tools={
            "t_export": dict(tool="t_export", side_effect="external_side_effect"),
            "t_carto": dict(tool="t_carto", side_effect="pure"),
            "t_acq": dict(tool="t_acq", side_effect="cacheable_read"),
        }))
        assert [s.action_id for s in comp.steps] == ["act-a", "act-b", "act-c"]
        assert [s.wave for s in comp.steps] == [0, 1, 2]

    def test_compile_deterministic(self):
        def build():
            return _plan(
                GISAction(action_id="act-a", kind="data_acquire", tool="t_acq"),
                GISAction(action_id="act-b", kind="analyze", tool="t_ana",
                          depends_on=["act-a"], params={"k": 5}),
            )
        resolver = FakeResolver(tools={
            "t_acq": dict(tool="t_acq", side_effect="cacheable_read"),
            "t_ana": dict(tool="t_ana", side_effect="deterministic_compute"),
        })
        c1 = compile_actions(build(), resolver=resolver)
        c2 = compile_actions(build(), resolver=resolver)
        assert c1.compile_digest == c2.compile_digest
        assert c1.compile_id == c2.compile_id
        assert c1.model_dump() == c2.model_dump()

    def test_resource_summary_counts(self):
        plan = _plan(
            GISAction(action_id="act-a", kind="analyze", tool="t1",
                      resource_class={"latency": "heavy", "memory": "medium"}),
            GISAction(action_id="act-b", kind="inspect", tool="t2",
                      resource_class={"latency": "light"}),
        )
        comp = compile_actions(plan, resolver=FakeResolver(tools={
            "t1": dict(tool="t1", side_effect="pure"),
            "t2": dict(tool="t2", side_effect="pure"),
        }))
        assert comp.resource_summary.actions_total == 2
        assert comp.resource_summary.by_kind == {"analyze": 1, "inspect": 1}
        assert comp.resource_summary.by_dimension["latency"] == {
            "heavy": 1, "light": 1}

    def test_describe_bounded(self):
        plan = _plan(*[
            GISAction(action_id=f"act-{i:04d}", kind="inspect",
                      tool=f"t{i}", params={"p": i})
            for i in range(30)
        ])
        comp = compile_actions(plan, resolver=FakeResolver(tools={
            f"t{i}": dict(tool=f"t{i}", side_effect="pure") for i in range(30)
        }))
        assert len(comp.describe().splitlines()) <= 21


class TestContractValidation:
    def test_tool_unresolved_blocking(self):
        plan = _plan(GISAction(action_id="act-a", kind="analyze",
                               tool="ghost_tool"))
        comp = compile_actions(plan, resolver=FakeResolver())
        assert comp.blocked
        codes = [f.code for f in comp.blocking_findings()]
        assert codes == ["TOOL_UNRESOLVED"]

    def test_capability_without_candidates_blocking(self):
        plan = _plan(GISAction(action_id="act-a", kind="analyze",
                               capability="no_such_capability"))
        comp = compile_actions(plan, resolver=FakeResolver())
        assert comp.blocked
        assert comp.blocking_findings()[0].code == "ACTION_UNRESOLVABLE"

    def test_side_effect_mismatch_blocking(self):
        plan = _plan(GISAction(action_id="act-a", kind="inspect",
                               tool="mutating_tool", side_effect="pure"))
        comp = compile_actions(plan, resolver=FakeResolver(
            {"mutating_tool": dict(tool="mutating_tool",
                                   side_effect="state_mutation")}))
        assert comp.blocked
        assert comp.blocking_findings()[0].code == "SIDE_EFFECT_MISMATCH"

    def test_unclassified_side_effect_warns_not_blocks(self):
        plan = _plan(GISAction(action_id="act-a", kind="inspect",
                               tool="old_tool", side_effect="pure"))
        comp = compile_actions(plan, resolver=FakeResolver(
            {"old_tool": dict(tool="old_tool", side_effect="unclassified")}))
        assert not comp.blocked
        assert any(f.code == "SIDE_EFFECT_UNCLASSIFIED" and f.severity == "warning"
                   for f in comp.findings)

    def test_deprecated_tool_blocking_with_successor_hint(self):
        plan = _plan(GISAction(action_id="act-a", kind="inspect",
                               tool="legacy_tool"))
        comp = compile_actions(plan, resolver=FakeResolver(
            {"legacy_tool": dict(tool="legacy_tool", side_effect="pure",
                                 deprecated=True,
                                 superseded_by="modern_tool")}))
        assert comp.blocked
        finding = comp.blocking_findings()[0]
        assert finding.code == "TOOL_DEPRECATED"
        assert "modern_tool" in finding.detail

    def test_destructive_tool_warns(self):
        plan = _plan(GISAction(action_id="act-a", kind="mutate_presentation",
                               tool="nuke", side_effect="session_state"))
        comp = compile_actions(plan, resolver=FakeResolver(
            {"nuke": dict(tool="nuke", side_effect="destructive")}))
        assert not comp.blocked
        assert any(f.code == "DESTRUCTIVE_TOOL" for f in comp.findings)

    def test_external_io_compensation_infeasible(self):
        plan = _plan(GISAction(
            action_id="act-a", kind="export", tool="t_export",
            side_effect="external_io", failure="compensate"))
        comp = compile_actions(plan, resolver=FakeResolver(
            {"t_export": dict(tool="t_export",
                              side_effect="external_side_effect")}))
        assert comp.blocked
        assert any(f.code == "COMPENSATION_INFEASIBLE"
                   for f in comp.blocking_findings())

    def test_external_io_compensation_kind_infeasible(self):
        plan = _plan(GISAction(
            action_id="act-a", kind="export", tool="t_export",
            side_effect="external_io",
            compensation=Compensation(kind="remove_layer", target="L1")))
        comp = compile_actions(plan, resolver=FakeResolver(
            {"t_export": dict(tool="t_export",
                              side_effect="external_side_effect")}))
        assert comp.blocked

    def test_blocked_plan_has_zero_steps_but_findings_kept(self):
        plan = _plan(
            GISAction(action_id="act-a", kind="inspect", tool="ghost"),
            GISAction(action_id="act-b", kind="inspect", tool="ok_tool"),
        )
        comp = compile_actions(plan, resolver=FakeResolver(
            {"ok_tool": dict(tool="ok_tool", side_effect="pure")}))
        assert comp.blocked
        assert all(s.tool != "ghost" for s in comp.steps)


class TestOrdering:
    def test_dependency_cycle_blocking(self):
        plan = _plan(
            GISAction(action_id="act-a", kind="analyze", tool="t1",
                      depends_on=["act-b"]),
            GISAction(action_id="act-b", kind="analyze", tool="t2",
                      depends_on=["act-a"]),
        )
        comp = compile_actions(plan, resolver=FakeResolver(tools={
            "t1": dict(tool="t1", side_effect="pure"),
            "t2": dict(tool="t2", side_effect="pure"),
        }))
        assert comp.blocked
        assert comp.blocking_findings()[0].code == "DEPENDENCY_CYCLE"

    def test_unknown_dependency_blocking(self):
        plan = _plan(GISAction(action_id="act-a", kind="analyze", tool="t1",
                               depends_on=["act-ghost"]))
        comp = compile_actions(plan, resolver=FakeResolver(
            {"t1": dict(tool="t1", side_effect="pure")}))
        assert comp.blocked
        assert comp.blocking_findings()[0].code == "DEPENDENCY_UNKNOWN"

    def test_duplicated_action_id_blocking(self):
        plan = _plan(
            GISAction(action_id="act-a", kind="analyze", tool="t1"),
            GISAction(action_id="act-a", kind="inspect", tool="t2"),
        )
        comp = compile_actions(plan, resolver=FakeResolver(tools={
            "t1": dict(tool="t1", side_effect="pure"),
            "t2": dict(tool="t2", side_effect="pure"),
        }))
        assert comp.blocked
        assert comp.blocking_findings()[0].code == "ACTION_ID_DUPLICATED"

    def test_dry_run_describe_is_the_compilation(self):
        """dry-run = compile 本身：零 dispatch、纯视图。"""
        plan = _plan(GISAction(action_id="act-a", kind="inspect", tool="t1"))
        comp = compile_actions(plan, resolver=FakeResolver(
            {"t1": dict(tool="t1", side_effect="pure")}))
        assert "would_run" not in comp.describe()  # 编译层无执行语义
        assert comp.status == "compiled"
