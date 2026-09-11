"""V7（Goal 08 Phase G+I）组件图 QA、浮动修复闭环与完成裁决消费测试.

覆盖：语义检查新增三条图级规则（DUPLICATE_LEGEND_BINDING /
COMPONENT_OUTSIDE_CANVAS / COMPONENT_LINK_CYCLE，deterministic 证据可评）、
quality_loop 的 resolve_floating_layout AUTO_SAFE 修复（只挪 x/y、
user-wins 边界）、derive_product_verdict 的 additive cartographic_review
参数（None 零漂移 / deterministic fail 降档 / 有界摘要 / 畸形输入守卫）。
"""

from app.lib.cartography.quality_loop import review_and_repair_cartography
from app.lib.cartography.semantic_checks import evaluate_cartography_semantics


def _spec_with_components(components, links=None):
    layout = {"components": components}
    if links is not None:
        layout["component_links"] = links
    return {
        "version": "1.2",
        "sources": {
            "src": {"type": "geojson",
                    "inlineData": {"type": "FeatureCollection", "features": []}},
        },
        "layers": [
            {"id": "districts", "source": "src", "type": "fill",
             "paint": {"fill-color": "#eff3ff"}},
        ],
        "layout": layout,
    }


def _checks_by_rule(report):
    return {c.rule: c for c in report.checks}


# ── 图级 QA 规则 ─────────────────────────────────────────────────────────


class TestGraphQARules:
    def test_clean_spec_all_pass(self):
        report = evaluate_cartography_semantics(_spec_with_components([
            {"id": "legend-main", "type": "legend",
             "options": {"layerId": "districts"}},
            {"id": "chart", "type": "chart_panel",
             "placement": {"mode": "floating", "x": 100, "y": 100,
                           "width": 300, "height": 200}},
        ]))
        checks = _checks_by_rule(report)
        for rule in ("DUPLICATE_LEGEND_BINDING", "COMPONENT_OUTSIDE_CANVAS",
                     "COMPONENT_LINK_CYCLE"):
            assert rule in checks, f"{rule} 应产出检查项"
            assert checks[rule].status == "pass"
            assert checks[rule].evidence_class == "deterministic"

    def test_no_components_no_graph_rules(self):
        report = evaluate_cartography_semantics(_spec_with_components([]))
        checks = _checks_by_rule(report)
        assert "DUPLICATE_LEGEND_BINDING" not in checks
        assert "COMPONENT_OUTSIDE_CANVAS" not in checks

    def test_duplicate_binding_warns_not_repairable(self):
        report = evaluate_cartography_semantics(_spec_with_components([
            {"id": "lg1", "type": "legend", "options": {"layerId": "districts"}},
            {"id": "lg2", "type": "legend", "options": {"layerId": "districts"}},
        ]))
        check = _checks_by_rule(report)["DUPLICATE_LEGEND_BINDING"]
        assert check.status == "warning"
        assert check.repairability == "not_repairable"

    def test_per_layer_legends_not_duplicate(self):
        report = evaluate_cartography_semantics(_spec_with_components([
            {"id": "legend-main", "type": "legend",
             "options": {"layerId": "districts"}},
            {"id": "cb-x", "type": "continuous_colorbar",
             "options": {"layerId": "districts"}},
        ]))
        check = _checks_by_rule(report)["DUPLICATE_LEGEND_BINDING"]
        assert check.status == "pass"

    def test_unreachable_floating_fails_with_auto_safe_fix(self):
        report = evaluate_cartography_semantics(_spec_with_components([
            {"id": "chart", "type": "chart_panel",
             "placement": {"mode": "floating", "x": -900, "y": 50,
                           "width": 300, "height": 200}},
        ]))
        check = _checks_by_rule(report)["COMPONENT_OUTSIDE_CANVAS"]
        assert check.status == "fail"
        assert check.repairability == "auto_safe"
        fix = check.suggested_fix
        assert fix["operation"] == "resolve_floating_layout"
        assert fix["placements"][0]["component_id"] == "chart"
        assert fix["placements"][0]["x"] >= 0

    def test_beyond_nominal_viewport_warns(self):
        report = evaluate_cartography_semantics(_spec_with_components([
            {"id": "chart", "type": "chart_panel",
             "placement": {"mode": "floating", "x": 3000, "y": 50,
                           "width": 300, "height": 200}},
        ]))
        check = _checks_by_rule(report)["COMPONENT_OUTSIDE_CANVAS"]
        assert check.status == "warning"

    def test_link_cycle_fails(self):
        report = evaluate_cartography_semantics(_spec_with_components([
            {"id": "a", "type": "legend", "options": {"layerId": "districts"}},
            {"id": "b", "type": "chart_panel"},
        ], links=[
            {"src": "a", "dst": "b", "type": "under"},
            {"src": "b", "dst": "a", "type": "under"},
        ]))
        check = _checks_by_rule(report)["COMPONENT_LINK_CYCLE"]
        assert check.status == "fail"
        assert check.severity == "error"


# ── quality_loop 修复闭环 ────────────────────────────────────────────────


class TestFloatingLayoutRepair:
    def test_unreachable_chart_converged_back(self):
        spec = _spec_with_components([
            {"id": "chart", "type": "chart_panel",
             "placement": {"mode": "floating", "x": -900, "y": 50,
                           "width": 300, "height": 200}},
        ])
        result = review_and_repair_cartography(spec)
        assert result.repair_count == 1
        # synthetic 单层 spec 常有需运行态证据的确定性检查未评 → partial
        # 是诚实终态；修复是否收敛看 placement，不看整体档位。
        placement = result.mapspec["layout"]["components"][0]["placement"]
        assert placement["x"] >= 0
        # 尺寸与状态不被触碰（只挪 x/y）
        assert placement["width"] == 300 and placement["height"] == 200
        checks = {c["rule"]: c for c in result.review.get("checks", [])}
        outside = checks.get("COMPONENT_OUTSIDE_CANVAS")
        if outside is not None:
            assert outside["status"] == "pass"

    def test_anchor_components_untouched(self):
        spec = _spec_with_components([
            {"id": "t", "type": "title", "position": "top-center"},
            {"id": "chart", "type": "chart_panel",
             "placement": {"mode": "floating", "x": -100, "y": 50,
                           "width": 300, "height": 200}},
        ])
        result = review_and_repair_cartography(spec)
        title = next(c for c in result.mapspec["layout"]["components"]
                     if c["id"] == "t")
        assert title["position"] == "top-center"

    def test_source_spec_not_mutated(self):
        spec = _spec_with_components([
            {"id": "chart", "type": "chart_panel",
             "placement": {"mode": "floating", "x": -900, "y": 50,
                           "width": 300, "height": 200}},
        ])
        review_and_repair_cartography(spec)
        placement = spec["layout"]["components"][0]["placement"]
        assert placement["x"] == -900  # 原始 desired-state 不被改写


# ── 完成裁决消费 ──────────────────────────────────────────────────────────


class TestVerdictCartographyConsumption:
    def _make(self):
        from app.services.gis_harness.completion.contracts import (
            MapCompletionFinding,
            MapCompletionResult,
        )
        return MapCompletionResult(
            status="complete",
            findings=[MapCompletionFinding(code="minor_warning",
                                           severity="warning")],
        )

    def test_default_none_zero_drift(self):
        from app.services.gis_harness.completion.contracts import (
            derive_product_verdict,
        )
        payload = derive_product_verdict(self._make())
        assert "cartography" not in payload

    def test_review_with_fail_downgrades_ready(self):
        from app.services.gis_harness.completion.contracts import (
            VERDICT_NEEDS_REPAIR,
            derive_product_verdict,
        )
        review = {
            "status": "failed_unrepairable",
            "no_deterministic_failures": False,
            "checks": [
                {"rule": "COMPONENT_OUTSIDE_CANVAS", "status": "fail",
                 "evidence_class": "deterministic"},
                {"rule": "DUPLICATE_LEGEND_BINDING", "status": "warning"},
            ],
        }
        payload = derive_product_verdict(self._make(),
                                         cartographic_review=review)
        assert payload["verdict"] == VERDICT_NEEDS_REPAIR
        assert any(r.startswith("cartography:COMPONENT_OUTSIDE_CANVAS")
                   for r in payload["reasons"])
        summary = payload["cartography"]
        assert summary["blocking_rules"] == ["COMPONENT_OUTSIDE_CANVAS"]
        assert "DUPLICATE_LEGEND_BINDING" in summary["warning_rules"]

    def test_clean_review_keeps_ready(self):
        from app.services.gis_harness.completion.contracts import (
            VERDICT_READY_WITH_WARNINGS,
            derive_product_verdict,
        )
        review = {
            "status": "passed_with_warnings",
            "no_deterministic_failures": True,
            "checks": [
                {"rule": "COMPONENT_OUTSIDE_CANVAS", "status": "pass",
                 "evidence_class": "deterministic"},
            ],
        }
        payload = derive_product_verdict(_complete_result(),
                                         cartographic_review=review)
        assert payload["verdict"] == VERDICT_READY_WITH_WARNINGS
        assert payload["cartography"]["no_deterministic_failures"] is True

    def test_malformed_review_guarded(self):
        from app.services.gis_harness.completion.contracts import (
            derive_product_verdict,
        )
        payload = derive_product_verdict(self._make(), cartographic_review={})
        assert "cartography" not in payload

    def test_loop_result_shape_accepted(self):
        from app.services.gis_harness.completion.contracts import (
            derive_product_verdict,
        )
        review = {
            "stage": "desired_state",
            "status": "failed_unrepairable",
            "review": {
                "status": "fail",
                "no_deterministic_failures": False,
                "checks": [
                    {"rule": "COMPONENT_LINK_CYCLE", "status": "fail",
                     "evidence_class": "deterministic"},
                ],
            },
            "attempts": [
                {"iteration": 1, "repairs": [
                    {"operation": "resolve_floating_layout"}]},
            ],
        }
        payload = derive_product_verdict(self._make(),
                                         cartographic_review=review)
        summary = payload["cartography"]
        assert summary["blocking_rules"] == ["COMPONENT_LINK_CYCLE"]
        assert summary["auto_repairable"] == ["resolve_floating_layout"]

    def test_bounded_summary(self):
        from app.services.gis_harness.completion.contracts import (
            derive_product_verdict,
        )
        review = {
            "status": "passed",
            "no_deterministic_failures": True,
            "checks": [
                {"rule": f"RULE_{i}", "status": "warning"} for i in range(20)
            ],
        }
        payload = derive_product_verdict(self._make(),
                                         cartographic_review=review)
        assert len(payload["cartography"]["warning_rules"]) <= 8


def _complete_result():
    from app.services.gis_harness.completion.contracts import (
        MapCompletionFinding,
        MapCompletionResult,
    )
    return MapCompletionResult(
        status="complete",
        findings=[MapCompletionFinding(code="minor_warning",
                                       severity="warning")],
    )


class TestReviewP2Regressions:
    """独立 review P2/P3 回归：duplicate 规则语义域 + 空 id 守卫。"""

    def test_dual_charts_on_same_layer_not_flagged(self):
        """chart_panel 同层多实例是合法构成（多图表产品）—— 不得触发
        DUPLICATE_LEGEND_BINDING，review 状态不得被翻成 warning。"""
        report = evaluate_cartography_semantics(_spec_with_components([
            {"id": "chart-a", "type": "chart_panel",
             "options": {"layerId": "districts"}},
            {"id": "chart-b", "type": "chart_panel",
             "options": {"layerId": "districts"}},
        ]))
        check = _checks_by_rule(report)["DUPLICATE_LEGEND_BINDING"]
        assert check.status == "pass"

    def test_empty_id_floating_component_skipped(self):
        """空 id 浮动组件：不可达检测跳过（不产出悬空 auto_safe 建议）。"""
        report = evaluate_cartography_semantics(_spec_with_components([
            {"id": "", "type": "chart_panel",
             "placement": {"mode": "floating", "x": -900, "y": 50,
                           "width": 300, "height": 200}},
        ]))
        check = _checks_by_rule(report)["COMPONENT_OUTSIDE_CANVAS"]
        assert check.status == "pass"

    def test_verdict_all_pass_but_missing_bool_key_no_downgrade(self):
        """手工 dict 缺 no_deterministic_failures 键：零 fail 不得误降档。"""
        from app.services.gis_harness.completion.contracts import (
            VERDICT_READY_WITH_WARNINGS,
            derive_product_verdict,
        )
        review = {
            "status": "passed",
            "checks": [
                {"rule": "COMPONENT_OUTSIDE_CANVAS", "status": "pass",
                 "evidence_class": "deterministic"},
            ],
        }
        payload = derive_product_verdict(_complete_result(),
                                         cartographic_review=review)
        assert payload["verdict"] == VERDICT_READY_WITH_WARNINGS

    def test_verdict_checks_without_rule_keys(self):
        """缺 rule 键的 check 不得产出 "None" 规则名。"""
        from app.services.gis_harness.completion.contracts import (
            derive_product_verdict,
        )
        review = {
            "status": "failed_unrepairable",
            "no_deterministic_failures": False,
            "checks": [
                {"status": "fail", "evidence_class": "deterministic"},
                {"rule": "COMPONENT_LINK_CYCLE", "status": "fail",
                 "evidence_class": "deterministic"},
            ],
        }
        payload = derive_product_verdict(_complete_result(),
                                         cartographic_review=review)
        assert "None" not in payload["cartography"]["blocking_rules"]
        assert payload["cartography"]["blocking_rules"] == \
            ["COMPONENT_LINK_CYCLE"]
