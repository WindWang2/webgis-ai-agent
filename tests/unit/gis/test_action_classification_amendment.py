"""H10：编译链集成 —— 物化后的 IR 经 compile_plan 产出带真实 breaks 的
最小 mutation（DoD：核心制图旅程的参数修改产生确定性派生重算）。"""
from __future__ import annotations

import pytest

from app.lib.cartography.plan_ir import LayerBlueprint, LayerIntent, MapPlanIR
from app.services.gis_action.derive import DERIVE_OK, materialize_classification
from app.services.map_plan_compiler.compiler import compile_plan

_VALUES = [3.0, 8.0, 13.0, 21.0, 34.0, 55.0, 42.0, 17.0, 5.0, 29.0,
           61.0, 11.0, 47.0, 2.0, 38.0, 26.0]


def _geojson() -> dict:
    return {"type": "FeatureCollection", "features": [
        {"properties": {"pop": v}} for v in _VALUES]}


def _ir_with_classification(k: int) -> MapPlanIR:
    bp = LayerBlueprint(
        layer_type="fill",
        classification={"k": k, "method": "quantiles", "palette": "YlOrRd",
                        "field": "pop"},
    )
    return MapPlanIR(ir_id="mpir-t", layer_intents=[
        LayerIntent(intent_id="li-L1", action="present_primary",
                    layer_id="L1", source_ref="ref:d1", blueprint=bp),
    ])


async def _loader(ref: str):
    return _geojson()


@pytest.mark.asyncio
async def test_compiled_upsert_carries_recomputed_breaks():
    """k=7 的分级参数 → 编译产物 legend_spec 携带 8 断点（非裸 token）。"""
    ir, records = await materialize_classification(
        _ir_with_classification(k=7), current={}, load_geojson=_loader)
    assert [r.code for r in records] == [DERIVE_OK]
    comp = compile_plan(ir, {}, base_revision=0, analysis_output_ids=["ref:d1"])
    assert comp.status == "compiled"
    upserts = [m for m in comp.mutations if m.intent == "upsert_layer"]
    assert upserts, "新层应编译出 upsert"
    layer = upserts[0].payload["layer"]
    legend = layer["legend_spec"]
    assert len(legend["breaks"]) == 8
    assert legend["method"] == "quantiles"
    assert len(legend["palette_colors"]) == 7
    assert legend["labels"]


@pytest.mark.asyncio
async def test_compile_deterministic_after_materialization():
    """同输入（IR+数据）两次物化+编译 → 字节级同输出。"""
    async def run():
        ir, _ = await materialize_classification(
            _ir_with_classification(k=5), current={}, load_geojson=_loader)
        return compile_plan(ir, {}, base_revision=0, analysis_output_ids=["ref:d1"])

    c1 = await run()
    c2 = await run()
    assert c1.compile_digest == c2.compile_digest
    assert c1.model_dump() == c2.model_dump()


@pytest.mark.asyncio
async def test_restyle_existing_layer_merges_recomputed_legend():
    """既有层 restyle（amendment 形态）：物化后编译出合并 upsert，
    携带重算 legend（而非只改数字字段）。"""
    current = {
        "layers": [{
            "id": "L1", "source": "ref:d1", "type": "fill",
            "visible": True,
            "legend_spec": {"field": "pop", "breaks": [1, 2, 3],
                            "method": "quantiles", "k": 2},
        }],
    }
    bp = LayerBlueprint(
        layer_type="fill",
        classification={"k": 7, "method": "quantiles", "palette": "YlOrRd"},
    )
    ir = MapPlanIR(ir_id="mpir-t", layer_intents=[
        LayerIntent(intent_id="li-L1", action="restyle", layer_id="L1",
                    source_ref="ref:d1", blueprint=bp),
    ])
    ir2, records = await materialize_classification(
        ir, current=current, load_geojson=_loader)
    assert [r.code for r in records] == [DERIVE_OK]
    comp = compile_plan(ir2, current, base_revision=3, analysis_output_ids=["ref:d1"])
    merges = [m for m in comp.mutations if m.intent == "upsert_layer"]
    assert merges, "legend 变化应编译出合并 upsert"
    legend = merges[0].payload["layer"]["legend_spec"]
    assert len(legend["breaks"]) == 8
    assert legend["field"] == "pop"
    assert merges[0].payload.get("merge") is True
