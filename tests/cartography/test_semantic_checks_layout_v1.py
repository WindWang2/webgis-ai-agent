"""ADR-0283 D4 · 版式/标签确定性发现词表（semantic_checks_layout）契约.

锁定：
- 词表 closed（LAYOUT_COLLISION_UNRESOLVED / KEY_COMPONENT_SUPPRESSED /
  OVERFLOW_SUPPRESSED / SAFE_AREA_VIOLATION / LABEL_SUPPRESSED_RATIO）；
- 从 LayoutSolutionV5（conflicts/constraint_findings/suppressed）确定性推导；
- 标注抑制比阈值梯（≥0.10 warning / ≥0.25 error）；stats 缺席 =
  not_evaluated（fail-closed，不冒充 pass）；
- safe area 像素几何违规 → warning + 证据；
- fix_hint 路由仅到既有修复动作词表（presentation-only，无新执行器）；
- selfheal_actions 注册新动作（trigger 词汇对齐），登记面 ≥5 不回退。
"""
from __future__ import annotations

import pytest

from app.lib.cartography.layout_constraint_spec import (
    ComponentLayoutConstraint,
    LayoutConstraintDocument,
)
from app.lib.cartography.layout_solver import (
    ConstraintFinding,
    LayoutConflict,
    LayoutPlacementV3,
    LayoutSolutionV5,
    solve_layout_v5,
)
from app.lib.cartography.selfheal_actions import SELFHEAL_ACTIONS, actions_by_id
from app.lib.cartography.semantic_checks_layout import (
    LAYOUT_FINDING_CODES,
    evaluate_layout_findings,
)

pytestmark = pytest.mark.cartography


def _solution(
    *,
    conflicts=(),
    findings=(),
    suppressed=(),
) -> LayoutSolutionV5:
    return LayoutSolutionV5(
        placements=[
            LayoutPlacementV3(id="t", type="title", zone="top-center"),
        ],
        suppressed=list(suppressed),
        conflicts=list(conflicts),
        constraint_findings=list(findings),
    )


# ── 1. 词表与推导 ────────────────────────────────────────────────────────


def test_finding_codes_closed_vocab():
    assert LAYOUT_FINDING_CODES == frozenset({
        "LAYOUT_COLLISION_UNRESOLVED",
        "KEY_COMPONENT_SUPPRESSED",
        "OVERFLOW_SUPPRESSED",
        "SAFE_AREA_VIOLATION",
        "LABEL_SUPPRESSED_RATIO",
    })


def test_conflict_becomes_layout_collision_unresolved():
    from app.lib.cartography.component_composer import REPAIR_ACTIONS
    sol = _solution(conflicts=[LayoutConflict(
        component_id="chart", conflict_type="zone_exhausted",
        detail_zh="x")])
    out = evaluate_layout_findings(sol)
    codes = {f.check for f in out}
    assert "LAYOUT_COLLISION_UNRESOLVED" in codes
    f = next(f for f in out if f.check == "LAYOUT_COLLISION_UNRESOLVED")
    assert f.severity == "error"
    assert f.evidence_class == "deterministic"
    assert f.repairability != "not_repairable"
    assert f.suggested_fix is not None
    assert f.suggested_fix["action"] in set(REPAIR_ACTIONS) | set(actions_by_id())


def test_key_component_suppressed_is_error():
    sol = _solution(findings=[ConstraintFinding(
        code="key_component_suppressed", component_id="method",
        detail="must_fit violated")])
    out = evaluate_layout_findings(sol)
    f = next(f for f in out if f.check == "KEY_COMPONENT_SUPPRESSED")
    assert f.severity == "error"
    assert f.evidence["component_id"] == "method"


def test_suppress_policy_disclosure_is_warning():
    sol = _solution(findings=[ConstraintFinding(
        code="overflow_suppressed", component_id="unc",
        detail="optional removed under suppress policy")])
    out = evaluate_layout_findings(sol)
    f = next(f for f in out if f.check == "OVERFLOW_SUPPRESSED")
    assert f.severity == "warning"


def test_clean_solution_yields_no_layout_findings():
    out = evaluate_layout_findings(_solution())
    assert [f.check for f in out if f.check != "LABEL_SUPPRESSED_RATIO"] == []


# ── 2. 标注抑制比阈值梯 ──────────────────────────────────────────────────


@pytest.mark.parametrize("suppressed,total,sev", [
    (0, 100, None),          # 无抑制 → 无发现
    (5, 100, None),          # 0.05 < 0.10 → 无发现
    (10, 100, "warning"),    # = 0.10 → warning
    (25, 100, "error"),      # = 0.25 → error
    (80, 100, "error"),      # 0.80 → error
])
def test_label_suppressed_ratio_ladder(suppressed, total, sev):
    sol = _solution()
    out = evaluate_layout_findings(
        sol, label_stats={"total": total, "placed": total - suppressed,
                          "suppressed": suppressed})
    matches = [f for f in out if f.check == "LABEL_SUPPRESSED_RATIO"]
    if sev is None:
        assert matches == []
    else:
        assert len(matches) == 1
        assert matches[0].severity == sev
        assert matches[0].evidence["ratio"] == pytest.approx(
            suppressed / total)


def test_label_stats_absent_is_not_evaluated():
    out = evaluate_layout_findings(_solution())  # 不传 label_stats
    f = next(f for f in out if f.check == "LABEL_SUPPRESSED_RATIO")
    assert f.evaluated is False
    assert f.status == "not_evaluated"


def test_label_stats_zero_total_is_not_evaluated():
    out = evaluate_layout_findings(
        _solution(), label_stats={"total": 0, "placed": 0, "suppressed": 0})
    f = next(f for f in out if f.check == "LABEL_SUPPRESSED_RATIO")
    assert f.evaluated is False


# ── 3. safe area 几何 ────────────────────────────────────────────────────


def test_safe_area_violation_detected():
    sol = _solution()
    rects = {
        "legend": {"x": 2, "y": 700, "width": 300, "height": 250},
    }
    safe_px = {"top": 40, "right": 40, "bottom": 40, "left": 40}
    out = evaluate_layout_findings(
        sol, component_rects_px=rects, canvas_px={"width": 1200, "height": 900},
        safe_area_px=safe_px)
    f = next(f for f in out if f.check == "SAFE_AREA_VIOLATION")
    assert f.severity == "warning"
    assert f.evidence["component_id"] == "legend"
    assert f.evidence["violations"]  # 具体越界边列表


def test_safe_area_clean_when_inside():
    sol = _solution()
    rects = {"legend": {"x": 60, "y": 600, "width": 300, "height": 250}}
    out = evaluate_layout_findings(
        sol, component_rects_px=rects, canvas_px={"width": 1200, "height": 900},
        safe_area_px={"top": 40, "right": 40, "bottom": 40, "left": 40})
    assert not [f for f in out if f.check == "SAFE_AREA_VIOLATION"]


def test_safe_area_absent_with_rects_is_not_evaluated():
    """几何域已进入（rects 在场）但版心声明缺席 → not_evaluated（对称纪律：
    证据不可得不冒充通过）。两输入都不在场 → 该域未参与，无条目。"""
    out = evaluate_layout_findings(_solution(), component_rects_px={
        "legend": {"x": 0, "y": 0, "width": 300, "height": 250}})
    f = next(f for f in out if f.check == "SAFE_AREA_VIOLATION")
    assert f.evaluated is False


def test_geometry_domain_absent_no_safe_area_entry():
    out = evaluate_layout_findings(_solution())
    assert not [f for f in out if f.check == "SAFE_AREA_VIOLATION"]


# ── 4. 端到端：V5 求解结果直接消费 ──────────────────────────────────────


def test_end_to_end_from_solve_layout_v5():
    parts = [
        # 密集 participants 触发溢出
    ]
    from app.lib.cartography.layout_solver import LayoutParticipantV4
    parts = [
        LayoutParticipantV4(
            id="t", type="title", requested_zone="top-center",
            priority=1, optional=False),
        LayoutParticipantV4(
            id="chart", type="chart_panel", requested_zone="top-left",
            priority=20, optional=True, width_units=2),
        LayoutParticipantV4(
            id="unc", type="uncertainty_panel", requested_zone="top-left",
            priority=22, optional=True),
        LayoutParticipantV4(
            id="dec", type="decision_panel", requested_zone="top-left",
            priority=23, optional=True),
        LayoutParticipantV4(
            id="stats", type="statistics_panel", requested_zone="top-left",
            priority=24, optional=True),
    ]
    doc = LayoutConstraintDocument(
        page_profile="viewport",
        components={
            "dec": ComponentLayoutConstraint(
                component_id="dec", overflow_policy="suppress"),
        },
    )
    sol = solve_layout_v5(parts, constraint_spec=doc, page_profile="viewport")
    out = evaluate_layout_findings(
        sol, label_stats={"total": 50, "placed": 40, "suppressed": 10})
    codes = {f.check for f in out}
    # 所有产出码都在 closed vocab 内
    assert codes <= set(LAYOUT_FINDING_CODES)


# ── 5. 修复路由契约（R9 P2-2 清偿后）──────────────────────────────────


def test_fix_route_uses_existing_action_vocabularies():
    """路由动作词必须落在既有执行器词表内，不新造动作 id：
    - 版式类 → component_composer.REPAIR_ACTIONS（AC-07 修复链词汇）；
    - 标注类 → selfheal_actions 已注册动作（adjust_labels）。
    """
    from app.lib.cartography.component_composer import REPAIR_ACTIONS
    from app.lib.cartography.semantic_checks_layout import _FIX_ROUTE

    by_id = actions_by_id()
    assert len(SELFHEAL_ACTIONS) >= 5  # 既有下限不回退（未撤销既有动作）
    for code, action in _FIX_ROUTE.items():
        assert code in LAYOUT_FINDING_CODES
        assert action in REPAIR_ACTIONS or action in by_id, (
            f"{code} 路由到未注册动作 {action!r}")
    # 版式类路由具体映射（closed 契约锁定）
    assert _FIX_ROUTE["LAYOUT_COLLISION_UNRESOLVED"] == "change_anchor"
    assert _FIX_ROUTE["OVERFLOW_SUPPRESSED"] == "collapse_to_overflow"
    assert _FIX_ROUTE["LABEL_SUPPRESSED_RATIO"] == "adjust_labels"
    assert "adjust_labels" in by_id  # selfheal 既有注册，非本 PR 新造


def test_no_dead_layout_action_ids_registered():
    """R9 P2-2：relayout_* 死端动作 id 不再注册（无执行器分支的
    AUTO_SAFE 标注具有误导性 —— 撤销注册面，路由直连既有词表）。"""
    ids = {a.action_id for a in SELFHEAL_ACTIONS}
    assert "relayout_change_anchor" not in ids
    assert "relayout_collapse_overflow" not in ids
