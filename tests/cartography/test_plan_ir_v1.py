"""MapPlanIR 契约测试（F12 / ADR-0214 D1）。

锁定：版本化、内容寻址、确定性指纹、refs-only 有界性（payload 走私 =
构造期拒绝）、frozen 不可变性。
"""
import pytest
from pydantic import ValidationError

from app.lib.cartography.plan_ir import (
    IR_ID_PREFIX,
    PLAN_IR_VERSION,
    ComponentIntent,
    LayerBlueprint,
    LayerIntent,
    MapPlanIR,
    compute_ir_id,
)


def _ir(**overrides) -> MapPlanIR:
    payload = dict(
        ir_id=f"{IR_ID_PREFIX}test0001",
        layer_intents=[LayerIntent(
            intent_id="li-01-primary", action="present_primary",
            layer_id="pl-1", source_ref="ds:pop",
            blueprint=LayerBlueprint(layer_type="fill", paint={"fill-color": "#333"}),
        )],
        component_intents=[ComponentIntent(
            intent_id="ci-g-legend", component_type="legend",
            component_id="comp-legend", required=True,
        )],
        disclosures=["methodology_warnings:1"],
        reason_codes=["PLAN_PROJECTED"],
    )
    payload.update(overrides)
    return MapPlanIR(**payload)


@pytest.mark.cartography
def test_ir_version_and_identity_deterministic():
    ir1 = _ir()
    ir2 = _ir()
    assert ir1.ir_version == PLAN_IR_VERSION == "1.0.0"
    assert ir1.ir_fingerprint() == ir2.ir_fingerprint()
    assert ir1.ir_fingerprint().startswith("mpir-sha256:")


@pytest.mark.cartography
def test_ir_fingerprint_changes_with_semantic_change():
    ir1 = _ir()
    ir2 = _ir(disclosures=["methodology_warnings:2"])
    assert ir1.ir_fingerprint() != ir2.ir_fingerprint()


@pytest.mark.cartography
def test_compute_ir_id_excludes_ir_id_itself():
    a = compute_ir_id({"ir_id": "mpir-aaa", "x": 1, "y": [1, 2]})
    b = compute_ir_id({"ir_id": "mpir-bbb", "x": 1, "y": [1, 2]})
    assert a == b, "ir_id 不参与内容寻址（重放对齐键）"
    assert a.startswith(IR_ID_PREFIX)


@pytest.mark.cartography
def test_ir_is_frozen():
    ir = _ir()
    with pytest.raises(ValidationError):
        ir.revision = 5


@pytest.mark.cartography
def test_blueprint_rejects_payload_smuggling_by_key_count():
    big_paint = {f"key-{i}": i for i in range(30)}
    with pytest.raises(ValidationError, match="键数"):
        LayerBlueprint(layer_type="fill", paint=big_paint)


@pytest.mark.cartography
def test_blueprint_rejects_payload_smuggling_by_bytes():
    fat = {"fill-color": "x" * 9000}
    with pytest.raises(ValidationError, match="字节"):
        LayerBlueprint(layer_type="fill", paint=fat)


@pytest.mark.cartography
def test_ir_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        _ir(mystery_payload={"huge": "data"})


@pytest.mark.cartography
def test_layer_intent_bounds():
    with pytest.raises(ValidationError):
        LayerIntent(intent_id="", action="present_primary")
    with pytest.raises(ValidationError):
        LayerIntent(intent_id="li-x", action="teleport")  # type: ignore[arg-type]


@pytest.mark.cartography
def test_model_dump_roundtrip_is_canonical():
    ir = _ir()
    dumped = ir.model_dump(mode="json")
    ir2 = MapPlanIR(**dumped)
    assert ir2 == ir
    assert ir2.ir_fingerprint() == ir.ir_fingerprint()
