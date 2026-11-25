import uuid

import pytest

from app.lib.cartography.mapspec_schema import KNOWN_VERSIONS, LATEST_VERSION, parse_mapspec
from app.services.mapspec.lifecycle_engine import MapSpecLifecycleEngine
from app.services.mapspec.intents import ApplyFlowProductIntent, InitProjectIntent
from app.services.session_data import session_data_manager


def test_flow_layer_schema_is_additive_and_round_trips():
    flow = {
        "schema_version": "od_flow.v1",
        "product_ref": "ref:flow-product-1234567890abcdef",
        "family_id": "odf_test_family",
        "source_ref": "ref:bench-1234567890abcdef",
        "source_revision": 3,
        "source_fingerprint": "sha256:abc",
        "product_fingerprint": "sha256:def",
        "direction_mode": "net",
        "geometry": "curve",
        "show_arrows": True,
        "weight_field": "weight",
        "source_weight_field": "trips",
        "weight_unit": "trips",
        "supported_modes": ["directed", "undirected-total", "net"],
        "node_totals_scope": "filtered",
        "stats": {"displayed": {"edges": 1, "gross": 11.0}},
        "encoding": {"domain": [0, 11], "fixed": True, "transform": "linear"},
        "time_window": {"start": "2026-01-01T08:00:00+08:00", "end_inclusive": False},
    }
    spec = {
        "version": "1.10",
        "sources": {},
        "layers": [{
            "id": "flow-edges",
            "source": "flow-source",
            "type": "line",
            "flow": flow,
        }],
        "layout": {"components": []},
    }
    parsed = parse_mapspec(spec)
    assert parsed.valid
    # 版本链合并后 LATEST 属于更高 additive 版本（G15/G16 号位）；flow 合同
    # 自身锚在 1.10，spec 恒可向前升级解析。
    assert "1.10" in KNOWN_VERSIONS
    assert parsed.document == spec


def _flow_block(product_ref: str, source_ref: str) -> dict:
    return {
        "schema_version": "od_flow.v1",
        "product_ref": product_ref,
        "family_id": "odf_demo",
        "source_ref": source_ref,
        "source_revision": 1,
        "source_fingerprint": "sha256:source",
        "product_fingerprint": "sha256:product",
        "direction_mode": "directed",
        "geometry": "curve",
        "show_arrows": True,
        "weight_field": "weight",
        "source_weight_field": "trips",
        "weight_unit": "trips",
        "supported_modes": ["directed", "undirected-total", "net"],
        "node_totals_scope": "filtered",
        "stats": {"displayed": {"edges": 1, "gross": 8.0}},
        "encoding": {"domain": [0, 8], "fixed": True, "transform": "linear"},
    }


async def _seed_product(session_id: str, *, include_nodes: bool = True) -> tuple[str, str]:
    source_ref = await session_data_manager.store(session_id, {
        "type": "od_table",
        "rows": [{"origin_id": "a", "destination_id": "b", "trips": 8}],
    }, prefix="bench")
    edge_ref = await session_data_manager.store(session_id, {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "id": "ode_edge",
            "geometry": {"type": "LineString", "coordinates": [[104, 30], [104.1, 30.1]]},
            "properties": {"id": "ode_edge", "weight": 8, "weight_encoded": 8},
        }],
    }, prefix="geojson")
    node_ref = await session_data_manager.store(session_id, {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "id": "odn_a",
            "geometry": {"type": "Point", "coordinates": [104, 30]},
            "properties": {"id": "odn_a", "name": "A", "radius_value": 8},
        }],
    }, prefix="flow-nodes")
    product_ref = "ref:flow-product-pending"
    edge_source = "flow-odf_demo-edges-source"
    node_source = "flow-odf_demo-nodes-source"
    layers = [{
        "id": "flow-odf_demo-edges",
        "type": "line",
        "source": edge_source,
        "paint": {"width": {"method": "interpolate", "field": "weight_encoded", "stops": [[0, 1], [8, 8]]}},
        "flow": _flow_block(product_ref, source_ref),
        "metadata": {"flow_family_id": "odf_demo", "flow_part": "edges"},
    }, {
        "id": "flow-odf_demo-direction",
        "type": "symbol",
        "source": edge_source,
        "layout": {
            "symbol-placement": "line",
            "text-field": "▶",
            "text-size": 12,
            "text-rotation-alignment": "map",
            "text-keep-upright": False,
        },
        "metadata": {"flow_family_id": "odf_demo", "flow_part": "direction"},
    }]
    sources = {edge_source: {"type": "geojson", "url": edge_ref}}
    if include_nodes:
        layers.append({
            "id": "flow-odf_demo-nodes",
            "type": "circle",
            "source": node_source,
            "paint": {"circle-radius": {"method": "interpolate", "field": "radius_value", "stops": [[0, 3], [8, 10]]}},
            "metadata": {"flow_family_id": "odf_demo", "flow_part": "nodes"},
        })
        sources[node_source] = {"type": "geojson", "url": node_ref}
    product_ref = await session_data_manager.store(session_id, {
        "schema_version": "od_flow_product.v1",
        "family_id": "odf_demo",
        "source_ref": source_ref,
        "source_revision": 1,
        "edge_ref": edge_ref,
        "node_ref": node_ref,
        "mapspec": {"sources": sources, "layers": layers},
    }, prefix="flow-product")
    for layer in layers:
        if "flow" in layer:
            layer["flow"]["product_ref"] = product_ref
    await session_data_manager.overwrite(session_id, product_ref, {
        "schema_version": "od_flow_product.v1",
        "family_id": "odf_demo",
        "source_ref": source_ref,
        "source_revision": 1,
        "edge_ref": edge_ref,
        "node_ref": node_ref,
        "mapspec": {"sources": sources, "layers": layers},
    })
    return product_ref, source_ref


@pytest.mark.asyncio
async def test_flow_apply_is_atomic_idempotent_and_preserves_user_presentation():
    from app.services.mapspec.intents import PatchLayerPresentationIntent

    session_id = f"flow-map-{uuid.uuid4().hex[:8]}"
    engine = MapSpecLifecycleEngine()
    product_ref, _ = await _seed_product(session_id)
    initialized = await engine.apply_mutation(session_id, InitProjectIntent())
    applied = await engine.apply_mutation(
        session_id,
        ApplyFlowProductIntent(product_ref=product_ref, family_id="odf_demo"),
        origin="agent",
        expected_revision=initialized.mutation_revision,
        mutation_id="flow-apply-first",
    )
    assert not applied.is_error, applied.error_msg
    assert len(applied.mapspec["layers"]) == 3
    assert len(applied.mapspec["sources"]) == 2
    assert applied.mapspec["version"] == "1.10"

    hidden = await engine.apply_mutation(
        session_id,
        PatchLayerPresentationIntent(layer_id="flow-odf_demo-edges", visible=False, opacity=0.2),
        origin="user",
        expected_revision=applied.mutation_revision,
    )
    reapplied = await engine.apply_mutation(
        session_id,
        ApplyFlowProductIntent(product_ref=product_ref, family_id="odf_demo"),
        origin="agent",
        expected_revision=hidden.mutation_revision,
        mutation_id="flow-apply-second",
    )
    assert not reapplied.is_error, reapplied.error_msg
    assert len(reapplied.mapspec["layers"]) == 3
    edge = next(layer for layer in reapplied.mapspec["layers"] if layer["id"] == "flow-odf_demo-edges")
    assert edge["layout"]["visibility"] == "none"
    assert edge["paint"]["opacity"] == 0.2

    duplicate = await engine.apply_mutation(
        session_id,
        ApplyFlowProductIntent(product_ref=product_ref, family_id="odf_demo"),
        origin="agent",
        expected_revision=hidden.mutation_revision,
        mutation_id="flow-apply-second",
    )
    assert duplicate.duplicate
    assert duplicate.mutation_revision == reapplied.mutation_revision
    await session_data_manager.clear_session(session_id)


@pytest.mark.asyncio
async def test_flow_apply_refuses_non_finite_artifact_profile(monkeypatch):
    import app.services.spatial_meta_profiler as profiler

    monkeypatch.setattr(profiler, "profile_from_descriptor", lambda descriptor: {
        "bbox": [float("nan"), 0.0, 1.0, 1.0],
        "crs": None,
        "crs_status": "unknown",
        "featureCount": 1,
        "geometryTypes": [],
        "fields": {},
        "fields_status": "unknown",
        "suggestedView": {},
        "temporalProfile": None,
    })
    session_id = f"flow-map-{uuid.uuid4().hex[:8]}"
    engine = MapSpecLifecycleEngine()
    product_ref, _ = await _seed_product(session_id)
    initialized = await engine.apply_mutation(session_id, InitProjectIntent())
    result = await engine.apply_mutation(
        session_id,
        ApplyFlowProductIntent(product_ref=product_ref, family_id="odf_demo"),
        origin="agent",
        expected_revision=initialized.mutation_revision,
        mutation_id="flow-apply-nan-profile",
    )
    assert result.is_error
    assert result.error_code == "FLOW_PRODUCT_INVALID"
    await session_data_manager.clear_session(session_id)


def test_flow_apply_http_body_uses_the_same_intent():
    from app.schemas.mapspec_mutation_schema import ApplyFlowProductBody
    from app.services.mapspec.intent_codec import body_to_intent

    body = ApplyFlowProductBody(
        intent="apply_flow_product",
        expected_revision=7,
        product_ref="ref:flow-product-1234567890abcdef",
        family_id="odf_demo",
        client_mutation_id="flow-ui-apply-1",
    )
    intent = body_to_intent(body)
    assert isinstance(intent, ApplyFlowProductIntent)
    assert intent.product_ref == body.product_ref
    assert intent.family_id == "odf_demo"
