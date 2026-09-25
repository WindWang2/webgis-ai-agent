"""failure trace 最小化契约（ADR-0214 D8，WP7）。"""
from __future__ import annotations

import copy

import pytest

from app.lib.harness.replay.replayer import (
    OfflineReplayer,
    Scenario,
    ScenarioOp,
    TurnSpec,
)
from app.lib.harness.replay.shrink import (
    DEFAULT_MAX_CANDIDATES,
    replayer_oracle,
    shrink_scenario,
)

pytestmark = pytest.mark.cartography


def _op(cid: str, *, is_error: bool = False) -> ScenarioOp:
    return ScenarioOp(
        call_id=cid, tool="spatial_aggregate",
        arguments={"seed": cid},
        result={"geojson_ref": f"ref:geojson:{cid}"},
        is_error=is_error,
    )


def _replay(scenario: Scenario):
    return OfflineReplayer(seed=3).replay_scenario(scenario)


class TestShrink:
    @pytest.mark.asyncio
    async def test_green_scenario_returns_honestly(self):
        """输入本身绿 → 无可最小化，诚实返回原场景（reproduced=False）。"""
        scenario = Scenario(
            scenario_id="shrink-green", category="recorded",
            turns=[TurnSpec(ops=[_op("a"), _op("b")])],
        )
        oracle = replayer_oracle(OfflineReplayer(seed=3))
        result = await shrink_scenario(scenario, oracle)
        assert result.reproduced is False
        assert result.candidates_tried == 0
        assert len(result.scenario.turns[0].ops) == 2

    @pytest.mark.asyncio
    async def test_multi_turn_failure_shrinks_to_minimal_prefix(self):
        """红依赖特定 op —— oracle 无关性：后续 turn / 噪声 op / 无关 fault
        全部裁掉，最小复现保留 poison。"""
        noise = _op("n1")
        poison = _op("p1", is_error=True)
        filler = _op("n2")
        scenario = Scenario(
            scenario_id="shrink-multi", category="recorded",
            turns=[
                TurnSpec(ops=[noise, poison]),
                TurnSpec(ops=[filler]),
                TurnSpec(ops=[filler, noise]),
            ],
            faults=[{"kind": "timeout", "op": "spatial_aggregate"}],
        )

        async def poison_oracle(candidate: Scenario) -> bool:
            """红 = 恰好还含 call_id 'p1' 的 error op（且已缩到 1 turn）。"""
            return any(
                op.is_error and op.call_id == "p1"
                for t in candidate.turns for op in t.ops
            )

        result = await shrink_scenario(scenario, poison_oracle)
        assert result.reproduced is True
        assert len(result.scenario.turns) == 1
        assert result.scenario.turns[0].ops[0].call_id == "p1"
        assert result.scenario.faults == [], "无关 fault 应被裁掉"
        assert result.removed["faults"], "removed 收据记录被裁 fault"
        assert result.removed["turns"], "removed 收据记录被裁 turn"

    @pytest.mark.asyncio
    async def test_replayer_oracle_shrinks_red_expectation(self):
        """集成：goal pin（无 cartography fixture 恒红）驱动 replayer oracle
        —— 裁剪至单 turn 后红仍复现（最小可复现成立）。"""
        scenario = Scenario(
            scenario_id="shrink-replay", category="recorded",
            turns=[
                TurnSpec(
                    ops=[_op("a")],
                    expect={"goal": {"status": "pass"}},
                ),
                TurnSpec(
                    ops=[_op("b")],
                    expect={"goal": {"status": "pass"}},
                ),
            ],
        )
        oracle = replayer_oracle(OfflineReplayer(seed=3))
        result = await shrink_scenario(scenario, oracle)
        assert result.reproduced is True
        assert len(result.scenario.turns) == 1, (
            "goal pin 与 op 无关 → 多余 turn 应被裁掉")

    @pytest.mark.asyncio
    async def test_shrink_is_deterministic(self):
        """同输入 → 同最小复现（无随机；回归可复现的前提）。"""
        def _build() -> Scenario:
            return Scenario(
                scenario_id="shrink-det", category="recorded",
                turns=[
                    TurnSpec(ops=[_op("x1"), _op("bad", is_error=True)]),
                    TurnSpec(ops=[_op("x2")]),
                ],
            )

        async def poison_oracle(candidate: Scenario) -> bool:
            return any(
                op.is_error for t in candidate.turns for op in t.ops
            )

        r1 = await shrink_scenario(_build(), poison_oracle)
        r2 = await shrink_scenario(_build(), poison_oracle)
        assert r1.as_dict() == r2.as_dict()

    @pytest.mark.asyncio
    async def test_budget_bounds_the_search(self):
        """max_candidates 硬上界：搜索有界停机（truncated_by_budget 披露）。"""
        scenario = Scenario(
            scenario_id="shrink-budget", category="recorded",
            turns=[TurnSpec(ops=[_op(str(i)) for i in range(8)])],
        )
        scenario.faults = [{"kind": "timeout"}] * 4

        # oracle 恒红：每个候选都复现 → 搜索以预算为唯一停机条件。
        async def always_red(_scenario):
            return True

        result = await shrink_scenario(
            scenario, always_red, max_rounds=64, max_candidates=3)
        assert result.candidates_tried <= 3
        assert result.truncated_by_budget is True

    @pytest.mark.asyncio
    async def test_input_scenario_not_mutated(self):
        """原场景不被原地修改（深拷贝纪律 —— shrink 是纯消费侧）。"""
        scenario = Scenario(
            scenario_id="shrink-pure", category="recorded",
            turns=[TurnSpec(ops=[_op("a"), _op("bad", is_error=True)])],
        )
        before = copy.deepcopy(scenario)
        oracle = replayer_oracle(OfflineReplayer(seed=3))
        await shrink_scenario(scenario, oracle)
        assert len(scenario.turns[0].ops) == 2
        assert scenario.turns[0].ops[0].call_id == before.turns[0].ops[0].call_id
