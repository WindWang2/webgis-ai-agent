import uuid

import pytest

from app.services.flow_product.contracts import FlowProductConfig, FlowProductError
from app.services.flow_product.service import (
    FlowServiceError,
    apply_flow_product_preview,
    inspect_flow_source,
    preview,
    query_flow_product,
)
from app.services.session_data import session_data_manager


def _rows():
    return {"type": "od_table", "rows": [
        {"origin_id": "A", "destination_id": "B", "origin_lng": 104.0,
         "origin_lat": 30.0, "destination_lng": 104.1, "destination_lat": 30.1, "trips": 8},
        {"origin_id": "B", "destination_id": "A", "origin_lng": 104.1,
         "origin_lat": 30.1, "destination_lng": 104.0, "destination_lat": 30.0, "trips": 3},
    ]}


async def _session_with_source():
    sid = f"flow-service-{uuid.uuid4().hex[:8]}"
    source_ref = await session_data_manager.store(sid, _rows(), prefix="bench")
    await session_data_manager.set_alias(sid, source_ref, "odt")
    return sid, source_ref


def _config(**updates):
    values = {
        "od_table_ref": "odt",
        "weight_field": "trips",
        "weight_unit": "trips",
        "geometry": "curve",
        "top_n": 10,
    }
    values.update(updates)
    return FlowProductConfig(**values)


@pytest.mark.asyncio
async def test_preview_apply_query_restore_and_cross_session_denial():
    sid, _ = await _session_with_source()
    other = f"flow-service-{uuid.uuid4().hex[:8]}"
    result = await preview(sid, _config())
    assert result["cache_hit"] == "miss"
    assert result["stats"]["gross"]["displayed"] == 11
    assert result["stats"]["gross"]["retained"] == 1.0
    assert result["can_apply"]
    assert len(result["mapspec_fragment"]["layers"]) == 3

    applied = await apply_flow_product_preview(
        sid, result["product_ref"], expected_revision=result["mapspec_revision"],
        mutation_id="flow-service-apply-1",
    )
    assert applied["success"], applied.get("error_msg")
    assert len(applied["mapspec"]["layers"]) == 3
    duplicate = await apply_flow_product_preview(
        sid, result["product_ref"], expected_revision=result["mapspec_revision"],
        mutation_id="flow-service-apply-1",
    )
    assert duplicate["duplicate"]
    assert len(duplicate["mapspec"]["layers"]) == 3

    node = await query_flow_product(
        sid, result["product_ref"], kind="node", object_id="A", direction="outbound")
    assert node["node"]["outbound"] == 8
    assert node["node"]["inbound"] == 3
    assert node["edges"]["matched_count"] == 1
    assert node["edges"]["items"][0]["weight"] == 8
    edge_id = node["edges"]["items"][0]["id"]
    edge = await query_flow_product(sid, result["product_ref"], kind="edge", object_id=edge_id)
    assert edge["edge"]["gross"] == 11
    assert edge["members"]["matched_count"] == 1

    restored = await query_flow_product(sid, result["product_ref"], kind="product")
    assert restored["stats"] == result["stats"]
    with pytest.raises(FlowServiceError, match="not found"):
        await query_flow_product(other, result["product_ref"], kind="product")
    await session_data_manager.clear_session(sid)
    await session_data_manager.clear_session(other)


@pytest.mark.asyncio
async def test_production_cache_hit_style_reuse_and_source_invalidation():
    sid, source_ref = await _session_with_source()
    first = await preview(sid, _config())
    second = await preview(sid, _config())
    assert second["cache_hit"] == "preview"
    assert second["product_ref"] == first["product_ref"]

    restyled = await preview(sid, _config(width_max_px=12, palette=["#000000", "#ffffff"]))
    assert restyled["cache_hit"] == "analysis"
    assert restyled["aggregation_reused"]
    assert restyled["edge_ref"] == first["edge_ref"]
    # 仅样式不同也是独立产品：不得复用并覆盖先前 preview 的 product_ref
    assert restyled["product_ref"] != first["product_ref"]

    filtered = await preview(sid, _config(top_n=1))
    assert filtered["cache_hit"] == "miss"
    assert filtered["stats"]["gross"]["displayed"] == 8

    await session_data_manager.overwrite(sid, source_ref, _rows())
    stale = await preview(sid, _config())
    assert stale["cache_hit"] == "miss"
    assert stale["source_revision"] == 2
    with pytest.raises(FlowServiceError, match="changed"):
        await query_flow_product(sid, first["product_ref"], kind="product")
    await session_data_manager.clear_session(sid)


@pytest.mark.asyncio
async def test_restyled_preview_does_not_overwrite_prior_product():
    sid, _ = await _session_with_source()
    first = await preview(sid, _config())
    restyled = await preview(sid, _config(width_max_px=12, palette=["#000000", "#ffffff"]))
    assert restyled["cache_hit"] == "analysis"
    assert restyled["product_ref"] != first["product_ref"]

    # 分析缓存复用不得改写先前 preview 的产品载荷
    stored_first = await session_data_manager.get(sid, first["product_ref"])
    assert stored_first["config"]["width_max_px"] == 8.0
    assert stored_first["mapspec"] == first["mapspec_fragment"]
    stored_restyled = await session_data_manager.get(sid, restyled["product_ref"])
    assert stored_restyled["config"]["width_max_px"] == 12.0

    # apply 先前的 ref 必须套用先前 envelope 的样式，而非后一次 preview 的样式
    applied = await apply_flow_product_preview(
        sid, first["product_ref"], expected_revision=first["mapspec_revision"],
        mutation_id="flow-service-restyle-apply-first",
    )
    assert applied["success"], applied.get("error_msg")
    edge_layer = next(
        layer for layer in applied["mapspec"]["layers"]
        if layer["id"].endswith("-edges")
    )
    assert edge_layer["paint"]["width"]["stops"][1][1] == 8.0
    assert edge_layer["flow"]["product_ref"] == first["product_ref"]
    await session_data_manager.clear_session(sid)


@pytest.mark.asyncio
async def test_mode_availability_net_gross_and_undirected_arrows():
    sid, source_ref = await _session_with_source()
    directed = await preview(sid, _config())
    assert "net" in directed["supported_modes"]
    net = await preview(sid, _config(direction_mode="net"))
    assert net["stats"]["gross"]["retained"] == 1.0
    assert net["stats"]["net"]["displayed_magnitude"] == 5
    assert any(layer["id"].endswith("-direction") for layer in net["mapspec_fragment"]["layers"])

    gross = await preview(sid, _config(direction_mode="undirected-total"))
    assert gross["stats"]["edges"]["displayed"] == 1
    assert all(not layer["id"].endswith("-direction") for layer in gross["mapspec_fragment"]["layers"])

    await session_data_manager.overwrite(sid, source_ref, {
        "type": "od_table", "rows": [_rows()["rows"][0]],
    })
    one_way = await preview(sid, _config())
    assert "net" not in one_way["supported_modes"]
    assert one_way["can_apply"]
    with pytest.raises(FlowProductError, match="净流"):
        await preview(sid, _config(direction_mode="net"))
    await session_data_manager.clear_session(sid)


@pytest.mark.asyncio
async def test_inspect_is_bounded_and_source_scoped():
    sid, _ = await _session_with_source()
    result = await inspect_flow_source(sid, _config())
    assert result["operation"] == "inspect"
    assert result["source_ref"].startswith("ref:bench-")
    assert result["fields"]["trips"] == "int"
    assert len(result["sample"]) == 2
    with pytest.raises(FlowServiceError):
        await inspect_flow_source(sid, _config(od_table_ref="missing"))
    await session_data_manager.clear_session(sid)


@pytest.mark.asyncio
async def test_restyle_with_negative_fixed_domain_nonlinear_transform_does_not_crash():
    sid, _ = await _session_with_source()
    built = await preview(sid, _config())
    assert built["cache_hit"] == "miss"
    for transform in ("sqrt", "log1p"):
        restyled = await preview(
            sid, _config(weight_domain=(-10.0, -1.0), weight_transform=transform))
        assert restyled["cache_hit"] == "analysis"
        edge_fc = await session_data_manager.get(sid, restyled["edge_ref"])
        assert all(
            f["properties"]["weight_encoded"] == 0.0
            for f in edge_fc["features"]
        )
    await session_data_manager.clear_session(sid)


@pytest.mark.asyncio
async def test_repeated_preview_after_style_variant_hits_preview_cache():
    sid, _ = await _session_with_source()
    styled = await preview(sid, _config(width_max_px=12, palette=["#000000", "#ffffff"]))
    assert styled["cache_hit"] == "miss"
    # 同一 config 首次经 analysis 缓存 restyle 落产品后，产品指纹必须与
    # build 路径一致（full_key），再次预览才能命中 preview 缓存。
    base = await preview(sid, _config())
    assert base["cache_hit"] == "analysis"
    repeat = await preview(sid, _config())
    assert repeat["cache_hit"] == "preview"
    assert repeat["product_ref"] == base["product_ref"]
    assert repeat["product_fingerprint"] == base["product_fingerprint"]
    await session_data_manager.clear_session(sid)
