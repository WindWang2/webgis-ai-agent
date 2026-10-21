"""G05 task-level 场景库（corpus）。

场景 = 人工选取的不变量期望（不是录制当前输出）；12+ case family /
20+ 场景的正/负/多轮/恢复/注入矩阵见 planning/g05/TEST_MATRIX.md。
本模块是唯一场景事实源；fixtures/task_scenarios/*.json 由
scripts/g05_task_acceptance.py 从此处 pin 生成（逐字节一致性由
test_corpus 钉住）。
"""
from __future__ import annotations

from typing import Dict, List

from .contracts import (
    AgentStep,
    DatasetSpec,
    Expectation,
    Face,
    ScenarioContract,
    StepKind,
    TurnSpec,
)

_Q = "看看成都小学都在哪里，标出点位"


def _step(call_id: str, kind: StepKind, **kw) -> AgentStep:
    return AgentStep(call_id=call_id, kind=kind, **kw)


def _exp(eid: str, face: Face, kind: str, path: str, op: str = "eq",
         value=None, expected: str = "pass", note: str = "") -> Expectation:
    return Expectation(
        id=eid, face=face, kind=kind, path=path, op=op, value=value,
        expected_verdict=expected, note=note,
    )


#: 每个场景共享的诚实边界声明（NL 理解不在离线 lane 判定范围）。
COMMON_NOT_EVALUATED = [
    "nl_understanding: Pi LLM 的 NL→tool-call 决策由 scripted steps 代替，"
    "语义理解正确性不在本 lane 判定（见 contracts.py 设计披露）",
    "final_text: 最终答复文本经 Pi transcript 持久化，dispatch 缝不可观测",
]


# ── F01 学校分布点图（正例基线）───────────────────────────────────────────

def f01_school_points_map() -> ScenarioContract:
    return ScenarioContract(
        id="f01-school-points-map",
        family="F01",
        title="成都小学点位分布 → 点位图产品（离线诚实降级）",
        description=(
            "意图→计划→数据存储→产品组装→结算→goal_satisfaction/导出全链。"
            "query 选取确定性 planner 的 poi_distribution_overview recipe"
            "（mandatory=poi_query+point_profile，离线可完成——admin_boundary"
            " 族查询的诚实降级另见 F03 族场景）。"),
        user_goal=_Q,
        datasets=[DatasetSpec(name="schools", dataset_kind="school_points")],
        turns=[TurnSpec(
            turn_id="t1", message=_Q,
            steps=[
                _step("intent", StepKind.INTENT),
                _step("store", StepKind.STORE_DATA,
                      arguments={"name": "schools",
                                 "dataset_kind": "school_points"}),
                _step("product", StepKind.PRODUCT,
                      arguments={"primary_ref": "{{ref:schools}}",
                                 "title": "成都市小学分布（模拟数据）"}),
            ],
        )],
        expectations=[
            _exp("f01-intent-scope", Face.INTENT, "intent_resolved",
                 "plan.recipe_id", op="exists"),
            _exp("f01-mapspec-exists", Face.MAPSPEC, "mapspec_present",
                 "mapspec.exists", value=True),
            _exp("f01-points-layer", Face.MAPSPEC, "mapspec_layer",
                 "mapspec.layer_types", op="contains", value="circle"),
            _exp("f01-components", Face.MAPSPEC, "mapspec_component",
                 "mapspec.component_types", op="exists"),
            _exp("f01-export-check", Face.RENDER_EXPORT, "export_status",
                 "export.status", op="in",
                 value=["pass", "warning"]),
            _exp("f01-requirement-doc", Face.REQUIREMENT,
                 "requirement_state", "requirement.exists", value=True),
            # fail-closed 红线：离线数据缺口下任务不得宣称完成
            _exp("f01-fail-closed-verdict", Face.FINAL_ANSWER, "goal_verdict",
                 "map_product.goal_satisfaction_verdict", value="blocked",
                 note="poi/admin boundary 数据源离线不可达 → BLOCKED_BY_DATA"),
            _exp("f01-fail-closed-task", Face.FINAL_ANSWER, "task_complete",
                 "map_product.task_complete", value=False),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["baseline", "points"],
    )


_ALL_BUILDERS = []


def register(builder):
    _ALL_BUILDERS.append(builder)
    return builder


def all_scenarios() -> List[ScenarioContract]:
    return [b() for b in _ALL_BUILDERS]


def scenarios_by_id() -> Dict[str, ScenarioContract]:
    return {s.id: s for s in all_scenarios()}


def scenarios_by_family() -> Dict[str, List[ScenarioContract]]:
    out: Dict[str, List[ScenarioContract]] = {}
    for s in all_scenarios():
        out.setdefault(s.family, []).append(s)
    return out


# ── 注册区（W1 起逐步填充至 12+ family / 20+ 场景）──────────────────────
register(f01_school_points_map)
