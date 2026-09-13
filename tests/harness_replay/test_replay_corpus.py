"""场景语料契约测试（B4/B5）：规模下限、类别覆盖、全量离线重放绿。"""
from __future__ import annotations

import pytest

from app.lib.harness.replay import scenarios as scenarios_module
from app.lib.harness.replay.replayer import OfflineReplayer
from app.lib.harness.replay.scenarios import (
    build_corpus,
    corpus_stats,
    dump_corpus,
)

pytestmark = pytest.mark.cartography


@pytest.fixture(scope="module")
def corpus():
    return build_corpus()


class TestCorpusShape:
    def test_scale_floors(self, corpus):
        stats = corpus_stats(corpus)
        assert stats["total"] >= 120
        assert stats["core_single_turn"] >= 100
        assert stats["multi_turn"] >= 30
        low, high = stats["multi_turn_turn_range"]
        assert 3 <= low and high <= 8

    def test_category_coverage(self, corpus):
        categories = {s.category for s in corpus}
        required = {
            "point_distribution", "district_comparison", "heatmap",
            "choropleth", "raster_terrain", "remote_sensing_classification",
            "land_use_change", "accessibility_network",
            "multi_source_fallback", "missing_crs", "dirty_data",
            "cvd_print", "presentation", "user_followup", "user_pin_hide",
            "retry_reconnect", "data_version_drift",
        }
        assert required <= categories

    def test_unique_ids_and_versioned(self, corpus):
        ids = [s.scenario_id for s in corpus]
        assert len(ids) == len(set(ids))
        assert all(s.schema_version == 1 for s in corpus)

    def test_dump_and_reload_roundtrip(self, corpus, tmp_path):
        files = dump_corpus(tmp_path, indent=1)
        assert (tmp_path / "index.csv").exists()
        assert len(files) == len(corpus)
        reloaded = [
            scenarios_module.Scenario.from_dict(
                __import__("json").loads(p.read_text(encoding="utf-8")))
            for p in sorted(tmp_path.glob("*.json"))
        ]
        assert len(reloaded) == len(corpus)
        by_id = {s.scenario_id: s for s in corpus}
        for scenario in reloaded:
            original = by_id[scenario.scenario_id]
            assert len(scenario.turns) == len(original.turns)
            assert scenario.turns[0].expect == original.turns[0].expect


class TestCorpusReplay:
    @pytest.mark.asyncio
    async def test_all_scenarios_green(self, corpus):
        """全语料离线重放：每条场景的语义期望必须全绿（确定性管线）。"""
        replayer = OfflineReplayer(seed=1234)
        failures = []
        for scenario in corpus:
            result = await replayer.replay_scenario(scenario)
            if not result.ok:
                diffs = [
                    f"{t.turn_index}:{d['path']} exp={d['expected']!r} "
                    f"act={d['actual']!r}"
                    for t in result.turns for d in t.exact_diffs
                ]
                failures.append(f"{scenario.scenario_id}: {diffs[:3]}")
        assert not failures, "\n".join(failures[:20])

    @pytest.mark.asyncio
    async def test_corpus_replay_deterministic(self, corpus):
        sample = [s for s in corpus if s.scenario_id.endswith("01")][:12]
        replayer = OfflineReplayer(seed=99)
        first = [await replayer.replay_scenario(s) for s in sample]
        second = [await replayer.replay_scenario(s) for s in sample]
        assert [r.replay_digest for r in first] == \
            [r.replay_digest for r in second]

    @pytest.mark.asyncio
    async def test_failure_drift_turns_scenario_red(self, corpus):
        """故意劣化：任一 green 场景的期望被破坏 → 语料门必须红。"""
        target = next(
            s for s in corpus if s.category == "point_distribution")
        import copy

        broken = copy.deepcopy(target)
        broken.turns[0].expect["gate"]["overall_passed"] = True  # 谎报绿
        result = await OfflineReplayer().replay_scenario(broken)
        # point_distribution 绿场景 overall 本就 True → 改 MSV 期望制造漂移
        if not result.turns[0].exact_diffs:
            broken.turns[0].expect["gate"]["checks"]["MapSpecValidity"] = {
                "evaluated": True, "passed": False}
            result = await OfflineReplayer().replay_scenario(broken)
        assert result.ok is False
        assert result.turns[0].exact_diffs
