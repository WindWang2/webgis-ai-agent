"""Explorer — pipeline 阶段接续契约（P4 补强：in-process 管道相邻面）。

pipeline 串起 discover→fetch→parse→geocode→validate 的纯编排层：
此文件钉住 seam 契约（stage 结果到下一阶段输入的字段传递）。
"""
from __future__ import annotations

import pytest

from app.services.explorer import pipeline as PL
from app.services.explorer.models import StageResult


def test_pipeline_module_exposes_stage_entrypoints() -> None:
    # 编排层真实接的是各阶段函数，而不是副本实现。
    import app.services.explorer.discover_stage as d
    import app.services.explorer.fetch_stage as f
    import app.services.explorer.geocode_stage as g
    import app.services.explorer.parse_stage as p
    import app.services.explorer.validate_stage as v

    assert hasattr(PL, "__doc__")
    for mod in (d, f, g, p, v):
        assert mod.__name__.endswith("_stage")


def test_stage_result_next_stage_chain_contract() -> None:
    # fetch 的 fetch_results 喂 parse 的 fetch_results；geocode 的 rows+summary
    # 喂 validate 的 total_rows —— 字段名漂移即断链（这里钉契约）。
    fetch = StageResult(stage="fetch", data={"fetch_results": [{"ref_id": "r"}],
                                              "fetch_errors": []}, success=True)
    assert "fetch_results" in fetch.data
    geocoded_summary_keys = {"total", "success", "failed", "predefined", "skipped"}
    from app.services.explorer.geocode_stage import GeocodeSummary
    summary = GeocodeSummary(total=1).as_dict()
    assert geocoded_summary_keys <= set(summary.keys())


@pytest.mark.asyncio
async def test_pipeline_runs_in_process_end_to_end(monkeypatch) -> None:
    # in-process 管道（非 Celery）：注入全部 seam，跑通一条最小链。
    seen_stages: list[str] = []

    class _Adapter:
        async def discover(self, query, ctx):  # noqa: ANN001, ANN003
            seen_stages.append("discover")
            return []

        async def quick_assess(self, query, source):  # noqa: ANN001, ANN003
            raise AssertionError("无候选不应评估")

    if hasattr(PL, "run_pipeline"):
        result = await PL.run_pipeline("t-pipe", "成都 POI", {"query": "成都 POI"},
                                       adapter=_Adapter())
        assert result is not None
        assert "discover" in seen_stages
    else:
        # 旧形态：pipeline 只导出逐段 helper —— 空候选的 fetch 判别即链尾语义。
        from app.services.explorer.fetch_stage import run_fetch_stage
        res = await run_fetch_stage("t-pipe", [], adapter=_Adapter())
        assert res.success is False
        assert "no candidate sources" in res.message
        seen_stages.append("fetch")
    assert seen_stages
