"""Runtime Scenario Corpus（V4 Wave 9 — ADR-0104 决策 10）。

Phase-0 审计 07：20,088 一致性语料 100% plan-tier；超时/取消/工具失败/
陈旧产物/样式编辑/观察失败/修复闭环等 runtime 类别只有 ~40 个散案例；
E2E 场景 7 个（目标 ≥100）。

本模块沿用人审定表 × 确定性扩展的成熟模式（conformance 同门）建立
**runtime 层语料**：

- ``RUNTIME_SITUATIONS``：24 个人审定的 runtime 情境类别（失败/编辑/
  续跑/披露），每类带机器可读期望码 + **可追溯性**（指向锁定该语义的
  回归套件路径 —— 语料不是自证断言，而是回归地基的索引面）；
- ``build_runtime_corpus()``：情境 × 语义族（复用 conformance 审定
  标签）× scope × 语言 × 句式 → 确定性展开 ≥3,000 条案例。**诚实构成**
  （review R3）：这是「情境索引的 plan-身份回归」—— 144 个唯一查询 ×
  24 情境标签，plan 契约随标签不变（同查询同身份由测试钉住）；情境
  期望码/verdict_gate 是**可追溯元数据**（索引回归套件），不经 runner
  断言；
- ``build_runtime_execution_corpus()``：**真实执行层** —— 5 个派发可
  观察情境（失败注入/缺 ref/重复失败/依赖链/大载荷）× 语义族 × 数据
  规模 = 60 条案例，经 ``simulate_agent_loop`` 在真实 registry 派发
  断言（错误码/结果形态/no-progress 原因码）；
- ``build_e2e_scenario_corpus()``：7 个既有代表性场景 × 变体（续跑/
  编辑/故障注入）× 语言 → ≥100 个 ≥2-turn 复合场景定义。

全部确定性（同输入同语料、零 LLM、零网络）；语言地板 ≥30% en。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

# ── 人审定的 runtime 情境表（期望码 + 回归可追溯性）─────────────────────

@dataclass(frozen=True)
class RuntimeSituation:
    """一个 runtime 情境类别（人审定工件）。

    - expectation：该情境下 runtime 必须兑现的行为码（稳定字面量）；
    - traceability：锁定该语义的回归套件路径（相对 repo root）——
      语料索引回归地基，不自证；
    - verdict_gate：该情境触发时产品裁决的允许集（评测消费）。
    """

    situation_id: str
    kind: str                      # failure | edit | continuation | disclosure
    description: str
    expectation: Tuple[str, ...]
    traceability: str
    verdict_gate: Tuple[str, ...] = ()


RUNTIME_SITUATIONS: Tuple[RuntimeSituation, ...] = (
    RuntimeSituation(
        "missing-data", "failure", "必需数据缺席",
        ("blocked_disclosed", "BLOCKED_BY_DATA"),
        "tests/unit/gis_harness/test_data_qualification.py",
        ("BLOCKED_BY_DATA", "BLOCKED_BY_METHOD", "NEEDS_REPAIR"),
    ),
    RuntimeSituation(
        "data-later-arrives", "continuation", "数据迟到（blocked 后补齐）",
        ("blocked_recompute_on_arrival", "contract_unblock_rewrite"),
        "tests/unit/gis_harness/test_workflow_instance.py",
        ("READY", "READY_WITH_WARNINGS"),
    ),
    RuntimeSituation(
        "wrong-crs", "failure", "坐标系不符合方法要求",
        ("crs_gate_blocks_or_requires_transform", "unknown_not_unsatisfied"),
        "tests/unit/gis_harness/test_data_qualification.py",
        ("BLOCKED_BY_METHOD", "BLOCKED_BY_DATA", "NEEDS_REPAIR"),
    ),
    RuntimeSituation(
        "invalid-geometry", "failure", "无效几何（自交/空）",
        ("quality_warning_disclosed", "no_false_ready"),
        "tests/unit/gis_harness/test_data_qualification.py",
        ("READY_WITH_WARNINGS", "NEEDS_REPAIR", "BLOCKED_BY_DATA"),
    ),
    RuntimeSituation(
        "conflicting-intent", "failure", "冲突意图（多任务混杂）",
        ("conservative_task_resolution", "ontology_top1_pinned"),
        "tests/unit/gis_harness/test_conformance_corpus.py",
        ("READY", "READY_WITH_WARNINGS"),
    ),
    RuntimeSituation(
        "explicit-algorithm", "continuation", "用户点名算法",
        ("hint_via_hard_gates", "facts_over_text"),
        "tests/unit/gis_harness/test_kriging_vertical_slice.py",
        ("READY", "READY_WITH_WARNINGS", "BLOCKED_BY_METHOD"),
    ),
    RuntimeSituation(
        "algorithm-unavailable", "failure", "点名算法不可用",
        ("fallback_chain_with_disclosure", "not_allowed_blocks_completion"),
        "tests/unit/gis_harness/test_fallback_v3.py",
        ("READY_WITH_WARNINGS", "BLOCKED_BY_METHOD", "NEEDS_REPAIR"),
    ),
    RuntimeSituation(
        "backend-downgrade", "failure", "后端/执行档位降级",
        ("downgrade_class_disclosed",),
        "tests/unit/gis_harness/test_fallback_v3.py",
        ("READY_WITH_WARNINGS", "NEEDS_REPAIR"),
    ),
    RuntimeSituation(
        "timeout", "failure", "工具/turn 超时",
        ("honest_failure", "no_retry_loop"),
        "tests/unit/test_reliability_security_perf_v2.py",
        ("NEEDS_REPAIR",),
    ),
    RuntimeSituation(
        "cancellation", "failure", "用户取消",
        ("cancelled_not_failed", "dedup_slot_released"),
        "tests/unit/test_reliability_security_perf_v2.py",
        ("NEEDS_REPAIR",),
    ),
    RuntimeSituation(
        "tool-failure", "failure", "工具执行失败",
        ("row_failed_retryable", "dag_downstream_blocked"),
        "tests/test_tool_error_classification_529.py",
        ("NEEDS_REPAIR", "BLOCKED_BY_DATA", "BLOCKED_BY_METHOD"),
    ),
    RuntimeSituation(
        "provider-fallback", "failure", "模型提供方降级",
        ("fallback_after_model_selected",),
        "tests/unit/test_trace_replay_v2.py",
        ("READY", "READY_WITH_WARNINGS"),
    ),
    RuntimeSituation(
        "context-overflow", "failure", "上下文溢出",
        ("deterministic_recovery", "no_silent_truncation_of_safety"),
        "tests/test_history_compression.py",
        ("READY", "READY_WITH_WARNINGS", "NEEDS_REPAIR"),
    ),
    RuntimeSituation(
        "stale-artifact", "edit", "上游产物过期/失效",
        ("stale_disclosed", "no_false_ready", "downstream_only_invalidation"),
        "tests/unit/gis_harness/test_workflow_instance.py",
        ("READY_WITH_WARNINGS", "NEEDS_REPAIR", "BLOCKED_BY_DATA"),
    ),
    RuntimeSituation(
        "style-only-edit", "edit", "纯样式编辑（颜色/底图）",
        ("no_science_recompute", "presentation_revision_only"),
        "tests/unit/gis_harness/test_workflow_instance.py",
        ("READY", "READY_WITH_WARNINGS"),
    ),
    RuntimeSituation(
        "layer-visibility-edit", "edit", "图层显隐编辑",
        ("user_wins_respected", "desired_state_audit"),
        "tests/unit/gis_harness/test_map_completion.py",
        ("READY", "READY_WITH_WARNINGS", "NEEDS_REPAIR"),
    ),
    RuntimeSituation(
        "chart-edit", "edit", "图表组件编辑",
        ("chart_contract_maintained", "chart_required_slot_enforced"),
        "tests/unit/gis_harness/test_observation_completion_v4.py",
        ("READY", "READY_WITH_WARNINGS", "NEEDS_REPAIR"),
    ),
    RuntimeSituation(
        "observation-failure", "failure", "渲染观察缺失/失败",
        ("render_unknown_not_ready_gate", "honest_disclosure"),
        "tests/unit/gis_harness/test_map_verification.py",
        ("NEEDS_REPAIR",),
    ),
    RuntimeSituation(
        "repair-success", "continuation", "修复成功收敛",
        ("needs_repair_to_complete", "reobserve_then_verify"),
        "tests/unit/test_map_product_finalization_scenarios.py",
        ("READY", "READY_WITH_WARNINGS"),
    ),
    RuntimeSituation(
        "repair-failure", "failure", "修复预算内未收敛",
        ("honest_needs_repair_persist", "budget_bounded"),
        "tests/unit/test_map_product_finalization_scenarios.py",
        ("NEEDS_REPAIR", "BLOCKED_BY_METHOD"),
    ),
    RuntimeSituation(
        "no-progress-recovery", "failure", "无进展停滞",
        ("no_progress_hints_surface", "pattern_reason_codes"),
        "tests/unit/test_reliability_security_perf_v2.py",
        ("NEEDS_REPAIR",),
    ),
    RuntimeSituation(
        "multi-turn-followup", "continuation", "多轮追问/范围收窄",
        ("plan_continuity", "goal_supersede_lineage"),
        "tests/unit/gis_harness/test_multiturn_scenarios.py",
        ("READY", "READY_WITH_WARNINGS"),
    ),
    RuntimeSituation(
        "uncertainty-owed-disclosure", "disclosure", "欠不确定性披露",
        ("uncertainty_dim_requires_evidence",),
        "tests/unit/gis_harness/test_observation_completion_v4.py",
        ("READY_WITH_WARNINGS", "NEEDS_REPAIR"),
    ),
    RuntimeSituation(
        "param-change-recompute", "edit", "参数/算法变更重算",
        ("param_visible_to_invalidation", "stale_propagation"),
        "tests/unit/gis_harness/test_workflow_instance.py",
        ("READY", "READY_WITH_WARNINGS", "NEEDS_REPAIR"),
    ),
)

#: runtime 语义族（从 conformance 审定族中确定性选取：有 task/recipe
#: 契约 + en 种子；按 domain 去重取前 8）。
RUNTIME_DOMAINS = (
    "distribution", "density", "interpolation", "equity",
    "decision", "network", "terrain", "statistics",
)

#: scope 子集（conformance SCOPE_VARIANTS 的 3 个代表：无/市/跨市）。
RUNTIME_SCOPE_IDS = ("city-unspecified", "city-chengdu", "city-beijing")

#: 句式子集（conformance UTTERANCE_VARIANTS 的 4 个代表）。
RUNTIME_UTTERANCE_IDS = ("direct", "analyze", "situation", "interrogative")


@dataclass(frozen=True)
class RuntimeCase:
    """一条 runtime 层案例：可执行的 plan 契约 + 情境期望索引。"""

    runtime_id: str
    situation_id: str
    family_id: str
    scope_id: str
    lang: str
    utterance_id: str
    query: str
    expectation: Tuple[str, ...]
    traceability: str
    verdict_gate: Tuple[str, ...]
    plan_case: object = None  # GISBenchmarkCase（避免循环 import 用 object 标注）


def _select_runtime_families() -> List[object]:
    """确定性语义族选取：有 task/recipe 契约 + en 种子；按 RUNTIME_DOMAINS
    序取每个 domain 的第一个合格族，至多 8 个。"""
    from app.evaluation.conformance import CONFORMANCE_FAMILIES

    by_domain: Dict[str, object] = {}
    for fam in CONFORMANCE_FAMILIES:
        domain = str(getattr(fam, "domain", ""))
        if domain not in RUNTIME_DOMAINS or domain in by_domain:
            continue
        if not (getattr(fam, "expected_task", None)
                and getattr(fam, "expected_recipe", None)
                and getattr(fam, "phrases_en", None)):
            continue
        by_domain[domain] = fam
    return [by_domain[d] for d in RUNTIME_DOMAINS if d in by_domain]


def build_runtime_corpus() -> List[RuntimeCase]:
    """确定性展开（同输入同输出；case id 稳定）。"""
    from app.evaluation.case import GISBenchmarkCase
    from app.evaluation.conformance import (
        SCOPE_VARIANTS,
        UTTERANCE_VARIANTS,
        _SCOPE_EN,
    )

    scopes = {
        sid: (prefix, _SCOPE_EN.get(sid, "")) for prefix, sid in SCOPE_VARIANTS
    }
    utterances = {uid: (zh_t, en_t) for zh_t, en_t, uid in UTTERANCE_VARIANTS}
    families = _select_runtime_families()
    situations = RUNTIME_SITUATIONS

    cases: List[RuntimeCase] = []
    for situation in situations:
        for fam in families:
            for scope_id in RUNTIME_SCOPE_IDS:
                prefix, scope_en = scopes[scope_id]
                for lang in ("zh", "en"):
                    seed = (
                        fam.phrases_zh[0] if lang == "zh" else fam.phrases_en[0]
                    )
                    for utt_id in RUNTIME_UTTERANCE_IDS:
                        zh_t, en_t = utterances[utt_id]
                        if lang == "zh":
                            query = zh_t.format(scope=prefix, q=seed)
                        else:
                            query = en_t.format(
                                scope_en=scope_en, q=seed)
                        rid = (
                            f"RT-{situation.situation_id}-{fam.family_id}"
                            f"-{scope_id}-{lang}-{utt_id}"
                        )
                        plan_case = GISBenchmarkCase(
                            id=rid,
                            name=f"runtime:{situation.situation_id}",
                            # 复用语义族自带的 conformance group（closed
                            # Literal，runtime 层不加新词表值）。
                            group=getattr(fam, "group", None) or "conformance",
                            query=query,
                            plan_only=True,
                            expected_task=fam.expected_task,
                            expected_recipe=fam.expected_recipe,
                            expected_capabilities=list(fam.expected_capabilities),
                            expected_warning_codes=list(fam.expected_warning_codes),
                            forbidden_warning_codes=list(fam.forbidden_warning_codes),
                            expected_ontology_task=fam.expected_ontology_task,
                        )
                        cases.append(RuntimeCase(
                            runtime_id=rid,
                            situation_id=situation.situation_id,
                            family_id=fam.family_id,
                            scope_id=scope_id,
                            lang=lang,
                            utterance_id=utt_id,
                            query=query,
                            expectation=situation.expectation,
                            traceability=situation.traceability,
                            verdict_gate=situation.verdict_gate,
                            plan_case=plan_case,
                        ))
    return cases


# ── 复合 E2E 场景（≥100：base × 变体 × 语言）────────────────────────────

@dataclass(frozen=True)
class ScenarioTurn:
    """复合场景的一个 turn：查询 + 该 turn 的行为期望码。"""

    query: str
    expectation: Tuple[str, ...] = ()


@dataclass(frozen=True)
class CompositeScenario:
    """≥2 turn 的端到端复合场景定义（确定性、离线可走查）。"""

    scenario_id: str
    base_id: str
    variant: str
    lang: str
    title: str
    turns: Tuple[ScenarioTurn, ...] = ()

    def to_bounded_dict(self) -> Dict[str, object]:
        return {
            "scenario_id": self.scenario_id,
            "base_id": self.base_id,
            "variant": self.variant,
            "lang": self.lang,
            "title": self.title[:160],
            "turn_count": len(self.turns),
            "turns": [
                {"query": t.query[:200], "expectation": list(t.expectation)}
                for t in self.turns
            ],
        }


#: 变体模板（lang, turn-2 查询, 期望码）。plain 变体 = 基线单焦点复跑。
_VARIANTS: Tuple[Tuple[str, str, str, Tuple[str, ...]], ...] = (
    ("plain", "把上一目标按当前范围重新整理", "restate the previous goal for the current area",
     ("plan_continuity",)),
    ("scope-refine", "把范围聚焦到武侯区", "narrow the scope to Wuhou District",
     ("plan_continuity", "scope_refine")),
    ("style-edit", "把专题图层颜色调深一点", "make the thematic layer colors darker",
     ("no_science_recompute",)),
    ("visibility-edit", "隐藏区县边界图层", "hide the district boundary layer",
     ("user_wins_respected",)),
    ("param-edit", "把分析精度调高一档", "increase the analysis resolution one notch",
     ("param_visible_to_invalidation",)),
    ("inject-timeout", "上一步执行超时了，请如实汇报", "the last step timed out, report honestly",
     ("honest_failure", "no_retry_loop")),
    ("inject-tool-failure", "如果上一步失败，标记为可重试", "if the last step failed, mark it retryable",
     ("row_failed_retryable",)),
    ("inject-stale-artifact", "上游数据已更新，请指出哪些结果过期", "the upstream data changed, point out stale results",
     ("stale_disclosed", "downstream_only_invalidation")),
    ("inject-cancel", "取消当前任务", "cancel the current task",
     ("cancelled_not_failed",)),
)


def build_e2e_scenario_corpus() -> List[CompositeScenario]:
    """7 个代表性 base × 9 变体 × 2 语言 = ≥100 复合场景（确定性）。"""
    from app.evaluation.scenarios import build_scenarios

    out: List[CompositeScenario] = []
    for base in build_scenarios():
        seed_query = ""
        if base.plan_cases:
            seed_query = base.plan_cases[0].query
        elif base.execute_cases:
            seed_query = base.execute_cases[0].query
        if not seed_query:
            continue
        for variant, zh_q, en_q, expectation in _VARIANTS:
            for lang in ("zh", "en"):
                follow = zh_q if lang == "zh" else en_q
                sid = f"E2E-{base.scenario_id}-{variant}-{lang}"
                out.append(CompositeScenario(
                    scenario_id=sid,
                    base_id=base.scenario_id,
                    variant=variant,
                    lang=lang,
                    title=f"{base.title} :: {variant} [{lang}]",
                    turns=(
                        ScenarioTurn(query=seed_query, expectation=("plan_identity",)),
                        ScenarioTurn(query=follow, expectation=expectation),
                    ),
                ))
    return out


__all__ = [
    "RuntimeSituation",
    "RUNTIME_SITUATIONS",
    "RuntimeCase",
    "build_runtime_corpus",
    "ScenarioTurn",
    "CompositeScenario",
    "build_e2e_scenario_corpus",
    "RuntimeExecutionCase",
    "build_runtime_execution_corpus",
]


# ── 真实执行层（review R3 MAJOR：情境必须可执行，不是元数据标签）─────────

@dataclass(frozen=True)
class RuntimeExecutionCase:
    """一条**真实执行**的 runtime 案例：scripted calls 经
    ``simulate_agent_loop`` 在真实 registry 上派发（无 LLM/无网络），
    断言错误码 / 结果形态 / no-progress 原因码。"""

    case_id: str
    situation_id: str
    family_id: str
    description: str
    script: Tuple[object, ...] = ()   # ScriptedCall 序列（避免循环 import）


def _family_payload(family_id: str, scale: int) -> Dict[str, Any]:
    """家族 → 确定性载荷（规模/形状随家族语义变化 —— 不只是换标签）。"""
    n = max(3, scale)
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point",
                          "coordinates": [104.0 + (i % 10) * 0.01,
                                          30.5 + (i // 10) * 0.01]},
             "properties": {
                 "name": f"{family_id}-{i}",
                 "value": (i * 7) % 23,
                 "population": 1000 + i,
                 "ts": f"2024-01-{(i % 28) + 1:02d}",
             }}
            for i in range(n)
        ],
    }


#: 情境 → 可执行脚本（(tool, args, expect_error_code, expect_outcome) 序列）。
#: 只包含**派发语义真实对应**的情境 —— 其余情境（样式编辑/取消/降级…）
#: 的运行时语义由其 traceability 套件在各自夹具中锁定（见
#: RUNTIME_SITUATIONS），此处不伪造。
_RUNTIME_EXECUTABLE = (
    "tool-failure",
    "missing-data",
    "no-progress-recovery",
    "multi-turn-followup",
    "context-overflow",
)

_SITUATION_SCRIPTS: Dict[str, Tuple[str, Tuple[Tuple[str, Any, Optional[str], str], ...]]] = {
    "tool-failure": (
        "工具失败 → 结构化错误 → 修复参数成功（失败不占重试位）",
        (
            ("boom", {"p": 1}, "TOOL_ERROR", "error"),
            ("echo", {"v": 1}, None, "ok"),
        ),
    ),
    "missing-data": (
        "引用缺失数据（不存在的 ref）→ 结构化错误",
        (
            ("echo", {"geojson": "ref:missing-data-ref"}, None, "error"),
        ),
    ),
    "no-progress-recovery": (
        "同签名重复失败 → no-progress 原因码（模式检测联动）",
        (
            ("boom", {"p": 7}, "TOOL_ERROR", "error"),
            ("boom", {"p": 7}, "TOOL_ERROR", "error"),
            ("boom", {"p": 7}, "TOOL_ERROR", "error"),
        ),
    ),
    "multi-turn-followup": (
        "多轮依赖：产出 → 消费（参数级数据流）",
        (
            ("make_data", {"name": "rt-data"}, None, "ok"),
            ("echo", {"v": 2, "geojson": {"type": "FeatureCollection",
                                          "features": []}}, None, "ok"),
        ),
    ),
    "context-overflow": (
        "大结果载荷 → ok（LLM 视图按合约视图有界化，不破坏执行）",
        (
            ("big", {"n": 400}, None, "ok"),
        ),
    ),
}


def build_runtime_execution_corpus(
    *,
    scale_small: int = 12,
    scale_large: int = 120,
) -> List[RuntimeExecutionCase]:
    """真实执行层语料：可执行情境 × 语义族 × 数据规模（确定性展开）。

    与 plan-identity 层的分工：本层每条案例都经 ``simulate_agent_loop``
    在真实 registry 上派发断言（错误码/结果形态/no-progress）；语义族
    差异化**载荷规模与形状**（不是换标签）。语言维度不参与 —— 派发语义
    与查询语言无关（不为计数注水）。
    """
    from app.evaluation.replay import ScriptedCall

    families = _select_runtime_families()
    family_ids = [f.family_id for f in families][:8]
    cases: List[RuntimeExecutionCase] = []
    for situation_id, (desc_tpl, steps) in _SITUATION_SCRIPTS.items():
        for family_id in family_ids:
            for scale_tag, scale in (("small", scale_small), ("large", scale_large)):
                script = []
                for tool, args, err, outcome in steps:
                    call_args = dict(args)
                    if ("geojson" in call_args and isinstance(
                            call_args["geojson"], dict)
                            and call_args["geojson"].get("features")
                            and family_id):
                        call_args["geojson"] = _family_payload(family_id, scale)
                    script.append(ScriptedCall(
                        tool, call_args, expect_error_code=err,
                        expect_outcome=outcome))
                cases.append(RuntimeExecutionCase(
                    case_id=f"RTX-{situation_id}-{family_id}-{scale_tag}",
                    situation_id=situation_id,
                    family_id=family_id,
                    description=f"{desc_tpl}（family={family_id}, scale={scale_tag}）",
                    script=tuple(script),
                ))
    return cases
