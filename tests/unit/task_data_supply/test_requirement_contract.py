"""WP1 · 任务数据需求合同测试（Task Data Requirement Contract，TDR-v1）。

验收矩阵（GOAL-20261126-02 WP1）：
- 确定性：同输入 → 同 requirement_id；place 归一（大小写/空白）；零 LLM；
- 范围：bbox 接受跨日期线（minx>maxx 约定）并显式标注 crosses_dateline；
  坏 bbox（NaN/形状错）→ 类型化拒绝，不静默丢弃；
- 时间：time_range 有序 ISO；倒序/坏格式 → 类型化拒绝；不声明 = 合法（unknown）；
- 封闭词表：未知 role / 未知消费方法 → 类型化拒绝（附合法词表）；
- workflow_v4 投影：bound→satisfied(+bound_ref)、external/unresolved→
  not_searched、degraded→degraded（披露保留）——不虚构「已满足」；
- missing obligations：required 且未满足的角色逐条列出（含 reason_code）；
- 有界输出：geometry 以摘要（顶点数/bbox/跨线旗标）呈现，不泄漏全量坐标；
- 可追溯：provenance、confirmed_by_user/confirmation_ref 进入有界输出；
  范围变化 → requirement fingerprint 变化（范围切换可追溯）。
"""
import pytest

from app.services.task_data_supply.requirement import (
    CONSUMPTION_METHODS,
    DEMAND_STATES,
    RoleDemand,
    TaskScope,
    requirement_from_role_demands,
    requirement_from_workflow_v4,
    task_requirement,
)


# ── 范围声明 ─────────────────────────────────────────────────────────────

class TestTaskScope:
    def test_place_normalization_is_deterministic(self):
        a = TaskScope(place="成都")
        b = TaskScope(place="  成都 ")
        assert a.normalized() == b.normalized()

    def test_dateline_bbox_accepted_and_flagged(self):
        scope = TaskScope(place="斐济", bbox=[170.0, -20.0, -170.0, 20.0])
        assert scope.crosses_dateline is True
        assert scope.normalized()["bbox"] == [170.0, -20.0, -170.0, 20.0]

    def test_bad_bbox_typed_rejection(self):
        with pytest.raises(ValueError, match="REQUIREMENT_SCOPE_INVALID"):
            TaskScope(bbox=[104.0, float("nan"), 107.0, 31.0])
        with pytest.raises(ValueError, match="REQUIREMENT_SCOPE_INVALID"):
            TaskScope(bbox=[104.0, 30.0])  # 形状错

    def test_time_range_ordering_enforced(self):
        TaskScope(time_range=["2020-01-01", "2021-06-30"])
        with pytest.raises(ValueError, match="REQUIREMENT_TIME_INVALID"):
            TaskScope(time_range=["2021-06-30", "2020-01-01"])
        with pytest.raises(ValueError, match="REQUIREMENT_TIME_INVALID"):
            TaskScope(time_range=["not-a-date", "2021-06-30"])

    def test_empty_scope_is_legal_honest_declaration(self):
        scope = TaskScope()
        assert scope.normalized() == {}

    def test_geometry_summary_not_full_coords(self):
        ring = [[104.0 + i * 0.001, 30.5 + i * 0.001] for i in range(50)]
        ring.append(ring[0])
        scope = TaskScope(geometry={"type": "Polygon", "coordinates": [ring]})
        bounded = scope.to_bounded_dict()
        assert bounded["geometry_summary"]["vertex_count"] == 51
        assert "coordinates" not in bounded["geometry_summary"]
        assert bounded["geometry_summary"]["bbox"]


# ── 角色需求与封闭词表 ───────────────────────────────────────────────────

class TestRoleDemand:
    def test_unknown_role_typed_rejection_lists_valid(self):
        with pytest.raises(ValueError, match="REQUIREMENT_ROLE_UNKNOWN") as ei:
            RoleDemand(role="school_pois")
        assert "subject" in str(ei.value)

    def test_unknown_consumption_method_rejected(self):
        with pytest.raises(ValueError, match="REQUIREMENT_CONSUMPTION_UNKNOWN"):
            RoleDemand(role="subject", consumption_methods=["fly_to_mars"])
        d = RoleDemand(role="subject", consumption_methods=["numeric_stats"])
        assert list(d.consumption_methods) == ["numeric_stats"]

    def test_vocabularies_are_closed_tuples(self):
        assert isinstance(DEMAND_STATES, tuple)
        assert isinstance(CONSUMPTION_METHODS, tuple)
        assert "not_searched" in DEMAND_STATES
        assert "unknown" in DEMAND_STATES
        assert "numeric_stats" in CONSUMPTION_METHODS
        assert "map_display" in CONSUMPTION_METHODS


# ── 合同构造与确定性 ─────────────────────────────────────────────────────

def _chengdu_subject_demand(**kw):
    base = dict(
        role="subject", geometry_kind="point",
        consumption_methods=["numeric_stats"],
        required_fields=["name"],
        admin_level="district",
    )
    base.update(kw)
    return base


class TestTaskDataRequirement:
    def test_deterministic_requirement_id(self):
        r1 = task_requirement(
            task_scope=TaskScope(place="成都"),
            demands=[_chengdu_subject_demand()],
            provenance=[{"source": "user_message"}],
        )
        r2 = task_requirement(
            task_scope=TaskScope(place=" 成都"),
            demands=[_chengdu_subject_demand()],
            provenance=[{"source": "user_message"}],
        )
        assert r1.requirement_id == r2.requirement_id

    def test_scope_change_changes_fingerprint(self):
        r1 = task_requirement(
            task_scope=TaskScope(place="成都"),
            demands=[_chengdu_subject_demand()],
            provenance=[{"source": "user_message"}],
        )
        r2 = task_requirement(
            task_scope=TaskScope(place="北京"),
            demands=[_chengdu_subject_demand()],
            provenance=[{"source": "user_message"}],
        )
        assert r1.requirement_id != r2.requirement_id

    def test_confirmation_traceability(self):
        req = task_requirement(
            task_scope=TaskScope(place="成都"),
            demands=[_chengdu_subject_demand()],
            provenance=[{"source": "user_message"}],
            confirmed_by_user=True,
            confirmation_ref="rev:abc123",
        )
        bounded = req.to_bounded_dict()
        assert bounded["confirmed_by_user"] is True
        assert bounded["confirmation_ref"] == "rev:abc123"

    def test_bounded_dict_has_no_geometry_coordinates(self):
        ring = [[104.0 + i * 0.001, 30.5] for i in range(10)]
        ring.append(ring[0])
        req = task_requirement(
            task_scope=TaskScope(place="成都",
                                 geometry={"type": "Polygon",
                                           "coordinates": [ring]}),
            demands=[_chengdu_subject_demand()],
            provenance=[{"source": "user_message"}],
        )
        blob = str(req.to_bounded_dict())
        assert '"coordinates"' not in blob  # 全量坐标树不进有界输出
        assert "vertex_count" in blob  # 摘要可见（顶点数/bbox/跨线旗标）


# ── workflow_v4 投影 ─────────────────────────────────────────────────────

class TestWorkflowV4Projection:
    def _resolutions(self):
        return [
            {"role": "subject", "status": "bound", "bound_ref":
             "ref:fabric-parquet/pois", "required": True},
            {"role": "boundary", "status": "external", "required": True,
             "acquisition": "data_fabric"},
            {"role": "denominator", "status": "unresolved", "required": True},
            {"role": "reference", "status": "degraded", "required": True,
             "disclosure": "参考底图缺失已按策略降级"},
        ]

    def test_projection_states_honest(self):
        req = requirement_from_workflow_v4(
            self._resolutions(),
            task_scope=TaskScope(place="成都"),
        )
        states = {i.role: i.state for i in req.items}
        assert states["subject"] == "satisfied"
        assert states["boundary"] == "not_searched"   # external ≠ 已找到
        assert states["denominator"] == "not_searched"
        assert states["reference"] == "degraded"
        item = req.item("subject")
        assert item.bound_ref == "ref:fabric-parquet/pois"

    def test_degraded_disclosure_preserved(self):
        req = requirement_from_workflow_v4(
            self._resolutions(), task_scope=TaskScope(place="成都"))
        item = req.item("reference")
        assert "降级" in item.disclosure

    def test_missing_obligations(self):
        req = requirement_from_workflow_v4(
            self._resolutions(), task_scope=TaskScope(place="成都"))
        missing = req.missing_obligations()
        roles = {m["role"] for m in missing}
        assert roles == {"boundary", "denominator", "reference"}
        by_role = {m["role"]: m for m in missing}
        assert by_role["boundary"]["reason_code"].startswith(
            "SUPPLY_DEMAND_")
        # satisfied 不在 missing 里；degraded 带披露旗标
        assert by_role["reference"]["degraded"] is True
        assert "subject" not in roles

    def test_optional_unsatisfied_role_not_obligation(self):
        req = requirement_from_workflow_v4(
            [{"role": "elevation", "status": "unresolved",
              "required": False}],
            task_scope=TaskScope(place="成都"),
        )
        assert req.missing_obligations() == []
        assert req.item("elevation").state == "not_searched"


# ── 通用角色需求投影 ─────────────────────────────────────────────────────

class TestRoleDemandsProjection:
    def test_plain_demands_start_not_searched(self):
        req = requirement_from_role_demands(
            [_chengdu_subject_demand(),
             _chengdu_subject_demand(role="boundary",
                                     geometry_kind="polygon",
                                     consumption_methods=["geometry_skeleton"],
                                     admin_level="district")],
            task_scope=TaskScope(place="成都", time_range=[
                "2024-01-01", "2024-12-31"]),
            provenance=[{"source": "methodology",
                         "family": "point_pattern_analysis"}],
        )
        assert all(i.state == "not_searched" for i in req.items)
        assert req.task_scope.time_range == ["2024-01-01", "2024-12-31"]
        assert req.provenance[0]["family"] == "point_pattern_analysis"

    def test_duplicate_roles_merged_with_provenance_list(self):
        req = requirement_from_role_demands(
            [_chengdu_subject_demand(),
             _chengdu_subject_demand(required_fields=["name", "type"])],
            task_scope=TaskScope(place="成都"),
        )
        assert len(req.items) == 1
        item = req.items[0]
        assert set(item.required_fields) == {"name", "type"}

    def test_time_and_admin_level_in_bounded_output(self):
        req = requirement_from_role_demands(
            [_chengdu_subject_demand()],
            task_scope=TaskScope(place="成都", time_range=[
                "2024-01-01", "2024-12-31"]),
        )
        item = req.item("subject")
        assert item.to_bounded_dict()["admin_level"] == "district"
        assert req.to_bounded_dict()["task_scope"]["time_range"] == [
            "2024-01-01", "2024-12-31"]
