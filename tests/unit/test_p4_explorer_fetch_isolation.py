"""Explorer — fetch stage 每源隔离与诚实失败面（P4 补强 E5/#774）。

单源失败不取消兄弟源（gather return_exceptions 语义）、部分成功携带
fetch_errors、全失败与零候选的消息区分。
"""
from __future__ import annotations


import pytest

from app.services.explorer.fetch_stage import run_fetch_stage
from app.services.explorer.models import RawContent


class _FlakyAdapter:
    """第一个源失败、第二个源成功的双面适配器。"""

    def __init__(self):
        self.calls: list[str] = []

    async def fetch(self, source):
        self.calls.append(source.id)
        if source.id == "bad":
            raise RuntimeError("connection reset")
        return RawContent(data=b"raw-bytes", content_type="text/csv",
                          encoding="utf-8")


def _source(sid: str) -> dict:
    return {"source": {"id": sid, "name": sid, "format": "csv", "url": "https://x"}}


@pytest.mark.asyncio
async def test_one_source_failure_does_not_cancel_siblings() -> None:
    adapter = _FlakyAdapter()
    result = await run_fetch_stage(
        "t1", [_source("bad"), _source("good")], adapter=adapter)
    assert adapter.calls == ["bad", "good"]
    assert result.success is True
    ok = [r for r in result.data["fetch_results"] if "ref_id" in r]
    bad = result.data["fetch_errors"]
    assert len(ok) == 1 and len(bad) == 1
    # #774: 部分失败的错误骑在结果里（不再 log-only）。
    assert result.data.get("fetch_errors")


@pytest.mark.asyncio
async def test_all_sources_failed_is_explicit() -> None:
    class _Down:
        async def fetch(self, source):
            raise RuntimeError("down")

    result = await run_fetch_stage("t2", [_source("a"), _source("b")], adapter=_Down())
    assert result.success is False
    assert "All source fetches failed" in result.message


@pytest.mark.asyncio
async def test_no_candidate_sources_message_is_distinct() -> None:
    result = await run_fetch_stage("t3", [], adapter=_FlakyAdapter())
    assert result.success is False
    assert "no candidate sources" in result.message


@pytest.mark.asyncio
async def test_progress_reaches_hundred_on_success() -> None:
    seen: list[int] = []
    await run_fetch_stage("t4", [_source("good")], adapter=_FlakyAdapter(),
                          on_progress=seen.append)
    assert seen[-1] == 100


# ── #1721：畸形 source dict 不得逃逸隔离边界崩掉整阶段 ────────────────


@pytest.mark.asyncio
async def test_malformed_source_dict_lands_in_fetch_errors() -> None:
    """DataSource 校验失败（缺 id/name、非 mapping payload）= 单源错误。

    构造发生在 gather 隔离边界内（fetch_stage.py:45）；归集循环曾**二次**
    构造 DataSource（隔离边界外），任一条畸形 dict 就让整个 fetch 阶段抛
    未捕获 ValidationError 崩溃。修复后错误必须落 fetch_errors，兄弟源
    照常成功。
    """
    result = await run_fetch_stage(
        "t5",
        [{"source": {"format": "csv"}},   # 缺 id/name → ValidationError
         {"source": None},                # 非 mapping → TypeError
         _source("good")],
        adapter=_FlakyAdapter(),
    )
    assert result.success is True
    assert [r["source_id"] for r in result.data["fetch_results"]] == ["good"]
    errs = result.data["fetch_errors"]
    assert len(errs) == 2
    assert all(e["source_id"] == "<invalid source>" for e in errs)
    assert all(e["error"] for e in errs)
    assert "validation" in errs[0]["error"].lower()


@pytest.mark.asyncio
async def test_all_sources_malformed_is_stage_failure_not_crash() -> None:
    result = await run_fetch_stage("t6", [{"source": {}}], adapter=_FlakyAdapter())
    assert result.success is False
    assert "All source fetches failed" in result.message
    assert result.data["fetch_errors"][0]["source_id"] == "<invalid source>"
