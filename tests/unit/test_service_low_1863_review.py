"""Scoped behavior regressions for the remaining 1863 service findings."""
from types import SimpleNamespace

import pytest


def test_rejected_induction_member_cannot_enter_parameter_evidence_pool():
    from app.services.gis_skills.induction.trace_analyzer import InducedStep, TraceAnalysis, merge_analyses
    step = InducedStep(seq=0, tool_name="buffer", capability_id="buffer", kind="analyze", arguments={"distance": 10})
    accepted = TraceAnalysis(accepted=True, steps=[step])
    rejected = TraceAnalysis(accepted=False, steps=[step.model_copy(update={"arguments": {"distance": 999}})])
    merged, codes = merge_analyses([accepted, rejected])
    assert codes == ["IND_MEMBER_REJECTED"]
    assert merged.steps[0].observations == {}


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), True, "2", 10 ** 1000])
def test_invalid_distribution_is_unknown_not_stable(bad):
    from app.services.cartography.distribution_drift import detect_distribution_drift, distribution_evidence, distribution_fingerprint
    valid = {"quantiles": [1, 2, 3], "null_ratio": 0}
    invalid = {"quantiles": [1, bad, 3], "null_ratio": 0}
    assert detect_distribution_drift(valid, invalid)["evaluated"] is False
    assert distribution_evidence(invalid) is None
    assert distribution_fingerprint(invalid) is None
    assert detect_distribution_drift(invalid, invalid)["evaluated"] is False


def test_null_ratio_cannot_claim_distribution_verified_when_nonfinite():
    from app.services.cartography.distribution_drift import detect_distribution_drift
    assert detect_distribution_drift({"quantiles": [1]}, {"quantiles": [1], "null_ratio": float("nan")})["evaluated"] is False


def test_system_reconciliation_cannot_reassert_a_declared_incumbent():
    from app.services.gis_world_state.precedence import can_override, resolve_field
    assert not can_override("SYSTEM_DEFAULT", "SYSTEM_DEFAULT")
    assert resolve_field(False, "SYSTEM_DEFAULT", True, "SYSTEM_DEFAULT") == (False, "SYSTEM_DEFAULT", "kept")
    assert can_override("SYSTEM_DEFAULT", "SYSTEM_DEFAULT", incumbent_declared=False)
    assert can_override("REPAIR_AUTOFILL", "SYSTEM_DEFAULT")


def test_action_resolver_rejects_metadata_fallback_for_nonexistent_tool():
    from app.services.gis_action.service import RegistryToolResolver
    class Registry:
        def metadata(self, _name):
            return {"tier": 1, "side_effect": "unclassified"}
        def has(self, _name):
            return False
    assert RegistryToolResolver(Registry()).resolve_tool("not-registered") is None


def test_action_resolver_canonicalizes_alias_and_keeps_declared_replacement():
    from app.services.gis_action.service import RegistryToolResolver
    registry = SimpleNamespace(
        resolve_name=lambda name: "canonical" if name == "alias" else name,
        descriptor=lambda name: {"name": name},
        metadata=lambda name: {"side_effect": "pure", "deprecation_of": "replacement"},
    )
    resolved = RegistryToolResolver(registry).resolve_tool("alias")
    assert resolved.tool == "canonical" and resolved.superseded_by == "replacement"


def test_batch_presentation_owner_is_latest_revision_and_legacy_ring_is_complete():
    from app.services.gis_world_state.provenance import durable_presentation_owner, last_presentation_owner
    entries = [None,
        {"kind": "PatchLayerPresentationIntent", "target": "l", "origin": "user",
         "seq": 90, "revision": 2, "detail": {"visible": False}},
        {"kind": "GISMutationBatch", "origin": "user", "seq": 3, "revision": 3,
         "detail": {"shown": ["l"], "hidden": []}},
    ]
    last = last_presentation_owner(entries, "l")
    assert last["detail"]["visible"] is True and last["kind"] == "GISMutationBatch"
    assert durable_presentation_owner(entries, "l", revision_threshold=999) == last


@pytest.fixture
def learning_factory(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.core.database import Base
    from app.models.project import Project
    from app.services.cartography import memory_harvest
    engine = create_engine(f"sqlite:///{tmp_path / 'learning.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        db.add(Project(id="p", name="Learning"))
        db.commit()
    monkeypatch.setattr(memory_harvest, "_session_local_factory", factory)
    yield factory
    engine.dispose()


def test_recipe_outcome_cannot_commit_its_callers_transaction(learning_factory):
    from app.models.project import Project
    from app.models.intent_learning import CartoRecipeAffinity
    from app.services.cartography.intent_learning import record_recipe_outcome
    with learning_factory() as db:
        db.add(Project(id="uncommitted", name="outer transaction"))
        db.flush()
        record_recipe_outcome(db, recipe_id="r", success=True)
        db.rollback()
    with learning_factory() as db:
        assert db.get(Project, "uncommitted") is None
        assert db.get(CartoRecipeAffinity, "r") is None


def test_harvest_affinity_failure_rolls_back_project_facts_too(learning_factory, monkeypatch):
    from sqlalchemy import select
    from app.models.project import CartoProjectFact
    from app.models.intent_learning import CartoRecipeAffinity
    from app.services.cartography import memory_harvest, intent_learning
    original = intent_learning.record_recipe_outcome
    def fail_after_flush(db, **kwargs):
        original(db, **kwargs)
        raise RuntimeError("post-affinity failure")
    monkeypatch.setattr(intent_learning, "record_recipe_outcome", fail_after_flush)
    with pytest.raises(RuntimeError, match="post-affinity"):
        memory_harvest._harvest_sync("p", {"layers": [], "sources": {}}, {"overall_passed": True}, "r", "s")
    with learning_factory() as db:
        assert list(db.execute(select(CartoProjectFact)).scalars()) == []
        assert db.get(CartoRecipeAffinity, "r") is None


def test_same_session_map_is_not_recounted_for_review_timing_or_repeated_turns(learning_factory):
    from app.models.intent_learning import CartoRecipeAffinity
    from app.services.cartography.memory_harvest import _harvest_sync
    def spec(color):
        return {"layers": [{"id": "l", "type": "fill", "source": "s", "paint": {"fill-color": color}}], "sources": {"s": {}}}
    for state, duration, sid in [(spec("red"), 1, "s"), (spec("red"), 2, "s"),
                                 (spec("blue"), 3, "s"), (spec("red"), 4, "s"),
                                 (spec("red"), 5, "other-session")]:
        _harvest_sync("p", state, {"overall_passed": True, "duration": duration}, "r", sid)
    with learning_factory() as db:
        assert db.get(CartoRecipeAffinity, "r").success_count == 3


def test_selfheal_history_is_single_query_and_cold_start_is_neutral(learning_factory):
    from sqlalchemy import event
    from app.services.cartography.selfheal_policy import action_success_table, rank_actions_by_history, record_repair_outcome
    with learning_factory() as db:
        record_repair_outcome(db, action_id="good", context="x", expected_effect=1, actual_delta=1, success=True)
        record_repair_outcome(db, action_id="bad", context="x", expected_effect=1, actual_delta=0, success=False)
        queries = []
        engine = db.get_bind()
        def observe(_conn, _cursor, statement, _parameters, _context, _executemany):
            queries.append(statement)
        event.listen(engine, "before_cursor_execute", observe)
        try:
            table = action_success_table(db)
        finally:
            event.remove(engine, "before_cursor_execute", observe)
        assert len(queries) == 1 and len(table) == 2
        assert rank_actions_by_history(db, ["bad", "new", "good"]) == ["good", "new", "bad"]


def test_intent_extraction_failure_still_records_safe_remaining_fields(monkeypatch):
    from contextlib import nullcontext
    from app.services.cartography import intent_learning
    recorded = {}
    class Intent:
        task = "distribution"
        @property
        def fallback_decision(self):
            raise ValueError("malformed optional metadata")
    def record(_db, **kwargs):
        recorded.update(kwargs)
        return 42
    monkeypatch.setattr(intent_learning, "_get_session_local", lambda: lambda: nullcontext(object()))
    monkeypatch.setattr(intent_learning, "record_intent_adjudication", record)
    assert intent_learning.capture_intent_adjudication(query="q", intent=Intent()) == 42
    assert recorded["matched_rules"] == [] and recorded["fallback"] is False


@pytest.mark.asyncio
async def test_batch_refusal_exposes_stable_code_and_keeps_engine_stamp():
    from app.services.gis_world_state.mutation import apply_gis_mutation_batch
    from app.services.mapspec.lifecycle_engine import PatchLayerPresentationIntent
    from app.services.mapspec.mutation_contracts import MapSpecBatchResult
    prior = {"layers": [{"id": "l", "cartographic_intent": {
        "presentation_owner": "user", "expected_visible": False}}]}
    observed = []
    class Engine:
        async def apply_presentation_batch(self, sid, intents, **kwargs):
            refused = await kwargs["pre_commit_check"](sid, intents[0], "agent", prior)
            observed.append(refused.error_code)
            return MapSpecBatchResult(producer_class="USER_PINNED")
    result = await apply_gis_mutation_batch("batch-low-review", [PatchLayerPresentationIntent(layer_id="l", visible=True)],
                                          origin="agent", engine=Engine())
    assert observed == ["user_wins_presentation"]
    assert result.producer_class == "USER_PINNED"
