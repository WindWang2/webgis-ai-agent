"""H10：classification 派生重算测试（参数级语义变化 → 确定性展开）。

核心契约：改分级数 k=5→7 必须产生 breaks/labels/palette_colors 的确定性
重算（而非只改数字字段）；与工具路径（thematic_spec.build_graduated_spec）
同源同结果（parity）。
"""
from __future__ import annotations

import copy

import pytest

from app.lib.cartography.plan_ir import LayerBlueprint, LayerIntent, MapPlanIR
from app.lib.cartography.thematic_spec import build_graduated_spec
from app.services.gis_action.derive import (
    DERIVE_NO_DATA,
    DERIVE_NO_FIELD,
    DERIVE_OK,
    DERIVE_SOURCE_UNRESOLVED,
    classification_params_of,
    derive_classification_spec,
    materialize_classification,
)

_VALUES = [3.0, 8.0, 13.0, 21.0, 34.0, 55.0, 42.0, 17.0, 5.0, 29.0,
           61.0, 11.0, 47.0, 2.0, 38.0, 26.0]


def _geojson(values=_VALUES, field="pop") -> dict:
    return {"type": "FeatureCollection", "features": [
        {"properties": {field: v}} for v in values]}


class TestDeriveSpec:
    def test_k_change_recomputes_breaks(self):
        s5 = derive_classification_spec(_VALUES, "pop", k=5,
                                        method="quantiles", palette="YlOrRd")
        s7 = derive_classification_spec(_VALUES, "pop", k=7,
                                        method="quantiles", palette="YlOrRd")
        assert len(s5["breaks"]) == 6
        assert len(s7["breaks"]) == 8
        assert s5["breaks"] != s7["breaks"]
        assert len(s7["labels"]) == 7
        assert len(s7["palette_colors"]) == 7

    def test_parity_with_tool_path(self):
        """IR 路径与工具路径同源同结果（单一 classification 纪律）。"""
        via_ir = derive_classification_spec(_VALUES, "pop", k=6,
                                            method="natural_breaks",
                                            palette="YlOrRd")
        via_tool = build_graduated_spec(_geojson(), "pop", method="natural_breaks",
                                        k=6, palette="YlOrRd")
        assert via_ir["breaks"] == via_tool["breaks"]
        assert via_ir["palette_colors"] == via_tool["palette_colors"]
        assert via_ir["labels"] == via_tool["labels"]

    def test_deterministic(self):
        a = derive_classification_spec(_VALUES, "pop", k=5, method="quantiles",
                                       palette="YlOrRd")
        b = derive_classification_spec(_VALUES, "pop", k=5, method="quantiles",
                                       palette="YlOrRd")
        assert a == b

    def test_insufficient_data_returns_none(self):
        assert derive_classification_spec([1.0], "pop", k=5,
                                          method="quantiles") is None
        assert derive_classification_spec([], "pop", k=5,
                                          method="quantiles") is None

    def test_classification_params_of_tolerant(self):
        params = classification_params_of({"k": "7", "method": "quantiles"})
        assert params.k == 7 and params.method == "quantiles"
        assert not classification_params_of(None).present
        assert not classification_params_of({"k": "abc"}).present


def _layer_intent(layer_id="L1", source="ref:d1", k=5,
                  classification=None, legend=None) -> LayerIntent:
    bp = LayerBlueprint(
        layer_type="fill",
        classification=classification if classification is not None
        else {"k": k, "method": "quantiles", "palette": "YlOrRd",
              "field": "pop"},
        legend_spec=legend or {},
    )
    return LayerIntent(intent_id=f"li-{layer_id}", action="present_primary",
                       layer_id=layer_id, source_ref=source, blueprint=bp)


def _ir(*intents) -> MapPlanIR:
    ir = MapPlanIR(ir_id="mpir-t", layer_intents=list(intents))
    return ir


class TestMaterialize:
    @pytest.mark.asyncio
    async def test_materializes_full_legend_from_classification(self):
        ir = _ir(_layer_intent())
        loaders = {"ref:d1": _geojson()}

        async def loader(ref):
            return loaders.get(ref)

        updated, records = await materialize_classification(
            ir, current={}, load_geojson=loader)
        assert [r.code for r in records] == [DERIVE_OK]
        legend = updated.layer_intents[0].blueprint.legend_spec
        assert len(legend["breaks"]) == 6          # k=5 → 6 breaks
        assert legend["method"] == "quantiles"
        assert legend["labels"] and legend["palette_colors"]
        assert records[0].breaks_count == 6

    @pytest.mark.asyncio
    async def test_k_amendment_changes_derived_breaks(self):
        """DoD：把分级数改成 7 → breaks/legend 确定性重算为 7 级。"""
        ir5 = _ir(_layer_intent(k=5))
        ir7 = _ir(_layer_intent(k=7))

        async def loader(ref):
            return _geojson()

        up5, _ = await materialize_classification(ir5, current={}, load_geojson=loader)
        up7, _ = await materialize_classification(ir7, current={}, load_geojson=loader)
        b5 = up5.layer_intents[0].blueprint.legend_spec["breaks"]
        b7 = up7.layer_intents[0].blueprint.legend_spec["breaks"]
        assert len(b5) == 6 and len(b7) == 8
        assert b5 != b7

    @pytest.mark.asyncio
    async def test_field_from_current_layer(self):
        """既有层 restyle：字段取自 current legend_spec（amendment 只带 k）。"""
        current = {"layers": [{"id": "L1", "source": "ref:d1",
                               "legend_spec": {"field": "pop"}}]}
        ir = _ir(_layer_intent(classification={"k": 7}))
        seen = {}

        async def loader(ref):
            seen["ref"] = ref
            return _geojson()

        updated, records = await materialize_classification(
            ir, current=current, load_geojson=loader)
        assert seen["ref"] == "ref:d1"
        assert records[0].code == DERIVE_OK
        assert updated.layer_intents[0].blueprint.legend_spec["field"] == "pop"

    @pytest.mark.asyncio
    async def test_source_unresolved_falls_back_typed(self):
        ir = _ir(_layer_intent())

        async def loader(ref):
            return None

        updated, records = await materialize_classification(
            ir, current={}, load_geojson=loader)
        assert [r.code for r in records] == [DERIVE_SOURCE_UNRESOLVED]
        # 回落：IR 原样（token 形态，向后兼容既有行为）。
        assert updated.layer_intents[0].blueprint.legend_spec == {}

    @pytest.mark.asyncio
    async def test_no_numeric_data_typed(self):
        ir = _ir(_layer_intent(source="ref:empty"))

        async def loader(ref):
            return _geojson(values=[], field="other")

        _, records = await materialize_classification(
            ir, current={}, load_geojson=loader)
        assert records[0].code == DERIVE_NO_DATA

    @pytest.mark.asyncio
    async def test_no_field_typed(self):
        ir = _ir(_layer_intent(classification={"k": 5, "field": ""}))
        current = {"layers": [{"id": "L1", "source": "ref:d1"}]}

        async def loader(ref):
            return _geojson()

        _, records = await materialize_classification(
            ir, current=current, load_geojson=loader)
        assert records[0].code == DERIVE_NO_FIELD

    @pytest.mark.asyncio
    async def test_loader_crash_fails_closed_to_unresolved(self):
        ir = _ir(_layer_intent())

        async def loader(ref):
            raise RuntimeError("store down")

        _, records = await materialize_classification(
            ir, current={}, load_geojson=loader)
        assert records[0].code == DERIVE_SOURCE_UNRESOLVED

    @pytest.mark.asyncio
    async def test_already_materialized_legend_skipped(self):
        full = build_graduated_spec(_geojson(), "pop", method="quantiles",
                                    k=5, palette="YlOrRd")
        ir = _ir(_layer_intent(legend=copy.deepcopy(full)))
        called = []

        async def loader(ref):
            called.append(ref)
            return _geojson()

        updated, records = await materialize_classification(
            ir, current={}, load_geojson=loader)
        assert called == []
        assert records == []
        assert updated.layer_intents[0].blueprint.legend_spec["breaks"] == full["breaks"]
