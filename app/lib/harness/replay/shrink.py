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

    每维度从粗块（len/2）开始：删块成功即采纳并以新序列重启；一整轮
    全部块失败 → 粒度减半（…→ 单元素），直到单元素删除也失败
    （review P1-2：无粒度退化阶段的「ddmin」会停在非最小复现）。
    removed 收据记录**元素身份**（call_id / op 名），不是易失位索引。
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
            adopted = await _shrink_dimension(
                result, dimension, oracle,
                rounds=rounds, max_rounds=max_rounds,
                max_candidates=max_candidates,
                state={"candidates": candidates, "exceeded": False},
                removed=removed,
            )
            candidates = adopted["candidates"]
            if adopted["exceeded"]:
                budget_exceeded = True
                break
            if adopted["progressed"]:
                progressed = True
        if budget_exceeded or not progressed:
            break
    result.reproduced = await _check(oracle, result.scenario)
    result.rounds = rounds
    result.candidates_tried = candidates
    result.truncated_by_budget = budget_exceeded
    return result


async def _shrink_dimension(
    result: "ShrinkResult",
    dimension: str,
    oracle: Oracle,
    *,
    rounds: int,
    max_rounds: int,
    max_candidates: int,
    state: Dict[str, Any],
    removed: Dict[str, List[Any]],
) -> Dict[str, Any]:
    """单维度 ddmin：粗块 → 粒度减半 → 单元素；采纳即以新序列重开。"""
    while True:
        if state["candidates"] >= max_candidates or rounds >= max_rounds:
            state["exceeded"] = True
            return {"progressed": False,
                    "candidates": state["candidates"], "exceeded": True}
        scenario = result.scenario
        seq = _sequence_of(scenario, dimension)
        if not seq:
            return {"progressed": False,
                    "candidates": state["candidates"], "exceeded": False}
        chunk_size = max(1, len(seq) // 2)
        progressed_any = False
        while chunk_size >= 1:
            adopted_this_pass = False
            for start in range(0, len(seq), chunk_size):
                chunk = seq[start:start + chunk_size]
                if state["candidates"] >= max_candidates:
                    state["exceeded"] = True
                    return {"progressed": progressed_any,
                            "candidates": state["candidates"],
                            "exceeded": True}
                state["candidates"] += 1
                identities = [
                    _identity_of(scenario, dimension, index)
                    for index in chunk
                ]
                candidate = _drop(scenario, dimension, chunk)
                if candidate is None:
                    continue
                if await _check(oracle, candidate):
                    result.scenario = scenario = candidate
                    removed[dimension].extend(
                        str(i) for i in identities[:64])
                    progressed_any = True
                    adopted_this_pass = True
                    break  # 序列已变 → 以新序列重开当前粒度
            if adopted_this_pass:
                break
            if chunk_size == 1:
                break
            chunk_size = max(1, chunk_size // 2)
        return {"progressed": progressed_any,
                "candidates": state["candidates"], "exceeded": False}


def _identity_of(scenario: Scenario, dimension: str, index: Any) -> str:
    """被删元素的稳定身份（收据可审计；位索引会随裁剪漂移）。"""
    if dimension == "turns":
        ti = int(index)
        turn = scenario.turns[ti] if 0 <= ti < len(scenario.turns) else None
        return turn.ops[0].call_id if (turn and turn.ops) else f"turn[{ti}]"
    if dimension == "ops":
        ti, oi = index
        try:
            return str(scenario.turns[ti].ops[oi].call_id)
        except (IndexError, TypeError):
            return f"op[{index}]"
    if dimension == "faults":
        try:
            fault = scenario.faults[int(index)] or {}
            return str(fault.get("kind") or fault.get("op")
                       or f"fault[{index}]")
        except (IndexError, TypeError):
            return f"fault[{index}]"
    if dimension == "mutations":
        ti, mi = index
        try:
            return str(scenario.turns[ti].mutations[mi].get("op")
                       or f"mut[{index}]")
        except (IndexError, TypeError, AttributeError):
            return f"mut[{index}]"
    return str(index)


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


async def _check(oracle: Oracle, scenario: Scenario) -> bool:
    try:
        return bool(await oracle(scenario))
    except Exception:  # noqa: BLE001 — oracle 异常 = 不复现（宁缺毋红）
        return False


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
