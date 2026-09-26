"""Core Purposes Pack 契约测试（ADR-0214 D8 / F11）.

覆盖：composition.classified_categorical 注册语义（priority=46、分类图例
required 槽位 all_thematic 绑定）、配对契约可解析且注册表干净、三注册表
（composition/contract/template spec）创作期校验零 issue、以及**默认选择
零漂移**红线（seed 优先序 / administrative_choropleth 默认不被新模板抢占 /
组合候选不落新模板）。
"""
from __future__ import annotations

import pytest

from app.lib.cartography.composition_packs import COMPOSITION_PACK_TEMPLATES
from app.lib.cartography.composition_packs.core_purposes import (
    CLASSIFIED_CATEGORICAL_CONTRACT,
    CLASSIFIED_MODELS,
    CORE_PURPOSES_PACK,
)
from app.lib.cartography.composition_templates import (
    SEED_COMPOSITION_TEMPLATES,
    get_composition_template_registry,
)
from app.lib.cartography.composition_selection import (
    TaskCartographyContext,
    select_composition_alternatives,
)
from app.services.gis_harness.component_resolver import ComponentResolver

pytestmark = pytest.mark.cartography

CLASSIFIED_TEMPLATE_ID = "composition.classified_categorical"
CLASSIFIED_CONTRACT_ID = "contract.core.classified_categorical"


# ── 模板注册语义 ─────────────────────────────────────────────────────────


class TestClassifiedTemplate:
    def test_registered_with_priority_46(self):
        reg = get_composition_template_registry()
        tpl = reg.get(CLASSIFIED_TEMPLATE_ID)
        assert tpl is not None
        assert tpl.priority == 46
        assert tpl in COMPOSITION_PACK_TEMPLATES
        assert tpl.fallback_template == "composition.standard_analysis"

    def test_categorical_legend_slot_required_all_thematic(self):
        reg = get_composition_template_registry()
        tpl = reg.get(CLASSIFIED_TEMPLATE_ID)
        slot = next(s for s in tpl.component_slots if s.id == "legend")
        assert slot.cardinality == "required" and slot.required
        assert slot.bind_scope == "all_thematic"
        assert slot.allowed_component_types[0] == "categorical_legend"

    def test_pack_registered_exactly_once(self):
        ids = [t.id for t in CORE_PURPOSES_PACK]
        assert ids == [CLASSIFIED_TEMPLATE_ID]
        reg = get_composition_template_registry()
        assert sum(1 for t in reg.all_templates()
                   if t.id == CLASSIFIED_TEMPLATE_ID) == 1

    def test_compatible_models_resolve(self):
        from app.lib.cartography.model_library import get_map_model_registry

        model_reg = get_map_model_registry()
        for mid in CLASSIFIED_MODELS:
            assert model_reg.resolve(mid) is not None, mid


# ── 配对契约 ─────────────────────────────────────────────────────────────


class TestClassifiedContract:
    def test_resolvable_via_contract_registry(self):
        from app.lib.cartography.composition_contract import get_contract_registry

        contract = get_contract_registry().get(CLASSIFIED_CONTRACT_ID)
        assert contract is not None
        assert contract.template_id == CLASSIFIED_TEMPLATE_ID
        assert contract.purpose == "classified_categorical"
        assert CLASSIFIED_CATEGORICAL_CONTRACT is contract or \
            CLASSIFIED_CATEGORICAL_CONTRACT == contract

    def test_legend_slot_prefers_categorical_academic(self):
        from app.lib.cartography.composition_contract import get_contract_registry

        contract = get_contract_registry().get(CLASSIFIED_CONTRACT_ID)
        legend = next(s for s in contract.slots if s.slot_id == "legend")
        assert legend.preferred_template == "categorical-legend/academic"


# ── 三注册表创作期校验全部干净 ───────────────────────────────────────────


class TestRegistriesValidateClean:
    def test_composition_registry_validate_clean(self):
        # 含本方向新增的 slot zone 创作期检查（D5 接线）
        assert get_composition_template_registry().validate() == []

    def test_contract_registry_validate_clean(self):
        from app.lib.cartography.composition_contract import (
            get_contract_registry,
        )

        assert get_contract_registry().validate() == []

    def test_template_spec_registry_validate_clean(self):
        from app.lib.cartography.template_intelligence import (
            get_template_spec_registry,
            reset_template_spec_registry,
        )
        from app.lib.cartography.component_registry import get_component_registry
        from app.lib.cartography.composition_templates import (
            get_composition_template_registry,
        )
        from app.lib.gis.artifacts import get_artifact_type_registry
        from app.lib.gis.capability_registry import get_capability_registry
        from app.lib.gis.methodology.taxonomy import get_task_taxonomy
        from app.services.gis_harness.workflow_schema import DATA_ROLES
        from app.services.gis_harness.workflow_v4.methodology import (
            get_methodology_registry,
        )

        reset_template_spec_registry()
        spec_reg = get_template_spec_registry()
        spec = spec_reg.get("spec.classified_categorical")
        assert spec is not None
        assert spec.base_composition_template_id == CLASSIFIED_TEMPLATE_ID
        assert spec.provenance_id == "f11-core-purposes"
        comps = get_component_registry()
        assert spec_reg.validate(
            composition_exists=get_composition_template_registry().has,
            category_exists=get_task_taxonomy().has,
            family_exists=lambda f: get_methodology_registry().family(f) is not None,
            artifact_type_exists=get_artifact_type_registry().has,
            component_exists=lambda c: comps.has(c)
            or comps.get_by_type(c) is not None,
            capability_exists=get_capability_registry().has,
            data_role_vocabulary=tuple(DATA_ROLES),
        ) == []


# ── 默认选择零漂移（红线）────────────────────────────────────────────────


class TestDefaultSelectionUnchanged:
    def test_find_for_map_model_keeps_seeds_first(self):
        """新模板（通用型，priority=46）对全部模型可见但**永不带头**：
        任何模型的候选头部必须仍是 seed 或更高优先级既有模板
        （golden corpus 已锁 577 例零漂移 —— 本断言是快速护栏）。"""
        reg = get_composition_template_registry()
        seed_ids = {t.id for t in SEED_COMPOSITION_TEMPLATES}
        for model_id in ("administrative_choropleth", "aggregate_grid",
                         "categorical_thematic", "zoning_planning",
                         "classified_raster", "visual_heatmap"):
            candidates = [
                t for t in reg.find_for_map_model(model_id)
                if not t.compatible_map_models or model_id in t.compatible_map_models
            ]
            assert candidates, model_id
            assert candidates[0].id != CLASSIFIED_TEMPLATE_ID, (
                f"{model_id}: 候选头部被新模板抢占 -> {candidates[0].id}")
            assert candidates[0].id in seed_ids or candidates[0].priority <= 46

    def test_resolver_default_for_choropleth_unchanged(self):
        resolver = ComponentResolver()
        sel = resolver.resolve(
            map_model_id="administrative_choropleth",
            output_target="interactive")
        assert sel.composition_template_id == "composition.standard_analysis"

    def test_alternatives_never_pick_classified_for_choropleth(self):
        ctx = TaskCartographyContext(
            task_categories=("administrative_aggregation",),
            geometry_kind="polygon",
            variable_kind="rate",
            statistic="rate",
            output_target="pdf",
            artifact_types=("admin_aggregate_table",),
        )
        alts = select_composition_alternatives(ctx)
        assert alts
        assert all(a.composition_template_id != CLASSIFIED_TEMPLATE_ID
                   for a in alts)

    def test_golden_matrix_head_stable_across_all_models(self):
        """全模型扫描：新模板（通用型）不得成为任何模型的默认候选头部。
        （base 矩阵 577 例 golden 已证明零漂移 —— 本断言防回归。）"""
        from app.lib.cartography.model_library import get_map_model_registry
        reg = get_composition_template_registry()
        model_reg = get_map_model_registry()
        for model_id in sorted(getattr(model_reg, "_by_id", {}).keys()):
            candidates = reg.find_for_map_model(model_id)
            assert candidates, model_id
            assert candidates[0].id != CLASSIFIED_TEMPLATE_ID, model_id
