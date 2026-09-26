"""Purpose Presets 契约测试（ADR-0214 D7）.

覆盖：F11 四目的 bundle 齐备、引用完整性 fail-closed 校验干净、
bundle 与契约的锚定一致性、有界载荷形态、确定性。
"""
from __future__ import annotations

import pytest

from app.lib.cartography.component_presets import (
    PURPOSE_PRESETS,
    PurposeBundle,
    purpose_bundles_bounded,
    validate_purpose_presets,
)

pytestmark = pytest.mark.cartography

EXPECTED_PURPOSES = (
    "basic_thematic",
    "heat_distribution_stats",
    "classified_categorical",
    "change_comparison",
)


class TestBundleSurface:
    def test_four_purposes_present(self):
        assert set(PURPOSE_PRESETS) == set(EXPECTED_PURPOSES)

    def test_bundle_contract_alignment(self):
        from app.lib.cartography.composition_contract import get_contract_registry

        contract_reg = get_contract_registry()
        for purpose, bundle in PURPOSE_PRESETS.items():
            contract = contract_reg.get(bundle.contract_id)
            assert contract is not None, purpose
            assert contract.purpose == purpose
            assert contract.template_id == bundle.composition_template_id

    def test_bundles_are_reference_only_models(self):
        for bundle in PURPOSE_PRESETS.values():
            assert isinstance(bundle, PurposeBundle)
            assert bundle.composition_template_id.startswith("composition.")
            assert bundle.contract_id.startswith("contract.core.")


class TestFailClosedValidation:
    def test_pristine_validate_clean(self):
        assert validate_purpose_presets() == []

    def test_slot_presets_grounded_in_template_slots(self):
        """slot 预设的 key 必须真实存在（构造性抽查 + 校验器兜底）。"""
        from app.lib.cartography.composition_templates import (
            get_composition_template_registry,
        )

        reg = get_composition_template_registry()
        for bundle in PURPOSE_PRESETS.values():
            tpl = reg.get(bundle.composition_template_id)
            assert tpl is not None
            slot_ids = {s.id for s in tpl.component_slots}
            assert set(bundle.slot_presets) <= slot_ids

    def test_slot_preset_values_are_native_and_type_allowed(self):
        from app.lib.cartography.component_templates import (
            get_component_template_registry,
        )
        from app.lib.cartography.composition_templates import (
            get_composition_template_registry,
        )

        tmpl_reg = get_component_template_registry()
        compo_reg = get_composition_template_registry()
        for bundle in PURPOSE_PRESETS.values():
            tpl = compo_reg.get(bundle.composition_template_id)
            for slot_id, preset_id in bundle.slot_presets.items():
                ct = tmpl_reg.get(preset_id)
                assert ct is not None, preset_id
                assert ct.runtime_status == "native", preset_id
                slot = next(s for s in tpl.component_slots if s.id == slot_id)
                assert ct.component_type in slot.allowed_component_types

    def test_broken_reference_is_reported(self):
        """引用完整性是行为而非装饰：注入坏 bundle 必须产出 issue。"""
        broken = PurposeBundle(
            purpose="broken_fixture",
            composition_template_id="composition.does_not_exist",
            contract_id="contract.core.does_not_exist",
            slot_presets={"ghost_slot": "ghost/template"},
        )
        PURPOSE_PRESETS["broken_fixture"] = broken
        try:
            issues = validate_purpose_presets()
        finally:
            PURPOSE_PRESETS.pop("broken_fixture")
        assert any("composition.does_not_exist" in i for i in issues)
        assert any("contract.core.does_not_exist" in i for i in issues)
        assert any("ghost_slot" in i for i in issues)
        assert validate_purpose_presets() == []  # 恢复后干净


class TestBoundedPayload:
    def test_shape_and_bounds(self):
        payload = purpose_bundles_bounded()
        assert [b["purpose"] for b in payload] == sorted(EXPECTED_PURPOSES)
        for bundle in payload:
            assert set(bundle) == {
                "purpose", "composition_template_id", "contract_id",
                "slot_presets", "style_token_preset", "description",
            }
            assert len(bundle["purpose"]) <= 32
            assert len(bundle["composition_template_id"]) <= 48
            assert len(bundle["contract_id"]) <= 48
            assert len(bundle["style_token_preset"]) <= 16
            assert len(bundle["description"]) <= 160
            assert len(bundle["slot_presets"]) <= 8

    def test_deterministic(self):
        assert purpose_bundles_bounded() == purpose_bundles_bounded()
