"""H10：Action IR golden 测试 —— intent/product-plan → ActionPlan 的
确定性快照（compile digest / diff / 物化 legend 全部钉死）。

golden 文件：``tests/unit/gis/golden/action_ir_v1.json``。
语义级变更（词表/编译器/派生实现）必须 conscious bump：跑
``python -m tests.unit.gis.test_action_golden --regen`` 重录并在 PR 说明。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from app.lib.gis.action_ir import GISAction, GISActionPlan, compute_plan_id
from app.services.gis_action.compiler import ToolResolution, compile_actions
from app.services.gis_action.derive import materialize_classification
from app.lib.cartography.plan_ir import LayerBlueprint, LayerIntent, MapPlanIR

GOLDEN = Path(__file__).parent / "golden" / "action_ir_v1.json"

_VALUES = [3.0, 8.0, 13.0, 21.0, 34.0, 55.0, 42.0, 17.0, 5.0, 29.0,
           61.0, 11.0, 47.0, 2.0, 38.0, 26.0]

_RESOLVER_FACTS = {
    "t_acquire": dict(tool="t_acquire", side_effect="cacheable_read"),
    "t_analyze": dict(tool="t_analyze", side_effect="deterministic_compute"),
    "t_upsert": dict(tool="t_upsert", side_effect="state_mutation"),
    "t_export": dict(tool="t_export", side_effect="external_side_effect"),
}


class _GoldenResolver:
    def resolve_tool(self, name):
        fact = _RESOLVER_FACTS.get(name)
        return ToolResolution(**fact) if fact else None

    def capability_candidates(self, cap):
        return ()


async def _derive_case() -> dict:
    """产品意图（分级制图）→ ActionPlan 投影 → 物化 → 编译。"""
    bp = LayerBlueprint(
        layer_type="fill",
        classification={"k": 7, "method": "quantiles", "palette": "YlOrRd",
                        "field": "pop"},
    )
    ir = MapPlanIR(ir_id="mpir-golden", layer_intents=[
        LayerIntent(intent_id="li-1", action="present_primary",
                    layer_id="L1", source_ref="ref:d1", blueprint=bp),
    ])

    async def loader(ref):
        return {"type": "FeatureCollection", "features": [
            {"properties": {"pop": v}} for v in _VALUES]}

    ir2, records = await materialize_classification(
        ir, current={}, load_geojson=loader)
    legend = ir2.layer_intents[0].blueprint.legend_spec
    return {
        "records": [r.to_dict() for r in records],
        "legend_breaks": legend.get("breaks"),
        "legend_labels": legend.get("labels"),
        "legend_palette_colors": legend.get("palette_colors"),
        "legend_method": legend.get("method"),
        "k_resolved": legend.get("k"),
    }


def _multi_step_case() -> dict:
    """取数 → 分析 → 呈现 → 导出 计划的编译确定性。"""
    actions = [
        GISAction(action_id="act-01", kind="data_acquire", tool="t_acquire",
                  params={"source": "census"}),
        GISAction(action_id="act-02", kind="analyze", tool="t_analyze",
                  params={"k": 5}, depends_on=["act-01"]),
        GISAction(action_id="act-03", kind="mutate_presentation",
                  tool="t_upsert", side_effect="session_state",
                  idempotency="duplicate_safe", depends_on=["act-02"],
                  compensation={"kind": "remove_layer", "target": "L1"}),
        GISAction(action_id="act-04", kind="export", tool="t_export",
                  side_effect="external_io", idempotency="non_idempotent",
                  depends_on=["act-03"]),
    ]
    body = {"plan_version": "1.0.0", "revision": 1, "origin": "product_plan",
            "actions": [a.model_dump() for a in actions]}
    plan = GISActionPlan(plan_id=compute_plan_id(body), origin="product_plan",
                         actions=actions)
    comp = compile_actions(plan, resolver=_GoldenResolver())
    return {
        "plan_id": plan.plan_id,
        "plan_fingerprint": plan.plan_fingerprint(),
        "compile_id": comp.compile_id,
        "compile_digest": comp.compile_digest,
        "status": comp.status,
        "steps": [
            {"step": s.step, "wave": s.wave, "kind": s.kind, "tool": s.tool,
             "client_action_id": s.client_action_id,
             "side_effect": s.side_effect, "idempotency": s.idempotency,
             "failure": s.failure}
            for s in comp.steps
        ],
        "resource_summary": comp.resource_summary.model_dump(),
        "finding_codes": [f.code for f in comp.findings],
    }


def _diff_case() -> dict:
    """k 5→7 的计划 diff（参数级回答"改了什么"）。"""
    def plan(k: int) -> GISActionPlan:
        return GISActionPlan(plan_id="gap-d", actions=[
            GISAction(action_id="act-1", kind="cartograph", tool="t_upsert",
                      params={"k": k, "palette": "YlOrRd"})])

    from app.services.gis_action.diff import diff_plans
    d = diff_plans(plan(5), plan(7))
    return d.to_dict()


def build_golden() -> dict:
    return {
        "golden_version": "1",
        "derive_case": asyncio.run(_derive_case()),
        "multi_step_case": _multi_step_case(),
        "diff_case": _diff_case(),
    }


if __name__ == "__main__" and "--regen" in sys.argv:
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(
        json.dumps(build_golden(), ensure_ascii=False, indent=2,
                   sort_keys=True) + "\n",
        encoding="utf-8")
    print(f"golden regenerated: {GOLDEN}")


class TestActionIRGolden:
    def test_golden_matches(self):
        assert GOLDEN.exists(), "golden 文件缺失"
        expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
        actual = build_golden()
        assert actual == expected, (
            "Action IR golden 漂移 —— 词表/编译器/派生实现变更必须"
            " conscious bump：--regen 重录并在 PR 说明")
