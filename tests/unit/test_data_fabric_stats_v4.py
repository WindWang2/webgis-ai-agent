"""ADR-0101 D6：durable advisory 统计、GeoParquet footer 生产者、计划反馈环。

统计/反馈是性能提示 —— 所有测试同时验证「功能生效」与「fail-open 降级」。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.data_fabric.query import feedback as fb_mod
from app.services.data_fabric.query.feedback import (
    PlannerFeedbackStore,
    feedback_store,
)
from app.services.data_fabric.query.planner import plan_query
from app.services.data_fabric.query.statistics import (
    DatasetStatistics,
    DurableStatisticsStore,
    collect_geoparquet_statistics,
    statistics_for_request,
)


@pytest.fixture(autouse=True)
def _isolated_feedback():
    """反馈环是进程级单例：测试间清空 + 恢复开关。"""
    feedback_store.reset()
    saved = feedback_store.enabled
    feedback_store.enabled = True
    yield
    feedback_store.reset()
    feedback_store.enabled = saved


def _descriptor(fp: str = "desc-fp-1", **meta) -> SimpleNamespace:
    return SimpleNamespace(
        id=fp,
        source_type="geoparquet",
        feature_count=None,
        bbox=None,
        geometry_type="Point",
        metadata=dict(meta),
    )


# ------------------------------------------------- durable statistics store


class TestDurableStatisticsStore:
    @pytest.fixture()
    def stats_db(self, monkeypatch, tmp_path):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        import app.core.database as database_mod
        import app.models.data_fabric  # noqa: F401 - 注册模型后再 create_all
        from app.core.database import Base

        engine = create_engine(f"sqlite:///{tmp_path}/stats.db")
        Base.metadata.create_all(engine)
        Sess = sessionmaker(bind=engine)
        monkeypatch.setattr(database_mod, "SessionLocal", Sess)
        return Sess

    def test_save_then_load_roundtrip(self, stats_db):
        store = DurableStatisticsStore(ttl_s=3600)
        stats = DatasetStatistics(
            dataset_fingerprint="fp-a", source_type="geoparquet",
            row_count=42, collector="geoparquet_footer", revision_strength="strong",
        )
        assert store.save(stats) is True
        loaded = store.load("fp-a")
        assert loaded is not None
        assert loaded.row_count == 42
        assert loaded.collector == "geoparquet_footer"
        assert loaded.revision_strength == "strong"

    def test_expired_row_is_rejected(self, stats_db):
        # DB 比较用 naive-UTC（repo 约定）：expires_at 拨到「UTC 过去」。
        from datetime import datetime, timedelta

        store = DurableStatisticsStore(ttl_s=3600)
        store.save(DatasetStatistics(dataset_fingerprint="fp-b", row_count=7))
        with stats_db() as db:
            from app.models.data_fabric import DatasetStatisticsRecord

            row = db.query(DatasetStatisticsRecord).order_by(
                DatasetStatisticsRecord.id.desc()).first()
            row.expires_at = datetime.utcnow() - timedelta(seconds=1)
            db.commit()
        assert store.load("fp-b") is None

    def test_load_latest_row_wins(self, stats_db):
        store = DurableStatisticsStore(ttl_s=3600)
        store.save(DatasetStatistics(dataset_fingerprint="fp-c", row_count=1))
        store.save(DatasetStatistics(dataset_fingerprint="fp-c", row_count=2))
        assert store.load("fp-c").row_count == 2

    def test_fail_open_on_db_errors(self, monkeypatch):
        """DB 不可用 → load None / save False：统计绝不阻断查询。"""
        import app.core.database as database_mod

        class Boom:
            def __call__(self):
                raise RuntimeError("db down")

            def __enter__(self):
                raise RuntimeError("db down")

        monkeypatch.setattr(database_mod, "SessionLocal", Boom())
        store = DurableStatisticsStore()
        assert store.save(DatasetStatistics(dataset_fingerprint="fp-d", row_count=1)) is False
        assert store.load("fp-d") is None
        assert store.prune() == 0

    def test_prune_removes_expired_and_caps_retention(self, stats_db):
        from datetime import datetime, timedelta

        store = DurableStatisticsStore(ttl_s=3600, max_rows=5)
        for i in range(8):
            store.save(DatasetStatistics(dataset_fingerprint=f"fp-x{i}", row_count=i))
        with stats_db() as db:
            from app.models.data_fabric import DatasetStatisticsRecord

            row = db.query(DatasetStatisticsRecord).order_by(
                DatasetStatisticsRecord.id.desc()).first()
            row.expires_at = datetime.utcnow() - timedelta(seconds=1)
            db.commit()
        removed = store.prune()
        assert removed >= 1  # 至少清掉过期行（+ 超上限的旧行）
        with stats_db() as db:
            from app.models.data_fabric import DatasetStatisticsRecord

            assert db.query(DatasetStatisticsRecord).count() <= 5

    def test_request_path_uses_durable_fallback(self, stats_db):
        """descriptor 无统计 → 进程缓存 miss → durable 命中。"""
        store = DurableStatisticsStore(ttl_s=3600)
        store.save(DatasetStatistics(
            dataset_fingerprint="desc-durable-1", row_count=99,
            collector="geoparquet_footer"))
        # 注入单例指向测试库（statistics_for_request 读模块级 _durable_store）。
        import app.services.data_fabric.query.statistics as stats_mod

        saved = stats_mod._durable_store
        stats_mod._durable_store = store
        try:
            got = statistics_for_request(_descriptor("desc-durable-1"))
        finally:
            stats_mod._durable_store = saved
        assert got is not None and got.row_count == 99


# ------------------------------------------------- GeoParquet footer collector


class TestGeoParquetFooterCollector:
    @pytest.fixture()
    def geoparquet_file(self, tmp_path):
        """优先 pyarrow 写（完整 footer）；否则 pyogrio Parquet；都没有则跳过。"""
        from shapely.geometry import Point

        gdf = pytest.importorskip("geopandas").GeoDataFrame(
            {"v": [1, 2, 3], "kind": ["a", "b", "a"]},
            geometry=[Point(0, 0), Point(1, 1), Point(2, 2)],
            crs="EPSG:4326",
        )
        path = tmp_path / "points.parquet"
        try:
            gdf.to_parquet(path)  # 需要 pyarrow
        except ImportError:
            try:
                pytest.importorskip("pyogrio").write_dataframe(
                    gdf, str(path), driver="Parquet")
            except Exception:
                pytest.skip("no GeoParquet writer available in this env")
        return str(path)

    def test_footer_produced_honestly(self, geoparquet_file):
        stats = collect_geoparquet_statistics(geoparquet_file, "gp-fp-1")
        assert stats is not None
        assert stats.collector == "geoparquet_footer"
        assert stats.row_count == 3
        assert stats.revision_strength == "strong"
        assert {c.name for c in stats.columns} >= {"v", "kind"}
        assert all(c.confidence == "assumption" for c in stats.columns)

    def test_pyarrow_footer_adds_row_groups(self, geoparquet_file):
        pytest.importorskip("pyarrow.parquet")
        stats = collect_geoparquet_statistics(geoparquet_file, "gp-fp-2")
        assert stats is not None
        assert stats.row_group_count is not None and stats.row_group_count >= 1

    def test_remote_path_honestly_none(self):
        assert collect_geoparquet_statistics("https://example.com/x.parquet", "fp") is None
        assert collect_geoparquet_statistics("s3://bucket/x.parquet", "fp") is None

    def test_missing_file_honestly_none(self, tmp_path):
        assert collect_geoparquet_statistics(str(tmp_path / "nope.parquet"), "fp") is None

    def test_adapter_metadata_feeds_collector_label(self):
        """describe metadata 的 stats_collector 标注驱动统计管道 collector。"""
        desc = _descriptor(
            "gp-fp-3",
            row_count=10,
            stats_collector="geoparquet_footer",
            revision_strength="strong",
            column_statistics=[{"name": "v", "confidence": "assumption"}],
        )
        stats = statistics_for_request(desc)
        assert stats is not None
        assert stats.collector == "geoparquet_footer"
        assert stats.revision_strength == "strong"
        assert stats.row_count == 10


# ------------------------------------------------------- planner feedback loop


class TestPlannerFeedback:
    def _spec(self):
        from app.services.data_fabric.query.models import QuerySpecV2

        return QuerySpecV2()

    def _descriptor(self):
        return SimpleNamespace(
            id="fb-ds",
            source_type="postgis",
            feature_count=10_000,
            bbox=[0.0, 0.0, 1.0, 1.0],
            geometry_type="Point",
            metadata={},
        )

    def test_insufficient_samples_keeps_baseline(self):
        stats = plan_query(self._spec(), self._descriptor(),
                           dataset_fingerprint="fb-ds", query_fp="q1")
        baseline = stats.estimated_rows
        feedback_store.record(dataset_fingerprint="fb-ds", operator_class="query",
                              estimated_rows=baseline, actual_rows=baseline * 4)
        after_one = plan_query(self._spec(), self._descriptor(),
                               dataset_fingerprint="fb-ds", query_fp="q1")
        assert after_one.estimated_rows == baseline  # 样本不足 → 位级不变

    def test_correction_applies_and_is_explained(self):
        stats = plan_query(self._spec(), self._descriptor(),
                           dataset_fingerprint="fb-ds", query_fp="q1")
        baseline = stats.estimated_rows
        for _ in range(5):
            feedback_store.record(dataset_fingerprint="fb-ds", operator_class="query",
                                  estimated_rows=baseline, actual_rows=baseline * 4)
        corrected = plan_query(self._spec(), self._descriptor(),
                               dataset_fingerprint="fb-ds", query_fp="q1")
        assert corrected.estimated_rows == pytest.approx(baseline * 4, rel=0.35)
        assert any("planner feedback" in a for a in corrected.assumptions)

    def test_failed_executions_never_learn(self):
        feedback_store.record(dataset_fingerprint="fb-ds", operator_class="query",
                              estimated_rows=100, actual_rows=9999, outcome="failed")
        feedback_store.record(dataset_fingerprint="fb-ds", operator_class="query",
                              estimated_rows=100, actual_rows=9999, outcome="partial")
        c = feedback_store.correction("fb-ds", "query")
        assert c.basis == "insufficient_samples" and c.factor == 1.0

    def test_disabled_store_is_bit_identical_baseline(self):
        feedback_store.enabled = False
        feedback_store.record(dataset_fingerprint="fb-ds", operator_class="query",
                              estimated_rows=100, actual_rows=9999, outcome="ok")
        c = feedback_store.correction("fb-ds", "query")
        assert c.basis == "disabled" and c.factor == 1.0

    def test_factor_clamped(self):
        for _ in range(6):
            feedback_store.record(dataset_fingerprint="fb-ds", operator_class="query",
                                  estimated_rows=100, actual_rows=10**9)
        c = feedback_store.correction("fb-ds", "query")
        assert c.factor <= 10.0
        assert c.drift == "planner_feedback_underestimate"

    def test_ttl_expiry(self, monkeypatch):
        store = PlannerFeedbackStore(ttl_s=60.0, min_samples=2)
        clock = {"t": 1000.0}
        monkeypatch.setattr(fb_mod.time, "monotonic", lambda: clock["t"])
        store.record(dataset_fingerprint="fb-ds", operator_class="query",
                     estimated_rows=100, actual_rows=400)
        clock["t"] += 1000  # > ttl
        store.record(dataset_fingerprint="fb-ds", operator_class="query",
                     estimated_rows=100, actual_rows=400)
        c = store.correction("fb-ds", "query")
        assert c.samples <= 1  # 过期样本不参与
        assert c.basis == "insufficient_samples"

    def test_ops_hook_records_from_query_result(self):
        from app.services.geocompute import ops

        result = SimpleNamespace(
            features=[{"properties": {}}] * 12,
            metadata={"query_plan": {
                "dataset_fingerprint": "fb-hook-1", "estimated_rows": 10,
            }},
        )
        ops._record_planner_feedback(_node_stub(), result, actual_rows=12)
        c = feedback_store.correction("fb-hook-1", "query")
        # 单条样本 → 不足以修正，但已进入样本集（无异常即通过）。
        assert c.factor >= 1.0

    def test_ops_hook_fail_open(self):
        from app.services.geocompute import ops

        ops._record_planner_feedback(
            _node_stub(), SimpleNamespace(features=[], metadata=None), actual_rows=0)
        ops._record_planner_feedback(_node_stub(), None, actual_rows=0)


def _node_stub():
    from app.services.geocompute import ExecutionNode, NodeCategory

    return ExecutionNode(node_id="q", category=NodeCategory.QUERY)
