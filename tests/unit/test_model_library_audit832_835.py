"""Regression tests for audit-ff9a392 model-library/cartography findings
(#832-#835).

#832: geometry-polymorphic cartography (categorical_thematic) resolves its
      planned layer_type from the real profile geometry so the primary binds.
#833: MapModel.default_class_count survives seed construction; planner's
      _CARTOGRAPHY_LAYER_TYPE stays locked to the library's layer types.
#834: task hints recompute output_intents (and geometry expectation).
#835: interrogative「哪个区」is not a district scope; the declared
      NEEDS_ADMIN_UNITS fallback is reachable (recorded when the fill primary
      cannot bind while a circle layer did).
"""

import pytest


# ─── #832: geometry-aware layer type ────────────────────────────────────


class TestAudit832GeometryLayerType:
    def test_categorical_on_points_plans_circle_primary(self):
        from app.services.gis_harness.intent import resolve_map_request_intent
        from app.services.gis_harness.planner import MapProductPlanner

        intent = resolve_map_request_intent("成都餐厅各类别占比与类型分布")
        planner = MapProductPlanner()
        plan = planner.plan_from_intent(intent)
        assert plan.map_layers[0].cartography == "categorical_thematic"
        assert plan.map_layers[0].layer_type == "fill"  # draft default

        final = planner.finalize_with_profile(
            plan, {"geometryTypes": ["Point"], "featureCount": 200})
        primary = next(ly for ly in final.map_layers if ly.role == "primary")
        assert primary.layer_type == "circle", (
            "point-profile data must re-type the polymorphic primary so the "
            "authorized circle layer can bind (#832)"
        )

    def test_categorical_on_polygons_stays_fill(self):
        from app.services.gis_harness.intent import resolve_map_request_intent
        from app.services.gis_harness.planner import MapProductPlanner

        # categorical task on polygon data (land-use categories)
        intent = resolve_map_request_intent("成都各区用地类型分布")
        assert intent.task == "categorical_distribution"
        planner = MapProductPlanner()
        plan = planner.plan_from_intent(intent)
        final = planner.finalize_with_profile(
            plan, {"geometryTypes": ["Polygon"], "featureCount": 20})
        primary = next(ly for ly in final.map_layers if ly.role == "primary")
        assert primary.layer_type == "fill"

    def test_unknown_geometry_keeps_planned_type(self):
        from app.services.gis_harness.planner import _geometry_aware_layer_type

        assert _geometry_aware_layer_type("categorical_thematic", "fill", "unknown") == "fill"
        assert _geometry_aware_layer_type("administrative_choropleth", "fill", "point") == "fill"


# ─── #833: MapModel field projection + planner/library parity ───────────


class TestAudit833ModelLibrary:
    def test_default_class_count_survives_seed(self):
        from app.lib.cartography.model_library import get_map_model

        m = get_map_model("administrative_choropleth")
        assert m is not None
        assert m.default_class_count == 5
        m2 = get_map_model("aggregate_grid")
        assert m2 is not None
        assert m2.default_class_count == 5
        # models without the param stay None (visible, not fabricated)
        m3 = get_map_model("visual_heatmap")
        assert m3 is not None
        assert m3.default_class_count is None

    def test_planner_layer_type_table_matches_model_library(self):
        """Lock planner's hand-copied mirror to the library's authority.

        The library has no runtime consumer today; this test makes drift
        between the two fact sources fail loudly instead of silently
        diverging (audit #833)."""
        from app.lib.cartography.model_library import get_map_model
        from app.services.gis_harness.planner import _CARTOGRAPHY_LAYER_TYPE

        for carto, layer_type in _CARTOGRAPHY_LAYER_TYPE.items():
            model = get_map_model(carto)
            if model is None:
                continue  # planner-only vocabulary (e.g. density_overview)
            assert model.maplibre_layer_type == layer_type, (
                f"{carto}: planner says {layer_type}, model library says "
                f"{model.maplibre_layer_type} — the two fact sources drifted"
            )


# ─── #834: output_intents recompute on task hint ────────────────────────


class TestAudit834OutputIntents:
    def test_simple_view_hint_recomputes_outputs(self):
        from app.services.gis_harness.intent import (
            merge_intent_hints, resolve_map_request_intent,
        )

        base = resolve_map_request_intent("成都餐饮店分类别分布占比")
        assert "chart" in base.output_intents  # categorical default carries charts
        merged = merge_intent_hints(base, {"task": "simple_view"})
        assert merged.task == "simple_view"
        assert merged.output_intents == ["map", "summary"], (
            "task hint must recompute output_intents — stale chart/statistics "
            "outputs previously polluted lightweight plans (#834)"
        )

    def test_administrative_statistic_hint_gains_table(self):
        from app.services.gis_harness.intent import (
            merge_intent_hints, resolve_map_request_intent,
        )

        base = resolve_map_request_intent("成都学校分布")
        merged = merge_intent_hints(base, {"task": "administrative_statistic"})
        assert "table" in merged.output_intents


# ─── #835: interrogative scope + NEEDS_ADMIN_UNITS reachability ─────────


class TestAudit835IntentDeadEnds:
    def test_interrogative_district_not_captured_as_scope(self):
        from app.services.gis_harness.intent import resolve_map_request_intent

        intent = resolve_map_request_intent("哪个区学校最多")
        assert intent.scope.name != "哪个区", (
            "interrogative limiters must not become a literal district scope (#835)"
        )
        assert intent.scope.level != "district" or intent.scope.name not in ("哪个区",)

    def test_real_district_scope_still_works(self):
        from app.services.gis_harness.intent import resolve_map_request_intent

        intent = resolve_map_request_intent("武侯区小学分布")
        assert intent.scope.name == "武侯区"

    def test_needs_admin_units_fallback_declared_and_reason_known(self):
        """The declared fallback's reason_code is produced by the binding
        adjudication in webgis_map_product (tools.py). This unit-level check
        pins the declaration + the producing code path symbol exist."""
        from app.services.gis_harness.recipes import get_recipe_registry

        recipe = get_recipe_registry().get("administrative_choropleth")
        assert recipe is not None
        fb = next(f for f in recipe.fallbacks if f.reason_code == "NEEDS_ADMIN_UNITS")
        assert fb.use == "point_distribution"
        # the producer exists in tools.py
        import inspect
        from app.services.gis_harness import tools as gis_tools
        assert "NEEDS_ADMIN_UNITS" in inspect.getsource(gis_tools)
