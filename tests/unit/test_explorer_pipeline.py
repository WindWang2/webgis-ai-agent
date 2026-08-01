"""
Unit tests for pure 5-stage ExplorerPipeline engine.
"""
import pytest
from app.services.explorer.discover_stage import run_discover_stage
from app.services.explorer.fetch_stage import run_fetch_stage
from app.services.explorer.models import SearchContext, StageResult
from app.services.explorer.orchestrator import ExplorerOrchestrator
from app.services.explorer.parse_stage import auto_field_mapping, mapping_confidence, run_parse_stage
from app.services.explorer.pipeline import ExplorerPipeline
from app.services.explorer.validate_stage import run_validate_stage


@pytest.mark.asyncio
async def test_discover_stage():
    ctx = SearchContext(query="hospitals", city="Beijing")
    res = await run_discover_stage("task_100", "hospitals", ctx.model_dump())
    assert isinstance(res, StageResult)
    assert res.success is True
    assert res.stage == "discover"
    assert "selected_sources" in res.data


@pytest.mark.asyncio
async def test_fetch_stage():
    selected_sources = [{
        "source": {
            "id": "src_1",
            "name": "Test Source",
            "url": "http://example.com/data",
            "format": "csv",
        }
    }]
    refs = {}

    def mock_store(data: dict, prefix: str) -> str:
        ref_id = f"ref_{prefix}_1"
        refs[ref_id] = data
        return ref_id

    res = await run_fetch_stage("task_101", selected_sources, store_ref=mock_store)
    assert isinstance(res, StageResult)
    assert res.stage == "fetch"


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
