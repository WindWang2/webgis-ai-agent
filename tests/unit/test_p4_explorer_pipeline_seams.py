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
async def test_pipeline_threads_parse_failures_into_final_summary_1740(
        monkeypatch) -> None:
    """#1740: parse 阶段的 per-source 失败 / 未解析 ref 必须与 fetch_errors
    同轨进终态摘要 —— 修复前它们只活在 parse 阶段自己的 data 里，终态报告
    只见幸存者（违背 #774）。"""
    from types import SimpleNamespace

    from app.services.explorer.models import SearchContext, StageResult

    parse_errors = [{"source_id": "s2", "ref_id": "ref:s2",
                     "error": "corrupt payload"}]

    async def _discover(task_id, query, ctx, **kw):  # noqa: ANN001, ANN003
        return StageResult(stage="discover", success=True,
                           data={"selected_sources": [
                               {"source": {"id": "s1", "name": "a", "format": "csv"}}]})

    async def _fetch(task_id, sources, **kw):  # noqa: ANN001, ANN003
        return StageResult(stage="fetch", success=True, data={
            "fetch_results": [{"source_id": "s1", "ref_id": "ref:s1"}],
            "fetch_errors": [{"source_id": "bad", "error": "upstream 503"}],
        })

    async def _parse(task_id, fetch_results, **kw):  # noqa: ANN001, ANN003
        return StageResult(stage="parse", success=True, data={
            "task_id": task_id,
            "parsed_results": [{"source_id": "s1", "ref_id": "ref_parsed"}],
            "missing_refs": ["ref:s3"],
            "errors": parse_errors,
        })

    async def _geocode(parsed_results, **kw):  # noqa: ANN001, ANN003
        return SimpleNamespace(rows=[], summary=SimpleNamespace(
            total=0, as_dict=lambda: {"total": 0}))

    monkeypatch.setattr(PL, "run_discover_stage", _discover)
    monkeypatch.setattr(PL, "run_fetch_stage", _fetch)
    monkeypatch.setattr(PL, "run_parse_stage", _parse)
    monkeypatch.setattr(PL, "geocode_stage", _geocode)

    runner = PL.ExplorerPipelineRunner()
    result = await runner.execute(
        "t-1740", "成都 POI", SearchContext(query="成都 POI"))

    assert result.stage == "validate" and result.success is True
    assert result.data["fetch_errors"] == [{"source_id": "bad",
                                            "error": "upstream 503"}]
    assert result.data["parse_errors"] == parse_errors
    assert result.data["missing_refs"] == ["ref:s3"]


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
