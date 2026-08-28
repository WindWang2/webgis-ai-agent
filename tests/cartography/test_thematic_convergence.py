"""ADR-0078 thematic-style convergence regression suite.

Pins the single-source-of-truth contract: ``legend_spec`` is the canonical
thematic style; MapSpec ``paint`` and the legend UI are deterministic
projections of it. These tests are the GREEN target for the drift regressions
(field drift, color drift, no-data, divergent, cardinality, domain coverage)
and the compatibility guarantees (legacy legend_spec, NOT_EVALUATED policy).
"""
from app.lib.cartography import thematic_spec as ts
from app.lib.cartography.semantic_checks import evaluate_cartography_semantics
from app.services.analysis_cartography_converter import convert_analysis_to_mapspec_layer


# ─── helpers ────────────────────────────────────────────────────────────────


def _mapspec(layers, sources=None, layout=None):
    return {
        "version": "1.0",
        "sources": sources or {
            "src1": {
                "type": "geojson",
                "inlineData": {"type": "FeatureCollection", "features": []},
            }
        },
        "layers": layers,
        **({"layout": layout} if layout else {}),
    }


def _graduated_layer(breaks, colors, field="population", legend_field=None, paint_field=None,
                     layer_id="L1", source="src1", palette="YlOrRd", labels=None, nodata=None):
    """Build a layer with a step paint AND a matching legend_spec (drift-free by default)."""
    legend_field = legend_field if legend_field is not None else field
    paint_field = paint_field if paint_field is not None else field
    stops = [[float(breaks[i]), colors[i]] for i in range(1, len(breaks) - 1)]
    paint = {"color": {"method": "step", "field": paint_field,
                       "default": colors[0], "stops": stops}}
    legend = {"type": "graduated", "field": legend_field, "breaks": list(breaks),
              "palette": palette, "palette_colors": list(colors)}
    if labels is not None:
        legend["labels"] = labels
    if nodata is not None:
        legend["nodata"] = nodata
    return {"id": layer_id, "source": source, "type": "fill", "paint": paint, "legend_spec": legend}


def _profile(field="population", ftype="number", fmin=0.0, fmax=100.0,
             sample_values=None, null_count=None):
    info = {"type": ftype}
    if ftype == "number":
        info["min"], info["max"] = fmin, fmax
    if sample_values is not None:
        info["sampleValues"] = sample_values
    if null_count is not None:
        info["null_count"] = null_count
    return {"src1": {"featureCount": 50, "geometryTypes": ["Polygon"], "fields": {field: info}}}


def _check_names(report, evaluated_only=True):
    return {f.check for f in report.findings if (f.evaluated or not evaluated_only)}


# ─── spec_to_paint projections (parity with converter) ──────────────────────


def test_spec_to_paint_graduated_matches_converter_contract():
    spec = {"type": "graduated", "field": "pop", "breaks": [0.0, 10.0, 20.0, 30.0],
            "palette_colors": ["#a", "#b", "#c"]}
    paint, warns = ts.spec_to_paint(spec)
    assert paint == {"method": "step", "field": "pop", "default": "#a",
                     "stops": [[10.0, "#b"], [20.0, "#c"]]}
    assert warns == []


def test_spec_to_paint_continuous_even_stops():
    spec = {"type": "continuous", "field": "d", "min": 0.0, "max": 100.0,
            "palette_colors": ["#1", "#2", "#3"]}
    paint, _ = ts.spec_to_paint(spec)
    assert paint["method"] == "interpolate"
    assert paint["stops"] == [[0.0, "#1"], [50.0, "#2"], [100.0, "#3"]]


def test_spec_to_paint_categorical_default_is_last_color():
    spec = {"type": "categorical", "field": "use",
            "categories": [{"key": "r", "color": "#0", "label": "Res"},
                           {"key": "c", "color": "#1", "label": "Com"}]}
    paint, _ = ts.spec_to_paint(spec)
    assert paint["default"] == "#1"  # last category, NOT legend_spec.default
    assert paint["cases"] == [["r", "#0"], ["c", "#1"]]


def test_spec_to_paint_divergent_interpolates_domain():
    spec = ts.build_divergent_spec([-100, -5, 0, 7, 100], "dev", center=0, palette="Viridis")
    assert spec is not None
    paint, _ = ts.spec_to_paint(spec)
    assert paint["method"] == "interpolate"
    assert paint["stops"][0][0] == spec["min"]
    assert paint["stops"][-1][0] == spec["max"]


# ─── single classification + finite filtering ───────────────────────────────


def test_build_graduated_spec_filters_nan_inf_null():
    geojson = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
         "properties": {"v": v}} for v in [1, 2, 3, 4, 5, float("nan"), float("inf"), None, "x"]
    ]}
    spec = ts.build_graduated_spec(geojson, "v", method="quantiles", k=4, palette="YlOrRd")
    assert spec is not None
    # NaN/Inf/None/str filtered: breaks computed over [1..5] only — no NaN leaks.
    import math
    assert all(math.isfinite(b) for b in spec["breaks"])
    assert spec["field"] == "v"
    assert len(spec["palette_colors"]) == max(1, len(spec["breaks"]) - 1)


def test_build_graduated_spec_colors_match_breaks_cardinality():
    geojson = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
         "properties": {"v": float(i)}} for i in range(1, 21)
    ]}
    spec = ts.build_graduated_spec(geojson, "v", method="equal_interval", k=5, palette="YlOrRd")
    assert spec is not None
    assert len(spec["palette_colors"]) == len(spec["breaks"]) - 1


def test_build_graduated_spec_defaults_nodata_rule():
    """ADR-0078 no-data semantics: graduated/continuous/divergent specs default to
    a no-data rule so null/missing values are diverted on the live map rather
    than coerced by to-number into the lowest class."""
    geojson = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
         "properties": {"v": float(i)}} for i in range(1, 11)
    ]}
    spec = ts.build_graduated_spec(geojson, "v", method="quantiles", k=4, palette="YlOrRd")
    assert spec is not None and spec.get("nodata") is not None
    assert "color" in spec["nodata"] and "label" in spec["nodata"]
    # continuous + divergent also default nodata
    cont = ts.build_continuous_spec(0.0, 10.0, "Blues", field="d")
    assert cont is not None and cont.get("nodata") is not None
    div = ts.build_divergent_spec([-5, 0, 5], "d", center=0, palette="Viridis")
    assert div is not None and div.get("nodata") is not None
    # a caller can still override nodata
    custom = ts.build_graduated_spec(geojson, "v", k=4, palette="YlOrRd",
                                     nodata={"color": "#000", "label": "missing"})
    assert custom["nodata"] == {"color": "#000", "label": "missing"}


# ─── semantic checks: drift regressions ─────────────────────────────────────


# ─── #782: builders' degenerate single-class domains pass the review ────────


def test_constant_field_graduated_spec_passes_classification_integrity():
    """#782: build_graduated_spec 对常量字段刻意产出 [v, v] 单类 spec（#618-19
    归一化）—— 评审不得把系统自身构建器的合法输出判成失败。"""
    geojson = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
         "properties": {"v": 5.0}} for _ in range(6)
    ]}
    legend = ts.build_graduated_spec(geojson, "v", method="quantiles", k=4, palette="YlOrRd")
    assert legend is not None
    assert legend["breaks"] == [5.0, 5.0], "常量字段应归一化为单类 [v, v]"

    paint, _warns = ts.spec_to_paint(legend)
    layer = {"id": "L1", "source": "src1", "type": "fill",
             "paint": {"color": paint}, "legend_spec": legend}
    report = evaluate_cartography_semantics(_mapspec([layer]), _profile(field="v", fmin=5.0, fmax=5.0))
    checks = {c.rule: c for c in report.checks}
    integ = checks.get("CLASSIFICATION_INTEGRITY")
    assert integ is not None
    assert integ.status == "pass", (
        f"degenerate single-class graduated spec must pass, got {integ.status}"
    )
    assert integ.evidence.get("single_class_degenerate") is True


def test_raster_min_max_equal_continuous_legend_is_degenerate_not_invalid():
    """#782: raster_cartography_converter 对常量/全 nodata 栅格产出 min==max
    连续 legend（刻意的退化域）—— _classification_integrity 不得判 fail；
    min>max 仍是非法域。"""
    from app.lib.cartography.semantic_checks import _classification_integrity

    legend = {"type": "continuous", "min": 0.3, "max": 0.3, "palette": "Viridis",
              "palette_colors": ["#440154", "#3b528b", "#fde725"]}
    status, evidence, _msg = _classification_integrity(legend)
    assert status == "pass"
    assert evidence["degenerate_single_class"] is True

    inverted = {"type": "continuous", "min": 5.0, "max": 1.0, "palette": "Viridis",
                "palette_colors": ["#440154", "#3b528b", "#fde725"]}
    status, _, _msg = _classification_integrity(inverted)
    assert status == "fail", "min>max 仍是真正的非法域"


def test_graduated_equal_adjacent_breaks_beyond_single_class_still_fails():
    """#782 放行的只有两断点完全相等的单类形态；多断点但相邻相等仍失败。"""
    from app.lib.cartography.semantic_checks import _classification_integrity

    legend = {"type": "graduated", "field": "v", "breaks": [0.0, 5.0, 5.0, 10.0],
              "palette": "YlOrRd", "palette_colors": ["#a", "#b", "#c"]}
    status, _, _msg = _classification_integrity(legend)
    assert status == "fail"


def test_legend_field_drift_is_detected():
    # Map paints field=population, legend declares field=income → drift.
    layer = _graduated_layer([0, 10, 20, 30], ["#a", "#b", "#c"],
                             paint_field="population", legend_field="income")
    report = evaluate_cartography_semantics(_mapspec([layer]))
    assert "LEGEND_STYLE_EQUIVALENCE" in _check_names(report)
    assert any("income" in f.message and "population" in f.message for f in report.findings)


def test_legend_color_drift_is_detected():
    # Map paints A/B/C, legend shows A/X/C.
    layer = _graduated_layer(
        [0, 10, 20, 30], ["#aaaaaa", "#bbbbbb", "#cccccc"]
    )
    # Tamper: legend palette_colors diverge from paint output colors.
    layer["legend_spec"]["palette_colors"] = ["#a", "#X", "#c"]
    report = evaluate_cartography_semantics(_mapspec([layer]))
    assert "LEGEND_STYLE_EQUIVALENCE" in _check_names(report)


def test_drift_free_graduated_passes_clean():
    # Colors must be perceptually separable — the near-gray ramp this test
    # used (#aaaaaa/#bbbbbb/#cccccc, ΔE00 < 5) now correctly fails
    # carto.color.separability, so the clean-pass fixture carries a
    # separable Reds-subset ramp instead.
    layer = _graduated_layer(
        [0, 10, 20, 30], ["#fee5d9", "#fb6a4a", "#a50f15"]
    )
    report = evaluate_cartography_semantics(
        _mapspec([layer]), _profile(fmin=0, fmax=30, null_count=0)
    )
    names = _check_names(report)
    assert "LEGEND_STYLE_EQUIVALENCE" not in names
    assert "CLASSIFICATION_CARDINALITY" not in names
    assert report.no_deterministic_failures
    assert not report.errors


# ─── semantic checks: numeric bad field ─────────────────────────────────────


def test_interpolate_on_string_field_flagged():
    paint = {"color": {"method": "interpolate", "field": "name",
                       "stops": [[0, "#a"], [10, "#b"]]}}
    layer = {"id": "L1", "source": "src1", "type": "fill", "paint": paint}
    report = evaluate_cartography_semantics(_mapspec([layer]), _profile(field="name", ftype="string"))
    assert "INTERPOLATE_NUMERIC_FIELD" in _check_names(report)


# ─── semantic checks: domain mismatch ───────────────────────────────────────


def test_breaks_outside_data_range_is_error():
    # data range 0..100, breaks 1000..2000 → no overlap.
    layer = _graduated_layer([1000, 1500, 2000], ["#a", "#b"])
    report = evaluate_cartography_semantics(_mapspec([layer]), _profile(fmin=0, fmax=100))
    names = _check_names(report)
    assert "CLASSIFICATION_DOMAIN_COVERAGE" in names
    assert any(f.severity == "error" for f in report.findings if f.check == "CLASSIFICATION_DOMAIN_COVERAGE")


def test_breaks_partial_coverage_is_warning():
    # data 0..100, breaks 30..60 → covers < 75% → warning (not error).
    layer = _graduated_layer([30, 45, 60], ["#a", "#b"])
    report = evaluate_cartography_semantics(_mapspec([layer]), _profile(fmin=0, fmax=100))
    cov = [f for f in report.findings if f.check == "CLASSIFICATION_DOMAIN_COVERAGE"]
    assert cov and all(f.severity == "warning" for f in cov)


# ─── semantic checks: categorical ───────────────────────────────────────────


def test_categorical_drift_free():
    legend = {"type": "categorical", "field": "use", "categories": [
        {"key": "residential", "color": "#0", "label": "Res"},
        {"key": "commercial", "color": "#1", "label": "Com"},
        {"key": "industrial", "color": "#2", "label": "Ind"}]}
    paint = {"color": {"method": "match", "field": "use",
                       "cases": [["residential", "#0"], ["commercial", "#1"], ["industrial", "#2"]],
                       "default": "#2"}}
    layer = {"id": "L1", "source": "src1", "type": "fill", "paint": paint, "legend_spec": legend}
    report = evaluate_cartography_semantics(_mapspec([layer]), _profile(field="use", ftype="string",
                                                                       sample_values=["residential", "commercial"]))
    names = _check_names(report)
    assert "CLASSIFICATION_CARDINALITY" not in names
    assert "CATEGORICAL_DOMAIN_CONSISTENCY" not in names


def test_categorical_missing_category_in_legend():
    # Data has 'foo' but legend has no 'foo' category.
    legend = {"type": "categorical", "field": "use", "categories": [
        {"key": "residential", "color": "#0", "label": "Res"}]}
    paint = {"color": {"method": "match", "field": "use", "cases": [["residential", "#0"]], "default": "#0"}}
    layer = {"id": "L1", "source": "src1", "type": "fill", "paint": paint, "legend_spec": legend}
    report = evaluate_cartography_semantics(
        _mapspec([layer]),
        _profile(field="use", ftype="string", sample_values=["residential", "foo"]),
    )
    assert "CATEGORICAL_DOMAIN_CONSISTENCY" in _check_names(report)


# ─── semantic checks: no-data ───────────────────────────────────────────────


def test_no_data_nulls_without_rule_flagged():
    layer = _graduated_layer([0, 10, 20], ["#a", "#b"], nodata=None)
    report = evaluate_cartography_semantics(_mapspec([layer]), _profile(fmin=0, fmax=20, null_count=7))
    assert "NO_DATA_SEMANTICS" in _check_names(report)


def test_no_data_rule_present_not_flagged():
    layer = _graduated_layer([0, 10, 20], ["#a", "#b"], nodata={"color": "#ccc", "label": "No data"})
    report = evaluate_cartography_semantics(_mapspec([layer]), _profile(fmin=0, fmax=20, null_count=7))
    assert "NO_DATA_SEMANTICS" not in _check_names(report)


def test_no_data_not_evaluated_without_null_count():
    layer = _graduated_layer([0, 10, 20], ["#fee5d9", "#a50f15"])
    report = evaluate_cartography_semantics(_mapspec([layer]), _profile(field="population", fmin=0, fmax=20))
    nodata_findings = [f for f in report.findings if f.check == "NO_DATA_SEMANTICS"]
    # A profile EXISTS but lacks null_count → an explicit NOT_EVALUATED info
    # finding must be present (never a fake pass, never silent). Asserting
    # presence guards against the check being silently dropped.
    assert any(not f.evaluated for f in nodata_findings), \
        "NO_DATA_SEMANTICS must emit a NOT_EVALUATED info finding when null_count is absent"
    assert all((not f.evaluated) or f.severity != "error" for f in nodata_findings)


# ─── semantic checks: divergent ─────────────────────────────────────────────


def test_divergent_center_outside_domain_is_error():
    legend = {"type": "divergent", "field": "dev", "center": 500, "min": -100, "max": 100,
              "palette": "Viridis", "palette_colors": ["#1", "#2", "#3"]}
    paint = {"color": {"method": "interpolate", "field": "dev",
                       "stops": [[-100, "#1"], [0, "#2"], [100, "#3"]]}}
    layer = {"id": "L1", "source": "src1", "type": "fill", "paint": paint, "legend_spec": legend}
    report = evaluate_cartography_semantics(_mapspec([layer]))
    div = [f for f in report.findings if f.check == "DIVERGENT_DOMAIN"]
    assert div and any(f.severity == "error" for f in div)


def test_divergent_center_at_edge_is_warning():
    legend = {"type": "divergent", "field": "dev", "center": -100, "min": -100, "max": 100,
              "palette": "Viridis", "palette_colors": ["#1", "#2"]}
    paint = {"color": {"method": "interpolate", "field": "dev", "stops": [[-100, "#1"], [100, "#2"]]}}
    layer = {"id": "L1", "source": "src1", "type": "fill", "paint": paint, "legend_spec": legend}
    report = evaluate_cartography_semantics(_mapspec([layer]))
    div = [f for f in report.findings if f.check == "DIVERGENT_DOMAIN"]
    assert div and all(f.severity == "warning" for f in div)


def test_divergent_center_in_domain_is_clean():
    spec = ts.build_divergent_spec([-100, -5, 0, 7, 100], "dev", center=0, palette="Viridis")
    paint, _ = ts.spec_to_paint(spec)
    layer = {"id": "L1", "source": "src1", "type": "fill", "paint": {"color": paint}, "legend_spec": spec}
    report = evaluate_cartography_semantics(_mapspec([layer]))
    assert "DIVERGENT_DOMAIN" not in _check_names(report)


# ─── semantic checks: cardinality + palette ─────────────────────────────────


def test_palette_cardinality_too_many_classes():
    # 8 classes declared but palette 'Reds' has 5 colors.
    layer = _graduated_layer(list(range(9)), ["#%d" % i for i in range(8)], palette="Reds")
    report = evaluate_cartography_semantics(_mapspec([layer]))
    assert "PALETTE_CARDINALITY" in _check_names(report)


def test_cardinality_colors_breaks_mismatch():
    legend = {"type": "graduated", "field": "v", "breaks": [0, 10, 20, 30, 40],
              "palette": "YlOrRd", "palette_colors": ["#a", "#b"]}  # 4 classes, 2 colors
    paint = {"color": {"method": "step", "field": "v", "default": "#a",
                       "stops": [[10, "#b"], [20, "#b"], [30, "#b"]]}}
    layer = {"id": "L1", "source": "src1", "type": "fill", "paint": paint, "legend_spec": legend}
    report = evaluate_cartography_semantics(_mapspec([layer]))
    names = _check_names(report)
    assert "CLASSIFICATION_CARDINALITY" in names


# ─── semantic checks: NOT_EVALUATED policy ──────────────────────────────────


def test_missing_evidence_is_not_evaluated_not_success():
    # No profile → checks must be NOT_EVALUATED, never a fake pass.
    # Separable ramp: this test isolates missing-evidence semantics; the
    # near-gray pair it used would now (correctly) fail
    # carto.color.separability for an unrelated reason.
    layer = _graduated_layer([0, 10, 20], ["#fee5d9", "#a50f15"])
    report = evaluate_cartography_semantics(_mapspec([layer]))  # no source_profiles
    # No-data / domain checks that need a profile are NOT_EVALUATED (info).
    evaluated = [f for f in report.findings if f.check in ("NO_DATA_SEMANTICS", "CLASSIFICATION_DOMAIN_COVERAGE") and f.evaluated]
    assert evaluated == []
    # No fabricated failure is distinct from success: incomplete evidence is
    # not OK, while the compatibility diagnostic remains available.
    assert not report.ok
    assert report.no_deterministic_failures


# ─── legacy compatibility ───────────────────────────────────────────────────


def test_legacy_legend_spec_through_converter_normalizes():
    # A legacy legend_spec with `colors` alias + unsorted breaks still converts.
    geojson = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {"v": 1}}]}
    analysis = {"algorithm": "legacy", "data": geojson, "legend_spec": {
        "type": "graduated", "field": "v", "breaks": [0.0, 10.0, 20.0], "colors": ["#a", "#b"]}}
    layer, _geojson, warnings = convert_analysis_to_mapspec_layer(analysis)
    # converter attaches legend_spec + derives step paint from it.
    assert layer.get("legend_spec") is not None
    assert layer["paint"]["color"]["method"] == "step"
    # normalization: colors alias → palette_colors, breaks sorted.
    norm = ts.normalize_legend_spec(layer["legend_spec"])
    assert norm["palette_colors"] == ["#a", "#b"]


def test_composite_categorical_paint_is_seen_by_checks():
    # MapLibre-native categorical {property,type,stops} (no `method`) must now be
    # visible to semantic checks (previously a blind spot). POSITIVE assertion:
    # a composite-categorical paint referencing an ABSENT field fires
    # PAINT_FIELD_EXISTS — only possible if the normalizer saw it. (Under the
    # old blind spot the paint was invisible, so the check never ran.)
    paint = {"color": {"property": "missing_field", "type": "categorical",
                       "stops": [["residential", "#0"], ["commercial", "#1"]]}}
    layer = {"id": "L1", "source": "src1", "type": "fill", "paint": paint}
    report = evaluate_cartography_semantics(_mapspec([layer]), _profile(field="use", ftype="string"))
    assert "PAINT_FIELD_EXISTS" in _check_names(report)


def test_categorical_color_drift_is_detected():
    # Categorical color drift: same keys, different color for 'residential'.
    legend = {"type": "categorical", "field": "use", "categories": [
        {"key": "residential", "color": "#0", "label": "Res"},
        {"key": "commercial", "color": "#1", "label": "Com"}]}
    paint = {"color": {"method": "match", "field": "use",
                       "cases": [["residential", "#WRONG"], ["commercial", "#1"]],
                       "default": "#1"}}
    layer = {"id": "L1", "source": "src1", "type": "fill", "paint": paint, "legend_spec": legend}
    report = evaluate_cartography_semantics(_mapspec([layer]))
    assert "LEGEND_STYLE_EQUIVALENCE" in _check_names(report)
    assert any("residential" in f.message for f in report.findings if f.check == "LEGEND_STYLE_EQUIVALENCE")


def test_graduated_break_value_drift_is_detected():
    # Same field/colors/class-count, but the paint step uses DIFFERENT break
    # thresholds (5,15 vs 10,20). Color/count checks pass; only the break-value
    # comparison catches it.
    legend = {"type": "graduated", "field": "v", "breaks": [0, 10, 20, 30],
              "palette": "YlOrRd", "palette_colors": ["#a", "#b", "#c"]}
    paint = {"color": {"method": "step", "field": "v", "default": "#a",
                       "stops": [[5, "#b"], [15, "#c"]]}}
    layer = {"id": "L1", "source": "src1", "type": "fill", "paint": paint, "legend_spec": legend}
    report = evaluate_cartography_semantics(_mapspec([layer]))
    drift = [f for f in report.findings if f.check == "LEGEND_STYLE_EQUIVALENCE" and "break" in f.message.lower()]
    assert drift, "graduated break-value drift must be detected"


# ─── closed loop: valid mapspec + thematic consistency ──────────────────────


def test_closed_loop_graduated_paint_equals_legend():
    """The canonical invariant: paint output colors == legend palette_colors."""
    geojson = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Polygon",
                                          "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
         "properties": {"pop": float(v)}} for v in [5, 15, 25, 35, 45, 55, 65, 75, 85, 95]]}
    spec = ts.build_graduated_spec(geojson, "pop", method="equal_interval", k=4, palette="YlOrRd")
    assert spec is not None
    paint, _ = ts.spec_to_paint(spec)
    # Paint output colors must equal the legend palette_colors exactly.
    paint_colors = [paint["default"]] + [s[1] for s in paint["stops"]]
    assert paint_colors == spec["palette_colors"]
    # And the semantic check confirms no drift.
    layer = {"id": "L1", "source": "src1", "type": "fill", "paint": {"color": paint}, "legend_spec": spec}
    report = evaluate_cartography_semantics(
        _mapspec([layer]),
        _profile(field="pop", fmin=5, fmax=95, null_count=0),
    )
    assert "LEGEND_STYLE_EQUIVALENCE" not in _check_names(report)
    assert report.no_deterministic_failures
    assert not report.errors


def test_cartography_findings_flow_to_mapspec_result_and_harness_evidence():
    """ADR-0078 Phase 7: a drift mapspec's cartography errors must surface in
    MapSpecResult.cartography_findings and thence in the harness semantic_errors
    evidence channel (structural validity ≠ thematic correctness)."""
    from app.services.mapspec.lifecycle_engine import MapSpecResult

    # Drift: paint field=population, legend field=income.
    layer = _graduated_layer([0, 10, 20, 30], ["#a", "#b", "#c"],
                             paint_field="population", legend_field="income")
    findings = evaluate_cartography_semantics(_mapspec([layer])).to_dict()["findings"]
    res = MapSpecResult(
        mapspec=_mapspec([layer]), warnings=[], is_compiled=True,
        cartography_findings=findings,
    )
    d = res.to_dict()
    # cartography_findings carried on the result…
    assert any(f["check"] == "LEGEND_STYLE_EQUIVALENCE" and f["severity"] == "error"
               for f in d["cartography_findings"])

    # Drive the REAL harness with this result and confirm the merge surfaces the
    # cartography error into semantic_errors (the evidence channel).
    from app.lib.harness.pi_agent_harness import PiAgentHarness
    from app.lib.harness.tool_call_event import ToolCallEvent
    harness = PiAgentHarness(session_id="drift_test")
    harness.record_event(ToolCallEvent(
        tool_call_id="tc_drift", tool_name="webgis_layer_upsert",
        arguments={"layer": {"id": "L1"}},
        result={"success": True, "is_compiled": True, "cartography_findings": findings},
        is_error=False,
    ))
    mut = harness.mapspec_mutations[0]
    # Structural tier is SEMANTIC_VALID (is_compiled True), but the thematic
    # drift is surfaced as a semantic_errors entry.
    assert mut["is_valid"] is True
    assert any("LEGEND_STYLE_EQUIVALENCE" in e for e in mut["semantic_errors"])


def test_cartography_findings_forwarded_through_production_evidence_channel():
    """ADR-0078 Phase 7 (Round-2 fix): cartography_findings must survive the
    production tool/adapter/bridge forwarding layers (mapspec_store._with_evidence
    + agent_pi_bridge whitelist) — not just exist on MapSpecResult. Without this,
    the harness semantic_errors channel is starved in production while tests that
    hand-feed MapSpecResult pass."""
    from app.services.mapspec.lifecycle_engine import MapSpecResult
    from app.services.mapspec_store import _with_evidence

    findings = [{"check": "LEGEND_STYLE_EQUIVALENCE", "severity": "error",
                 "message": "drift", "evaluated": True}]
    res = MapSpecResult(is_compiled=True, cartography_findings=findings)
    forwarded = _with_evidence(res, {"success": True})
    # _with_evidence must carry cartography_findings onto the adapter dict.
    assert forwarded.get("cartography_findings") == findings

    # And the bridge whitelist includes it (the field is named in the forward list).
    bridge_src = open("app/agent_pi_bridge.py").read()
    assert "cartography_findings" in bridge_src


def test_cartography_not_evaluated_when_no_profile_no_legend():
    """A structurally-valid mapspec with no thematic encoding produces an empty
    (or NOT_EVALUATED-only) cartography report — never a fake thematic pass."""
    layer = {"id": "L1", "source": "src1", "type": "fill",
             "paint": {"color": "#3b82f6"}}  # plain constant, no legend_spec
    findings = evaluate_cartography_semantics(_mapspec([layer])).to_dict()["findings"]
    # No thematic checks fire (no legend_spec, no method-paint); any findings are
    # info/NOT_EVALUATED, never an error faking thematic correctness.
    assert not any(f.get("severity") == "error" and f.get("evaluated") for f in findings)
