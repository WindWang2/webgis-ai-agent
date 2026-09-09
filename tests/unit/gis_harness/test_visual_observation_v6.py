"""Deterministic Cartographic Observation（V6 Wave 8）回归锁。

不变式（对应 03-visual-observation.md W8）：
1. floating 组件提交态 placement 投影重叠 → 每对两条对称
   layout_conflict warning（各指一方互指；disclosure，不判 error ——
   组件可被用户拖动，transient 不阻完成）；
2. canvas 在场且组件完全越出 → layout_conflict warning；canvas 缺席 →
   像素级判定整体缺席（旧客户端零新 finding，诚实降级）；
3. 组件生命周期统一投影：requested/mounted/rendered/visible/layout_valid/
   data_bound/diagnostics，非 chart 族 rendered/data_bound 为 None（不虚构）；
   重叠对双方 diagnostics 均含 layout_conflict（对称）；
4. 布局 findings 进入 validate_render_observation 主链且受
   MAX_RENDER_FINDINGS 有界；_MAX_LAYOUT_FINDINGS 按对原子截断。
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.services.gis_harness.map_completion import (
    F_LAYOUT_CONFLICT,
    RENDER_VERIFIED,
)
from app.services.gis_harness.render_observation import (
    MAX_RENDER_FINDINGS,
    derive_component_lifecycle,
    derive_component_layout_findings,
    validate_render_observation,
)


def _comp(cid: str, ctype: str = "chart_panel", *, rect=None, mounted=True,
          collapsed=False, floating=True, enabled=True) -> Dict[str, Any]:
    return {
        "id": cid, "type": ctype, "enabled": enabled, "mounted": mounted,
        "anchor": "floating" if floating else "top-right",
        "floating": floating, "collapsed": collapsed,
        **({"rect": rect} if rect is not None else {}),
    }


def _observation(components: List[Dict[str, Any]], *, canvas=None,
                 charts=None, layers=None) -> Dict[str, Any]:
    obs: Dict[str, Any] = {
        "mapspec_revision": 3,
        "components": components,
        "layers": layers if layers is not None else [],
        "runtime_errors": [],
    }
    if canvas is not None:
        obs["canvas"] = canvas
    if charts is not None:
        obs["charts"] = charts
    return obs


# ── 1. 重叠检测（对称 finding）───────────────────────────────────────────

def test_overlap_detection_placement_projection() -> None:
    obs = _observation([
        _comp("chart-1", rect={"x": 10, "y": 10, "width": 100, "height": 80}),
        _comp("stats-1", "statistics_panel",
              rect={"x": 50, "y": 40, "width": 100, "height": 80}),
        _comp("legend-1", "legend",
              rect={"x": 400, "y": 300, "width": 80, "height": 60}),
    ])
    findings = derive_component_layout_findings(obs)
    overlaps = [f for f in findings if "∩" in f.detail]
    # B/M3 对称：每对两条 finding，各指一方互指。
    assert len(overlaps) == 2
    by_target = {f.target: f for f in overlaps}
    assert set(by_target) == {"chart-1", "stats-1"}
    for f in overlaps:
        assert f.code == F_LAYOUT_CONFLICT
        assert f.severity == "warning"  # disclosure，不判 error
    assert "stats-1" in by_target["chart-1"].detail
    assert "chart-1" in by_target["stats-1"].detail
    # 不相交的 legend 不产生 finding
    assert all("legend-1" not in f.detail for f in findings)


def test_overlap_pair_is_atomic_under_layout_cap() -> None:
    """B/M3 截断语义：剩余额度不足一对时整对跳过，不断对。"""
    from app.services.gis_harness.render_observation import (
        _MAX_LAYOUT_FINDINGS,
    )
    assert _MAX_LAYOUT_FINDINGS == 4
    many = [
        _comp(f"c{i}", rect={"x": 10 + i, "y": 10 + i,
                             "width": 100, "height": 80})
        for i in range(6)
    ]
    findings = derive_component_layout_findings(_observation(many))
    assert len(findings) == _MAX_LAYOUT_FINDINGS
    targets = sorted(f.target for f in findings)
    # 前两对完整（c0∩c1、c0∩c2 按观测序枚举），无半对。
    assert targets == ["c0", "c0", "c1", "c2"]


def test_non_positive_rect_rejected() -> None:
    """m6：非正尺寸 rect 不参与判定（零面积恒无交集，负尺寸方向不定）。"""
    obs = _observation([
        _comp("flat-1", rect={"x": 10, "y": 10, "width": 0, "height": 80}),
        _comp("neg-1", rect={"x": 10, "y": 10, "width": -5, "height": 80}),
        _comp("ok-1", rect={"x": 10, "y": 10, "width": 100, "height": 80}),
    ])
    assert derive_component_layout_findings(obs) == []


def test_floats_sliced_at_dto_cap() -> None:
    """m5：超限输入只看前 32（与 DTO max_length=32/前端采集同口径）。"""
    from app.services.gis_harness.render_observation import (
        _MAX_OBSERVED_COMPONENTS,
    )
    assert _MAX_OBSERVED_COMPONENTS == 32
    # 前 32 个互不重叠，第 33 个与首个重叠 → 超限部分不参与判定。
    comps = [
        _comp(f"c{i}", rect={"x": 1000 + i * 200, "y": 10,
                             "width": 100, "height": 80})
        for i in range(32)
    ]
    comps.append(
        _comp("late-1", rect={"x": 1010, "y": 20, "width": 100, "height": 80}))
    assert derive_component_layout_findings(_observation(comps)) == []
    lifecycle = {c["id"]: c for c in derive_component_lifecycle(
        _observation(comps))}
    assert "late-1" not in lifecycle
    assert len(lifecycle) == 32


def test_touching_edges_not_overlap() -> None:
    obs = _observation([
        _comp("a", rect={"x": 0, "y": 0, "width": 100, "height": 100}),
        _comp("b", rect={"x": 100, "y": 0, "width": 100, "height": 100}),
    ])
    assert derive_component_layout_findings(obs) == []


# ── 2. offscreen（canvas 门控）───────────────────────────────────────────

def test_offscreen_requires_canvas_and_fully_outside() -> None:
    comps = [
        _comp("gone-1", rect={"x": -500, "y": 10, "width": 100, "height": 80}),
        _comp("edge-1", rect={"x": 760, "y": 10, "width": 100, "height": 80}),
    ]
    # 无 canvas → 像素判定缺席（不做）
    assert derive_component_layout_findings(_observation(comps)) == []
    with_canvas = _observation(comps, canvas={"width": 800, "height": 600})
    findings = derive_component_layout_findings(with_canvas)
    # 完全越出 → finding；部分越界（edge-1 右缘超出）→ 不 finding（合法停靠）
    assert len(findings) == 1
    assert findings[0].target == "gone-1"
    assert "offscreen" in findings[0].detail
    assert findings[0].severity == "warning"


# ── 3. 生命周期投影 ──────────────────────────────────────────────────────

def test_component_lifecycle_projection() -> None:
    obs = _observation(
        [
            _comp("chart-1", rect={"x": 10, "y": 10, "width": 100, "height": 80}),
            _comp("stats-1", "statistics_panel",
                  rect={"x": 50, "y": 40, "width": 100, "height": 80}),
            _comp("legend-1", "legend", floating=False, rect=None),
        ],
        charts=[{"id": "chart-1", "rendered": True, "data_points": 12}],
    )
    lifecycle = {c["id"]: c for c in derive_component_lifecycle(obs)}
    chart = lifecycle["chart-1"]
    assert chart["requested"] is True
    assert chart["mounted"] is True
    assert chart["rendered"] is True
    assert chart["visible"] is True
    assert chart["layout_valid"] is False  # 与 stats-1 重叠
    assert chart["data_bound"] is True
    assert F_LAYOUT_CONFLICT in chart["diagnostics"]
    stats = lifecycle["stats-1"]
    assert stats["layout_valid"] is False
    assert stats["rendered"] is None      # 非 chart 族不虚构
    assert stats["data_bound"] is None
    # B/M3：对称 finding 使互指另一边同样有诊断（不再只有首端有诊断）。
    assert F_LAYOUT_CONFLICT in stats["diagnostics"]
    legend = lifecycle["legend-1"]
    assert legend["layout_valid"] is True
    assert legend["visible"] is True      # anchored 且 mounted 未 collapsed


def test_lifecycle_collapsed_and_unmounted() -> None:
    obs = _observation([
        _comp("chart-1", rect={"x": 0, "y": 0, "width": 10, "height": 10},
              collapsed=True),
        _comp("ghost-1", mounted=False),
    ])
    lifecycle = {c["id"]: c for c in derive_component_lifecycle(obs)}
    assert lifecycle["chart-1"]["visible"] is False
    assert lifecycle["ghost-1"]["visible"] is False


# ── 4. 主链接入 + 有界 ───────────────────────────────────────────────────

def test_validate_includes_layout_findings_bounded() -> None:
    chapter = {
        "map_layers": [
            {"layer_id": "l1", "role": "primary", "enabled": True},
        ],
    }
    mapspec = {
        "layers": [{"id": "l1", "source": "s1",
                    "layout": {"visibility": "visible"}}],
    }
    many = [
        _comp(f"c{i}", rect={"x": 10 + i, "y": 10 + i, "width": 100, "height": 80})
        for i in range(8)
    ]
    obs = _observation(
        many,
        canvas={"width": 800, "height": 600},
        layers=[{"id": "l1", "visible": True, "runtime_layer_count": 1,
                 "source_converged": True, "render_complete": True,
                 "style_converged": True}],
    )
    obs["mapspec_revision"] = 3
    status, findings = validate_render_observation(
        chapter, mapspec, obs, 3)
    assert status == RENDER_VERIFIED  # 层断言全绿；布局仅 warning
    assert len(findings) <= MAX_RENDER_FINDINGS
    assert any(f.code == F_LAYOUT_CONFLICT for f in findings)
    assert all(f.severity == "warning" for f in findings)


# ── Wave 9：Visual Evaluation Seam ───────────────────────────────────────

def test_trigger_whitelist() -> None:
    from app.services.gis_harness.visual_evaluator import (
        should_run_visual_evaluation,
    )
    assert should_run_visual_evaluation("finalization") is True
    assert should_run_visual_evaluation("major_layout_change") is True
    assert should_run_visual_evaluation("map_model_change") is True
    assert should_run_visual_evaluation("visual_repair") is True
    assert should_run_visual_evaluation("user_request") is True
    assert should_run_visual_evaluation("pan") is False
    assert should_run_visual_evaluation("zoom") is False
    assert should_run_visual_evaluation("style_mutation") is False
    assert should_run_visual_evaluation("") is False


def test_evaluator_absent_by_default() -> None:
    from app.services.gis_harness.visual_evaluator import (
        get_visual_evaluator,
        run_visual_evaluation,
    )
    # 未配置 GIS_VISUAL_EVALUATOR → None → 评估为空（特性缺席）
    assert get_visual_evaluator() is None
    assert run_visual_evaluation(None, {"image": "x"}) == []


def test_run_visual_evaluation_sanitizes_output() -> None:
    from app.services.gis_harness.completion.unified_findings import (
        UnifiedFinding,
    )
    from app.services.gis_harness.visual_evaluator import run_visual_evaluation

    def good_evaluator(snapshot):
        assert "image_ref" in snapshot
        return [
            UnifiedFinding(
                domain="visual", code="V_WEAK_VISUAL_HIERARCHY",
                severity="warning", evidence="result not salient",
                blocks_completion=True,  # 会被强制改写：软评估不硬阻断
            ),
            {"code": "V_LEGEND_DOMINATES", "severity": "warning",
             "evidence": "legend area > map focus"},
        ]

    findings = run_visual_evaluation(good_evaluator, {"image_ref": "local://x"})
    assert len(findings) == 2
    for f in findings:
        assert f.domain == "visual"
        assert f.degradation_only is True
        assert f.blocks_completion is False  # 软评估强制不硬阻断
        assert f.retryable is False
    assert findings[0].code == "V_WEAK_VISUAL_HIERARCHY"
    assert findings[1].code == "V_LEGEND_DOMINATES"


def test_run_visual_evaluation_rejects_mutation_intents() -> None:
    from app.services.gis_harness.visual_evaluator import run_visual_evaluation

    def malicious_evaluator(snapshot):
        return [
            {"code": "V_X", "mutation": {"type": "PatchComponent"}},  # 改图意图 → 废
            {"mutation": {}},                       # 无 code + mutation → 废
            "not-a-finding",                        # 非形状 → 废
            {"severity": "error"},                  # 无 code → 废
            {"code": "V_OK", "severity": "bogus"},  # 非法 severity → 归一 warning
        ]

    findings = run_visual_evaluation(malicious_evaluator, {})
    assert len(findings) == 1
    assert findings[0].code == "V_OK"
    assert findings[0].severity == "warning"


def test_run_visual_evaluation_failure_degrades_empty() -> None:
    from app.services.gis_harness.visual_evaluator import run_visual_evaluation

    def boom(snapshot):
        raise RuntimeError("vlm backend unreachable")

    assert run_visual_evaluation(boom, {}) == []
