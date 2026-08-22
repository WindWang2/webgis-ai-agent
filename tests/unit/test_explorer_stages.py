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
  # #775: payloads are stored base64 (~1.33x + padding), not hex (2x) — the
  # session-store footprint per source used to double.
  import base64
  raw = b"col1,col2\n1,2\n"
  assert payload["data"] == base64.b64encode(raw).decode("ascii")
  assert payload["codec"] == "base64"
  assert len(payload["data"]) == 4 * ((len(raw) + 2) // 3)  # exact b64 bound < 2x
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
  """A fetch-ref payload in the store shape the parse stage reads (#775: base64)."""
  import base64
  return {
      "data": base64.b64encode(rows_csv).decode("ascii"),
      "codec": "base64",
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
  """A corrupt payload (non-decodable data) skips just that source, others survive.

  Previously the decode of stored["data"] was unguarded and one bad payload
  aborted the whole stage (#775 renamed the codec to base64; the isolation
  contract is unchanged).
  """
  store = {
      "ref-bad": {"data": "not-base64!!", "codec": "base64", "content_type": "text/csv", "encoding": "utf-8"},
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


# ─── #774: fetch failures ride along; empty-source message is distinct ──────


@pytest.mark.asyncio
async def test_fetch_partial_failure_keeps_errors_in_data_774():
  """#774: with 1 of 3 sources failing, the success StageResult must carry the
  failure under fetch_errors — previously it vanished (log-only) and the
  survivors' total_rows masqueraded as the complete result."""
  adapter = FakeFetchAdapter({"a": b"1", "c": b"3"})  # "bad" absent => raises

  result = await run_fetch_stage(
      task_id="t774",
      selected_sources=[
          {"source": _source("bad").model_dump()},
          {"source": _source("a").model_dump()},
          {"source": _source("c").model_dump()},
      ],
      adapter=adapter,
  )

  assert result.success is True
  ids = [r["source_id"] for r in result.data["fetch_results"]]
  assert ids == ["a", "c"]
  assert [e["source_id"] for e in result.data["fetch_errors"]] == ["bad"]
  assert all("error" in e for e in result.data["fetch_errors"])


@pytest.mark.asyncio
async def test_fetch_empty_sources_message_is_distinct_774():
  """#774: discovery finding 0 sources must NOT report 'All source fetches
  failed: []' — an empty candidate list is a distinct (and differently
  debuggable) condition."""
  result = await run_fetch_stage(
      task_id="t774",
      selected_sources=[],
      adapter=FakeFetchAdapter({}),
  )
  assert result.success is False
  assert "no sources" in result.message
  assert "All source fetches failed" not in result.message


@pytest.mark.asyncio
async def test_validate_summary_carries_fetch_errors_774():
  """#774: the validate stage's completion summary includes the fetch-stage
  per-source failures (threaded through by the chain/pipeline callers)."""
  from app.services.explorer.validate_stage import run_validate_stage

  fetch_errors = [{"source_id": "bad", "error": "upstream 503"}]
  result = await run_validate_stage(
      "t774", geocoded_ref_id="ref-1", total_rows=5, fetch_errors=fetch_errors
  )
  assert result.success is True
  assert result.data["fetch_errors"] == fetch_errors


# ─── #775: base64 storage round-trip + legacy hex payloads ───────────────────


@pytest.mark.asyncio
async def test_fetch_parse_base64_roundtrip_775():
  """#775: fetch stores base64 and parse decodes it back byte-exact, with the
  stored string at most 1.4x the payload size (hex was 2x)."""
  import base64

  raw_bytes = "名称,地址\nA,北京市海淀区\nB,上海市浦东新区\n".encode("utf-8")
  stored_payloads = {}

  def store_ref(payload, kind):
      stored_payloads[kind] = payload
      return "ref-775"

  fetch_res = await run_fetch_stage(
      task_id="t775",
      selected_sources=[{"source": _source("s1").model_dump()}],
      adapter=FakeFetchAdapter({"s1": raw_bytes}),
      store_ref=store_ref,
  )
  payload = stored_payloads["fetch"]
  assert payload["codec"] == "base64"
  assert len(payload["data"]) <= 1.4 * len(raw_bytes)

  class _EchoAdapter:
      async def parse(self, raw):
          from app.services.explorer.models import StructuredData, FieldInfo
          text = raw.data.decode("utf-8")
          rows = [
              dict(zip(text.splitlines()[0].split(","), line.split(",")))
              for line in text.splitlines()[1:]
          ]
          return StructuredData(rows=rows, fields=[FieldInfo(name="地址")])

  parse_res = await run_parse_stage(
      "t775",
      fetch_res.data["fetch_results"],
      load_ref=lambda ref_id: stored_payloads["fetch"],
      store_ref=store_ref,
      adapter=_EchoAdapter(),
  )
  assert parse_res.data["parsed_results"][0]["row_count"] == 2


def test_decode_fetch_payload_legacy_hex_still_supported_775():
  """#775: payloads stored before the base64 switch (no codec marker) are hex
  and must still decode — an in-flight chain across a deploy must not lose its
  data."""
  from app.services.explorer.parse_stage import decode_fetch_payload

  legacy = {"data": "e4b8ade69687", "content_type": "text/csv", "encoding": "utf-8"}
  assert decode_fetch_payload(legacy) == "中文".encode("utf-8")
  modern = {"data": "5Lit5paH", "codec": "base64", "content_type": "text/csv", "encoding": "utf-8"}
  assert decode_fetch_payload(modern) == "中文".encode("utf-8")


# ── #776: explorer 产出桥接进 chat session 命名空间 ──────────────────────

class _FakeBridgeStore:
    """get_session_store() 的最小替身：explorer 命名空间预置 geocoded ref，
    记录 store/set_alias 调用供断言。"""

    def __init__(self, payload):
        self._explorer_payload = payload
        self.stored: list = []
        self.aliases: list = []

    async def get(self, session_id, ref_id):
        if session_id.startswith("explorer:") and ref_id == "geocoded-ref-1":
            return self._explorer_payload
        return None

    async def store(self, session_id, data, prefix=""):
        self.stored.append((session_id, prefix, data))
        return "session-ref-42"

    async def set_alias(self, session_id, ref_id, alias):
        self.aliases.append((session_id, ref_id, alias))


@pytest.mark.asyncio
async def test_validate_stage_bridges_geocoded_rows_into_chat_session(monkeypatch):
    """#776: validate 段把 explorer 命名空间的 geocoded 结果存入 chat session
    + 登记 alias —— 会话侧 ref:/前端/agent 从此可消费（此前零消费者）。"""
    from app.services.explorer import validate_stage as vs
    import app.services.session_data_protocol as protocol

    payload = {"rows": [{"addr": "a", "_lat": 1.0, "_lon": 2.0}], "summary": {"total": 1}}
    fake = _FakeBridgeStore(payload)
    monkeypatch.setattr(protocol, "get_session_store", lambda: fake)

    res = await vs.run_validate_stage(
        "exp_sess-1_100",
        geocoded_ref_id="geocoded-ref-1",
        total_rows=1,
        session_id="sess-1",
    )

    assert res.success
    assert res.data["session_ref_id"] == "session-ref-42"
    assert res.data["session_ref_alias"] == "explorer:exp_sess-1_100"
    # 同一 payload 存入 chat session 命名空间
    assert fake.stored == [("sess-1", "explorer_geocoded", payload)]
    assert fake.aliases == [("sess-1", "session-ref-42", "explorer:exp_sess-1_100")]


@pytest.mark.asyncio
async def test_validate_stage_without_session_stays_task_scoped():
    """#776 对照：无 chat session 上下文（匿名任务）时不桥接，行为不变。"""
    from app.services.explorer.validate_stage import run_validate_stage

    res = await run_validate_stage("exp_x_1", geocoded_ref_id="r", total_rows=3)
    assert res.success
    assert "session_ref_id" not in res.data
    assert res.data["geocoded_ref_id"] == "r"


@pytest.mark.asyncio
async def test_validate_stage_bridge_failure_is_not_fatal(monkeypatch):
    """#776: 桥接异常（如 store 故障）不得判探索失败 —— 只影响可消费性。"""
    from app.services.explorer import validate_stage as vs
    import app.services.session_data_protocol as protocol

    class _BrokenStore:
        async def get(self, *a, **kw):
            raise RuntimeError("store down")

    monkeypatch.setattr(protocol, "get_session_store", lambda: _BrokenStore())
    res = await vs.run_validate_stage(
        "exp_s_1", geocoded_ref_id="r", total_rows=1, session_id="s"
    )
    assert res.success
    assert "session_ref_id" not in res.data
