"""Data Catalog V3 —— 联邦目录测试（§十七/§十八）。"""
import pytest

from app.lib.data import vocabulary as vocab
from app.services.artifact_registry import register_artifact
from app.services.data_catalog.catalog import (
    CatalogEntry,
    CatalogFilter,
    DataCatalog,
    _entry_from_fabric_descriptor,
    get_data_catalog,
    reset_data_catalog,
)


async def _seed(session_id: str):
    await register_artifact(
        session_id, artifact_id="ref:poi", producer_tool="query_osm_poi",
        artifact_type="poi_feature_set",
        descriptor={"bbox": [116.0, 39.0, 117.0, 40.0], "feature_count": 120,
                    "crs": "EPSG:4326"},
        metadata={"field_schema": {"name": {"type": "string"}, "rating": {"type": "number"}},
                  "logical_role": "observation", "tags": ["osm", "poi"],
                  "display_name": "北京POI"},
        inputs=[],
    )
    await register_artifact(
        session_id, artifact_id="ref:kde", producer_tool="kde",
        artifact_type="density_surface", inputs=["ref:poi"],
        descriptor={"bbox": [116.0, 39.0, 117.0, 40.0], "feature_count": 500},
        metadata={},
    )


@pytest.fixture(autouse=True)
def _reset():
    reset_data_catalog()
    yield
    reset_data_catalog()


class TestSessionScope:
    async def test_list_all(self):
        await _seed("cat-all")
        result = await get_data_catalog().search(session_id="cat-all")
        ids = {e.entry_id for e in result.entries}
        assert {"ref:poi", "ref:kde"} <= ids
        assert result.total_matched >= 2
        poi = next(e for e in result.entries if e.entry_id == "ref:poi")
        assert poi.category == "vector"
        assert poi.logical_role == "observation"
        assert poi.lifecycle == "ready"
        assert set(poi.fields) == {"name", "rating"}

    async def test_filter_by_role(self):
        await _seed("cat-role")
        result = await get_data_catalog().search(
            session_id="cat-role", filter_=CatalogFilter(role="observation")
        )
        assert [e.entry_id for e in result.entries] == ["ref:poi"]

    async def test_filter_bbox_requires_matchable_extent(self):
        await _seed("cat-bbox")
        # 与 extent 相交
        hit = await get_data_catalog().search(
            session_id="cat-bbox", filter_=CatalogFilter(bbox=[116.5, 39.5, 118.0, 41.0])
        )
        assert hit.total_matched == 2
        # 不相交 → 空
        miss = await get_data_catalog().search(
            session_id="cat-bbox", filter_=CatalogFilter(bbox=[0.0, 0.0, 1.0, 1.0])
        )
        assert miss.total_matched == 0

    async def test_filter_by_field_and_keyword(self):
        await _seed("cat-field")
        cat = get_data_catalog()
        by_field = await cat.search(
            session_id="cat-field", filter_=CatalogFilter(field="rating")
        )
        assert [e.entry_id for e in by_field.entries] == ["ref:poi"]
        by_kw = await cat.search(
            session_id="cat-field", filter_=CatalogFilter(keyword="北京")
        )
        assert by_kw.entries[0].entry_id == "ref:poi"

    async def test_limit_and_truncation(self):
        sid = "cat-limit"
        for i in range(10):
            await register_artifact(
                sid, artifact_id=f"ref:x{i}", producer_tool="t", inputs=[],
            )
        result = await get_data_catalog().search(
            session_id=sid, filter_=CatalogFilter(scope="session"), limit=3
        )
        assert len(result.entries) == 3
        assert result.truncated
        assert result.total_matched == 10

    async def test_stale_status_visible(self):
        sid = "cat-stale"
        await _seed(sid)
        from app.services.artifact_registry import mark_status
        from app.services.data_lifecycle.service import get_lifecycle_service

        await get_lifecycle_service().propagate_staleness(sid, "ref:poi", reason="x")
        result = await get_data_catalog().search(
            session_id=sid, filter_=CatalogFilter(status="stale")
        )
        assert {e.entry_id for e in result.entries} == {"ref:kde"}


class TestUploadScope:
    async def test_upload_source_via_db(self):
        """uploads 来源走本地 sqlite：插一条 UploadRecord 后可被目录检索。"""
        sid = "cat-upload"
        await _seed(sid)
        from app.models.upload import UploadRecord
        from app.core.database import Base  # noqa: F401 — 确保模型注册
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from pathlib import Path

        from app.core.config import settings

        # 默认 DATABASE_URL 是 sqlite:///./data/webgis.db —— sqlite 不会自建目录
        if settings.DATABASE_URL.startswith("sqlite"):
            Path("data").mkdir(exist_ok=True)
        engine = create_engine(settings.DATABASE_URL)
        Base.metadata.create_all(engine, tables=[UploadRecord.__table__], checkfirst=True)
        Session = sessionmaker(bind=engine)
        with Session() as db, db.begin():
            # 本地 sqlite 是持久文件：先清理同 session 旧数据保证幂等
            db.query(UploadRecord).filter(UploadRecord.session_id == sid).delete()
            db.add(UploadRecord(
                filename=f"uploads/abc123/original.geojson",
                original_name="clusters.geojson",
                file_type="vector",
                format="geojson",
                crs="EPSG:4326",
                geometry_type="Point",
                feature_count=42,
                bbox=[116.0, 39.0, 117.0, 40.0],
                file_size=12345,
                session_id=sid,
            ))
        result = await get_data_catalog().search(
            session_id=sid, filter_=CatalogFilter(scope="upload")
        )
        assert result.total_matched == 1
        entry = result.entries[0]
        assert entry.scope == "upload"
        assert entry.name == "clusters.geojson"
        assert entry.category == "vector"


class TestFabricScope:
    def test_fabric_descriptor_projection(self):
        entry = _entry_from_fabric_descriptor({
            "id": "ds-1", "source_type": "postgis", "title": "roads",
            "crs": "EPSG:4326", "bbox": [116.0, 39.0, 117.0, 40.0],
            "feature_count": 1000, "schema_fields": {"name": "string", "lanes": "number"},
        })
        assert entry.entry_id == "fabric:ds-1"
        assert entry.name == "roads"
        assert set(entry.fields) == {"name", "lanes"}
        assert "fabric" in entry.tags

    async def test_fabric_entries_surfaced(self):
        from app.services.data_fabric.spatial_catalog import spatial_catalog_service
        from app.schemas.data_fabric_schema import DatasetDescriptor

        spatial_catalog_service._catalog["ds-9"] = DatasetDescriptor(
            id="ds-9", source_type="wfs", title="parcels", crs="EPSG:4326"
        )
        result = await get_data_catalog().search(
            filter_=CatalogFilter(scope="fabric")
        )
        assert any(e.entry_id == "fabric:ds-9" for e in result.entries)
        spatial_catalog_service._catalog.pop("ds-9", None)


class TestHonestSources:
    async def test_session_loader_error_disclosed(self):
        cat = get_data_catalog()

        async def _boom(session_id):
            raise RuntimeError("store down")

        cat._session_records_loader = _boom
        result = await cat.search(session_id="cat-err")
        src = {s.source: s for s in result.sources_queried}
        assert "store down" in src["session"].error
        # uploads 来源（sqlite 无该会话数据）正常计数 0，fabric 计 0
        assert result.total_matched == 0


class TestDescribe:
    async def test_describe_entry(self):
        await _seed("cat-desc")
        entry = await get_data_catalog().describe("cat-desc", "ref:poi")
        assert entry is not None
        assert entry.feature_count == 120

    async def test_describe_missing_returns_none(self):
        assert await get_data_catalog().describe("cat-desc", "ref:ghost") is None


def test_filter_invalid_bbox_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CatalogFilter(bbox=[1.0, 2.0, 3.0])
