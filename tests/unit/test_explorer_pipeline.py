"""
Unit tests for pure 5-stage ExplorerPipeline engine.
"""
import pytest
from app.adapters.base import DataSource
from app.services.explorer.discover_stage import run_discover_stage
from app.services.explorer.fetch_stage import run_fetch_stage
from app.services.explorer.models import (
    DataSourceQualityScore,
    RawContent,
    SearchContext,
    StageResult,
)
from app.services.explorer.orchestrator import ExplorerOrchestrator
from app.services.explorer.parse_stage import auto_field_mapping, mapping_confidence, run_parse_stage
from app.services.explorer.pipeline import ExplorerPipeline
from app.services.explorer.validate_stage import run_validate_stage


# REVIEW-P1-7: the two old tests below defaulted to a real GovDataAdapter
# (live HTTP) and example.com (live HTTP). Inject fakes so the suite is
# offline + deterministic, matching the pattern in test_explorer_stages.py
# that #288 added for the new tests.


class _FakeDiscoverAdapter:
    """Returns one canned DataSource and a fixed quality score."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, SearchContext]] = []

    async def discover(self, query: str, context: SearchContext) -> list[DataSource]:
        self.calls.append((query, context))
        return [
            DataSource(
                id="src_offline_1", name="Offline Hospital Source",
                url="http://offline.local/hospitals", format="json",
            )
        ]

    async def quick_assess(self, query: str, source: DataSource) -> DataSourceQualityScore:
        return DataSourceQualityScore(
            temporal_score=0.9, thematic_score=0.9, spatial_score=0.8,
            field_score=0.9, precision_score=0.9, overall=0.88,
        )


class _FakeFetchAdapter:
    """Returns canned RawContent so run_fetch_stage never hits example.com."""

    def __init__(self, payload: bytes = b'{"name": "Central Hospital"}') -> None:
        self._payload = payload
        self.calls: list[str] = []

    async def fetch(self, source: DataSource) -> RawContent:
        self.calls.append(source.id)
        return RawContent(
            data=self._payload, content_type="application/json", encoding="utf-8"
        )


@pytest.mark.asyncio
async def test_discover_stage():
    ctx = SearchContext(query="hospitals", city="Beijing")
    adapter = _FakeDiscoverAdapter()
    res = await run_discover_stage(
        "task_100", "hospitals", ctx.model_dump(), adapter=adapter
    )
    assert isinstance(res, StageResult)
    assert res.success is True
    assert res.stage == "discover"
    assert "selected_sources" in res.data
    # Confirm the fake was actually used (not a real adapter fallback).
    assert len(adapter.calls) == 1


@pytest.mark.asyncio
async def test_fetch_stage():
    selected_sources = [{
        "source": {
            "id": "src_1",
            "name": "Test Source",
            "url": "http://offline.local/data",
            "format": "json",
        }
    }]
    refs = {}

    def mock_store(data: dict, prefix: str) -> str:
        ref_id = f"ref_{prefix}_1"
        refs[ref_id] = data
        return ref_id

    adapter = _FakeFetchAdapter()
    res = await run_fetch_stage(
        "task_101", selected_sources, adapter=adapter, store_ref=mock_store
    )
    assert isinstance(res, StageResult)
    assert res.stage == "fetch"
    assert adapter.calls == ["src_1"]  # offline, not example.com
    # mock_store keys by ref_id, not source id; one ref for one source.
    assert len(refs) == 1 and list(refs.keys())[0].startswith("ref_fetch_")


@pytest.mark.asyncio
async def test_auto_field_mapping():
    class DummyField:
        def __init__(self, name):
            self.name = name

    fields = [DummyField("Hospital Name"), DummyField("Address Detail"), DummyField("Latitude")]
    mapping = auto_field_mapping(fields)
    assert mapping["name"] == "Hospital Name"
    assert mapping["address"] == "Address Detail"
    assert mapping["lat"] == "Latitude"
    assert mapping_confidence(mapping) == 1.0


class MockAdapter:
    async def discover(self, query, ctx):
        from app.adapters.base import DataSource
        return [DataSource(id="src_mock_1", name="Mock Hospital Source", url="http://mock.local", format="json")]

    async def quick_assess(self, query, source):
        from app.services.explorer.models import DataSourceQualityScore
        return DataSourceQualityScore(
            temporal_score=0.9, thematic_score=0.9, spatial_score=0.8, field_score=0.9, precision_score=0.9, overall=0.88
        )

    async def fetch(self, source):
        from app.services.explorer.models import RawContent
        return RawContent(data=b'{"name": "Central Hospital", "address": "123 Main St"}', content_type="application/json", encoding="utf-8")

    async def parse(self, raw):
        from app.services.explorer.models import FieldInfo, StructuredData
        return StructuredData(
            rows=[{"name": "Central Hospital", "address": "123 Main St"}],
            fields=[FieldInfo(name="name", data_type="string"), FieldInfo(name="address", data_type="string")],
        )


@pytest.mark.asyncio
async def test_explorer_pipeline_in_process():
    pipeline = ExplorerPipeline()
    ctx = SearchContext(query="schools", city="Beijing")
    adapter = MockAdapter()
    res = await pipeline.run_in_process("task_pipeline_1", "schools", ctx, adapter=adapter)
    assert isinstance(res, StageResult)
    assert res.success is True
    assert res.stage == "validate"
    assert res.data.get("status") == "completed"


@pytest.mark.asyncio
async def test_orchestrator_in_process_mode():
    orchestrator = ExplorerOrchestrator()
    ctx = SearchContext(query="parks", city="Shanghai")
    task_id = await orchestrator.start_exploration(
        "parks", ctx, session_id="sess_123", mode="in_process"
    )
    assert task_id.startswith("exp_sess_123_")
