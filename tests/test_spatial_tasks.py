"""Tests for spatial_tasks Celery task definitions."""
import asyncio
import contextlib
import json as _json
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.db_model import Base


def test_no_spatial_join_task():
    """run_spatial_join should not exist — SpatialAnalyzer.spatial_join was never implemented."""
    from app.services import spatial_tasks
    assert not hasattr(spatial_tasks, "run_spatial_join"), (
        "run_spatial_join references nonexistent SpatialAnalyzer.spatial_join"
    )


def test_no_zonal_stats_task():
    """run_zonal_stats should not exist — SpatialAnalyzer.zonal_statistics was never implemented."""
    from app.services import spatial_tasks
    assert not hasattr(spatial_tasks, "run_zonal_stats"), (
        "run_zonal_stats references nonexistent SpatialAnalyzer.zonal_statistics"
    )


def test_valid_tasks_exist():
    """The 3 live Celery tasks (with real .delay/.apply_async callers) must still exist.

    The 6 dead wrappers (run_buffer_analysis / run_spatial_stats / run_nearest_neighbor /
    run_overlay_analysis / run_attribute_filter / run_path_analysis) were deleted (D1):
    agent tools call SpatialAnalyzer directly (ADR-0013 deleted the dispatch seam that
    routed to them). These watchdogs lock in that they stay gone.
    """
    from app.services import spatial_tasks
    assert hasattr(spatial_tasks, "run_heatmap_generation")
    assert hasattr(spatial_tasks, "run_ndvi_analysis")
    assert hasattr(spatial_tasks, "run_change_detection")
    # Dead wrappers must stay deleted
    for dead in (
        "run_buffer_analysis", "run_spatial_stats", "run_nearest_neighbor",
        "run_overlay_analysis", "run_attribute_filter", "run_path_analysis",
    ):
        assert not hasattr(spatial_tasks, dead), f"{dead} should have been deleted (D1)"


# ── PR2: 变化检测送达链路 & 热力图 Celery/进程内契约一致 ──────────────


@pytest.fixture
def job_db(tmp_path, monkeypatch):
    """把 `db_session`（工具/worker 用的同步会话）指向临时 SQLite。

    复用 tests/jobs/test_job_celery_e2e.py 的搭建方式。
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'pr2.db'}")
    Base.metadata.create_all(engine)
    Sess = sessionmaker(bind=engine)

    @contextlib.contextmanager
    def fake_db_session():
        db = Sess()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    for target in (
        "app.tools._utils.db_session",
        "app.services.jobs.submit.db_session",
        "app.services.spatial_tasks.db_session",
        "app.services.jobs.worker._default_session_factory",
    ):
        monkeypatch.setattr(target, fake_db_session)
    yield {"factory": fake_db_session, "Sess": Sess}
    engine.dispose()


def _make_change_job(job_db) -> int:
    from app.services.jobs import DurableJobStore, JobKind, JobStatus

    with job_db["Sess"]() as db:
        job = DurableJobStore.create_sync(
            db,
            dispatch_spec={"task": "app.services.spatial_tasks.run_change_detection"},
            task_type="change_detection",
            kind=JobKind.analysis,
            parameters={"bbox": [116.0, 39.0, 116.1, 39.1]},
            session_id="sess-a",
        )
        DurableJobStore.transition_sync(db, job.id, JobStatus.queued, expected=[JobStatus.pending])
        db.commit()
        return int(job.id)


def _fake_spectral_engine(monkeypatch, t1=None, t2=None):
    """替换 SpectralRasterEngine，返回受控的两期指数结果（按起始日期区分 T1/T2）。"""
    from app.services import spatial_tasks

    t1 = t1 if t1 is not None else {"stats": {"mean": 0.5}, "cloud_cover": 5.0}
    t2 = t2 if t2 is not None else {"stats": {"mean": 0.45}, "cloud_cover": 8.0}
    instance = type("E", (), {})()

    async def fake_compute(bbox, date_from, date_to, index_type="ndvi"):
        return dict(t2 if date_from.startswith("2023-06") else t1)

    instance.compute_vegetation_index = fake_compute
    monkeypatch.setattr(spatial_tasks, "SpectralRasterEngine", lambda: instance)


def _task_stub(monkeypatch):
    """Celery bind=True 任务体直接调用时的 self 桩（update_state 在无 backend 时会抛）。"""
    from app.services import spatial_tasks

    monkeypatch.setattr(spatial_tasks.run_change_detection, "update_state", lambda **kw: None)


def test_change_detection_durable_path_completes_and_registers_asset(job_db, tmp_path, monkeypatch):
    """job_id 路径走 durable 运行时，结果落 GeoJSON 资产并挂到会话。"""
    from app.core.config import settings as app_settings
    from app.models.upload import UploadRecord
    from app.services import spatial_tasks
    from app.services.jobs import DurableJobStore, JobStatus, coerce_status

    monkeypatch.setattr(app_settings, "DATA_DIR", str(tmp_path))
    job_id = _make_change_job(job_db)
    _fake_spectral_engine(monkeypatch)
    _task_stub(monkeypatch)

    res = spatial_tasks.run_change_detection(
        bbox=[116.0, 39.0, 116.1, 39.1],
        t1_from="2023-01-01", t1_to="2023-01-10",
        t2_from="2023-06-01", t2_to="2023-06-10",
        index_type="ndvi", change_threshold=0.1,
        session_id="sess-a", job_id=job_id,
    )

    assert res["success"] is True
    assert res["job_id"] == str(job_id)
    assert res["data"]["change"]["category"] == "no_change"
    assert res["data"]["asset_id"] is not None

    result_path = res["data"]["result_path"]
    assert result_path.startswith(str(tmp_path / "analysis_results"))
    assert result_path.endswith(".geojson")

    with job_db["Sess"]() as db:
        job = DurableJobStore.get_sync(db, job_id)
        assert coerce_status(job.status) is JobStatus.completed
        assert job.result_ref == result_path
        # 结果可取回（任务中心/LLM 可读取的统计摘要）
        assert job.result_summary["data"]["change"]["category"] == "no_change"

        rec = db.query(UploadRecord).one()
        assert rec.session_id == "sess-a"
        assert rec.format == "geojson"
        assert rec.geometry_type == "change_detection"
        assert rec.bbox == [116.0, 39.0, 116.1, 39.1]

    with open(result_path, encoding="utf-8") as f:
        geojson = _json.load(f)
    props = geojson["features"][0]["properties"]
    assert props["analysis"] == "change_detection"
    assert props["category"] == "no_change"
    assert os.path.exists(result_path)


def test_change_detection_business_failure_keeps_dict_semantics(job_db, tmp_path, monkeypatch):
    """T1/T2 计算失败保留 dict 返回（业务失败），job 落 failed。"""
    from app.core.config import settings as app_settings
    from app.services import spatial_tasks
    from app.services.jobs import DurableJobStore, JobStatus, coerce_status

    monkeypatch.setattr(app_settings, "DATA_DIR", str(tmp_path))
    job_id = _make_change_job(job_db)
    _fake_spectral_engine(monkeypatch, t1={"error": "STAC 查询无可用影像"})
    _task_stub(monkeypatch)

    res = spatial_tasks.run_change_detection(
        bbox=[116.0, 39.0, 116.1, 39.1],
        t1_from="2023-01-01", t1_to="2023-01-10",
        t2_from="2023-06-01", t2_to="2023-06-10",
        index_type="ndvi", change_threshold=0.1,
        session_id="sess-a", job_id=job_id,
    )

    assert res["success"] is False
    assert "T1 计算失败" in res["error"]
    with job_db["Sess"]() as db:
        job = DurableJobStore.get_sync(db, job_id)
        assert coerce_status(job.status) is JobStatus.failed


def test_change_detection_unexpected_exception_raises(job_db, tmp_path, monkeypatch):
    """意外异常必须上抛（Celery 记 FAILURE），不得吞成 SUCCESS dict。"""
    from app.core.config import settings as app_settings
    from app.services import spatial_tasks
    from app.services.jobs import DurableJobStore, JobStatus, coerce_status

    monkeypatch.setattr(app_settings, "DATA_DIR", str(tmp_path))
    job_id = _make_change_job(job_db)
    monkeypatch.setattr(
        spatial_tasks, "SpectralRasterEngine",
        lambda: (_ for _ in ()).throw(RuntimeError("worker exploded")),
    )
    _task_stub(monkeypatch)

    with pytest.raises(RuntimeError, match="worker exploded"):
        spatial_tasks.run_change_detection(
            bbox=[116.0, 39.0, 116.1, 39.1],
            t1_from="2023-01-01", t1_to="2023-01-10",
            t2_from="2023-06-01", t2_to="2023-06-10",
            index_type="ndvi", change_threshold=0.1,
            session_id="sess-a", job_id=job_id,
        )
    with job_db["Sess"]() as db:
        job = DurableJobStore.get_sync(db, job_id)
        assert coerce_status(job.status) is JobStatus.failed


def test_change_detection_geojson_asset_trims_preview_blob(job_db, tmp_path, monkeypatch):
    """吸收 #445 后的资产契约：像元分类进任务结果，preview_png_b64 不进 GeoJSON 资产。"""
    import numpy as np

    from app.core.config import settings as app_settings
    from app.services import spatial_tasks

    monkeypatch.setattr(app_settings, "DATA_DIR", str(tmp_path))
    job_id = _make_change_job(job_db)

    def _epoch(array):
        return {
            "stats": {"mean": float(np.mean(array))},
            "cloud_cover": 0.0,
            "raster_source": {"array": array, "bounds": [0.0, 0.0, 1.0, 1.0]},
        }

    t1 = _epoch(np.full((4, 4), 0.3))
    t2_arr = np.full((4, 4), 0.3)
    t2_arr[0:2, 0:2] = 0.9  # +0.6 -> significant_improvement
    _fake_spectral_engine(monkeypatch, t1=t1, t2=_epoch(t2_arr))
    _task_stub(monkeypatch)

    res = spatial_tasks.run_change_detection(
        bbox=[116.0, 39.0, 116.1, 39.1],
        t1_from="2023-01-01", t1_to="2023-01-10",
        t2_from="2023-06-01", t2_to="2023-06-10",
        index_type="ndvi", change_threshold=0.1,
        session_id="sess-a", job_id=job_id,
    )

    assert res["success"] is True
    change = res["data"]["change"]
    assert change["method"] == "pixel_common_footprint"
    # 任务结果（任务中心/LLM 可读）保留预览图与分类统计
    assert change["classification"]["preview_png_b64"]
    assert change["classification"]["class_counts"]["significant_improvement"] == 4

    with open(res["data"]["result_path"], encoding="utf-8") as f:
        props = _json.load(f)["features"][0]["properties"]
    cls = props["classification"]
    assert "preview_png_b64" not in cls  # GeoJSON 资产裁掉 base64 PNG blob
    assert props["method"] == "pixel_common_footprint"
    assert cls["class_counts"]["significant_improvement"] == 4
    assert cls["class_percentages"]["significant_improvement"] == 25.0


def test_change_detection_legacy_path_still_works():
    """job_id=None 的兼容分支保留原行为（update_state + 统计 dict）。"""
    from unittest.mock import patch

    from app.services.spatial_tasks import run_change_detection

    with patch("app.services.spatial_tasks.SpectralRasterEngine") as mock_rss, \
         patch.object(run_change_detection, "update_state"):
        instance = mock_rss.return_value

        async def mock_compute_vi(bbox, date_from, date_to, index_type):
            return {"stats": {"mean": 0.5}, "cloud_cover": 5.0}

        instance.compute_vegetation_index.side_effect = mock_compute_vi

        res = run_change_detection(
            bbox=[116.0, 39.0, 116.1, 39.1],
            t1_from="2023-01-01", t1_to="2023-01-10",
            t2_from="2023-06-01", t2_to="2023-06-10",
            index_type="ndvi", change_threshold=0.1,
        )
        assert res["success"] is True
        assert res["data"]["change"]["category"] == "no_change"


def test_detect_vegetation_change_tool_submits_durable_job(job_db, tmp_path, monkeypatch):
    """工具不再 .delay 火忘投递 —— 经 submit_durable_job 注册任务中心可查。"""
    from app.core.config import settings as app_settings
    from app.services.jobs import DurableJobStore, JobStatus, coerce_status
    from app.tools.change_detection import register_change_detection_tools
    from app.tools.registry import ToolRegistry

    monkeypatch.setattr(app_settings, "DATA_DIR", str(tmp_path))

    enqueued: list[dict] = []

    class _FakeTask:
        name = "app.services.spatial_tasks.run_change_detection"

        def apply_async(self, args=None, kwargs=None, **_):
            enqueued.append({"args": args, "kwargs": kwargs})

            class _R:
                id = "celery-cd-1"

            return _R()

    monkeypatch.setattr("app.tools.change_detection.run_change_detection", _FakeTask())

    registry = ToolRegistry()
    register_change_detection_tools(registry)

    result = asyncio.run(registry.dispatch(
        "detect_vegetation_change",
        {
            "bbox": "[116.0, 39.0, 116.1, 39.1]",
            "t1_from": "2023-01-01", "t1_to": "2023-01-10",
            "t2_from": "2023-06-01", "t2_to": "2023-06-10",
        },
        session_id="sess-a",
    ))

    assert result["status"] == "analysis_task_started"
    assert result["job_id"]
    # 消息诚实：不再承诺「自动推送到地图」
    assert "推送到地图" not in result["message"]
    assert len(enqueued) == 1
    args = enqueued[0]["args"]
    assert args[0] == [116.0, 39.0, 116.1, 39.1]
    assert "job_id" in enqueued[0]["kwargs"]

    job_id = int(result["job_id"])
    with job_db["Sess"]() as db:
        job = DurableJobStore.get_sync(db, job_id)
        assert job is not None, "任务中心必须能查到该 job"
        assert coerce_status(job.status) is JobStatus.queued
        assert job.task_type == "change_detection"
        assert job.parameters["index_type"] == "ndvi"


@pytest.mark.heavy
def test_heatmap_celery_path_matches_inprocess_contract():
    """Celery 路径与进程内回退共用实现 —— palette 生效且写 metadata。"""
    from app.lib.geo_analysis.density import generate_heatmap_raster
    from app.services.spatial_tasks import _do_heatmap_generation

    features = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.4, 39.9]}}]

    for palette in ("magma", "viridis", "classic"):
        via_celery = _do_heatmap_generation(features, 500, 1000, "raster", palette)
        inprocess = generate_heatmap_raster(features, 500, 1000, "raster", palette)
        assert via_celery["success"] is True
        # metadata 契约一致（前端 legend 依赖），palette 真实回传
        assert via_celery["data"]["metadata"] == inprocess["data"]["metadata"]
        assert via_celery["data"]["metadata"]["palette"] == palette

    grid = _do_heatmap_generation(features, 500, 1000, "grid", "viridis")
    assert grid["success"] is True
    assert grid["data"]["metadata"]["palette"] == "viridis"


@pytest.mark.heavy
def test_heatmap_celery_path_empty_features_keeps_success_false():
    """空点集分支保留 Celery 结果消费方依赖的 success=False 语义。"""
    from app.services.spatial_tasks import _do_heatmap_generation

    result = _do_heatmap_generation(features=[], cell_size=500, radius=1000)
    assert result["success"] is False
    assert "No valid point features found" in result["error"]
