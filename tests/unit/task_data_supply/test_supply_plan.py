"""WP3 · 受预算多候选供给计划 oracle 测试。

验收矩阵（GOAL-20261126-02 WP3）：
- 确定性/幂等：同需求+同选择 → 同 plan_id 与稳定步骤序；重复请求不产生
  新计划（dedup）；候选版本变化 → 指纹变化（版本漂移不复用旧证明）；
- 已满足角色 → use_bound 零成本步骤，不进获取清单；
- FULL+资格合格候选 → 主获取步骤，携带对 ADS PlanRequest 的投影
  （dataset_key/bbox/time_range/columns/version）——不新建执行引擎；
- 覆盖不足/未核验 → 显式替代方案（缩小范围/保留缺口/换数据/上传），
  计划状态 requires_confirmation，绝不静默采样/填补/合并异质数据；
- display_only × 数值统计 → 步骤不可数值资格 + 替代（换数值级/上传）；
- 预算：估算超上限 → budget_status=exceeded + 替代方案（不自动降级）；
  用户显式接受后仍标记 acknowledged，不伪装成预算内；
- 选择可追溯：决策记录进计划与有界输出；未知角色选择 → 类型化拒绝；
- 步骤结果回写：partial/completed/cancelled 诚实分流，局部失败保留
  合法产物状态。
"""
import pytest

from app.services.task_data_supply.coverage import (
    CoverageCandidate,
    CoverageMatrix,
    evaluate_task_coverage,
)
from app.services.task_data_supply.requirement import (
    TaskScope,
    task_requirement,
)
from app.services.task_data_supply.supply_plan import (
    PLAN_ACTION_CLARIFY,
    PLAN_ACTION_USE_BOUND,
    SupplyPlanner,
    build_supply_plan,
)


# ── fixture ──────────────────────────────────────────────────────────────

def _square(x0, y0, x1, y1):
    return {"type": "Polygon", "coordinates": [[
        [x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


def _req(extra_demands=None):
    demands = [
        {"role": "subject", "geometry_kind": "point",
         "consumption_methods": ["numeric_stats"],
         "required_fields": ["name"]},
    ]
    if extra_demands:
        demands.extend(extra_demands)
    return task_requirement(
        task_scope=TaskScope(bbox=[104.0, 30.0, 105.0, 31.0],
                             time_range=["2024-01-01", "2024-12-31"]),
        demands=demands,
        provenance=[{"source": "user_message"}],
    )


def _cand(**kw):
    base = dict(
        candidate_id="src/pois", channel="data_fabric_catalog",
        serves_roles=("subject",),
        declared_bbox=[104.0, 30.0, 105.0, 31.0],
        temporal_coverage=["2020-01-01", "2025-12-31"],
        fields=("name", "category"), geometry_kind="point",
        role_class="vector_features",
        cost_hint={"rows": 5000, "bytes": 2_000_000, "latency_ms": 1500},
    )
    base.update(kw)
    return CoverageCandidate(**base)


def _matrix(requirement, candidates):
    return evaluate_task_coverage(requirement, candidates)


# ── 确定性与幂等 ─────────────────────────────────────────────────────────

class TestDeterminismAndIdempotency:
    def test_same_inputs_same_plan_id(self):
        req = _req()
        cands = [_cand(footprint_geometry=_square(104.0, 30.0, 105.0, 31.0))]
        p1 = build_supply_plan(req, _matrix(req, cands), cands)
        p2 = build_supply_plan(req, _matrix(req, cands), cands)
        assert p1.plan_id == p2.plan_id
        assert [s.role for s in p1.steps] == [s.role for s in p2.steps]

    def test_version_change_changes_fingerprint(self):
        req = _req()
        cands = [_cand(footprint_geometry=_square(104.0, 30.0, 105.0, 31.0))]
        p1 = build_supply_plan(req, _matrix(req, cands), cands)
        cands2 = [_cand(version="v2025.1",
                        footprint_geometry=_square(104.0, 30.0, 105.0, 31.0))]
        p2 = build_supply_plan(req, _matrix(req, cands2), cands2)
        assert p1.plan_id != p2.plan_id

    def test_user_choices_change_plan(self):
        req = _req()
        cands = [_cand(candidate_id="a/one",
                       footprint_geometry=_square(104.0, 30.0, 104.6, 31.0)),
                 _cand(candidate_id="b/two",
                       footprint_geometry=_square(104.0, 30.0, 105.0, 31.0))]
        p1 = build_supply_plan(req, _matrix(req, cands), cands)
        p2 = build_supply_plan(req, _matrix(req, cands), cands,
                               choices={"subject": "switch:b/two"})
        assert p1.plan_id != p2.plan_id
        step = p2.step("subject")
        assert step.candidate_id == "b/two"


# ── 步骤组成 ─────────────────────────────────────────────────────────────

class TestStepComposition:
    def test_satisfied_role_binds_without_acquisition(self):
        req = task_requirement(
            task_scope=TaskScope(place="成都"),
            demands=[{"role": "subject", "bound_ref": "ref:x/1",
                      "state": "satisfied"}],
            provenance=[{"source": "user_message"}])
        plan = build_supply_plan(req, CoverageMatrix(), [])
        step = plan.step("subject")
        assert step.action == PLAN_ACTION_USE_BOUND
        assert step.cost_estimate["bytes"] == 0

    def test_full_candidate_projects_to_ads_request(self):
        req = _req()
        cands = [_cand(footprint_geometry=_square(104.0, 30.0, 105.0, 31.0),
                       version="v7")]
        plan = build_supply_plan(req, _matrix(req, cands), cands)
        step = plan.step("subject")
        assert step.action == "acquire"
        assert step.ads_request["dataset_key"] == "src/pois"
        assert step.ads_request["bbox"] == [104.0, 30.0, 105.0, 31.0]
        assert step.ads_request["time_range"] == [
            "2024-01-01", "2024-12-31"]
        assert step.ads_request["columns"] == ["name"]
        assert step.ads_request["version"] == "v7"

    def test_declared_only_gets_disclosure_and_alternatives(self):
        req = _req()
        cands = [_cand()]  # 只有声明 bbox，无 footprint
        plan = build_supply_plan(req, _matrix(req, cands), cands)
        step = plan.step("subject")
        assert step.action == "acquire"
        assert any("未核验" in d for d in step.disclosures)
        assert plan.state == "requires_confirmation"
        kinds = {a["kind"] for a in step.alternatives}
        assert {"keep_gap", "shrink_scope"} <= kinds

    def test_missing_role_proposes_search_and_upload_not_silence(self):
        req = _req()
        plan = build_supply_plan(req, _matrix(req, []), [])
        step = plan.step("subject")
        assert step.action == PLAN_ACTION_CLARIFY
        kinds = {a["kind"] for a in step.alternatives}
        assert "search_more" in kinds
        assert "upload" in kinds
        assert plan.state == "requires_confirmation"

    def test_display_only_numeric_alternatives(self):
        req = _req()
        cands = [_cand(role_class="display_only",
                       footprint_geometry=_square(104.0, 30.0, 105.0, 31.0))]
        plan = build_supply_plan(req, _matrix(req, cands), cands)
        step = plan.step("subject")
        assert step.numeric_stats_qualified is False
        kinds = {a["kind"] for a in step.alternatives}
        assert "upload" in kinds


# ── 预算 ─────────────────────────────────────────────────────────────────

class TestBudget:
    def test_within_budget_ready(self):
        req = _req()
        cands = [_cand(footprint_geometry=_square(104.0, 30.0, 105.0, 31.0))]
        plan = build_supply_plan(req, _matrix(req, cands), cands,
                                 budget_max_bytes=10_000_000)
        assert plan.budget_status == "within"
        assert plan.state == "ready"

    def test_over_budget_exceeded_with_alternatives_no_auto_degrade(self):
        req = _req()
        cands = [_cand(footprint_geometry=_square(104.0, 30.0, 105.0, 31.0),
                       cost_hint={"rows": 9_000_000, "bytes": 900_000_000,
                                  "latency_ms": 60_000})]
        plan = build_supply_plan(req, _matrix(req, cands), cands,
                                 budget_max_bytes=10_000_000)
        assert plan.budget_status == "exceeded"
        assert plan.state == "requires_confirmation"
        assert any(a["kind"] == "keep_gap" for a in
                   plan.step("subject").alternatives)
        # 不自动抽样/缩域：主步骤仍为原候选原投影
        assert plan.step("subject").ads_request["sample_rate"] in (None, 1.0)

    def test_over_budget_user_acknowledged_recorded(self):
        req = _req()
        cands = [_cand(footprint_geometry=_square(104.0, 30.0, 105.0, 31.0),
                       cost_hint={"rows": 9_000_000, "bytes": 900_000_000,
                                  "latency_ms": 60_000})]
        plan = build_supply_plan(req, _matrix(req, cands), cands,
                                 budget_max_bytes=10_000_000,
                                 choices={"subject": "accept_exceed"})
        assert plan.budget_status == "exceeded"
        assert plan.budget_acknowledged is True
        assert plan.state == "ready"  # 用户显式接受 → 可批准执行


# ── 决策回写与类型化拒绝 ─────────────────────────────────────────────────

class TestDecisionsAndOutcomes:
    def test_unknown_choice_role_rejected(self):
        req = _req()
        cands = [_cand()]
        with pytest.raises(ValueError, match="SUPPLY_CHOICE_ROLE_UNKNOWN"):
            build_supply_plan(req, _matrix(req, cands), cands,
                              choices={"volcano": "keep_gap"})

    def test_unknown_choice_kind_rejected(self):
        req = _req()
        cands = [_cand()]
        with pytest.raises(ValueError, match="SUPPLY_CHOICE_KIND_UNKNOWN"):
            build_supply_plan(req, _matrix(req, cands), cands,
                              choices={"subject": "wave_a_wand"})

    def test_outcome_recording_partial_honest(self):
        req = _req()
        cands = [_cand(footprint_geometry=_square(104.0, 30.0, 105.0, 31.0))]
        plan = build_supply_plan(req, _matrix(req, cands), cands)
        planner = SupplyPlanner()
        planner.record_outcome(plan, "subject", outcome="partial",
                               receipt_ref="ref:fabric-parquet/part1",
                               rows_written=1200, disclosure="南郊瓦片超时")
        assert plan.state == "partial"
        step = plan.step("subject")
        assert step.outcome == "partial"
        assert step.receipt_ref == "ref:fabric-parquet/part1"
        assert any("超时" in d for d in step.disclosures)

    def test_complete_outcome_ready_to_settle(self):
        req = _req()
        cands = [_cand(footprint_geometry=_square(104.0, 30.0, 105.0, 31.0))]
        plan = build_supply_plan(req, _matrix(req, cands), cands)
        planner = SupplyPlanner()
        planner.record_outcome(plan, "subject", outcome="completed",
                               receipt_ref="ref:fabric-parquet/full",
                               rows_written=5000)
        assert plan.state == "completed"

    def test_cancelled_keeps_partial_products_visible(self):
        req = _req()
        cands = [_cand(footprint_geometry=_square(104.0, 30.0, 105.0, 31.0))]
        plan = build_supply_plan(req, _matrix(req, cands), cands)
        planner = SupplyPlanner()
        planner.record_outcome(plan, "subject", outcome="partial",
                               receipt_ref="ref:fabric-parquet/part1",
                               rows_written=800)
        planner.record_cancel(plan, reason="用户取消")
        assert plan.state == "cancelled"
        assert plan.step("subject").receipt_ref  # 已产物保留可见

    def test_bounded_output_shape(self):
        req = _req(extra_demands=[
            {"role": "boundary", "geometry_kind": "polygon",
             "consumption_methods": ["geometry_skeleton"]}])
        cands = [_cand(footprint_geometry=_square(104.0, 30.0, 105.0, 31.0))]
        plan = build_supply_plan(req, _matrix(req, cands), cands)
        bounded = plan.to_bounded_dict()
        assert len(bounded["steps"]) == 2
        assert bounded["plan_id"].startswith("sup-")
        assert bounded["state"] in ("ready", "requires_confirmation")
