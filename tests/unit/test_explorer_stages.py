"""Unit tests for the Explorer discover and fetch stages (ADR-0034).

These two stages were extracted into pure async functions with injectable seams
(`adapter`, `store_ref`, `on_progress`) but shipped without tests. The behaviours
pinned here are the ones a caller depends on and that a refactor could silently
break:

  discover: dict->SearchContext coercion, top-3 truncation, score-descending sort
  fetch:    per-source error isolation, store_ref seam, all-failed => success=False

Both stages default to a real `GovDataAdapter` when no adapter is passed, so every
test injects a fake to keep the suite offline and deterministic.
"""
import pytest

from app.adapters.base import DataSource
from app.services.explorer.discover_stage import run_discover_stage
from app.services.explorer.fetch_stage import run_fetch_stage
from app.services.explorer.models import (
    DataSourceQualityScore,
    RawContent,
    SearchContext,
)


def _source(source_id: str, fmt: str = "csv") -> DataSource:
  return DataSource(id=source_id, name=f"source-{source_id}", format=fmt)


def _score(overall: float) -> DataSourceQualityScore:
  """A quality score whose `overall` is the only dimension under test."""
  return DataSourceQualityScore(
      temporal_score=overall,
      thematic_score=overall,
      spatial_score=overall,
      field_score=overall,
      precision_score=overall,
      overall=overall,
  )


class FakeDiscoverAdapter:
  """Adapter double for the discover stage.

  `scores` maps source id -> overall score so a test can control ranking without
  caring about the other four quality dimensions.
  """

  def __init__(self, sources: list[DataSource], scores: dict[str, float] | None = None):
    self._sources = sources
    self._scores = scores or {}
    self.discover_calls: list[tuple[str, SearchContext]] = []
    self.assessed: list[str] = []

  async def discover(self, query: str, context: SearchContext) -> list[DataSource]:
    self.discover_calls.append((query, context))
    return self._sources

  async def quick_assess(self, query: str, source: DataSource) -> DataSourceQualityScore:
    self.assessed.append(source.id)
    return _score(self._scores.get(source.id, 0.5))


class FakeFetchAdapter:
  """Adapter double for the fetch stage.

  `payloads` maps source id -> bytes to return. A source id absent from the map
  raises, which is how the per-source failure path gets exercised.
  """

  def __init__(self, payloads: dict[str, bytes]):
    self._payloads = payloads
    self.fetched: list[str] = []

  async def fetch(self, source: DataSource) -> RawContent:
    self.fetched.append(source.id)
    if source.id not in self._payloads:
      raise RuntimeError(f"upstream 503 for {source.id}")
    return RawContent(data=self._payloads[source.id], content_type="text/csv")


# ─── discover stage ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_discover_coerces_dict_context_and_defaults_query():
  """A dict context is coerced to SearchContext, with `query` filled from the arg.

  Callers (the Celery adapter) pass a plain dict, so the stage must not require a
  pre-built model, and must not lose the query when the dict omits it.
  """
  adapter = FakeDiscoverAdapter([_source("a")])

  await run_discover_stage(
      task_id="t1",
      query="海淀区学校分布",
      context={"expected_data_type": "poi_list"},
      adapter=adapter,
  )

  _, ctx = adapter.discover_calls[0]
  assert isinstance(ctx, SearchContext)
  assert ctx.query == "海淀区学校分布"
  assert ctx.expected_data_type == "poi_list"


@pytest.mark.asyncio
async def test_discover_does_not_overwrite_explicit_context_query():
  """An explicit query already in the context wins over the positional arg."""
  adapter = FakeDiscoverAdapter([_source("a")])

  await run_discover_stage(
      task_id="t1",
      query="positional",
      context={"query": "explicit"},
      adapter=adapter,
  )

  _, ctx = adapter.discover_calls[0]
  assert ctx.query == "explicit"


@pytest.mark.asyncio
async def test_discover_assesses_at_most_three_sources():
  """Only the top 3 candidates are scored — quick_assess is the expensive call."""
  adapter = FakeDiscoverAdapter([_source(str(i)) for i in range(6)])

  result = await run_discover_stage(
      task_id="t1",
      query="q",
      context={"query": "q"},
      adapter=adapter,
  )

  assert len(adapter.assessed) == 3
  assert len(result.data["selected_sources"]) == 3


@pytest.mark.asyncio
async def test_discover_sorts_selected_sources_by_score_descending():
  """The caller picks `selected_sources[0]`, so best-first ordering is the contract."""
  adapter = FakeDiscoverAdapter(
      [_source("low"), _source("high"), _source("mid")],
      scores={"low": 0.1, "high": 0.9, "mid": 0.5},
  )

  result = await run_discover_stage(
      task_id="t1",
      query="q",
      context={"query": "q"},
      adapter=adapter,
  )

  ordered = [item["source"]["id"] for item in result.data["selected_sources"]]
  assert ordered == ["high", "mid", "low"]


@pytest.mark.asyncio
async def test_discover_reports_progress_and_echoes_task_id():
  adapter = FakeDiscoverAdapter([_source("a")])
  progress: list[int] = []

  result = await run_discover_stage(
      task_id="task-42",
      query="q",
      context={"query": "q"},
      adapter=adapter,
      on_progress=progress.append,
  )

  assert progress == [10, 100]
  assert result.success is True
  assert result.stage == "discover"
  assert result.data["task_id"] == "task-42"


# ─── fetch stage ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_stores_payload_through_store_ref_seam():
  """Payloads go through the injected store_ref; the stage returns only its id.

  Raw bytes must not ride along in the stage result — that is what the ref seam
  exists to avoid (Celery meta would otherwise carry whole files).
  """
  adapter = FakeFetchAdapter({"a": b"col1,col2\n1,2\n"})
  stored: list[tuple[dict, str]] = []

  def store_ref(payload: dict, kind: str) -> str:
    stored.append((payload, kind))
    return "ref-123"

  result = await run_fetch_stage(
      task_id="t1",
      selected_sources=[{"source": _source("a").model_dump()}],
      adapter=adapter,
      store_ref=store_ref,
  )

  assert result.success is True
  entry = result.data["fetch_results"][0]
  assert entry == {
      "source_id": "a",
      "ref_id": "ref-123",
      "size_bytes": len(b"col1,col2\n1,2\n"),
      "format": "csv",
  }

  payload, kind = stored[0]
  assert kind == "fetch"
  assert payload["data"] == b"col1,col2\n1,2\n".hex()
  assert payload["content_type"] == "text/csv"


@pytest.mark.asyncio
async def test_fetch_falls_back_to_synthetic_ref_without_store_ref():
  """With no store_ref injected the stage still succeeds, using a derived id."""
  adapter = FakeFetchAdapter({"a": b"x"})

  result = await run_fetch_stage(
      task_id="t1",
      selected_sources=[{"source": _source("a").model_dump()}],
      adapter=adapter,
  )

  assert result.data["fetch_results"][0]["ref_id"] == "ref_fetch_a"


@pytest.mark.asyncio
async def test_fetch_isolates_one_failing_source_and_keeps_the_rest():
  """One bad source must not sink the batch — partial success is still success."""
  adapter = FakeFetchAdapter({"good": b"data"})  # "bad" is absent => raises

  result = await run_fetch_stage(
      task_id="t1",
      selected_sources=[
          {"source": _source("bad").model_dump()},
          {"source": _source("good").model_dump()},
      ],
      adapter=adapter,
  )

  assert result.success is True
  ids = [r["source_id"] for r in result.data["fetch_results"]]
  # Only successful fetches are forwarded; the failed one is dropped from results.
  assert ids == ["good"]
  assert adapter.fetched == ["bad", "good"]


@pytest.mark.asyncio
async def test_fetch_fails_when_every_source_fails():
  """All-failed is a stage failure, and the errors are kept for the caller."""
  adapter = FakeFetchAdapter({})  # every fetch raises

  result = await run_fetch_stage(
      task_id="t1",
      selected_sources=[
          {"source": _source("a").model_dump()},
          {"source": _source("b").model_dump()},
      ],
      adapter=adapter,
  )

  assert result.success is False
  assert result.stage == "fetch"
  assert "All source fetches failed" in result.message
  errored = [e["source_id"] for e in result.data["errors"]]
  assert errored == ["a", "b"]
  assert all("error" in e for e in result.data["errors"])


@pytest.mark.asyncio
async def test_fetch_skips_final_progress_when_all_sources_fail():
  """Progress must not report 100% for a stage that failed."""
  adapter = FakeFetchAdapter({})
  progress: list[int] = []

  result = await run_fetch_stage(
      task_id="t1",
      selected_sources=[{"source": _source("a").model_dump()}],
      adapter=adapter,
      on_progress=progress.append,
  )

  assert result.success is False
  assert progress == [10]


@pytest.mark.asyncio
async def test_fetch_reports_progress_on_success():
  adapter = FakeFetchAdapter({"a": b"x"})
  progress: list[int] = []

  await run_fetch_stage(
      task_id="t1",
      selected_sources=[{"source": _source("a").model_dump()}],
      adapter=adapter,
      on_progress=progress.append,
  )

  assert progress == [10, 100]


# ─── Fetch concurrency (review §3 item 3c) ────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_runs_sources_concurrently():
  """Sources are fetched concurrently, not serially.

  Regression for the serial-await defect: the loop previously did one
  ``await data_adapter.fetch(source)`` at a time, so N sources × 30s timeout
  blew the Celery soft_time_limit=55. Now uses asyncio.gather. This test
  proves concurrency by having two fetches that each block on an asyncio.Event
  until the other has started - impossible to satisfy serially.
  """
  import asyncio as _asyncio

  a_started = _asyncio.Event()
  b_started = _asyncio.Event()

  class ConcurrencyProbeAdapter:
    """Adapter whose fetch gates on the sibling starting."""

    async def fetch(self, source):
      if source.id == "a":
        a_started.set()
        # Wait until B has started (proves they overlap).
        await _asyncio.wait_for(b_started.wait(), timeout=2.0)
        return RawContent(data=b"a", content_type="text/csv")
      else:
        b_started.set()
        await _asyncio.wait_for(a_started.wait(), timeout=2.0)
        return RawContent(data=b"b", content_type="text/csv")

  adapter = ConcurrencyProbeAdapter()
  result = await run_fetch_stage(
      task_id="t1",
      selected_sources=[
          {"source": _source("a").model_dump()},
          {"source": _source("b").model_dump()},
      ],
      adapter=adapter,
  )

  # If fetches were serial, A would wait for B (which never starts) -> timeout
  # -> the stage would fail or error. Both succeeded -> they ran concurrently.
  assert result.success is True
  assert len(result.data["fetch_results"]) == 2
#
# The parse stage previously swallowed a missing fetch ref as a `continue` and
# still returned success=True — so a cross-worker handoff break (the ref stored
# in worker A's MemorySessionStore invisible to worker B) produced an empty
# parsed_results that silently sailed through to geocode and validate. These
# tests pin the fail-fast + per-source isolation contract.

from app.services.explorer.parse_stage import run_parse_stage
from app.services.explorer.models import FieldInfo, StructuredData


class FakeParseAdapter:
  """Adapter double for the parse stage.

  `parsed` maps source_id -> the rows/fields to return. Absent => raise, which
  exercises the per-source parse-error isolation path.
  """

  def __init__(self, parsed: dict[str, list[dict]] | None = None):
    self._parsed = parsed or {}
    self.parsed_ids: list[str] = []

  async def parse(self, raw) -> StructuredData:
    # Recover the source id from the hex payload is awkward; tests instead key
    # off call order. For determinism we just return the next queued rows.
    self.parsed_ids.append(len(self.parsed_ids))
    rows = self._rows_for(self.parsed_ids[-1] - 1)
    return StructuredData(rows=rows, fields=[FieldInfo(name="address")])

  def _rows_for(self, idx: int) -> list[dict]:
    items = list(self._parsed.values())
    if idx >= len(items):
      raise RuntimeError(f"parse boom for source #{idx}")
    return items[idx]


def _stored_fetch_payload(rows_csv: bytes = b"address\nMain St\n") -> dict:
  """A fetch-ref payload in the store shape the parse stage reads."""
  return {
      "data": rows_csv.hex(),
      "content_type": "text/csv",
      "encoding": "utf-8",
  }


@pytest.mark.asyncio
async def test_parse_fails_when_all_refs_missing():
  """Every fetch ref unresolved => success=False (fail-fast, not silent empty).

  Regression for the handoff-break defect: without the gate, parse returns
  success=True with empty parsed_results and the pipeline reports a successful
  exploration that produced nothing.
  """
  result = await run_parse_stage(
      task_id="t1",
      fetch_results=[{"source_id": "a", "ref_id": "ref-a"}, {"source_id": "b", "ref_id": "ref-b"}],
      load_ref=lambda ref_id: None,  # all refs missing (cross-worker break)
      adapter=FakeParseAdapter(),
  )
  assert result.success is False
  assert result.data["parsed_results"] == []
  assert set(result.data["missing_refs"]) == {"ref-a", "ref-b"}
  assert "unresolved" in result.message


@pytest.mark.asyncio
async def test_parse_partial_missing_keeps_success():
  """Some refs resolve, some missing => success=True with the misses recorded.

  One source's ref expiring must not sink the whole pipeline; the resolved
  source's parsed result still flows to geocode.
  """
  store = {"ref-a": _stored_fetch_payload()}

  def store_ref(payload, kind):
    return f"ref-parsed-{kind}"

  result = await run_parse_stage(
      task_id="t1",
      fetch_results=[{"source_id": "a", "ref_id": "ref-a"}, {"source_id": "b", "ref_id": "ref-b"}],
      load_ref=lambda ref_id: store.get(ref_id),
      store_ref=store_ref,
      adapter=FakeParseAdapter({"a": [{"address": "Main St"}]}),
  )
  assert result.success is True
  assert len(result.data["parsed_results"]) == 1
  assert result.data["parsed_results"][0]["source_id"] == "a"
  assert result.data["missing_refs"] == ["ref-b"]


@pytest.mark.asyncio
async def test_parse_isolates_bad_hex_payload():
  """A corrupt payload (non-hex data) skips just that source, others survive.

  Previously bytes.fromhex(stored["data"]) was unguarded and one bad payload
  aborted the whole stage with a ValueError.
  """
  store = {
      "ref-bad": {"data": "not-hex!!", "content_type": "text/csv", "encoding": "utf-8"},
      "ref-ok": _stored_fetch_payload(),
  }

  def store_ref(payload, kind):
    return f"ref-parsed-{kind}"

  result = await run_parse_stage(
      task_id="t1",
      fetch_results=[{"source_id": "bad", "ref_id": "ref-bad"}, {"source_id": "ok", "ref_id": "ref-ok"}],
      load_ref=lambda ref_id: store.get(ref_id),
      store_ref=store_ref,
      adapter=FakeParseAdapter({"ok": [{"address": "Main St"}]}),
  )
  assert result.success is True
  assert len(result.data["parsed_results"]) == 1
  assert result.data["parsed_results"][0]["source_id"] == "ok"
  # The bad source is recorded as a per-source error, not a missing ref.
  assert result.data["errors"]
  assert result.data["errors"][0]["source_id"] == "bad"


@pytest.mark.asyncio
async def test_parse_empty_input_succeeds():
  """No fetch results at all => success=True with empty output (not a failure).

  An empty input is distinct from all-refs-missing: there was nothing to parse,
  so it's not a handoff failure.
  """
  result = await run_parse_stage(
      task_id="t1",
      fetch_results=[],
      load_ref=lambda ref_id: None,
      adapter=FakeParseAdapter(),
  )
  assert result.success is True
  assert result.data["parsed_results"] == []
