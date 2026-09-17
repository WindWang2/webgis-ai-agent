"""SkillPolicy Benchmark Corpus（V2；GOAL 里程碑 6）。

每个案例 = ``(SelectionFacts, 期望 SkillPolicyDecision 子集)`` 的审定行，
经 runner 的 opt-in policy tier 以**生产** ``SkillPolicy.resolve`` 裁决。
期望值为 2026-09-16 对照 faa453a8 的手工审定真值（探针运行 + policy.py
规则推导双确认），冻结后即回归锁：mode/trust/skill 漂移 = 语料 diff。

覆盖全部可达 mode：``execute_guided`` / ``guide`` / ``fallback`` /
``blocked`` / ``none``（``shadow`` 以 shadow_candidate 表达，非独立 mode）；
含 kill-switch（GIS_SKILL_POLICY=0 → none）与决策确定性双跑。

红线：不测 resolver 内部（那是 #1327 单测的职责）；本语料锁的是
**策略裁决面**（同输入同决策、信任边界、kill-switch 语义）。
全部离线、确定、零 LLM。
"""
from __future__ import annotations

from typing import List

from app.evaluation.case import GISBenchmarkCase, PolicyExpectation

#: 高置信 core 情形（审定：conf=0.75 ≥ execute 阈值，geometry 匹配）。
_FACTS_POINT_DISTRICT = {
    "goal_text": "成都小学分布情况",
    "ontology_matches": ["distribution.point_distribution"],
    "geometry_kinds": ["point"],
}


def build_skill_policy_corpus() -> List[GISBenchmarkCase]:
    """SkillPolicy 策略裁决语料（审定冻结表；按 id 排序）。"""
    cases = [
        GISBenchmarkCase(
            id="SP-execute-guided-core", name="高置信 core + 几何匹配 → execute_guided",
            group="benchmark-policy", query="成都小学分布情况",
            description="审定：conf=0.75 core + point 几何匹配 → execute_guided",
            plan_only=True, tags=["skill-policy", "mode-execute-guided"],
            policy_expectation=PolicyExpectation(
                facts=dict(_FACTS_POINT_DISTRICT),
                expected_mode="execute_guided",
                expected_trust_tier="core",
                expected_selected_skill="point_distribution_analysis",
                forbidden_modes=["blocked", "none"],
                check_determinism=True,
            ),
        ),
        GISBenchmarkCase(
            id="SP-fallback-lean-facts", name="事实面最小投影 → fallback",
            group="benchmark-policy", query="成都小学分布情况",
            description="仅 goal_text（无本体/几何提示）：审定 conf=0.25 < medium "
                        "→ fallback，技能仅作候选不强制（低置信红线）",
            plan_only=True, tags=["skill-policy", "mode-fallback"],
            policy_expectation=PolicyExpectation(
                facts={"goal_text": "成都小学分布情况"},
                expected_mode="fallback",
                expected_selected_skill="point_distribution_analysis",
                forbidden_modes=["execute_guided"],
            ),
        ),
        GISBenchmarkCase(
            id="SP-guide-medium-confidence", name="中置信（缺几何）→ guide",
            group="benchmark-policy", query="成都小学分布情况",
            description="审定：conf=0.625（medium）无几何提示 → guide 不强制执行",
            plan_only=True, tags=["skill-policy", "mode-guide"],
            policy_expectation=PolicyExpectation(
                facts={
                    "goal_text": "成都小学分布情况",
                    "ontology_matches": ["distribution.point_distribution"],
                },
                expected_mode="guide",
                expected_trust_tier="core",
                expected_selected_skill="point_distribution_analysis",
                forbidden_modes=["execute_guided"],
            ),
        ),
        GISBenchmarkCase(
            id="SP-fallback-geometry-mismatch", name="几何错配低置信 → fallback",
            group="benchmark-policy", query="做个分级统计图",
            description="点几何请求分级统计图：审定 conf=0.125 < medium → "
                        "fallback（不得以 core 技能强制执行错配程序）",
            plan_only=True, tags=["skill-policy", "mode-fallback", "hard-negative"],
            policy_expectation=PolicyExpectation(
                facts={"goal_text": "做个分级统计图", "geometry_kinds": ["point"]},
                expected_mode="fallback",
                expected_selected_skill="administrative_aggregation",
                forbidden_modes=["execute_guided"],
                check_determinism=True,
            ),
        ),
        GISBenchmarkCase(
            id="SP-none-no-match", name="无关目标 → none",
            group="benchmark-policy", query="完全无关的量子物理公式推导",
            description="审定：NO_MATCH → mode=none（干净回落既有 planner）",
            plan_only=True, tags=["skill-policy", "mode-none"],
            policy_expectation=PolicyExpectation(
                facts={"goal_text": "完全无关的量子物理公式推导"},
                expected_mode="none",
                expected_selected_skill=None,
            ),
        ),
        GISBenchmarkCase(
            id="SP-none-empty-goal", name="空目标 → none（empty_goal）",
            group="benchmark-policy", query="",
            description="审定：EMPTY_GOAL → mode=none（不虚构技能选择）",
            plan_only=True, tags=["skill-policy", "mode-none", "boundary"],
            policy_expectation=PolicyExpectation(
                facts={"goal_text": ""},
                expected_mode="none",
            ),
        ),
        GISBenchmarkCase(
            id="SP-blocked-quarantined", name="隔离集技能 → blocked",
            group="benchmark-policy", query="成都小学分布情况",
            description="审定：quarantine_ids 命中所选技能 → blocked + "
                        "trust_tier=quarantined（fail-closed）",
            plan_only=True, tags=["skill-policy", "mode-blocked", "security"],
            policy_expectation=PolicyExpectation(
                facts=dict(_FACTS_POINT_DISTRICT),
                expected_mode="blocked",
                expected_trust_tier="quarantined",
                expected_selected_skill="point_distribution_analysis",
                quarantine_ids=["point_distribution_analysis"],
                forbidden_modes=["execute_guided", "guide"],
            ),
        ),
        GISBenchmarkCase(
            id="SP-kill-switch-none", name="kill-switch 强制 none",
            group="benchmark-policy", query="成都小学分布情况",
            description="GIS_SKILL_POLICY=0 → mode=none（干净回落；零技能副作用）",
            plan_only=True, tags=["skill-policy", "mode-none", "kill-switch"],
            policy_expectation=PolicyExpectation(
                facts=dict(_FACTS_POINT_DISTRICT),
                disable_policy=True,
                expected_mode="none",
                forbidden_modes=["execute_guided", "guide", "blocked"],
            ),
        ),
        GISBenchmarkCase(
            id="SP-shadow-induced-candidate", name="induced 仅影子候选",
            group="benchmark-policy", query="成都小学分布情况",
            description="审定：induced 技能只出现在 shadow_candidate，绝不进入"
                        " trusted 选择（生产红线：induced 永不控制执行）",
            plan_only=True, tags=["skill-policy", "shadow", "security"],
            policy_expectation=PolicyExpectation(
                facts=dict(_FACTS_POINT_DISTRICT),
                shadow_induced=True,
                expected_selected_skill="point_distribution_analysis",
                expected_shadow_candidate="induced.demo_poi",
                expected_mode="execute_guided",
                check_determinism=True,
            ),
        ),
        GISBenchmarkCase(
            id="SP-en-goal-fallback", name="英文弱匹配 → fallback（双语诚实面）",
            group="benchmark-policy", query="show the distribution of schools in Chengdu",
            description="英文 goal_text 仅命中弱 intent 信号：审定 conf=0.125 < "
                        "medium → fallback（低置信不强制技能；双语输入同红线）",
            plan_only=True, tags=["skill-policy", "bilingual", "mode-fallback"],
            policy_expectation=PolicyExpectation(
                facts={"goal_text": "show the distribution of schools in Chengdu"},
                expected_mode="fallback",
                expected_selected_skill="point_distribution_analysis",
                forbidden_modes=["execute_guided", "blocked"],
            ),
        ),
        GISBenchmarkCase(
            id="SP-determinism-double-resolve", name="双跑决策逐字段一致",
            group="benchmark-policy", query="成都小学分布情况",
            description="同输入同决策（bounded dict 相等）—— 策略确定性红线",
            plan_only=True, tags=["skill-policy", "determinism"],
            policy_expectation=PolicyExpectation(
                facts=dict(_FACTS_POINT_DISTRICT),
                expected_mode="execute_guided",
                check_determinism=True,
            ),
        ),
    ]
    cases.sort(key=lambda c: c.id)
    # 构建期守卫：id 唯一 / 全部声明 policy 契约 / mode 词表覆盖
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids)), f"duplicate policy case ids: {ids}"
    for c in cases:
        assert c.policy_expectation is not None, f"{c.id} missing policy_expectation"
        assert c.plan_only, f"{c.id} policy cases must be plan_only"
    covered_modes = {
        c.policy_expectation.expected_mode
        for c in cases if c.policy_expectation.expected_mode
    }
    assert covered_modes >= {"execute_guided", "guide", "fallback", "none", "blocked"}, (
        f"policy mode coverage hole: {covered_modes}"
    )
    return cases
