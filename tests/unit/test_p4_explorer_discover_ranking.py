"""Explorer — discover 阶段候选评分与 Top-3 截断（P4 补强 E6 前段）。"""
from __future__ import annotations

import pytest

from app.adapters.base import DataSource
from app.adapters.gov.gov_data_adapter import GovDataAdapter  # noqa: F401 — 契约参照
from app.services.explorer.discover_stage import run_discover_stage
from app.services.explorer.models import DataSourceQualityScore


class _Adapter:
    def __init__(self, n: int):
        self._n = n

    async def discover(self, query, ctx):  # noqa: ANN001, ANN003
        return [DataSource(id=f"s{i}", name=f"src{i}", format="csv",
                           url=f"https://x/{i}") for i in range(self._n)]

    async def quick_assess(self, query, source):  # noqa: ANN001, ANN003
        # 分数随 id 递减：s0 最高。
        score = max(0.0, 1.0 - float(source.id[1:]) * 0.1)
        return DataSourceQualityScore(
            temporal_score=score, thematic_score=score, spatial_score=score,
            field_score=score, precision_score=score, overall=score)


@pytest.mark.asyncio
async def test_discover_ranks_candidates_desc() -> None:
    result = await run_discover_stage("t1", "成都 POI", {"query": "成都 POI"},
                                      adapter=_Adapter(3))
    assert result.success is True
    scores = [c["score"]["overall"] for c in result.data["selected_sources"]]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_discover_caps_at_three_candidates() -> None:
    result = await run_discover_stage("t2", "成都 POI", {"query": "成都 POI"},
                                      adapter=_Adapter(7))
    assert len(result.data["selected_sources"]) <= 3


@pytest.mark.asyncio
async def test_discover_empty_world_yields_empty_success() -> None:
    # 零候选是合法世界（下游 fetch 阶段才把它判为显式失败 #774）。
    result = await run_discover_stage("t3", "成都 POI", {"query": "成都 POI"},
                                      adapter=_Adapter(0))
    assert result.success is True
    assert result.data["selected_sources"] == []


@pytest.mark.asyncio
async def test_discover_progress_reports() -> None:
    seen: list[int] = []
    await run_discover_stage("t4", "成都 POI", {"query": "成都 POI"},
                             adapter=_Adapter(1), on_progress=seen.append)
    assert seen[0] == 10 and seen[-1] == 100


@pytest.mark.asyncio
async def test_discover_scores_later_candidates_and_isolates_one_failure() -> None:
    import asyncio

    class Adapter(_Adapter):
        active = 0
        peak = 0

        async def quick_assess(self, query, source):
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                await asyncio.sleep(0)
                if source.id == "s0":
                    raise ValueError("invalid candidate")
                score = int(source.id[1:]) / 10
                return DataSourceQualityScore(
                    temporal_score=score, thematic_score=score, spatial_score=score,
                    field_score=score, precision_score=score, overall=score,
                )
            finally:
                self.active -= 1

    adapter = Adapter(8)
    result = await run_discover_stage("rank-review", "schools", {}, adapter=adapter)
    assert result.success
    assert [item["source"]["id"] for item in result.data["selected_sources"]] == ["s7", "s6", "s5"]
    assert result.data["assessment_failures"] == 1
    assert adapter.peak == 4
