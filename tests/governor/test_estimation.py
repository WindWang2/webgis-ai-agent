"""R2/R13 估算投影单测：档位先验、DF 投影、render 单调性、计划聚合。"""
from __future__ import annotations

import pytest

from app.services.governor.contract import (
    Dimension,
    Certainty,
    ResourceClass,
    Subsystem,
)
from app.services.governor.estimation import (
    DfCostView,
    estimate_for_tool,
    raster_window_from_args,
    sum_estimates,
)
from app.services.governor.render_budget import (
    RenderWorkInput,
    estimate_render,
    export_dpi_factor,
    render_work_units,
)


class TestToolPrior:
    def test_light_vs_heavy_monotonic_memory(self):
        light = estimate_for_tool("t_small", tool_class="light")
        heavy = estimate_for_tool("t_big", tool_class="heavy")
        assert (light.adjudged(Dimension.MEMORY_BYTES)
                < heavy.adjudged(Dimension.MEMORY_BYTES))
        assert (light.adjudged(Dimension.WALL_TIME_S)
                < heavy.adjudged(Dimension.WALL_TIME_S))

    def test_prior_estimates_are_ranges_with_evidence(self):
        est = estimate_for_tool("t", tool_class="medium")
        mem = est.dim(Dimension.MEMORY_BYTES)
        assert mem.certainty is Certainty.ESTIMATED
        assert mem.min is not None and mem.expected is not None and mem.max is not None
        assert mem.source.startswith("tool_prior.v1")
        assert 0 < mem.confidence <= 1

    def test_feature_hint_refines_and_never_overrides_df(self):
        args = {"limit": 5000}
        hinted = estimate_for_tool("t", tool_class="light", args=args)
        assert hinted.dim(Dimension.FEATURE_COUNT).certainty is Certainty.ESTIMATED
        # DF 证据在场时覆盖参数 hint（owner 语义优先）
        df = estimate_for_tool(
            "t", tool_class="light", args=args,
            df_cost=DfCostView(rows=100.0, bytes=2048.0, latency_s=2.0),
        )
        assert df.dim(Dimension.FEATURE_COUNT).expected == pytest.approx(100.0)
        assert df.dim(Dimension.NETWORK_BYTES).certainty is Certainty.ESTIMATED
        # DF 投影维自身携带高 confidence；整体置信度是短板语义（先验维仍 0.4）
        assert df.dim(Dimension.FEATURE_COUNT).confidence >= 0.7
        assert df.dim(Dimension.NETWORK_BYTES).confidence >= 0.7
        assert df.overall_confidence() == pytest.approx(0.4)

    def test_unknown_class_falls_back_to_light(self):
        est = estimate_for_tool("t", tool_class="banana")
        assert est.resource_class is ResourceClass.LIGHT

    def test_llm_token_projection(self):
        est = estimate_for_tool(
            "chat", subsystem=Subsystem.LLM_CONTEXT,
            context_tokens=10_000, output_tokens=1_000,
        )
        assert est.dim(Dimension.CONTEXT_TOKENS).certainty is Certainty.KNOWN
        assert est.dim(Dimension.ESTIMATED_LLM_COST).expected > 0
        assert est.dim(Dimension.ESTIMATED_LLM_COST).max >= est.dim(
            Dimension.ESTIMATED_LLM_COST).expected

    def test_render_refinement_marks_browser(self):
        est = estimate_for_tool(
            "webgis_map_product",
            render_input=RenderWorkInput(layer_count=3, feature_count=1000),
        )
        assert est.browser_required is True
        assert est.dim(Dimension.RENDER_WORK_UNITS).certainty is Certainty.ESTIMATED


class TestRenderBudget:
    def test_work_is_deterministic_and_monotonic(self):
        base = RenderWorkInput(layer_count=2, feature_count=100)
        more = RenderWorkInput(layer_count=5, feature_count=10_000)
        assert render_work_units(base) == render_work_units(base)
        assert render_work_units(base) < render_work_units(more)

    def test_labels_costlier_than_features(self):
        a = RenderWorkInput(feature_count=1000, label_count=0)
        b = RenderWorkInput(feature_count=0, label_count=1000)
        assert render_work_units(b) > render_work_units(a)

    def test_export_dpi_scales_work(self):
        screen = RenderWorkInput(feature_count=1000)
        export = RenderWorkInput(feature_count=1000, export_dpi=300)
        assert render_work_units(export) > render_work_units(screen)
        assert export_dpi_factor(96) == 1.0
        assert export_dpi_factor(None) == 1.0
        assert export_dpi_factor(300) > export_dpi_factor(150)

    def test_symbol_complexity_capped(self):
        wild = RenderWorkInput(feature_count=100, symbol_complexity=10_000.0)
        capped = RenderWorkInput(feature_count=100, symbol_complexity=8.0)
        assert render_work_units(wild) == render_work_units(capped)

    def test_negative_inputs_clamped(self):
        est = RenderWorkInput(layer_count=-5, feature_count=-100)
        assert render_work_units(est) >= 0

    def test_estimate_dimensions_are_ranges(self):
        r = estimate_render(RenderWorkInput(
            layer_count=4, feature_count=20_000, label_count=5_000,
            pixel_width=1920, pixel_height=1080, export_dpi=150,
        ))
        for d in (Dimension.RENDER_WORK_UNITS, Dimension.MEMORY_BYTES,
                  Dimension.WALL_TIME_S):
            dv = r.dim(d)
            assert dv.certainty is Certainty.ESTIMATED
            assert dv.min <= dv.expected <= dv.max
        assert r.subsystem is Subsystem.RENDER
        assert r.browser_required is True


class TestPlanSum:
    def test_empty_parts_honest_empty(self):
        s = sum_estimates([])
        assert s.source == "plan_sum:empty"
        assert not s.dims

    def test_sum_adds_expected_and_widens_confidence_shortboard(self):
        a = estimate_for_tool("a", tool_class="light")
        b = estimate_for_tool("b", tool_class="heavy")
        s = sum_estimates([a, b])
        assert (s.adjudged(Dimension.MEMORY_BYTES)
                == pytest.approx(a.adjudged(Dimension.MEMORY_BYTES)
                                 + b.adjudged(Dimension.MEMORY_BYTES)))
        assert s.overall_confidence() <= min(a.overall_confidence(),
                                             b.overall_confidence()) + 1e-9

    def test_unavailable_dims_skipped_not_zeroed(self):
        a = estimate_for_tool("a", tool_class="light")  # 无 FEATURE_COUNT
        s = sum_estimates([a])
        assert s.dim(Dimension.FEATURE_COUNT).certainty is Certainty.UNAVAILABLE


class TestRasterWindow:
    def test_window_from_args(self):
        w = raster_window_from_args({"width": 800, "height": 600, "bands": 3})
        assert w is not None and w.pixels == 800 * 600 * 3

    def test_window_absent_returns_none(self):
        assert raster_window_from_args({"bbox": [1, 2, 3, 4]}) is None
