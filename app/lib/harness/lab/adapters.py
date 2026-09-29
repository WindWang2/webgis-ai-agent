"""Adapter 编排层（E15 D1/D8）：把既有 evaluator 接进统一 runner。

**不复制评测逻辑** —— 每个 adapter 只做三件事：编译输入、调用既有
evaluator、把输出投影成统一维裁决贡献。当前内置：

- ``ReplayAdapter``     → ``replay.OfflineReplayer``（T1–T4 + 故障合同）
- ``SettlementAdapter`` → ``lab.settlement``（生产结算/准入 seam）
- ``BenchmarkAdapter``  → ``evaluation.GISBenchmarkRunner``（分层评测）
- ``CartographyAdapter``→ ``evaluate_cartography_semantics``（语义检查）
- ``VisualFixtureAdapter`` → 视觉裁判 env 缝 + fixture 截图（离线）

science oracles 的加载器在 tests 域（app 不静态 import tests），由
CLI/tests 侧经 :func:`register_adapter` 注入 ``ScienceOracleAdapter``。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

from app.lib.harness.lab.metrics import (
    FAIL,
    NOT_EVALUATED,
    PASS,
    DimensionVerdict,
)
from app.lib.harness.lab.spec import LabScenario

__all__ = [
    "AdapterOutcome",
    "EvalAdapter",
    "ReplayAdapter",
    "SettlementAdapter",
    "BenchmarkAdapter",
    "CartographyAdapter",
    "VisualFixtureAdapter",
    "register_adapter",
    "default_adapters",
    "registered_adapters",
]


@dataclass
class AdapterOutcome:
    """单 adapter 对单规格的产出（维贡献 + 观测 + digest 事实）。"""

    adapter: str
    contributions: List[DimensionVerdict] = field(default_factory=list)
    observations: Dict[str, Any] = field(default_factory=dict)
    replay_digest: str = ""
    #: tolerant ratchet 行（计时/字节数等；原样透传给报告）。
    rows: List[Dict[str, Any]] = field(default_factory=list)
    #: fail-closed 断言违规（fault contract 等）→ 直接进 spec failures。
    violations: List[str] = field(default_factory=list)


EvalAdapter = Callable


class ReplayAdapter:
    """replay kind 唯一执行面：委托 ``OfflineReplayer``（含故障编译）。"""

    name = "replay"

    async def __call__(self, spec: LabScenario,
                       *, seed: int = 0) -> AdapterOutcome:
        from app.lib.harness.replay.faults import (
            apply_faults,
            assert_fault_contract,
        )
        from app.lib.harness.replay.replayer import OfflineReplayer

        outcome = AdapterOutcome(adapter=self.name)
        scenario = spec.compile_replay_scenario()
        replayed = apply_faults(scenario) if scenario.faults else scenario
        result = await OfflineReplayer(seed=seed).replay_scenario(replayed)
        outcome.replay_digest = result.replay_digest
        outcome.observations = {
            "levels_run": result.levels_run,
            "not_run": result.not_run,
            "turns": len(result.turns),
            "exact_diff_count": sum(
                len(t.exact_diffs) for t in result.turns),
        }
        # 期望门（goal/evidence/user-wins）——从重放实测投影编排层断言。
        # 故障旅程的设计性降级（expectations.goal_status="fail"）反过来
        # 断言：红必须真实发生 —— 无劣化的"带故障绿"是假成功。
        expected_goal = (spec.expectations_compiled().goal_status
                         or "pass").lower()
        goal_projection = (result.turns[-1].goal_satisfaction
                           if result.turns else {})
        if any(
            isinstance(t.cartography, dict) and t.cartography.get("mapspec")
            for t in replayed.turns
        ):
            # T1 旅程：goal 来自 L5 推导（cartography/visual 证据）。
            actual_goal = str(goal_projection.get("status") or "")
        else:
            # T2-only / 纯编排旅程：goal 由 mutation 期望树承载
            # （result.ok 已覆盖逐 op exact 比对），goal 面不虚构。
            actual_goal = "pass" if result.ok else "fail"
        # 降级面只看语义退化检查的**实际裁决**（evaluated=True ∧ passed=
        # False；"无证据未评估"对 T2-only 旅程是常态，不是故障信号；
        # ToolChoice/StepEfficiency 对 canned 场景结构性为红，同样不算）。
        degradation_checks = ("MapSpecValidity", "CartographicQuality",
                              "CursorResolutionRate")
        core_red = any(
            (c or {}).get("evaluated") is True
            and (c or {}).get("passed") is False
            for t in result.turns
            for name, c in (t.gate_result.get("checks") or {}).items()
            if name in degradation_checks)
        ok_effective = result.ok and actual_goal == "pass" and not core_red
        if expected_goal == "fail":
            goal_ok = (not result.ok) or actual_goal != "pass" or core_red
            goal_detail = (
                "" if goal_ok else
                "fault scenario stayed green — injected fault produced "
                "no honest degradation (fake success)")
        else:
            goal_ok = ok_effective
            goal_detail = (
                "" if goal_ok else
                f"replay red: exact_diffs="
                f"{outcome.observations['exact_diff_count']}, "
                f"not_run={result.not_run}")
        outcome.contributions.append(DimensionVerdict(
            dimension="goal_completion",
            status=PASS if goal_ok else FAIL,
            value=actual_goal or ("fail" if not result.ok else "pass"),
            detail=goal_detail,
        ))
        evidence = sum(t.evidence_count for t in result.turns)
        # 期望阈值由规格声明驱动（S3 review P1-2：未声明时 >0 即"有证据"，
        # 声明了 min 则按 min 裁决 —— 不再被"≥1 即 PASS"遮蔽）。
        evidence_min = max(1, spec.expectations_compiled().evidence_min_count)
        outcome.contributions.append(DimensionVerdict(
            dimension="evidence_completeness",
            status=PASS if evidence >= evidence_min else NOT_EVALUATED,
            value=evidence,
            detail="" if evidence >= evidence_min else
            f"evidence count {evidence} < threshold {evidence_min}",
        ))
        superseded = [bool(m.get("superseded"))
                      for t in result.turns for m in t.mutation_outcomes]
        if spec.expectations_compiled().user_wins:
            outcome.contributions.append(DimensionVerdict(
                dimension="user_wins_compliance",
                status=PASS if not any(superseded) else FAIL,
                value=len(superseded),
                detail="" if not any(superseded) else
                f"{sum(superseded)} user mutations superseded",
            ))
        if spec.replay_faults():
            violation = assert_fault_contract(
                replayed,
                [t.gate_result for t in result.turns],
                [t.goal_satisfaction for t in result.turns],
                ok_effective,
            )
            if violation:
                outcome.violations.append(violation)
            outcome.contributions.append(DimensionVerdict(
                dimension="recovery_correctness",
                status=PASS if not violation else FAIL,
                detail=violation,
            ))
        outcome.rows.extend(result.metrics_rows)
        return outcome


class SettlementAdapter:
    """settlement kind 唯一执行面：委托 ``lab.settlement`` 检查词表。"""

    name = "settlement"

    async def __call__(self, spec: LabScenario,
                       *, seed: int = 0) -> AdapterOutcome:
        from app.lib.harness.lab.settlement import run_settlement_checks

        outcome = AdapterOutcome(adapter=self.name)
        results = await run_settlement_checks(spec)
        outcome.observations = {
            r.check: {"status": r.status, "detail": r.detail,
                      **({"observations": r.observations}
                         if r.observations else {})}
            for r in results
        }
        failed = [r for r in results if r.status == FAIL]
        unevaluated = [r for r in results if r.status == NOT_EVALUATED]
        # 已声明检查未评出 = 规格红（S3 review P2-5：settlement 检查是
        # 规格显式声明的词表，缺席不得静默绿；detail 指明环境缺口）。
        outcome.contributions.append(DimensionVerdict(
            dimension="goal_completion",
            status=PASS if not failed and not unevaluated
            else (FAIL if failed or unevaluated else NOT_EVALUATED),
            value="pass" if not failed and not unevaluated else "fail",
            detail="; ".join(
                [f"{r.check}: {r.detail}" for r in failed]
                + [f"{r.check}: declared check not evaluated — {r.detail}"
                   for r in unevaluated]),
        ))
        outcome.contributions.append(DimensionVerdict(
            dimension="recovery_correctness",
            status=FAIL if failed else (NOT_EVALUATED if unevaluated else PASS),
            value=len(results),
            detail="; ".join(f"{r.check}: {r.detail}" for r in failed),
        ))
        for r in failed:
            outcome.violations.append(f"{r.check}: {r.detail}")
        return outcome


class BenchmarkAdapter:
    """benchmark kind 唯一执行面：委托 ``GISBenchmarkRunner.run_case``。"""

    name = "benchmark"

    async def __call__(self, spec: LabScenario,
                       *, seed: int = 0) -> AdapterOutcome:
        from app.evaluation.runner import GISBenchmarkRunner

        outcome = AdapterOutcome(adapter=self.name)
        case = spec.compile_benchmark_case()
        result = await GISBenchmarkRunner().run_case(case)
        outcome.observations = {
            "status": result.status,
            "metrics": result.metrics,
            "skipped_reason": result.skipped_reason,
        }
        outcome.rows.append({
            "scene_id": spec.spec_id,
            "tool_calls": result.metrics.get("tool_call_count"),
            "retry_count": result.metrics.get("retry_count"),
        })
        if result.status == "skipped":
            outcome.contributions.append(DimensionVerdict(
                dimension="goal_completion", status=NOT_EVALUATED,
                detail=f"skipped: {result.skipped_reason}",
            ))
            return outcome
        actual = "pass" if result.passed else "fail"
        # goal 期望与 ReplayAdapter 同一语义（S3 P0 修复的对称面）：规格
        # 声明 goal_status 时按声明裁决 —— 设计性失败（expect "fail"）在
        # case 通过时同样是红（期待未发生 = 假绿面）。
        expected = (spec.expectations_compiled().goal_status
                    or "pass").lower()
        goal_ok = actual == expected
        outcome.contributions.append(DimensionVerdict(
            dimension="goal_completion",
            status=PASS if goal_ok else FAIL,
            value=actual,
            detail="" if goal_ok else
            f"declared goal_status {expected!r} but case {actual}: "
            + "; ".join(result.failures[:8]),
        ))
        numerical = result.metrics.get("numerical_correct")
        if numerical is not None:
            outcome.contributions.append(DimensionVerdict(
                dimension="gis_semantic_correctness",
                status=PASS if numerical else FAIL,
                value=numerical,
            ))
        retries = result.metrics.get("retry_count")
        if retries is not None:
            outcome.contributions.append(DimensionVerdict(
                dimension="tool_retries", status=NOT_EVALUATED, value=retries))
        if result.failures:
            outcome.violations.extend(result.failures[:8])
        return outcome


class CartographyAdapter:
    """制图语义检查（委托 ``evaluate_cartography_semantics``，零渲染零网络）。"""

    name = "cartography"

    async def __call__(self, spec: LabScenario,
                       *, seed: int = 0) -> AdapterOutcome:
        from app.lib.cartography.semantic_checks import (
            evaluate_cartography_semantics,
        )

        outcome = AdapterOutcome(adapter=self.name)
        if not spec.cartography_checks or not isinstance(
                spec.initial_mapspec, dict):
            return outcome  # 未声明 → 无贡献（该维 not_evaluated）
        report = evaluate_cartography_semantics(spec.initial_mapspec)
        findings = getattr(report, "findings", None) or []
        errors = [f for f in findings
                  if str(getattr(f, "severity", "") or "") == "error"]
        component_state = {
            comp: _component_present(spec.initial_mapspec, comp)
            for comp in spec.expectations_compiled().mapspec_components
        }
        missing = [c for c, present in component_state.items() if not present]
        outcome.observations = {
            "finding_count": len(findings),
            "error_count": len(errors),
            "components": component_state,
        }
        outcome.contributions.append(DimensionVerdict(
            dimension="cartographic_compliance",
            status=FAIL if errors else PASS,
            value=len(errors),
            detail="; ".join(
                f"{getattr(f, 'code', '?')}: "
                f"{str(getattr(f, 'message', ''))[:80]}"
                for f in errors[:4]),
        ))
        if spec.expectations_compiled().mapspec_components:
            outcome.contributions.append(DimensionVerdict(
                dimension="cartographic_compliance",
                status=FAIL if missing else PASS,
                detail="" if not missing else
                f"declared components missing: {missing}",
            ))
        return outcome


class VisualFixtureAdapter:
    """视觉观察 fixture 面（E15：visual observation fixture 接进统一 runner）。

    驱动真实 ``VisualCriticEngine`` + 确定性 ``FakeVLMClient``（golden 样本
    路由）+ 确定性合成截图（``golden_images.render_golden_image``，与
    FakeVLM 样本同资产族、字节级可复现）—— 进程内直驱，无全局 env 状态
    竞争。fail-closed：引擎失效 = not_evaluated 报告，声明了 visual_judge
    的规格缺席即红。
    """

    name = "visual_fixture"
    _GOLDEN_SAMPLE = "overlapping_labels"

    async def __call__(self, spec: LabScenario,
                       *, seed: int = 0) -> AdapterOutcome:
        from app.lib.harness.visual_judge.critic_engine import (
            VisualCriticEngine,
        )
        from app.lib.harness.visual_judge.fake_vlm import FakeVLMClient
        from app.lib.harness.visual_judge.golden_images import (
            render_golden_image,
        )

        outcome = AdapterOutcome(adapter=self.name)
        if not spec.visual_judge:
            return outcome
        from app.lib.harness.replay.determinism import sha256_of

        image = render_golden_image(self._GOLDEN_SAMPLE)
        client = FakeVLMClient(sample=self._GOLDEN_SAMPLE)
        engine = VisualCriticEngine(client)
        report = await engine.evaluate(
            session_id=f"lab-visual-{spec.spec_id}",
            mapspec_fingerprint=sha256_of(spec.initial_mapspec or {}),
            image=image,
        )
        outcome.observations = {
            "visual_status": report.status,
            "overall_score": report.overall_score,
            "critique_count": len(report.critiques),
            "fake_calls": client.call_count,
        }
        # fixture-only 接线语义：golden 样本管线证明"视觉裁判面可达且可
        # 评"，不针对本场景地图内容 —— value 置 None 防止被读成场景得分。
        outcome.contributions.append(DimensionVerdict(
            dimension="cartographic_compliance",
            status=PASS if report.status == "evaluated" else NOT_EVALUATED,
            detail="" if report.status == "evaluated" else
            f"visual judge not_evaluated: {report.reason[:96]}",
        ))
        return outcome


def _component_present(mapspec: Dict[str, Any], component: str) -> bool:
    """组件存在性的保守判定：layer.component / components[].type 双面。"""
    for layer in mapspec.get("layers") or []:
        if not isinstance(layer, dict):
            continue
        if str(layer.get("component") or "") == component:
            return True
        comps = layer.get("components") or []
        if isinstance(comps, list) and any(
                isinstance(c, dict) and str(c.get("type") or "") == component
                for c in comps):
            return True
    for comp in mapspec.get("components") or []:
        if isinstance(comp, dict) and str(comp.get("type") or "") == component:
            return True
    return False



_REGISTRY: Dict[str, EvalAdapter] = {}


def register_adapter(adapter: EvalAdapter) -> None:
    """注册（或覆盖）一个 adapter —— science oracles 经此惰性注入。"""
    _REGISTRY[getattr(adapter, "name", adapter.__class__.__name__)] = adapter


def default_adapters() -> List[EvalAdapter]:
    return [
        ReplayAdapter(),
        SettlementAdapter(),
        BenchmarkAdapter(),
        CartographyAdapter(),
        VisualFixtureAdapter(),
    ]


def registered_adapters() -> List[EvalAdapter]:
    """默认 adapter + 外部注册（注册覆盖同名默认 —— 编排面唯一缝）。"""
    by_name = {getattr(a, "name", a.__class__.__name__): a
               for a in default_adapters()}
    by_name.update(_REGISTRY)
    return list(by_name.values())
