"""GISSituation 契约测试（方向 2 S1，ADR-0180）。

覆盖：SitFact 状态词表与序列化纪律（unknown 无 value）、GISSituation
严格 schema（extra=forbid）、round-trip 序列化、确定性、契约 schema 导出
漂移守护（scripts/situation_inspect.py --dump-schema 是唯一改动口）。
"""
import json
from pathlib import Path

import pytest

from app.services.gis_situation.contract import (
    GISSituation,
    SituationIdentity,
    SituationRevision,
)
from app.services.gis_situation.facts import (
    FACT_STATUSES,
    SitFact,
    known,
    is_usable,
    stale,
    unavailable,
    unknown,
)

REPO = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO / "docs" / "dev" / "situation-contracts" / "gis-situation.schema.json"


def _sit(session_id: str = "s-contract", compiled_at: str = "2026-09-13 00:00:00") -> GISSituation:
    """手织最小合法 GISSituation（不依赖 store）。"""
    from app.services.gis_situation.contract import (
        AnalysisContext,
        CartographicContext,
        ConstraintsContext,
        DataContext,
        DeliveryContext,
        GeographicContext,
        InteractionContext,
        MapContext,
        SituationEvidence,
        TemporalContext,
        UserGoalContext,
    )

    return GISSituation(
        identity=SituationIdentity(
            session_id=session_id,
            revision=SituationRevision(mutation_revision=3, observation_sequence=2,
                                       interaction_sequence=1),
            compiled_at=compiled_at,
        ),
        user_goal=UserGoalContext(
            goal=known("画一张人口热力图", source="session_plan.user_goal"),
            plan_id=unknown(source="plan"),
            recipe_id=unknown(source="recipe"),
            progress=unknown(source="progress"),
        ),
        geographic=GeographicContext(
            viewport=known({"center": [116.4, 39.9], "zoom": 11}, source="obs.viewport"),
            framed_view=unknown(source="mapspec.view"),
            scope_name=known("北京市海淀区", source="viewport_naming.lookup"),
            user_location=unknown(source="user_location"),
            scale=known("city", source="derived.zoom"),
            crs=unknown(source="ref_descriptor.crs"),
        ),
        temporal=TemporalContext(
            session_started_at=known("2026-09-13T00:00:00+00:00", source="started_at"),
            requested_period=unknown(source="time"),
            data_coverage=unknown(source="ref_descriptor"),
            active_time_slice=unknown(source="temporal"),
        ),
        data=DataContext(
            datasets=known(
                [{"ref_id": "ref:data-abc", "alias": "人口", "feature_count": 331}],
                source="ref_descriptor",
            ),
            active_roles=unknown(source="context_role"),
            quality=unknown(source="data_fabric.facts"),
            freshness=known(2, source="ref_descriptor.content_revision"),
        ),
        map=MapContext(
            desired_revision=known(3, source="_cartographic_mutation_revision"),
            fingerprint=known("fp-1234567890abcdef", source="cartographic_fingerprint"),
            layers=known(
                [{"id": "lyr-1", "type": "circle", "visible": True, "role": "base"}],
                source="mapspec.layers", revision=3,
            ),
            layer_count=known(1, source="mapspec.layers", revision=3),
            sources=unknown(source="mapspec.sources"),
            basemap=known("OSM 地图", source="map_state.base_layer"),
            observed=unknown(source="_cartographic_observation"),
        ),
        analysis=AnalysisContext(
            plan_progress=unknown(source="plan_graph"),
            stale_nodes=unknown(source="stale"),
            artifacts=unknown(source="bound_ref"),
        ),
        cartographic=CartographicContext(
            verdict=known({"status": "passed"}, source="_cartographic_review", revision=3),
            product_status=unknown(source="map_product"),
            render_status=unknown(source="render_status"),
            recipe_id=unknown(source="recipe_id"),
        ),
        interaction=InteractionContext(
            selected_feature=unknown(source="selected_feature"),
            focus_layer_id=known("lyr-1", source="focus_layer_id"),
            user_hidden_layers=unknown(source="provenance"),
            pending_mutations=unknown(source="event_log"),
            recent_interactions=unknown(source="interactions"),
            display_mode=known(False, source="is_3d"),
        ),
        delivery=DeliveryContext(
            target=unknown(source="map_product"),
            display_mode=known(False, source="is_3d"),
            export_format=unknown(source="map_product"),
        ),
        constraints=ConstraintsContext(
            explicit=unknown(source="constraints"),
            budget=unknown(source="budget"),
            security=unknown(source="security"),
        ),
        evidence=SituationEvidence(sources_ok=["map_state", "mapspec"]),
    )


# ── SitFact ──────────────────────────────────────────────────────────────


def test_fact_status_vocabulary_closed():
    assert FACT_STATUSES == ("known", "unknown", "stale", "unavailable")


def test_unknown_fact_serializes_without_value():
    fact = unknown(source="mapspec.layers")
    assert fact.value is None
    assert "value" not in fact.to_dict()
    assert fact.to_dict() == {"status": "unknown", "source": "mapspec.layers"}


def test_known_fact_serialization_keeps_bound_fields():
    fact = known("v", source="s", revision=2, ref="ref:data-1")
    d = fact.to_dict()
    assert d["value"] == "v" and d["revision"] == 2 and d["ref"] == "ref:data-1"
    assert "observed_at" not in d and "confidence" not in d  # None 字段不落盘


def test_unavailable_and_stale_preserve_source_semantics():
    assert unavailable(source="mapspec").status == "unavailable"
    s = stale("x", source="review", revision=1)
    assert s.status == "stale" and s.value == "x"
    assert not is_usable(s) and not is_usable(None) and is_usable(known(1, source="s"))


def test_fact_rejects_unknown_fields():
    with pytest.raises(Exception):
        SitFact(value=1, status="known", source="s", bogus_field=1)


# ── GISSituation ─────────────────────────────────────────────────────────


def test_situation_round_trip_preserves_facts():
    sit = _sit()
    data = sit.to_dict()
    restored = GISSituation.from_dict(json.loads(json.dumps(data)))
    assert restored == sit


def test_situation_unknown_facts_serialize_without_value():
    data = _sit().to_dict()
    # 手织情境里 plan_id/status 等是 unknown —— 序列化面不得出现 value。
    assert "value" not in data["user_goal"]["plan_id"]
    assert data["user_goal"]["plan_id"]["status"] == "unknown"


def test_situation_rejects_extra_fields():
    data = _sit().to_dict()
    data["bogus_context"] = {}
    with pytest.raises(Exception):
        GISSituation.from_dict(data)


def test_situation_revision_lexicographic_monotonic():
    a = SituationRevision(mutation_revision=1, observation_sequence=0, interaction_sequence=9)
    b = SituationRevision(mutation_revision=2, observation_sequence=0, interaction_sequence=0)
    assert b.ge(a) and not a.ge(b)
    assert SituationRevision().ge(SituationRevision())


def test_iter_facts_covers_all_ten_contexts():
    sit = _sit()
    ctxs = {ctx for ctx, _name, _f in sit.iter_facts()}
    assert ctxs == {
        "user_goal", "geographic", "temporal", "data", "map",
        "analysis", "cartographic", "interaction", "delivery", "constraints",
    }


def test_contract_schema_dump_has_no_drift():
    """契约 schema 与 scripts/situation_inspect.py --dump-schema 的产物一致。"""
    assert SCHEMA_PATH.exists(), "schema dump missing — run scripts/situation_inspect.py --dump-schema"
    committed = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    current = GISSituation.model_json_schema()
    current["$id"] = committed.get("$id")
    current["x-situation-contract-version"] = committed.get("x-situation-contract-version")
    assert committed == current, (
        "GISSituation 契约漂移：运行 python scripts/situation_inspect.py --dump-schema 刷新"
    )
