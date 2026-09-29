"""Lab 统一 runner（E15 D1/D5）：规格 → adapter 编排 → 统一裁决/报告。

资源纪律：顺序执行（无并发 env 竞争；settlement 视觉裁判与模块级替换
都是进程级状态）—— 与 replay bench 同一约束；多进程只发生在 replay
bench 的场景分片入口（``bench.run_suite_multiprocess``）。
"""
from __future__ import annotations

import time
from typing import List, Optional

from app.lib.harness.lab.adapters import (
    EvalAdapter,
    registered_adapters,
)
from app.lib.harness.lab.metrics import (
    DimensionVerdict,
    SpecEval,
    evaluate_expectations,
    merge_contributions,
)
from app.lib.harness.lab.spec import LabScenario

__all__ = ["LabRunner"]


class LabRunner:
    """统一离线评测入口：不复制 evaluator，只编排与裁决投影。"""

    def __init__(self, *, seed: int = 0,
                 adapters: Optional[List[EvalAdapter]] = None) -> None:
        self.seed = seed
        self._adapters = adapters

    def _adapter_chain(self) -> List[EvalAdapter]:
        if self._adapters is not None:
            return list(self._adapters)
        return registered_adapters()

    async def run_spec(self, spec: LabScenario) -> SpecEval:
        started = time.perf_counter()
        ev = SpecEval(spec_id=spec.spec_id, kind=spec.kind)
        contributions: List[DimensionVerdict] = []
        declared = set(spec.expectations_compiled().declared_dimensions())
        for adapter in self._adapter_chain():
            applicable = self._applies(adapter, spec)
            if not applicable:
                continue
            try:
                outcome = await adapter(spec, seed=self.seed)
            except Exception as exc:  # noqa: BLE001 — adapter 崩溃 = 该 spec 红
                ev.detail_failures.append(
                    f"adapter {getattr(adapter, 'name', '?')} crashed: {exc}")
                continue
            ev.adapters_run.append(getattr(adapter, "name", "?"))
            contributions.extend(outcome.contributions)
            ev.observations[getattr(adapter, "name", "?")] = \
                outcome.observations
            if outcome.replay_digest:
                ev.replay_digest = outcome.replay_digest
            ev.detail_failures.extend(outcome.violations)

        # 规格期望面的编排层断言（值来自既有 evaluator 的产出投影；
        # adapter 已裁决的维不重复断言）。
        contributed = {c.dimension for c in contributions
                       if c.dimension in ("goal_completion",
                                          "evidence_completeness",
                                          "user_wins_compliance")}
        contributions.extend(evaluate_expectations(
            spec,
            goal_status=self._last_goal_status(contributions),
            evidence_count=self._evidence_count(contributions),
            user_mutations_superseded=None,
            already_contributed=contributed,
        ))
        ev.dimensions = merge_contributions(contributions, declared)
        ev.observations["wall_clock_ms"] = round(
            (time.perf_counter() - started) * 1000.0, 1)
        # wall-clock 是纯观测维 —— 不影响 ok（as_dict 时经 spec_ok 计算）。
        return ev

    @staticmethod
    def _applies(adapter: EvalAdapter, spec: LabScenario) -> bool:
        name = getattr(adapter, "name", "")
        if name == "replay":
            return spec.kind == "replay"
        if name == "settlement":
            return spec.kind == "settlement"
        if name == "benchmark":
            return spec.kind == "benchmark"
        if name == "cartography":
            return spec.cartography_checks
        if name == "visual_fixture":
            return spec.visual_judge
        # 外部注册的 adapter（如 science oracles）自行声明适用面。
        applies = getattr(adapter, "applies", None)
        if applies is not None:
            return bool(applies(spec))
        return False

    @staticmethod
    def _last_goal_status(contributions: List[DimensionVerdict]) -> Optional[str]:
        for c in contributions:
            if c.dimension == "goal_completion" and c.value:
                return str(c.value)
        return None

    @staticmethod
    def _evidence_count(contributions: List[DimensionVerdict]) -> Optional[int]:
        total = 0
        seen = False
        for c in contributions:
            if c.dimension == "evidence_completeness" \
                    and isinstance(c.value, int):
                total += c.value
                seen = True
        return total if seen else None

    async def run_specs(self, specs: List[LabScenario]) -> List[SpecEval]:
        out: List[SpecEval] = []
        for spec in specs:
            out.append(await self.run_spec(spec))
        return out
