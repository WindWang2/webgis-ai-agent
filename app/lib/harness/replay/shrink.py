"""failure trace 最小化 / shrinking（ADR-0214 D8，WP7）。

多轮失败场景 → 最小可复现：确定性 delta-debug（分块折半 → 单元素删除），
oracle = 「场景仍红」。裁剪维度按信息密度排序：

1. **turns**：多轮场景保留失败前缀（后续轮常是噪声）；
2. **ops**：turn 内冻结工具步骤（失败往往只依赖少数几步）；
3. **faults**：故障注入条目（定位哪个故障触发红）；
4. **mutations**：T2 变异序列（定位哪个变异破坏指纹）。

有界纪律：``max_rounds`` / ``max_candidates`` 硬上限 —— 绝不无界搜索；
确定性：固定分块策略、无随机 —— 同输入同最小复现。输出 = 最小红场景
+ ``removed`` 收据（被裁掉的元素清单，可审计）。

shrink 是纯消费侧：不改 replayer 语义、不改语料、不做任何语义假设
（oracle 是唯一裁判 —— 它说红才算红）。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.lib.harness.replay.replayer import Scenario

#: 搜索预算（有界纪律；语料场景远小于该界）。
DEFAULT_MAX_ROUNDS = 48
DEFAULT_MAX_CANDIDATES = 256

Oracle = Callable[[Scenario], Awaitable[bool]]
#: oracle 契约：``async oracle(scenario) -> bool``（True = 仍复现目标失败）。


@dataclass
class ShrinkResult:
    """最小化结论：最小红场景 + removed 收据 + 搜索统计。"""

    scenario: Scenario
    reproduced: bool                 # 最终最小场景是否仍红（False = 预算内未收敛）
    removed: Dict[str, List[Any]] = field(default_factory=dict)
    rounds: int = 0
    candidates_tried: int = 0
    truncated_by_budget: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "scenario_id": self.scenario.scenario_id,
            "reproduced": self.reproduced,
            "removed": self.removed,
            "turns": len(self.scenario.turns),
            "ops": sum(len(t.ops) for t in self.scenario.turns),
            "faults": len(self.scenario.faults),
            "mutations": sum(len(t.mutations) for t in self.scenario.turns),
            "rounds": self.rounds,
            "candidates_tried": self.candidates_tried,
            "truncated_by_budget": self.truncated_by_budget,
        }


async def shrink_scenario(
    scenario: Scenario,
    oracle: Oracle,
    *,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> ShrinkResult:
    """确定性 delta-debug：保持红的约束下最大化裁剪。

    每轮对每个维度做「分块折半」删除尝试（块成功删除即重起一轮）；
    块删除停滞时退化为单元素删除。任何候选都必须 oracle 判红才被采纳。
    """
    removed: Dict[str, List[Any]] = {
        "turns": [], "ops": [], "faults": [], "mutations": []}
    result = ShrinkResult(scenario=copy.deepcopy(scenario), reproduced=False,
                          removed=removed)
    if not await _check(oracle, result.scenario):
        return result  # 输入本身不红 —— 无可最小化（诚实返回原场景）

    rounds = 0
    candidates = 0
    budget_exceeded = False
    while rounds < max_rounds and candidates < max_candidates:
        rounds += 1
        progressed = False
        for dimension in ("turns", "ops", "faults", "mutations"):
            chunks = _chunks_of(result.scenario, dimension)
            if not chunks:
                continue
            # ddmin：每个互补块都作为「删除候选」尝试（保留另一块）；
            # 单元素序列 = 单块（删到空也必须是候选 —— 否则最小化不彻底）。
            for chunk in chunks:
                if candidates >= max_candidates or rounds >= max_rounds:
                    budget_exceeded = True
                    break
                candidates += 1
                candidate = _drop(result.scenario, dimension, chunk)
                if candidate is None:
                    continue
                if await _check(oracle, candidate):
                    result.scenario = candidate
                    _record_removed(removed, dimension, chunk)
                    progressed = True
            if budget_exceeded:
                break
        if not progressed:
            break
    result.reproduced = await _check(oracle, result.scenario)
    result.rounds = rounds
    result.candidates_tried = candidates
    result.truncated_by_budget = budget_exceeded
    return result


async def _check(oracle: Oracle, scenario: Scenario) -> bool:
    try:
        return bool(await oracle(scenario))
    except Exception:  # noqa: BLE001 — oracle 异常 = 不复现（宁缺毋红）
        return False


def _sequence_of(scenario: Scenario, dimension: str) -> List[Any]:
    if dimension == "turns":
        return list(range(len(scenario.turns)))
    if dimension == "ops":
        return [
            (ti, oi) for ti, t in enumerate(scenario.turns)
            for oi in range(len(t.ops))
        ]
    if dimension == "faults":
        return list(range(len(scenario.faults)))
    if dimension == "mutations":
        return [
            (ti, mi) for ti, t in enumerate(scenario.turns)
            for mi in range(len(t.mutations))
        ]
    return []


def _chunks_of(scenario: Scenario, dimension: str) -> List[List[Any]]:
    seq = _sequence_of(scenario, dimension)
    if not seq:
        return []
    half = max(1, len(seq) // 2)
    return [seq[:half], seq[half:]] if len(seq) > 1 else [seq]


def _drop(scenario: Scenario, dimension: str,
          indices: List[Any]) -> Optional[Scenario]:
    """删除指定元素 → 新场景副本（原场景不动）。守恒约束：至少留 1 turn。"""
    candidate = copy.deepcopy(scenario)
    drop_set = set(indices)
    if dimension == "turns":
        keep = [t for i, t in enumerate(candidate.turns)
                if i not in drop_set]
        if not keep:
            return None
        candidate.turns = keep
    elif dimension == "ops":
        for ti, turn in enumerate(candidate.turns):
            turn.ops = [op for oi, op in enumerate(turn.ops)
                        if (ti, oi) not in drop_set]
    elif dimension == "faults":
        candidate.faults = [f for i, f in enumerate(candidate.faults)
                            if i not in drop_set]
    elif dimension == "mutations":
        for ti, turn in enumerate(candidate.turns):
            turn.mutations = [m for mi, m in enumerate(turn.mutations)
                              if (ti, mi) not in drop_set]
    else:
        return None
    return candidate


def _record_removed(removed: Dict[str, List[Any]], dimension: str,
                    indices: List[Any]) -> None:
    removed[dimension].extend(
        str(i) for i in indices[:64])


# ── 便捷 oracle 工厂 ─────────────────────────────────────────────────────────


def replayer_oracle(replayer: Any) -> Oracle:
    """OfflineReplayer 的 ok 判定 oracle：场景重放红（not ok）→ True。"""

    async def _oracle(scenario: Scenario) -> bool:
        result = await replayer.replay_scenario(scenario)
        return not result.ok

    return _oracle


__all__ = [
    "ShrinkResult",
    "shrink_scenario",
    "replayer_oracle",
    "DEFAULT_MAX_ROUNDS",
    "DEFAULT_MAX_CANDIDATES",
]
