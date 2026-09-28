"""统一指标投影（E15 D5）：九个核心指标 + 诚实裁决。

每维裁决 ∈ {pass, fail, not_evaluated}：

- **任何 fail 即 spec fail** —— 评测分数不得掩盖明确的 correctness failure；
- **声明了却没评出**（declared-but-not-evaluated）同样使 spec 不绿
  （假成功防线）；未声明的维 = not_evaluated（诚实缺席，不毒化）；
- 计时 / 字节数 / 重试计数是 tolerant 观测行 —— 永不进 digest。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.lib.harness.lab.spec import Expectation, LabScenario

__all__ = [
    "DIMENSIONS",
    "DimensionVerdict",
    "SpecEval",
    "merge_contributions",
    "evaluate_expectations",
    "spec_ok",
]

#: 核心指标维度（E15 任务书词表；顺序即报告顺序）。
DIMENSIONS = (
    "goal_completion",
    "gis_semantic_correctness",
    "cartographic_compliance",
    "evidence_completeness",
    "recovery_correctness",
    "user_wins_compliance",
    "context_token_cost",     # tolerant 观测（无 pass/fail 语义）
    "tool_retries",           # tolerant 观测
    "wall_clock_ms",          # tolerant 观测
)

#: 纯观测维（不计入裁决；只进报告/ratchet 行）。
_OBSERVATION_ONLY = frozenset({
    "context_token_cost", "tool_retries", "wall_clock_ms"})

PASS = "pass"
FAIL = "fail"
NOT_EVALUATED = "not_evaluated"


@dataclass
class DimensionVerdict:
    dimension: str
    status: str
    value: Any = None
    detail: str = ""
    #: 声明面：True = 规格显式要求该维（not_evaluated 即不绿）。
    declared: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "dimension": self.dimension, "status": self.status,
            "value": self.value, "detail": self.detail,
            "declared": self.declared,
        }


@dataclass
class SpecEval:
    """单规格评测投影（digest 只含确定性事实）。"""

    spec_id: str
    kind: str
    dimensions: Dict[str, DimensionVerdict] = field(default_factory=dict)
    observations: Dict[str, Any] = field(default_factory=dict)
    adapters_run: List[str] = field(default_factory=list)
    replay_digest: str = ""
    detail_failures: List[str] = field(default_factory=list)

    def digest_fields(self) -> Dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "kind": self.kind,
            "dimensions": {
                name: v.status for name, v in sorted(self.dimensions.items())
            },
            "replay_digest": self.replay_digest,
        }

    def as_dict(self) -> Dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "kind": self.kind,
            "ok": spec_ok(self),
            "dimensions": {
                name: v.as_dict()
                for name, v in sorted(self.dimensions.items())
            },
            "observations": self.observations,
            "adapters_run": list(self.adapters_run),
            "replay_digest": self.replay_digest,
            "failures": list(self.detail_failures),
        }


def merge_contributions(
    contributions: List[DimensionVerdict],
    declared: set,
) -> Dict[str, DimensionVerdict]:
    """多 adapter 的同维贡献归并：fail > pass > not_evaluated（fail 优先）。

    观测维（context/retries/wall-clock）取首个数值贡献，不做平均 ——
    平均分不得掩盖任何一方的 correctness failure（E15 红线）。
    """
    merged: Dict[str, DimensionVerdict] = {}
    for dim in DIMENSIONS:
        rows = [c for c in contributions if c.dimension == dim]
        if not rows:
            merged[dim] = DimensionVerdict(
                dimension=dim, status=NOT_EVALUATED,
                declared=dim in declared)
            continue
        if dim in _OBSERVATION_ONLY:
            value = next((r.value for r in rows if r.value is not None), None)
            merged[dim] = DimensionVerdict(
                dimension=dim, status=NOT_EVALUATED, value=value,
                declared=dim in declared)
            continue
        if any(r.status == FAIL for r in rows):
            worst = next(r for r in rows if r.status == FAIL)
            merged[dim] = DimensionVerdict(
                dimension=dim, status=FAIL, detail=worst.detail,
                declared=dim in declared)
        elif any(r.status == PASS for r in rows):
            ok_row = next(r for r in rows if r.status == PASS)
            merged[dim] = DimensionVerdict(
                dimension=dim, status=PASS, value=ok_row.value,
                declared=dim in declared)
        else:
            merged[dim] = DimensionVerdict(
                dimension=dim, status=NOT_EVALUATED,
                declared=dim in declared)
    return merged


def evaluate_expectations(
    spec: LabScenario,
    *,
    goal_status: Optional[str],
    evidence_count: Optional[int],
    user_mutations_superseded: Optional[List[bool]],
    already_contributed: Optional[set] = None,
) -> List[DimensionVerdict]:
    """规格期望面 → 裁决贡献（编排层断言；评测仍委托既有 evaluator 产出）。

    ``already_contributed``：adapter 已产出贡献的维 —— 编排层断言只补位，
    不重复裁决（贡献归并的 fail-优先语义已保证不掩盖）。
    """
    exp: Expectation = spec.expectations_compiled()
    skip = already_contributed or set()
    out: List[DimensionVerdict] = []
    if exp.goal_status and "goal_completion" not in skip:
        ok = goal_status is not None and goal_status == exp.goal_status
        out.append(DimensionVerdict(
            dimension="goal_completion",
            status=PASS if ok else FAIL,
            value=goal_status,
            detail="" if ok else
            f"expected goal status {exp.goal_status!r}, got {goal_status!r}",
            declared=True,
        ))
    if exp.evidence_min_count > 0 and "evidence_completeness" not in skip:
        count = evidence_count or 0
        ok = count >= exp.evidence_min_count
        out.append(DimensionVerdict(
            dimension="evidence_completeness",
            status=PASS if ok else FAIL,
            value=count,
            detail="" if ok else
            f"evidence count {count} < declared min {exp.evidence_min_count}",
            declared=True,
        ))
    if exp.user_wins and "user_wins_compliance" not in skip:
        if user_mutations_superseded is None:
            out.append(DimensionVerdict(
                dimension="user_wins_compliance", status=NOT_EVALUATED,
                declared=True))
        else:
            superseded = [s for s in user_mutations_superseded if s]
            out.append(DimensionVerdict(
                dimension="user_wins_compliance",
                status=PASS if not superseded else FAIL,
                value=len(user_mutations_superseded),
                detail="" if not superseded else
                f"user mutations superseded by system: {len(superseded)}",
                declared=True,
            ))
    return out


def spec_ok(ev: SpecEval) -> bool:
    """spec 终裁：无 fail 且无 declared-not-evaluated（假成功防线）。"""
    for verdict in ev.dimensions.values():
        if verdict.status == FAIL:
            return False
        if verdict.declared and verdict.status == NOT_EVALUATED:
            return False
    return not ev.detail_failures
