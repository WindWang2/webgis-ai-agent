"""GeoComputeAgent 空间计算专家领域方法（ADR-0188 D4/D6）。

Celery First：编排器进程内只做计划构造 / 校验 / 提交 / 提货券组装，
零算子执行（submitter 注入桩）。防御逻辑：``infer_utm_crs`` 由 bbox
中心经度推导 UTM zone（无证据 → None，绝不猜 EPSG）；``estimate_volume``
三级降级估算；``build_result_ref`` 8KB 序列化硬闸（截 metadata →
typed 诚实失败）。
"""
from __future__ import annotations

import json
import math
from typing import Any, Optional

import pytest

from app.services.agent_swarm.contracts import (
    SERIALIZATION_BUDGET_BYTES,
    ComputeRequest,
    SpatialProfileTooLargeError,
    VolumeEstimate,
)
from app.services.agent_swarm.geocompute import (
    AUTO_UTM_AREA_KM2_THRESHOLD,
    AUTO_UTM_ROWS_THRESHOLD,
    DENSITY_ROWS_PER_KM2,
    GeoComputeAgent,
    _bbox_area_km2,
)


class RecordingSubmitter:
    """durable job 提交替身（记录参数，返回脚本化 job_id/task_id）。"""

    def __init__(self, *, job_id: Optional[int] = 77, task_id: Optional[str] = "celery-1"):
        self.job_id = job_id
        self.task_id = task_id
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        out: dict[str, Any] = {}
        if self.job_id is not None:
            out["job_id"] = self.job_id
        if self.task_id is not None:
            out["task_id"] = self.task_id
        return out


def _agent(submitter: Any = None, **base_kwargs: Any) -> GeoComputeAgent:
    return GeoComputeAgent(submitter=submitter or RecordingSubmitter(), **base_kwargs)


class TestInferUtmCrs:
    """bbox 中心 → UTM WGS84 CRS（北 326xx / 南 327xx）。"""

    def test_northern_hemisphere_zone(self):
        # 北京附近（东经 116）：zone = (116+180)//6 + 1 = 50 → EPSG:32650
        assert GeoComputeAgent.infer_utm_crs([116.0, 39.0, 117.0, 40.0]) == "EPSG:32650"

    def test_southern_hemisphere_zone(self):
        # 悉尼附近（东经 151，南纬 -34）→ EPSG:32756
        assert GeoComputeAgent.infer_utm_crs([151.0, -34.0, 151.5, -33.5]) == "EPSG:32756"

    def test_zone_clamped_at_sixty(self):
        # 东经 179 → zone 60（上限钳制）
        assert GeoComputeAgent.infer_utm_crs([178.0, 10.0, 179.9, 11.0]) == "EPSG:32660"

    def test_zone_clamped_at_one(self):
        # 西经 179 → zone 1（下限钳制）
        assert GeoComputeAgent.infer_utm_crs([-179.0, 10.0, -178.0, 11.0]) == "EPSG:32601"

    def test_equator_is_northern(self):
        # 中心纬度 0.5°（北）→ 326；zone = (10+180)//6 + 1 = 32
        assert GeoComputeAgent.infer_utm_crs([10.0, 0.0, 11.0, 1.0]) == "EPSG:32632"

    def test_none_bbox_returns_none(self):
        assert GeoComputeAgent.infer_utm_crs(None) is None

    def test_malformed_bbox_returns_none(self):
        assert GeoComputeAgent.infer_utm_crs([1.0, 2.0, 3.0]) is None
        assert GeoComputeAgent.infer_utm_crs([]) is None

    def test_non_numeric_bbox_returns_none(self):
        assert GeoComputeAgent.infer_utm_crs(["a", "b", "c", "d"]) is None

    def test_non_finite_bbox_returns_none(self):
        assert GeoComputeAgent.infer_utm_crs([0.0, 0.0, math.inf, 1.0]) is None


class TestEstimateVolume:
    """三级降级估算：cost_hint → rows_hint → bbox 密度 → unknown。"""

    def test_cost_hint_wins(self):
        class _Descriptor:
            class cost_hint:
                rows = 1234

        estimate = _agent().estimate_volume(
            descriptor=_Descriptor(), bbox=[0, 0, 1, 1], rows_hint=99
        )
        assert estimate.method == "cost_hint"
        assert estimate.rows == 1234

    def test_rows_hint_second(self):
        estimate = _agent().estimate_volume(rows_hint=500, bbox=[0, 0, 1, 1])
        assert estimate.method == "rows_hint"
        assert estimate.rows == 500

    def test_bbox_density_third(self):
        bbox = [0.0, 0.0, 1.0, 1.0]
        area = _bbox_area_km2(bbox)
        assert area is not None
        estimate = _agent().estimate_volume(bbox=bbox)
        assert estimate.method == "bbox_density"
        assert estimate.rows == int(area * DENSITY_ROWS_PER_KM2)
        assert estimate.area_km2 == pytest.approx(area)

    def test_unknown_when_no_evidence(self):
        estimate = _agent().estimate_volume()
        assert estimate.method == "unknown"
        assert estimate.rows is None
        assert estimate.area_km2 is None

    def test_descriptor_without_cost_hint_falls_through(self):
        class _Descriptor:
            cost_hint = None

        estimate = _agent().estimate_volume(descriptor=_Descriptor(), rows_hint=7)
        assert estimate.method == "rows_hint"
        assert estimate.rows == 7

    def test_zero_cost_rows_falls_through(self):
        class _Descriptor:
            class cost_hint:
                rows = 0

        estimate = _agent().estimate_volume(descriptor=_Descriptor(), rows_hint=7)
        assert estimate.method == "rows_hint"

    def test_bbox_area_hand_computed(self):
        # 1°×1° 赤道附近：111.32 * cos(0.5°) * 110.57 ≈ 12308.18 km²
        #（实现口径：lon_span*111.32*cos(mid_lat)*lat_span*110.57）
        area = _bbox_area_km2([0.0, 0.0, 1.0, 1.0])
        expected = 111.32 * math.cos(math.radians(0.5)) * 110.57
        assert area == pytest.approx(expected, rel=1e-6)
        assert area == pytest.approx(12308.18, rel=1e-4)

    def test_bbox_area_rejects_degenerate(self):
        assert _bbox_area_km2([0.0, 0.0, 0.0, 1.0]) is None  # 零跨度
        assert _bbox_area_km2([1.0, 1.0, 0.0, 0.0]) is None  # 反跨越度
        assert _bbox_area_km2([0.0, 0.0, 1.0]) is None
        assert _bbox_area_km2(["x", 0.0, 1.0, 1.0]) is None


class TestPlanComputation:
    """意图 → 合法 durable 执行图 → 提交 → 提货券（零算子执行）。"""

    def test_small_request_submits_single_node(self):
        submitter = RecordingSubmitter()
        submission = _agent(submitter).plan_computation(
            ComputeRequest(operation="buffer_analysis", dataset_ref="ref:roads")
        )
        assert submission.node_count == 1
        assert submission.job_id == 77
        assert submission.celery_task_id == "celery-1"
        assert submission.ref.status == "submitted"
        assert submission.ref.ref_id is not None
        assert submission.ref.ref_id.startswith("gc-")
        assert submission.plan_built is not None
        assert submission.plan_json["plan_id"] == submission.plan_id

    def test_submitter_receives_durable_task_kwargs(self):
        submitter = RecordingSubmitter()
        submission = _agent(submitter).plan_computation(
            ComputeRequest(
                operation="clip", dataset_ref="ref:parcels", session_id="sess-1"
            )
        )
        call = submitter.calls[0]
        assert call["task_type"] == "geocompute_specialist"
        assert call["queue"] == "geocompute"
        assert call["session_id"] == "sess-1"
        task_kwargs = call["task_kwargs"]
        assert task_kwargs["session_id"] == "sess-1"
        assert task_kwargs["input_refs"] == {"input": "ref:parcels"}
        assert task_kwargs["run_id"] == f"run-{submission.plan_id}"
        assert task_kwargs["node"]["node_id"] == "compute_clip"

    def test_unsupported_operation_fails_loud(self):
        with pytest.raises(ValueError, match="unsupported operation"):
            _agent().plan_computation(
                ComputeRequest(operation="warp_drive", dataset_ref="ref:x")
            )

    def test_large_bbox_injects_auto_utm_defense(self):
        # 10°×10° 中纬度 ≈ 12 万 km² > 5000 km² 阈值
        submission = _agent().plan_computation(
            ComputeRequest(operation="overlay_analysis", dataset_ref="ref:x", bbox=[100, 30, 110, 40])
        )
        assert submission.node_count == 2
        assert submission.ref.crs_defense == "auto_utm"
        assert submission.ref.crs is not None and submission.ref.crs.startswith("EPSG:326")
        assert "auto utm defense" in " ".join(submission.ref.notes)

    def test_small_bbox_no_defense(self):
        submission = _agent().plan_computation(
            ComputeRequest(operation="clip", dataset_ref="ref:x", bbox=[100, 30, 100.1, 30.1])
        )
        assert submission.node_count == 1
        assert submission.ref.crs_defense is None

    def test_large_rows_hint_triggers_defense(self):
        submission = _agent().plan_computation(
            ComputeRequest(
                operation="aggregate",
                dataset_ref="ref:x",
                rows_hint=AUTO_UTM_ROWS_THRESHOLD + 1,
                bbox=[100, 30, 100.1, 30.1],
            )
        )
        assert submission.node_count == 2
        assert submission.ref.crs_defense == "auto_utm"

    def test_no_bbox_means_no_crs_and_no_defense(self):
        submission = _agent().plan_computation(
            ComputeRequest(operation="filter", dataset_ref="ref:x")
        )
        assert submission.ref.crs is None
        assert submission.ref.crs_defense is None
        assert submission.node_count == 1

    def test_summary_discloses_rows_and_nodes(self):
        submission = _agent().plan_computation(
            ComputeRequest(operation="h3_binning", dataset_ref="ref:pts")
        )
        assert "h3_binning on ref:pts" in submission.ref.summary
        assert "nodes=1" in submission.ref.summary

    def test_missing_job_ids_leave_ref_fields_none(self):
        submitter = RecordingSubmitter(job_id=None, task_id=None)
        submission = _agent(submitter).plan_computation(
            ComputeRequest(operation="clip", dataset_ref="ref:x")
        )
        assert submission.job_id is None
        assert submission.celery_task_id is None
        assert submission.ref.job_id is None

    def test_dict_request_accepted(self):
        submission = _agent().plan_computation(
            {"operation": "clip", "dataset_ref": "ref:x"}
        )
        assert submission.node_count == 1

    def test_deadline_flows_into_plan_budget(self):
        submission = _agent().plan_computation(
            ComputeRequest(operation="clip", dataset_ref="ref:x", deadline_s=42.0)
        )
        assert submission.plan_built.budget.deadline_s == 42.0

    def test_default_deadline_is_300(self):
        submission = _agent().plan_computation(
            ComputeRequest(operation="clip", dataset_ref="ref:x")
        )
        assert submission.plan_built.budget.deadline_s == 300.0

    def test_area_threshold_boundary(self):
        assert AUTO_UTM_AREA_KM2_THRESHOLD == 5000.0
        assert AUTO_UTM_ROWS_THRESHOLD == 200_000


class TestBuildResultRefBudget:
    """8KB 硬闸：metadata 逐键丢弃（truncated 诚实标注）→ 仍超限 typed 失败。"""

    def _ref(self, **kw):
        agent = _agent()
        kw.setdefault("plan_id", "gc-1")
        kw.setdefault("plan_digest", "digest-1")
        return agent.build_result_ref(**kw)

    def test_small_ref_untouched(self):
        ref = self._ref(summary="small")
        assert ref.truncated is False
        assert ref.metadata == {}
        assert len(ref.to_json_bytes()) < SERIALIZATION_BUDGET_BYTES

    def test_largest_metadata_dropped_first(self):
        big = "x" * 8000
        small = "y" * 10
        ref = self._ref(summary="s" * 500, metadata={"big": big, "small": small})
        assert ref.truncated is True
        assert "big" not in ref.metadata  # 最大键先丢
        assert ref.metadata == {"small": small}  # 小键保留
        assert len(ref.to_json_bytes()) < SERIALIZATION_BUDGET_BYTES

    def test_all_metadata_dropped_when_needed(self):
        ref = self._ref(
            summary="s" * 600,
            metadata={f"k{i}": "z" * 3000 for i in range(5)},
        )
        assert ref.truncated is True
        # 逐键丢弃至收敛即停（保留仍放得下的键，不做过度裁剪）
        assert 0 < len(ref.metadata) < 5
        assert len(ref.to_json_bytes()) < SERIALIZATION_BUDGET_BYTES

    def test_oversized_untrimmable_field_raises_typed(self):
        """ref_id 等调用方字段不受内部截断保护：metadata 耗尽仍超限 →
        typed 诚实失败（绝不静默裁剪载荷字段）。"""
        with pytest.raises(SpatialProfileTooLargeError, match="exceeds 8192B budget"):
            self._ref(ref_id="r" * 9000, metadata={"a": "x" * 100})

    def test_notes_bounded_to_eight(self):
        ref = self._ref(notes=tuple(f"n{i}" for i in range(20)))
        assert len(ref.notes) == 8
        assert ref.notes[0] == "n0"

    def test_summary_clipped_to_600(self):
        ref = self._ref(summary="s" * 900)
        assert len(ref.summary) == 600

    def test_volume_estimate_roundtrip(self):
        volume = VolumeEstimate(rows=100, area_km2=2.5, method="cost_hint")
        ref = self._ref(volume=volume, rows=100)
        assert ref.volume_estimate.rows == 100
        assert ref.rows == 100

    def test_json_bytes_is_valid_json(self):
        ref = self._ref(summary="ok", metadata={"k": "v"})
        payload = json.loads(ref.to_json_bytes().decode("utf-8"))
        assert payload["plan_id"] == "gc-1"
        assert payload["metadata"] == {"k": "v"}


class TestSpecialistIdentity:
    def test_name_and_role(self):
        agent = _agent()
        assert agent.name == "geocompute"
        assert agent.role_name == "geocompute"

    def test_allowlist_covers_execution_graph_and_operators(self):
        allowlist = GeoComputeAgent.TOOL_ALLOWLIST
        assert "execute_execution_plan" in allowlist
        assert "buffer_analysis" in allowlist
        assert "network_shortest_path" in allowlist
        # 与数据接入 / 制图审计面不相交
        assert "connect_data_source" not in allowlist
        assert "create_thematic_map" not in allowlist

    def test_prompt_declares_celery_first(self):
        assert "Celery First" in GeoComputeAgent.SPECIALIST_PROMPT
        assert "绝不内联" in GeoComputeAgent.SPECIALIST_PROMPT
