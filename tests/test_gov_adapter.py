"""GovDataAdapter tests"""
import pytest
from app.adapters.gov.gov_data_adapter import GovDataAdapter
from app.services.explorer.models import RawContent


def test_guess_format():
    adapter = GovDataAdapter()
    assert adapter._guess_format("http://example.com/data.csv") == "csv"
    assert adapter._guess_format("http://example.com/data.xlsx") == "xlsx"
    assert adapter._guess_format("http://example.com/data.json") == "json"
    assert adapter._guess_format("http://example.com/data") == "unknown"


def test_detect_encoding_utf8():
    adapter = GovDataAdapter()
    data = "hello,world".encode("utf-8")
    assert adapter._detect_encoding(data) == "utf-8"


def test_detect_encoding_gbk():
    adapter = GovDataAdapter()
    data = "中文".encode("gbk")
    assert adapter._detect_encoding(data) == "gbk"


def test_parse_date():
    adapter = GovDataAdapter()
    from datetime import datetime, timezone
    # Issue #482: _parse_date returns UTC-aware datetimes (offset-less gov
    # publish_time strings are interpreted as UTC).
    assert adapter._parse_date("2024-03-15") == datetime(2024, 3, 15, tzinfo=timezone.utc)
    assert adapter._parse_date("2024-03") == datetime(2024, 3, 1, tzinfo=timezone.utc)
    assert adapter._parse_date("") is None
    assert adapter._parse_date("invalid") is None


@pytest.mark.asyncio
async def test_parse_csv():
    adapter = GovDataAdapter()
    csv_data = "name,address,level\n清华附中,北京市海淀区,高中\n北大附中,北京市海淀区,高中".encode("utf-8")
    raw = RawContent(data=csv_data, content_type="text/csv", encoding="utf-8")

    structured = await adapter.parse(raw)
    assert len(structured.rows) == 2
    assert structured.rows[0]["name"] == "清华附中"
    assert len(structured.fields) == 3
    assert structured.fields[0].name == "name"


@pytest.mark.asyncio
async def test_parse_csv_with_nulls():
    adapter = GovDataAdapter()
    csv_data = "name,address\nA,\nB,addr2".encode("utf-8")
    raw = RawContent(data=csv_data, content_type="text/csv", encoding="utf-8")

    structured = await adapter.parse(raw)
    assert len(structured.rows) == 2
    assert structured.fields[0].nullable_ratio == 0.0
    assert structured.fields[1].nullable_ratio == 0.5


# ─── Issue #482: naive vs aware publish_time must not crash discover ───────

def test_parse_date_returns_utc_aware():
    """_parse_date must produce timezone-AWARE datetimes (UTC).

    Gov platform publish_time strings carry no offset; a naive strptime
    result later hits ``datetime.now(timezone.utc) - published_at`` in
    QualityEngine.calc_temporal_score and raises TypeError, killing the
    whole 5-stage chain after retries. House convention for naive inputs
    (services/temporal/*, services/jobs/*): assume UTC.
    """
    from datetime import timezone
    adapter = GovDataAdapter()
    dt = adapter._parse_date("2024-03-15")
    assert dt is not None
    assert dt.tzinfo is not None, "_parse_date returned a naive datetime"
    assert dt.utcoffset() == timezone.utc.utcoffset(None)
    # %Y-%m still parses to the first of month, now aware.
    dt2 = adapter._parse_date("2024-03")
    assert dt2 is not None and dt2.tzinfo is not None


@pytest.mark.asyncio
async def test_quick_assess_with_naive_publish_time_does_not_crash():
    """quick_assess over a naive published_at must yield a temporal score,
    not TypeError: can't subtract offset-naive and offset-aware datetimes."""
    from datetime import datetime
    from app.adapters.base import DataSource

    adapter = GovDataAdapter()
    source = DataSource(
        id="gov_test_1",
        name="海淀区学校名录",
        description="学校",
        url="http://example.com/schools.csv",
        format="csv",
        published_at=datetime(2024, 3, 15),  # naive — pre-fix shape
    )
    score = await adapter.quick_assess("海淀区学校", source)
    assert 0.0 <= score.temporal_score <= 1.0
    assert 0.0 <= score.overall <= 1.0


@pytest.mark.asyncio
async def test_quick_assess_aware_publish_time_still_works():
    """Aware published_at (post-fix adapter shape) keeps scoring correctly."""
    from datetime import datetime, timedelta, timezone
    from app.adapters.base import DataSource

    adapter = GovDataAdapter()
    source = DataSource(
        id="gov_test_2",
        name="海淀区学校名录",
        url="http://example.com/schools.csv",
        format="csv",
        published_at=datetime.now(timezone.utc) - timedelta(days=30),
    )
    score = await adapter.quick_assess("海淀区学校", source)
    assert 0.0 < score.temporal_score <= 1.0


@pytest.mark.asyncio
async def test_quick_assess_missing_publish_time_uses_default():
    """No publish_time at all: temporal falls back to the 0.5 default, no crash."""
    from app.adapters.base import DataSource

    adapter = GovDataAdapter()
    source = DataSource(
        id="gov_test_3",
        name="海淀区学校名录",
        url="http://example.com/schools.csv",
        format="csv",
        published_at=None,
    )
    score = await adapter.quick_assess("海淀区学校", source)
    assert score.temporal_score == 0.5


@pytest.mark.asyncio
async def test_run_discover_stage_with_publish_time_source_completes():
    """End-to-end through the discover stage: a gov source carrying a parseable
    naive publish_time must not kill the chain (issue #482's exact repro —
    explorer_discover_task retried 2x then FAILURE)."""
    from datetime import datetime
    from app.adapters.base import DataSource
    from app.services.explorer.discover_stage import run_discover_stage
    from app.services.explorer.models import SearchContext

    real_gov = GovDataAdapter()

    class PublishTimeAdapter:
        async def discover(self, query, context):
            return [
                DataSource(
                    id="gov_bj_1",
                    name="海淀区学校名录",
                    description="海淀区学校",
                    url="http://example.com/schools.csv",
                    format="csv",
                    published_at=datetime(2024, 3, 15),  # naive strptime shape
                )
            ]

        async def quick_assess(self, query, source):
            # Route through the REAL gov adapter's scoring path — that is
            # where the naive/aware subtraction crashed.
            return await real_gov.quick_assess(query, source)

    res = await run_discover_stage(
        "task_482", "海淀区学校", SearchContext(query="海淀区学校").model_dump(),
        adapter=PublishTimeAdapter(),
    )
    assert res.success is True
    assert res.stage == "discover"
    assert len(res.data["selected_sources"]) == 1
    assert 0.0 <= res.data["selected_sources"][0]["score"]["temporal_score"] <= 1.0


@pytest.mark.asyncio
async def test_run_discover_stage_empty_gov_results():
    """Empty gov platform response: discover succeeds with no candidates."""
    from app.services.explorer.discover_stage import run_discover_stage
    from app.services.explorer.models import SearchContext

    class EmptyAdapter:
        async def discover(self, query, context):
            return []

        async def quick_assess(self, query, source):  # pragma: no cover
            raise AssertionError("quick_assess must not run on empty results")

    res = await run_discover_stage(
        "task_482b", "anything", SearchContext(query="anything").model_dump(),
        adapter=EmptyAdapter(),
    )
    assert res.success is True
    assert res.data["selected_sources"] == []
