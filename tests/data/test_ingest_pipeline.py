"""Ingest Pipeline V3 —— 摄入 / 去重 / 回滚测试。"""
import pytest

from app.services.artifact_registry import (
    get_artifact,
    list_artifacts,
    update_record_metadata,
)
from app.services.data_ingest.pipeline import (
    compute_payload_fingerprint,
    get_ingest_pipeline,
    reset_ingest_pipeline,
)
from app.services.session_data import session_data_manager


@pytest.fixture(autouse=True)
def _reset():
    reset_ingest_pipeline()
    yield
    reset_ingest_pipeline()


def _fc(n=3, value="v"):
    return {
        "type": "FeatureCollection",
        "features": [
            {"geometry": {"type": "Point", "coordinates": [116.0 + i * 0.1, 39.0]},
             "properties": {"v": f"{value}{i}"}}
            for i in range(n)
        ],
    }


class TestHappyPath:
    async def test_ingest_registers_artifact_with_fingerprint(self):
        sid = "ingest-ok"
        result = await get_ingest_pipeline().ingest(
            sid, _fc(), name="poi 数据", crs="EPSG:4326"
        )
        assert result.ok
        assert not result.duplicate
        assert result.ref_id.startswith("ref:")
        assert result.steps_completed[-1] == "register"
        assert result.profile_summary["row_count"] == 3
        assert result.quality_summary["quality_status"] in ("valid", "warning")
        rec = await get_artifact(sid, result.ref_id)
        assert rec is not None
        assert rec.metadata["ingest_content_sha256"]
        assert rec.metadata["logical_role"] == "source"

    async def test_missing_crs_recorded_not_fabricated(self):
        sid = "ingest-crs"
        result = await get_ingest_pipeline().ingest(sid, _fc())
        assert result.ok
        # 未声明 CRS → quality 报 crs_missing，而不是静默 4326（§二十七）
        issues = result.quality_summary["issues"]
        assert any(i["code"] == "crs_missing" for i in issues)


class TestDedup:
    async def test_same_content_reuses_ref(self):
        sid = "ingest-dup"
        pipe = get_ingest_pipeline()
        first = await pipe.ingest(sid, _fc())
        second = await pipe.ingest(sid, _fc(3))  # 同内容（独立构造，同指纹）
        assert first.ok and second.ok
        assert second.duplicate
        assert second.duplicate_of == first.ref_id
        assert second.ref_id == first.ref_id
        # 账本只增加一条
        assert len(await list_artifacts(sid)) == 1

    async def test_different_content_new_ref(self):
        sid = "ingest-diff"
        pipe = get_ingest_pipeline()
        first = await pipe.ingest(sid, _fc(value="a"))
        second = await pipe.ingest(sid, _fc(value="b"))
        assert not second.duplicate
        assert second.ref_id != first.ref_id
        assert len(await list_artifacts(sid)) == 2

    async def test_dedup_ignores_stale_records(self):
        sid = "ingest-dup-stale"
        pipe = get_ingest_pipeline()
        first = await pipe.ingest(sid, _fc())
        await update_record_metadata(sid, first.ref_id, status="stale")
        second = await pipe.ingest(sid, _fc())
        assert not second.duplicate
        assert second.ref_id != first.ref_id


class TestFailureHandling:
    async def test_non_fc_shape_rejected(self):
        result = await get_ingest_pipeline().ingest("ingest-x", {"type": "chart_spec"})
        assert not result.ok
        assert result.error_code == "UNSUPPORTED_SHAPE"

    async def test_register_failure_rolls_back_ref(self, monkeypatch):
        sid = "ingest-rollback"
        from app.services import artifact_registry as ar

        real_register = ar.register_artifact

        async def failing_register(*a, **kw):
            return None  # 模拟注册被拒

        monkeypatch.setattr(ar, "register_artifact", failing_register)
        # 管线内部通过模块属性调用 → monkeypatch 生效
        pipe = get_ingest_pipeline()
        result = await pipe.ingest(sid, _fc())
        assert not result.ok
        assert result.error_code == "REGISTER_FAILED_ROLLED_BACK"
        # ref 已补偿删除（store 中不再存在）
        exists = await session_data_manager.ref_exists(sid, result.ref_id)
        assert not exists
        _ = real_register  # 保留引用避免 lint 噪音


def test_fingerprint_stable_and_sensitive():
    a = compute_payload_fingerprint(_fc())
    b = compute_payload_fingerprint(_fc())
    c = compute_payload_fingerprint(_fc(value="different"))
    assert a == b and a != c
