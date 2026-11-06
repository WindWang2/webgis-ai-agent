from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed


def _spec(layer_filter):
    return {
        "sources": {"s": {"type": "geojson", "inlineData": {
            "type": "FeatureCollection", "features": [
                {"type": "Feature", "properties": {"k": k}, "geometry": {"type": "Point", "coordinates": [k, k]}}
                for k in (1, 2)
            ],
        }}},
        "layers": [{"id": "dots", "source": "s", "type": "circle", "filter": layer_filter}],
    }


def test_svg_respects_authored_layer_filter():
    result = compile_mapspec_to_svg_detailed(_spec(["==", ["get", "k"], 1]))
    assert result.svg.count("<circle ") == 1


def test_unknown_filter_branch_fails_closed_with_diagnostic():
    result = compile_mapspec_to_svg_detailed(_spec(["any", True, ["within", {}]]))
    assert "<circle " not in result.svg
    assert any(d["code"] == "layer_filter_unsupported" for d in result.diagnostics)
