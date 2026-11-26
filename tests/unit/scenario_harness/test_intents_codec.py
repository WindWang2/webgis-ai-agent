"""G05 共享编解码单测：白名单/容量红线/字段闭包/往返一致。"""

from __future__ import annotations

import pytest

from app.services.mapspec.intents import (
    PatchComponentIntent,
    PatchLayerStyleIntent,
    RemoveLayerIntent,
    SetScenarioModeIntent,
    UpsertLayerIntent,
)
from app.services.scenario_harness.contracts import (
    MAX_DELTA_ITEM_BYTES,
    DeltaIntent,
    group_of_kind,
)
from app.services.scenario_harness.intents_codec import (
    DeltaCodecError,
    deserialize_intent,
    intent_field_closure,
    serialize_intent,
    serialize_intents,
)


def test_serialize_style_patch_roundtrip():
    intent = PatchLayerStyleIntent(layer_id="L1", paint={"opacity": 0.8})
    delta = serialize_intent(intent, seq=1)
    assert delta.kind == "PatchLayerStyleIntent"
    assert delta.group == "style"
    assert delta.target_id == "L1"
    assert delta.field_paths == ["paint.opacity"]
    rebuilt = deserialize_intent(delta)
    assert isinstance(rebuilt, PatchLayerStyleIntent)
    assert rebuilt.layer_id == "L1"
    assert rebuilt.paint == {"opacity": 0.8}


def test_field_closure_per_intent_kind():
    style = serialize_intent(
        PatchLayerStyleIntent(
            layer_id="L1", paint={"opacity": 0.5, "fill-opacity": 0.4}
        ),
        seq=1,
    )
    closure = intent_field_closure(style)
    assert closure == [
        ("PatchLayerStyleIntent", "L1", "paint.fill-opacity"),
        ("PatchLayerStyleIntent", "L1", "paint.opacity"),
    ]

    upsert = serialize_intent(
        UpsertLayerIntent(
            layer={"id": "schools", "type": "circle", "paint": {"opacity": 1.0}}
        ),
        seq=2,
    )
    assert upsert.target_id == "schools"
    assert "layer.paint" in upsert.field_paths

    component = serialize_intent(
        PatchComponentIntent(component_id="legend", enabled=False), seq=3
    )
    assert component.target_id == "legend"
    assert component.group == "component"

    mode = serialize_intent(SetScenarioModeIntent(scenario_mode="split_view"), seq=4)
    assert mode.target_id == ""
    assert intent_field_closure(mode) == [
        ("SetScenarioModeIntent", "", "scenario_mode")
    ]


def test_whitelist_rejects_rollback_family():
    """回滚/删除层等全局或不可重放意图拒绝提案。"""
    from app.services.mapspec.intents import RollbackIntent

    with pytest.raises(DeltaCodecError) as ei:
        serialize_intent(RollbackIntent(checkpoint_id="ck-1"), seq=1)
    assert ei.value.code == "intent_kind_unsupported"
    with pytest.raises(DeltaCodecError):
        serialize_intent(RemoveLayerIntent(layer_id="L1"), seq=1)


def test_size_cap_rejects_not_truncates():
    """超限载荷整笔拒绝（红线：绝不静默截断）。"""
    big_layer = {"id": "big", "paint": {"k" * 40: "v" * (MAX_DELTA_ITEM_BYTES)}}
    with pytest.raises(DeltaCodecError) as ei:
        serialize_intent(UpsertLayerIntent(layer=big_layer), seq=1)
    assert ei.value.code == "delta_item_too_large"


def test_empty_intent_rejected():
    with pytest.raises(DeltaCodecError) as ei:
        serialize_intent(SetScenarioModeIntent(scenario_mode=None), seq=1)
    assert ei.value.code == "intent_empty"


def test_unknown_payload_fields_rejected_on_deserialize():
    delta = DeltaIntent(
        seq=1,
        kind="PatchLayerStyleIntent",
        target_id="L1",
        field_paths=["paint.opacity"],
        payload={"layer_id": "L1", "paint": {"opacity": 1.0}, "hacker_field": "x"},
    )
    with pytest.raises(DeltaCodecError) as ei:
        deserialize_intent(delta)
    assert ei.value.code == "payload_fields_unknown"


def test_serialize_intents_sequential_and_total_cap():
    intents = [
        PatchLayerStyleIntent(layer_id=f"L{i}", paint={"opacity": 0.5})
        for i in range(3)
    ]
    deltas = serialize_intents(intents)
    assert [d.seq for d in deltas] == [1, 2, 3]
    assert all(d.group == group_of_kind("PatchLayerStyleIntent") for d in deltas)
    with pytest.raises(DeltaCodecError):
        serialize_intents([])
