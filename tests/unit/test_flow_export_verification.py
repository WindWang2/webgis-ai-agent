from app.services.flow_product.export import verify_flow_map_spec


def _spec():
    edge_source = "flow-odf_demo-edges-source"
    return {
        "version": "1.10",
        "sources": {edge_source: {"type": "geojson", "inlineData": {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature", "id": "e1",
                "geometry": {"type": "LineString", "coordinates": [[104, 30], [104.1, 30.1]]},
                "properties": {"id": "e1", "weight": 5},
            }],
        }}},
        "layers": [{
            "id": "flow-odf_demo-edges", "type": "line", "source": edge_source,
            "flow": {
                "schema_version": "od_flow.v1", "product_ref": "ref:flow-product-demo",
                "family_id": "odf_demo", "source_ref": "ref:bench-demo", "source_revision": 2,
                "source_fingerprint": "sha256:source", "product_fingerprint": "sha256:product",
                "direction_mode": "directed", "geometry": "curve", "show_arrows": True,
                "weight_field": "weight", "source_weight_field": "trips", "weight_unit": "trips",
                "supported_modes": ["directed", "undirected-total", "net"], "node_totals_scope": "filtered",
                "stats": {"edges": {"displayed": 1}},
                "encoding": {"transform": "linear"},
            },
            "legend_spec": {"field": "weight", "direction_mode": "directed", "unit": "trips", "transform": "linear"},
        }, {
            "id": "flow-odf_demo-direction", "type": "symbol", "source": edge_source,
            "layout": {"symbol-placement": "line", "text-field": "▶", "text-keep-upright": False},
        }],
        "layout": {"components": []},
    }


def test_directed_complete_product_and_export_receipt_facts():
    result = verify_flow_map_spec(_spec(), rendered_svg="<text>▶</text>")
    assert result["complete"]
    product = result["products"][0]
    assert product["source_ref"] == "ref:bench-demo"
    assert product["source_revision"] == 2
    assert product["direction_mode"] == "directed"
    assert product["declared_edges"] == product["actual_edges"] == 1


def test_missing_direction_stale_legend_and_count_are_not_complete():
    spec = _spec()
    spec["layers"] = [spec["layers"][0]]
    result = verify_flow_map_spec(spec, rendered_svg="<path/>")
    assert not result["complete"]
    assert {issue["code"] for issue in result["issues"]} == {"flow_direction_missing", "flow_direction_render_failed"}

    stale = _spec()
    stale["layers"][0]["legend_spec"]["field"] = "trips"
    stale["layers"][0]["flow"]["stats"]["edges"]["displayed"] = 2
    result = verify_flow_map_spec(stale, rendered_svg="▶")
    assert {issue["code"] for issue in result["issues"]} == {"flow_legend_stale", "flow_edge_count_mismatch"}


def test_undirected_has_no_arrow_and_unexpected_arrow_is_rejected():
    spec = _spec()
    spec["layers"][0]["flow"]["direction_mode"] = "undirected-total"
    spec["layers"][0]["legend_spec"]["direction_mode"] = "undirected-total"
    spec["layers"] = [spec["layers"][0]]
    assert verify_flow_map_spec(spec)["complete"]
    spec["layers"].append(_spec()["layers"][1])
    result = verify_flow_map_spec(spec)
    assert {issue["code"] for issue in result["issues"]} == {"flow_direction_unexpected"}


def test_svg_compiler_emits_flow_incompleteness_verdict_and_diagnostics():
    from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed

    spec = _spec()
    spec["layers"] = [spec["layers"][0]]
    result = compile_mapspec_to_svg_detailed(spec, target_dpi=96)
    assert result.flow_verification["complete"] is False
    codes = {item["code"] for item in result.diagnostics}
    assert "flow_direction_missing" in codes
    assert "flow_direction_render_failed" in codes
