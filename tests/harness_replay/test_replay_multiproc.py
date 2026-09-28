"""E15 多进程分片与单进程可比性测试（资源纪律：worker ≤ 2、切片 ≤ 4 场景）。"""
from __future__ import annotations

import asyncio

from app.lib.harness.replay.bench import (
    merge_shard_reports,
    run_suite_multiprocess,
    run_suite,
    shard_scenarios,
    write_baseline,
)
from app.lib.harness.replay.scenarios import build_corpus


def _strip_timing(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if k != "duration_ms"}


def _slice_corpus(n: int = 4):
    return build_corpus()[:n]


class TestSharding:
    def test_shard_scenarios_contiguous_and_total(self):
        corpus = _slice_corpus(5)
        shards = shard_scenarios(corpus, 2)
        assert len(shards) == 2
        flat = [s.scenario_id for shard in shards for s in shard]
        assert flat == [s.scenario_id for s in corpus]
        # 连续切片：每片内部保持原序。
        for shard in shards:
            ids = [s.scenario_id for s in shard]
            assert ids == sorted(ids, key=flat.index)

    def test_shard_count_capped_by_corpus_size(self):
        corpus = _slice_corpus(2)
        assert len(shard_scenarios(corpus, 8)) == 2


class TestMultiprocessEquivalence:
    def test_multiproc_equals_single_process_report(self):
        """归并报告与单进程逐字段一致（duration_ms 除外）。"""
        corpus = _slice_corpus(4)
        single = asyncio.run(run_suite(list(corpus), seed=7, profile="small"))
        multi = run_suite_multiprocess(list(corpus), seed=7, profile="small",
                                       procs=2)
        assert single["green"] == multi["green"]
        assert single["red"] == multi["red"]
        assert [s["scenario_id"] for s in single["entries"]] == \
            [s["scenario_id"] for s in multi["entries"]]
        assert [_strip_timing(e) for e in single["entries"]] == \
            [_strip_timing(e) for e in multi["entries"]]
        assert single["ratchet_rows"] == multi["ratchet_rows"]
        # 确定性 digest：分片不改变回归信号。
        assert [e["replay_digest"] for e in single["entries"]] == \
            [e["replay_digest"] for e in multi["entries"]]

    def test_merged_baseline_from_shards_matches_single(self, tmp_path):
        corpus = _slice_corpus(3)
        single = asyncio.run(run_suite(list(corpus), seed=3, profile="small"))
        multi = run_suite_multiprocess(list(corpus), seed=3, profile="small",
                                       procs=2)
        base_single = write_baseline(single, str(tmp_path / "s.json"))
        base_multi = write_baseline(multi, str(tmp_path / "m.json"))
        assert base_single["entries"] == base_multi["entries"]
