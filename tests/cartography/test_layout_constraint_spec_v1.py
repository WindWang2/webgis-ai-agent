"""LayoutConstraintSpec v1 + solve_layout_v5（ADR-0283 D1）—— 契约测试.

锁定：
- 序列化往返：spec_version 标签、closed vocab 校验、safe_area_mm 边界；
- 缺省派生：从组件序列推导缺省约束（单例 must_fit、可折叠族 collapse）；
- V4 等价：无约束文档输入 → V5 与 V4 输出逐字段一致（零回归面）；
- must_fit 语义：key 组件在溢出画幅下不消失（折叠/保位 + finding 披露），
  绝不出现在 suppressed；
- max_width_units 钳制 + min_width_units 提升（确定性、带 finding）；
- overflow_policy=suppress 仅对 optional 生效且必须带 overflow_suppressed 披露；
- 确定性：同输入双跑输出完全一致。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.lib.cartography.layout_constraint_spec import (
    SPEC_VERSION,
    ComponentLayoutConstraint,
    LayoutConstraintDocument,
    SafeAreaMM,
    derive_layout_constraints,
)
from app.lib.cartography.layout_solver import (
    LayoutParticipantV4,
    solve_layout_v4,
    solve_layout_v5,
)

pytestmark = pytest.mark.cartography


def _p(pid: str, ptype: str, zone: str, *, priority: int = 50,
       optional: bool = True, width_units: int = 1,
       min_width_units: int = 1) -> LayoutParticipantV4:
    return LayoutParticipantV4(
        id=pid, type=ptype, requested_zone=zone, priority=priority,
        optional=optional, width_units=width_units,
        min_width_units=min_width_units,
    )


# ── 1. 序列化契约 ────────────────────────────────────────────────────────


def test_spec_roundtrip_and_version_tag():
    doc = LayoutConstraintDocument(
        page_profile="a4_portrait",
        key_component_ids=["title", "methodology_note"],
        safe_area_mm=SafeAreaMM(top=12.5, right=10, bottom=15, left=10),
        components={
            "methodology_note": ComponentLayoutConstraint(
                component_id="methodology_note", must_fit=True,
                no_truncate=True, overflow_policy="collapse",
            ),
            "chart": ComponentLayoutConstraint(
                component_id="chart", min_width_units=2, max_width_units=3,
            ),
        },
    )
    dumped = doc.model_dump()
    assert dumped["spec_version"] == SPEC_VERSION
    again = LayoutConstraintDocument.model_validate(dumped)
    assert again == doc


def test_closed_vocab_and_bounds():
    with pytest.raises(ValidationError):
        ComponentLayoutConstraint(
            component_id="x", overflow_policy="explode")
    with pytest.raises(ValidationError):
        ComponentLayoutConstraint(component_id="x", min_width_units=5)
    with pytest.raises(ValidationError):
        ComponentLayoutConstraint(component_id="x", min_width_units=3,
                                  max_width_units=2)
    with pytest.raises(ValidationError):
        SafeAreaMM(top=-1)


# ── 2. 缺省派生 ─────────────────────────────────────────────────────────


class _Comp:
    """派生输入的最小鸭子型（MapSpecComponent/CartographyComponent 子集）。"""

    def __init__(self, cid: str, ctype: str, *, enabled: bool = True):
        self.id = cid
        self.type = ctype
        self.enabled = enabled


def test_derive_defaults_singletons_must_fit_and_collapsibles_collapse():
    doc = derive_layout_constraints([
        _Comp("t", "title"),
        _Comp("attr", "attribution"),
        _Comp("method", "methodology_note"),
        _Comp("chart", "chart_panel"),
        _Comp("hidden", "statistics_panel", enabled=False),
    ], page_profile="viewport")
    assert doc.spec_version == SPEC_VERSION
    assert doc.components["t"].must_fit is True
    assert doc.components["attr"].must_fit is True
    assert doc.components["method"].must_fit is True
    assert doc.components["method"].no_truncate is True
    assert doc.components["chart"].overflow_policy == "collapse"
    # disabled 组件不进约束表（不参与布局）
    assert "hidden" not in doc.components
    assert set(doc.key_component_ids) >= {"t", "attr", "method"}


def test_derive_empty_is_empty_document():
    doc = derive_layout_constraints([], page_profile="a4_landscape")
    assert doc.components == {}
    assert doc.key_component_ids == []


# ── 3. V4 等价（零回归面）──────────────────────────────────────────────


def _dense_participants() -> list[LayoutParticipantV4]:
    return [
        _p("t", "title", "top-center", priority=1, optional=False),
        _p("legend", "legend", "bottom-left", priority=10),
        _p("cat", "categorical_legend", "bottom-left", priority=11),
        _p("stats", "statistics_panel", "top-left", priority=20),
        _p("chart", "chart_panel", "top-left", priority=21, width_units=2),
        _p("unc", "uncertainty_panel", "top-left", priority=22),
        _p("dec", "decision_panel", "top-left", priority=23),
        _p("scale", "scale_bar", "bottom-right", priority=5),
        _p("north", "north_arrow", "top-right", priority=6),
        _p("attr", "attribution", "bottom-right", priority=7),
        _p("method", "methodology_note", "bottom-right", priority=8),
    ]


def test_v5_without_constraints_equals_v4():
    parts = _dense_participants()
    v4 = solve_layout_v4(parts, page_profile="viewport")
    v5 = solve_layout_v5(parts, page_profile="viewport")
    assert v5.placements == v4.placements
    assert v5.suppressed == v4.suppressed
    assert v5.warnings == v4.warnings
    assert v5.conflicts == v4.conflicts
    assert v5.repair_steps == v4.repair_steps
    assert v5.collapsed_ids == v4.collapsed_ids
    assert v5.hidden_ids == v4.hidden_ids
    assert v5.constraint_findings == []


# ── 4. must_fit / overflow 语义 ──────────────────────────────────────────


def test_must_fit_key_component_never_suppressed():
    """密集窄画幅下 methodology_note（must_fit+collapse）不消失。

    viewport profile 顶/底预算 3/3、bottom-right 容量 2 —— 11 个参与者
    必然溢出；V5 下 key 组件要么落位要么折叠进溢出槽，绝不在 suppressed。
    """
    parts = _dense_participants()
    doc = derive_layout_constraints(
        [_Comp(p.id, p.type) for p in parts], page_profile="viewport")
    sol = solve_layout_v5(parts, constraint_spec=doc, page_profile="viewport")
    suppressed_ids = {s.id for s in sol.suppressed}
    for key_id in doc.key_component_ids:
        assert key_id not in suppressed_ids, (
            f"key component {key_id} suppressed: {sol.suppressed}")


def test_suppress_policy_discloses_every_removal():
    """overflow_policy=suppress 的 optional 组件被移除时逐个披露。"""
    parts = _dense_participants()
    doc = LayoutConstraintDocument(
        page_profile="viewport",
        components={
            "unc": ComponentLayoutConstraint(
                component_id="unc", overflow_policy="suppress"),
            "dec": ComponentLayoutConstraint(
                component_id="dec", overflow_policy="suppress"),
        },
    )
    sol = solve_layout_v5(parts, constraint_spec=doc, page_profile="viewport")
    codes = {(f.component_id, f.code) for f in sol.constraint_findings}
    for pid in ("unc", "dec"):
        suppressed = pid in {s.id for s in sol.suppressed}
        if suppressed:
            assert (pid, "overflow_suppressed") in codes
    # keep 语义：required 不因 suppress 策略被移除
    assert "t" not in {s.id for s in sol.suppressed}


def test_max_width_clamp_and_finding():
    parts = [
        _p("t", "title", "top-center", priority=1, optional=False),
        _p("chart", "chart_panel", "bottom-left", priority=10, width_units=4),
        _p("scale", "scale_bar", "bottom-right", priority=5),
    ]
    doc = LayoutConstraintDocument(
        page_profile="a4_landscape",
        components={"chart": ComponentLayoutConstraint(
            component_id="chart", max_width_units=2)},
    )
    sol = solve_layout_v5(parts, constraint_spec=doc, page_profile="a4_landscape")
    placed = {p.id: p for p in sol.placements}
    assert placed["chart"].width_units == 2
    assert any(
        f.code == "max_width_clamped" and f.component_id == "chart"
        for f in sol.constraint_findings)


def test_determinism_double_run():
    parts = _dense_participants()
    doc = derive_layout_constraints(
        [_Comp(p.id, p.type) for p in parts], page_profile="viewport")
    a = solve_layout_v5(parts, constraint_spec=doc, page_profile="viewport")
    b = solve_layout_v5(parts, constraint_spec=doc, page_profile="viewport")
    assert a.model_dump() == b.model_dump()


def test_unknown_component_ids_in_doc_are_ignored_but_flagged():
    """约束表里的未知 id 不崩溃：忽略参与求解，info 级 finding 披露。"""
    parts = [
        _p("t", "title", "top-center", priority=1, optional=False),
        _p("scale", "scale_bar", "bottom-right", priority=5),
    ]
    doc = LayoutConstraintDocument(
        page_profile="viewport",
        components={"ghost": ComponentLayoutConstraint(component_id="ghost")},
    )
    sol = solve_layout_v5(parts, constraint_spec=doc, page_profile="viewport")
    assert sol.placements  # 正常求解
    assert any(
        f.code == "constraint_unmatched" and f.component_id == "ghost"
        for f in sol.constraint_findings)
