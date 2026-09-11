"""GeoCompute V8 — 空间感知分区（Phase D）。

覆盖：分区计划纯函数（raster 网格/halo 钳界/core 精确并集、vector 网格/
halo、自适应收缩、代表点分配、合并去重、偏斜检测）、executor 分区
fan-out 端到端（eager：scan(durable) → filter(durable+partition)）、
诚实失败路径（不支持类别/空输入/栅格依赖缺席）。
"""

from __future__ import annotations

import pytest

from app.services.geocompute import partitioning as P
from app.services.geocompute.plan import PartitionSpec


def _features_grid(count_per_cell: int = 3, cells=((116.0, 39.0), (117.0, 40.0))):
    """两角对置的点簇（保证分区后每格都有数据）。"""
    feats = []
    i = 0
    for cx, cy in cells:
        for j in range(count_per_cell):
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Point",
                             "coordinates": [cx + j * 0.001, cy]},
                "properties": {"kind": "a", "i": i},
            })
            i += 1
    return feats


# ═══════════════════════ 计划纯函数 ══════════════════════


class TestRasterPlan:
    def test_square_grid_exact_union(self):
        spec = PartitionSpec(scheme="raster_grid", target_tiles=4, halo_px=0)
        plan = P.plan_raster(spec, width=100, height=100, crs="EPSG:4326")
        assert plan.tiles == 4
        union = sum(p.core_width * p.core_height for p in plan.parts)
        assert union == 100 * 100
        # core 互不重叠（两两 core 窗口无交）
        for a in plan.parts:
            for b in plan.parts:
                if a.index >= b.index:
                    continue
                overlap = not (
                    a.core_col_off + a.core_width <= b.core_col_off
                    or b.core_col_off + b.core_width <= a.core_col_off
                    or a.core_row_off + a.core_height <= b.core_row_off
                    or b.core_row_off + b.core_height <= a.core_row_off
                )
                assert not overlap

    def test_halo_clamped_and_windows_superset(self):
        spec = PartitionSpec(scheme="raster_grid", target_tiles=4, halo_px=10)
        plan = P.plan_raster(spec, width=50, height=50)
        for p in plan.parts:
            assert p.col_off >= 0 and p.row_off >= 0
            assert p.col_off + p.width <= 50
            assert p.row_off + p.height <= 50
            # halo 窗口 ⊇ core 窗口
            assert p.col_off <= p.core_col_off
            assert p.row_off <= p.core_row_off
            assert p.col_off + p.width >= p.core_col_off + p.core_width
            assert p.row_off + p.height >= p.core_row_off + p.core_height

    def test_halo_present_on_interior_edges(self):
        """相邻 core 的 halo 窗口伸入邻居（邻域计算可用）。"""
        spec = PartitionSpec(scheme="raster_grid", target_tiles=4, halo_px=5)
        plan = P.plan_raster(spec, width=100, height=100)
        right = [p for p in plan.parts if p.core_col_off > 0]
        assert all(p.col_off < p.core_col_off for p in right)


class TestVectorPlan:
    def test_grid_and_halo_bbox(self):
        spec = PartitionSpec(scheme="vector_grid", target_tiles=4,
                             halo_ratio=0.1, min_rows_per_tile=0)
        plan = P.plan_vector(spec, bbox=(0.0, 0.0, 10.0, 10.0), crs="EPSG:4326")
        assert plan.tiles == 4
        assert plan.crs == "EPSG:4326"
        for p in plan.parts:
            x0, y0, x1, y1 = p.bbox
            hx0, hy0, hx1, hy1 = p.halo_bbox
            assert hx0 <= x0 and hy0 <= y0 and hx1 >= x1 and hy1 >= y1

    def test_invalid_bbox_typed_error(self):
        spec = PartitionSpec(scheme="vector_grid", target_tiles=2)
        with pytest.raises(Exception, match="positive-area"):
            P.plan_vector(spec, bbox=(5.0, 5.0, 5.0, 5.0))


class TestAdaptiveAndSkew:
    def test_adaptive_shrinks_on_few_rows(self):
        spec = PartitionSpec(scheme="vector_grid", target_tiles=16,
                             min_rows_per_tile=1000)
        assert P.adaptive_tile_count(spec, input_rows=2500) == 3

    def test_adaptive_grows_on_mem_budget(self):
        spec = PartitionSpec(scheme="vector_grid", target_tiles=2,
                             min_rows_per_tile=0,
                             per_tile_mem_budget_mb=100)
        assert P.adaptive_tile_count(
            spec, est_total_mem_mb=1000, input_rows=None) == 10

    def test_skew_detection(self):
        assert P.detect_skew([10, 10, 10]) is None
        # 单峰 + 4 长尾：ratio = 100/20.8 ≈ 4.81 > 阈值 4
        report = P.detect_skew([100, 1, 1, 1, 1])
        assert report is not None and report["ratio"] > 4
        assert P.detect_skew([0, 0, 0]) is None
        assert P.detect_skew([5]) is None


class TestAssignAndMerge:
    def test_assign_by_representative_point(self):
        spec = PartitionSpec(scheme="vector_grid", target_tiles=4,
                             min_rows_per_tile=0)
        plan = P.plan_vector(spec, bbox=(116.0, 39.0, 118.0, 41.0))
        feats = _features_grid()
        assigned = P.assign_features(feats, plan.parts)
        total = sum(len(v) for v in assigned.values())
        assert total == len(feats)  # 零丢失
        # 两角簇各占一格：恰好 2 个非空格
        nonempty = [idx for idx, v in assigned.items() if v]
        assert len(nonempty) == 2

    def test_unassignable_feature_kept_in_partition_zero(self):
        spec = PartitionSpec(scheme="vector_grid", target_tiles=4,
                             min_rows_per_tile=0)
        plan = P.plan_vector(spec, bbox=(0.0, 0.0, 10.0, 10.0))
        weird = [{"type": "Feature", "geometry": None, "properties": {}}]
        assigned = P.assign_features(weird, plan.parts)
        assert len(assigned[0]) == 1  # fail-open：不静默丢失

    def test_merge_dedup_and_metadata(self):
        f1 = {"type": "Feature", "geometry": {"type": "Point",
                                              "coordinates": [1, 2]},
              "properties": {"a": 1}}
        f2 = dict(f1)  # 内容相同（halo 复制）
        payload = P.merge_vector_payloads(
            [{"features": [f1, f1]}, {"features": [f2]}], node_id="n")
        assert len(payload["features"]) == 1
        assert payload["metadata"]["partition_merge"]["dedup_removed"] == 2
        assert payload["metadata"]["partition_merge"]["tiles"] == 2

    def test_feature_identity_stable(self):
        f = {"type": "Feature", "geometry": {"type": "Point",
                                             "coordinates": [1, 2]},
             "properties": {"z": 1, "a": 2}}
        g = {"properties": {"a": 2, "z": 1}, "geometry": {"type": "Point",
                                                          "coordinates": [1, 2]},
             "type": "Feature"}
        assert P.feature_identity(f) == P.feature_identity(g)


class TestPartitionGuard:
    def test_unsupported_category_typed_error(self):
        from app.services.geocompute.plan import ExecutionNode, NodeCategory

        node = ExecutionNode(node_id="q", category=NodeCategory.QUERY,
                             partition=PartitionSpec(scheme="vector_grid"))
        with pytest.raises(Exception, match="non-partitionable"):
            P.ensure_partition_supported(node)

    def test_supported_categories_pass(self):
        from app.services.geocompute.plan import ExecutionNode, NodeCategory

        for cat in (NodeCategory.FILTER, NodeCategory.VECTOR_OPERATION,
                    NodeCategory.RASTER_WINDOW_OPERATION):
            node = ExecutionNode(node_id="n", category=cat)
            P.ensure_partition_supported(node)  # 不抛


# ═══════════════════════ executor fan-out（eager 端到端）═════════════════════


@pytest.fixture(autouse=True)
def _celery_offline(monkeypatch):
    from app.services.task_queue import celery_app

    monkeypatch.setitem(celery_app.conf, "broker_url", "memory://")
    monkeypatch.setitem(celery_app.conf, "result_backend", "cache+memory://")
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)


@pytest.fixture(autouse=True)
def scan_stub(monkeypatch):
    """SOURCE_SCAN 测试桩（与 test_geocompute_v6_scheduler 同惯例）：
    parameters.features / parameters.raster_path 内联返回。"""
    import time as _time

    from app.services.geocompute.ops import REGISTRY
    from app.services.geocompute.plan import NodeCategory

    def _scan(ctx, node, payloads):
        params = node.parameters
        if params.get("raster_path"):
            return {"raster_path": params["raster_path"],
                    "metadata": {"via": "scan_stub"}}
        return {
            "features": params.get("features") or [],
            "metadata": {"via": "scan_stub"},
        }

    monkeypatch.setitem(REGISTRY, NodeCategory.SOURCE_SCAN, _scan)


@pytest.fixture()
def engine_env(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    eng = create_engine(f"sqlite:///{tmp_path / 'v8-part.db'}",
                        connect_args={"check_same_thread": False})
    from app.models.db_model import Base

    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, expire_on_commit=False)

    import contextlib

    @contextlib.contextmanager
    def fake_db_session():
        db = factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    from app.services.geocompute import durable, reuse_index, run_evidence
    from app.services.geocompute.cluster import store as csm
    import app.services.jobs.submit as submit_mod
    import app.services.jobs.worker as worker_mod

    monkeypatch.setattr(run_evidence, "session_factory", factory)
    monkeypatch.setattr(reuse_index, "session_factory", factory)
    monkeypatch.setattr(csm, "session_factory", factory)
    # eager durable：jobs 提交桥/任务体会话工厂 → 同一临时库
    monkeypatch.setattr(submit_mod, "db_session", fake_db_session)
    monkeypatch.setattr(worker_mod, "db_session", fake_db_session,
                        raising=False)
    if hasattr(worker_mod, "_default_session_factory"):
        monkeypatch.setattr(worker_mod, "_default_session_factory",
                            lambda: fake_db_session())
    monkeypatch.setattr(durable, "session_factory",
                        lambda: fake_db_session())
    yield
    eng.dispose()


def _plan_scan_filter_partition(feats, target_tiles=2, halo_ratio=0.0,
                                category="filter", operation="eq"):
    return {
        "plan_id": "v8-part-e2e",
        "nodes": [
            {
                "node_id": "scan", "category": "source_scan",
                "operation": "inline", "inputs": [],
                "parameters": {"features": feats},
                "policy": "durable_job",
            },
            {
                "node_id": "filt", "category": category,
                "operation": operation, "inputs": ["scan"],
                "parameters": {"predicate": {"op": "eq",
                                             "field": "kind", "value": "a"}},
                "policy": "durable_job",
                "partition": {
                    "scheme": "vector_grid", "target_tiles": target_tiles,
                    "halo_ratio": halo_ratio, "min_rows_per_tile": 0,
                },
            },
        ],
        "budget": {"max_rows": 100000},
    }


class TestPartitionE2E:
    def test_vector_partition_roundtrip_no_halo(self, engine_env):
        """durable scan → durable filter(2 tiles) → 合并结果 == 全量过滤。"""
        from app.services.geocompute.executor import GeoExecutionEngine
        from app.services.geocompute.plan import ExecutionPlan, ExecutionRunStatus
        from app.services.geocompute import graph

        feats = _features_grid(count_per_cell=4)
        plan = ExecutionPlan.model_validate(
            _plan_scan_filter_partition(feats, target_tiles=2))
        graph.validate_plan(plan)
        engine = GeoExecutionEngine()
        run = engine.execute_plan(plan, session_id="s-part-1",
                                  owner_scope_override="u:part")
        assert run.status is ExecutionRunStatus.COMPLETED, run.summary_lines()
        ev = run.evidence["filt"]
        assert ev.status == "completed"
        assert ev.output_summary.get("partition_tiles") == 2
        # 无 halo：无复制 → 无去重移除，行数守恒
        assert ev.rows_emitted == len(feats)
        assert ev.output_summary.get("partition_dedup_removed", 0) == 0

    def test_vector_partition_halo_dedup(self, engine_env):
        """halo 邻域复制 → tile 输入超分配 → 合并去重恢复行数守恒。"""
        from app.services.geocompute.executor import GeoExecutionEngine
        from app.services.geocompute.plan import ExecutionPlan, ExecutionRunStatus
        from app.services.geocompute import graph

        # bbox 由要素推导（116..117 × 39..40）→ 2×2 网格内界 x=116.5/
        # y=39.5，halo = 0.2×0.5° = 0.1°。放在内界 ±0.0005° 的两个点
        # 落入相邻格的 halo → 被多个 tile 复制计算 → 合并去重吸收。
        feats = [
            {"type": "Feature", "geometry": {"type": "Point",
                                             "coordinates": [116.0, 39.0]},
             "properties": {"kind": "a", "i": 0}},
            {"type": "Feature", "geometry": {"type": "Point",
                                             "coordinates": [117.0, 40.0]},
             "properties": {"kind": "a", "i": 1}},
            {"type": "Feature", "geometry": {"type": "Point",
                                             "coordinates": [116.4995, 39.5]},
             "properties": {"kind": "a", "i": 2}},
            {"type": "Feature", "geometry": {"type": "Point",
                                             "coordinates": [116.5005, 39.5]},
             "properties": {"kind": "a", "i": 3}},
        ]
        plan = ExecutionPlan.model_validate(
            _plan_scan_filter_partition(feats, target_tiles=4, halo_ratio=0.2))
        graph.validate_plan(plan)
        engine = GeoExecutionEngine()
        run = engine.execute_plan(plan, session_id="s-part-2",
                                  owner_scope_override="u:part")
        assert run.status is ExecutionRunStatus.COMPLETED, run.summary_lines()
        ev = run.evidence["filt"]
        assert ev.status == "completed"
        assert ev.output_summary.get("partition_tiles") == 4
        # halo 复制被合并去重吸收：最终行数 == 输入行数
        assert ev.rows_emitted == len(feats)
        assert ev.output_summary.get("partition_dedup_removed", 0) > 0

    def test_partition_on_unsupported_category_fails_honest(self, engine_env):
        from app.services.geocompute.errors import (
            NodeExecutionError,
        )
        from app.services.geocompute.executor import GeoExecutionEngine
        from app.services.geocompute.plan import ExecutionPlan, ExecutionRunStatus
        from app.services.geocompute import graph

        feats = _features_grid(count_per_cell=1)
        plan_dict = _plan_scan_filter_partition(
            feats, category="aggregate", operation="count")
        plan_dict["nodes"][1]["partition"] = {
            "scheme": "vector_grid", "target_tiles": 2,
        }
        plan = ExecutionPlan.model_validate(plan_dict)
        graph.validate_plan(plan)
        engine = GeoExecutionEngine()
        run = engine.execute_plan(plan, session_id="s-part-3",
                                  owner_scope_override="u:part")
        assert run.status is ExecutionRunStatus.FAILED
        ev = run.evidence["filt"]
        assert ev.error_code == "PARTITION_UNSUPPORTED"

    def test_partition_semantic_fingerprint_changes(self):
        """分区方案进语义指纹（seam 语义影响输出 → 后代失效正确）。"""
        from app.services.geocompute.plan import (
            ExecutionNode,
            NodeCategory,
            PartitionSpec,
        )

        base = dict(node_id="n", category=NodeCategory.FILTER, operation="eq")
        n1 = ExecutionNode(**base)
        n2 = ExecutionNode(**base, partition=PartitionSpec(
            scheme="vector_grid", target_tiles=2))
        n3 = ExecutionNode(**base, partition=PartitionSpec(
            scheme="vector_grid", target_tiles=4))
        assert n1.semantic_fingerprint() != n2.semantic_fingerprint()
        assert n2.semantic_fingerprint() != n3.semantic_fingerprint()


def _make_test_raster(path, width=40, height=30):
    """写入小测试栅格（每像素值 = 行号*1000+列号，便于 mosaic 校验）。"""
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    data = np.arange(height * width, dtype=np.float32).reshape(height, width)
    profile = {
        "driver": "GTiff", "width": width, "height": height, "count": 1,
        "dtype": "float32", "crs": "EPSG:4326",
        "transform": from_origin(116.0, 40.0, 0.01, 0.01),
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data, 1)
    return path


def _scan_raster_plan(raster_path, tiles=2, halo_px=2, op="raster_calculator"):
    return {
        "plan_id": "v8-raster-part",
        "nodes": [
            {
                "node_id": "rsrc", "category": "source_scan",
                "operation": "inline_raster", "inputs": [],
                "parameters": {"raster_path": str(raster_path)},
            },
            {
                "node_id": "rcalc", "category": "raster_window_operation",
                "operation": op, "inputs": ["rsrc"],
                "parameters": {"expression": "A*2"},
                "policy": "durable_job",
                "partition": {"scheme": "raster_grid", "target_tiles": tiles,
                              "halo_px": halo_px},
            },
        ],
        "budget": {},
    }


@pytest.mark.heavy
class TestRasterPartitionE2E:
    """真实 raster tiling fan-out（rasterio 环境才运行；heavy marker）。"""

    def test_raster_partition_roundtrip_with_halo(self, engine_env, tmp_path):
        import numpy as np
        import rasterio
        from app.services.geocompute import graph
        from app.services.geocompute.executor import GeoExecutionEngine
        from app.services.geocompute.plan import ExecutionPlan, ExecutionRunStatus

        src = _make_test_raster(tmp_path / "src.tif")
        plan = ExecutionPlan.model_validate(
            _scan_raster_plan(src, tiles=2, halo_px=2))
        graph.validate_plan(plan)
        run = GeoExecutionEngine().execute_plan(
            plan, session_id="s-part-5", owner_scope_override="u:part")
        assert run.status is ExecutionRunStatus.COMPLETED, run.summary_lines()
        ev = run.evidence["rcalc"]
        assert ev.status == "completed"
        assert ev.output_summary.get("partition_tiles") == 2
        assert ev.output_summary.get("partition_scheme") == "raster_grid"
        # 合并输出 = 全幅 a*2，halo 裁除后无接缝伪影
        out = ev.output_summary and None
        merged_path = None
        for line in run.summary_lines():
            pass
        # 从 session ref 取合并输出路径（executor 输出在 outputs 内部 ——
        # 经证据 output_ref / metadata 不可携带路径；直接读 tile 源头同目录）
        candidates = list(tmp_path.glob("merged-*.tif"))
        assert candidates, "merged raster missing"
        with rasterio.open(candidates[0]) as ds:
            assert ds.width == 40 and ds.height == 30
            assert str(ds.crs) == "EPSG:4326"
            data = ds.read(1)
        expected = np.arange(30 * 40, dtype=np.float32).reshape(30, 40) * 2
        assert np.allclose(data, expected), "mosaic seam mismatch"


class TestRasterPartitionHonestFailures:
    def test_raster_partition_without_rasterio_fails_typed(
            self, engine_env, monkeypatch, tmp_path):
        """rasterio 缺席 → RASTERIO_UNAVAILABLE 类型化失败，绝不假装成功。
        （monkeypatch mosaic 模块的依赖探测，模拟无 rasterio 环境。）"""
        import app.lib.geo_analysis.raster_mosaic as mosaic_mod
        from app.services.geocompute import graph
        from app.services.geocompute.executor import GeoExecutionEngine
        from app.services.geocompute.plan import (
            ExecutionPlan,
            ExecutionRunStatus,
        )

        def _missing():
            raise mosaic_mod.RasterioUnavailableError("no rasterio (test)")

        monkeypatch.setattr(mosaic_mod, "_require_rasterio", _missing)

        try:
            src = _make_test_raster(tmp_path / "src.tif")
        except Exception:
            pytest.skip("rasterio unavailable — cannot fabricate source raster")
        plan = ExecutionPlan.model_validate(
            _scan_raster_plan(src, tiles=2, halo_px=2))
        graph.validate_plan(plan)
        engine = GeoExecutionEngine()
        run = engine.execute_plan(plan, session_id="s-part-4",
                                  owner_scope_override="u:part")
        assert run.status is ExecutionRunStatus.FAILED
        assert run.evidence["rcalc"].error_code == "RASTERIO_UNAVAILABLE"
