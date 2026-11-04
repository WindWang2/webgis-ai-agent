"""Keep real planning/evidence while avoiding work with no possible consumer."""
from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest

from app.lib.runtime.context import bind_runtime_context
from app.services.gis_harness.recipes import CartographyRecipe, RecipeRegistry


@pytest.mark.parametrize(
    "task,cartography,geometry,expected",
    [
        ("", [], "", []),
        ("wanted", [], "", ["task"]),
        ("", ["dots"], "", ["cartography"]),
        ("", [], "raster", ["raster_intent", "raster_primary"]),
        ("wanted", ["dots"], "raster",
         ["raster_intent", "raster_primary", "task", "cartography"]),
    ],
)
def test_only_member_recipes_are_scored(
    monkeypatch: pytest.MonkeyPatch,
    task: str,
    cartography: list[str],
    geometry: str,
    expected: list[str],
) -> None:
    registry = RecipeRegistry()
    for recipe in (
        CartographyRecipe(id="task", name="t", intent_tasks=["wanted"]),
        CartographyRecipe(id="cartography", name="c", intent_cartography=["dots"]),
        CartographyRecipe(id="raster_primary", name="r", primary_cartography="raster_surface"),
        CartographyRecipe(id="raster_intent", name="r", intent_cartography=["raster_surface"]),
        CartographyRecipe(id="unrelated", name="u", priority=0),
    ):
        registry.register(recipe)
    # Priority is a ranking-only field, read once for every scored recipe.
    # This is a work-count assertion, not a machine-dependent timing test.
    scored = []
    original = CartographyRecipe.__getattribute__

    def observe(self: CartographyRecipe, name: str) -> object:
        if name == "priority":
            scored.append(original(self, "id"))
        return original(self, name)

    monkeypatch.setattr(CartographyRecipe, "__getattribute__", observe)
    intent = SimpleNamespace(
        task=task, cartography_intents=cartography,
        geometry_expectation=geometry, query="",
    )
    assert [r.id for r in registry.select_candidates(intent, limit=10)] == expected
    assert sorted(scored) == sorted(expected)


def test_recipe_membership_uses_live_descriptors() -> None:
    registry = RecipeRegistry()
    intent = SimpleNamespace(task="wanted", cartography_intents=[], query="")
    assert registry.select_candidates(intent) == []
    recipe = CartographyRecipe(id="dynamic", name="d", intent_tasks=["other"])
    registry.register(recipe)
    assert registry.select_candidates(intent) == []
    recipe.intent_tasks[:] = ["wanted"]
    assert registry.select_candidates(intent) == [recipe]
    recipe.intent_tasks.clear()
    recipe.intent_cartography[:] = ["raster_surface"]
    intent.geometry_expectation = "raster"
    assert registry.select_candidates(intent) == [recipe]
    assert registry.unregister(recipe.id)
    assert registry.select_candidates(intent) == []


@pytest.mark.parametrize("turn_id", [None, "", "active"])
def test_trace_payload_work_requires_turn_but_plan_evidence_does_not(
    monkeypatch: pytest.MonkeyPatch, turn_id: str | None,
) -> None:
    from app.lib.runtime import decision_record as records
    from app.lib.runtime.gis_trace import get_gis_trace_registry
    from app.services.gis_harness.capability_resolution import (
        CapabilityResolution, build_situation,
    )
    from app.services.gis_harness.intent import resolve_map_request_intent
    from app.services.gis_harness.planner import MapProductPlanner

    decision_spy = Mock(wraps=records.decision_record)
    projection_spy = Mock(wraps=CapabilityResolution.to_decision_records)
    monkeypatch.setattr(records, "decision_record", decision_spy)
    monkeypatch.setattr(
        CapabilityResolution, "to_decision_records", lambda self: projection_spy(self),
    )
    turn = f"hotpath-{uuid.uuid4().hex}" if turn_id else ""
    profile = {"featureCount": 40, "geometryTypes": ["Point"], "crs": "EPSG:32648"}
    intent = resolve_map_request_intent("成都小学的分布情况")
    planner = MapProductPlanner()
    context = (
        nullcontext() if turn_id is None
        else bind_runtime_context(request_id="request-only", turn_id=turn)
    )
    with context:
        plan = planner.plan_from_intent(
            intent, use_memo=False, situation=build_situation(task_hint=intent.task),
        )
        finalized = planner.finalize_with_profile(plan, profile)
    assert plan.capability_evidence["decisions"]
    assert finalized.capability_evidence["decisions"]
    if turn:
        assert decision_spy.call_count > 0
        assert projection_spy.call_count == 2
        chain = get_gis_trace_registry().get(turn)
        assert chain is not None
        stages = chain.as_dict()["stages"]
        assert any(s.get("decision", {}).get("kind") == "plan_selection" for s in stages)
        assert {s.get("phase") for s in stages} >= {
            "capability_resolution", "capability_resolution_finalize",
        }
        get_gis_trace_registry().drop(turn)
    else:
        assert decision_spy.call_count == 0
        assert projection_spy.call_count == 0


def test_chain_context_probe_matches_emitter_on_lookup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.lib.runtime import chain_emitters, context

    monkeypatch.setattr(context, "current_runtime_context", Mock(side_effect=RuntimeError))
    assert not chain_emitters.has_chain_context()
    assert not chain_emitters.emit_chain(chain_emitters.Stage.USER_INTENT, query="t")
