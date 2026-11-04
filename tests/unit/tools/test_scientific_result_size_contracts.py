"""Successful scientific callable results, before registry/LLM formatting.

These contracts cover the registered callable's normal return. Registry-owned
exception envelopes are a separate layer; the scientific callables must keep
raising their existing errors, not reject valid data to meet an output budget.
"""
import asyncio
from copy import deepcopy
import json

import numpy as np
import pytest

from app.lib.gis.algorithm_registry import get_algorithm_registry
from app.lib.gis.scientific_evidence import ScientificEvidenceBuilder, build_evidence
from app.tools.advanced_spatial import register_advanced_spatial_tools
from app.tools.registry import ToolRegistry


pytestmark = pytest.mark.unit

_TOOLS = ("directional_variogram_analysis", "variogram_model_selection")
# Include JSON-escaped characters as well as 400,000 CJK characters. This is a
# valid property key, not an invalid request that can be refused for its size.
_LONG_FIELD = '"\\\n' + "数" * 400_000
_SUCCESS_BUDGET_BYTES = 64 * 1024  # Conservative, below the bounded policy's 1 MiB.


@pytest.fixture(scope="module")
def registry():
    reg = ToolRegistry()
    register_advanced_spatial_tools(reg)
    return reg


def _points(field):
    rng = np.random.default_rng(42)
    lon = rng.uniform(103.95, 104.05, 96)
    lat = rng.uniform(30.55, 30.65, 96)
    values = np.sin(lon * 40) + np.cos(lat * 40) + rng.normal(0, 0.05, 96)
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [float(x), float(y)]},
                "properties": {field: float(value)},
            }
            for x, y, value in zip(lon, lat, values)
        ],
    }


@pytest.mark.parametrize(("name", "options"), (
    ("directional_variogram_analysis", {}),
    ("directional_variogram_analysis", {"azimuth_deg": 180.0, "tolerance_deg": 90.0, "band_width": 1000.0}),
    ("variogram_model_selection", {}),
    ("variogram_model_selection", {"robust": True, "matern_smoothness": 5.0}),
))
@pytest.mark.parametrize("n_lags", (4, 64))
def test_real_science_long_field_has_bounded_lossless_numeric_results(registry, name, options, n_lags):
    # Use the exact callable surface used by capability certification, not the
    # dispatcher whose formatter could conceal an oversized native result.
    call = registry._tools[name]
    ordinary = call(geojson=_points("v"), value_field="v", n_lags=n_lags, **options)
    result = call(geojson=_points(_LONG_FIELD), value_field=_LONG_FIELD, n_lags=n_lags, **options)

    assert ordinary["success"] is result["success"] is True
    assert result["scientific_evidence"]["parameters_applied"]["value_field"] == _LONG_FIELD[:160]
    assert ordinary["scientific_evidence"]["parameters_applied"]["value_field"] == "v"
    # Renaming the source property must not alter the lookup or any computed
    # statistic, ranking, metadata, or summary. Only evidence is a short view.
    comparable = deepcopy(result)
    comparable["scientific_evidence"]["parameters_applied"]["value_field"] = "v"
    assert comparable == ordinary

    if name == "directional_variogram_analysis":
        assert 0 < len(result["lags"]) <= n_lags
        assert len(result["lags"]) == len(result["gamma"]) == len(result["pair_counts"])
    else:
        assert len(result["ranking"]) == 6
        assert {row["model"] for row in result["ranking"]} == {
            "spherical", "exponential", "gaussian", "matern", "wave", "cubic",
        }
    for ensure_ascii in (False, True):
        encoded = json.dumps(result, ensure_ascii=ensure_ascii, separators=(",", ":")).encode("utf-8")
        assert len(encoded) < _SUCCESS_BUDGET_BYTES


@pytest.mark.parametrize("name", _TOOLS)
def test_scientific_descriptors_declare_actual_success_contract(registry, name):
    descriptor = registry.descriptor(name)
    assert descriptor.output_semantic_type == "stats"
    assert descriptor.result_size_policy == "bounded"


@pytest.mark.parametrize("name", _TOOLS)
def test_scientific_callable_errors_still_raise(registry, name):
    with pytest.raises(ValueError) as exc:
        registry._tools[name](geojson=_points("v"), value_field=_LONG_FIELD)
    assert _LONG_FIELD in str(exc.value)


def test_build_evidence_bounds_only_string_parameter_summaries():
    algorithm = get_algorithm_registry().get("interpolation.directional_variogram")
    parameters = {
        "value_field": _LONG_FIELD, "n_lags": 64, "enabled": True, "optional": None,
        "quantiles": [2.0, 98.0], "observer": [1.5, 0.5],
        "options": {"weights": [0.2, 0.8], "label": "nested"},
    }
    result = build_evidence(algorithm, parameters_applied=parameters)
    assert result["parameters_applied"] == {**parameters, "value_field": _LONG_FIELD[:160]}
    assert parameters["value_field"] == _LONG_FIELD


def test_evidence_emission_bounds_assignment_and_in_place_changes_without_mutation():
    builder = ScientificEvidenceBuilder(parameters_applied={"field": "short"})
    assigned = {"field": "assigned"}
    builder.parameters_applied = assigned
    assigned["field"] = _LONG_FIELD
    assigned["items"] = list(range(100))
    assigned["nested"] = {"values": [1, 2], "label": "nested"}
    assigned.update({f"n{i}": i for i in range(20)})
    # This fix intentionally does not change unrelated post-construction fields.
    builder.warnings = ["warning" * 100]
    original = deepcopy(assigned)

    first = builder.to_evidence()
    assert first == builder.to_evidence()
    assert first["parameters_applied"].keys() == assigned.keys()
    assert first["parameters_applied"]["field"] == _LONG_FIELD[:160]
    assert first["parameters_applied"]["items"] == assigned["items"]
    assert first["parameters_applied"]["nested"] == assigned["nested"]
    assert first["parameters_applied"]["n0"] == 0
    assert first["warnings"] == builder.warnings
    assert assigned == original
    assert builder.parameters_applied is assigned


@pytest.mark.parametrize("length", (0, 159, 160, 161))
def test_string_summary_boundary_preserves_short_values(length):
    algorithm = get_algorithm_registry().get("interpolation.directional_variogram")
    value = ('数"\\\n' * 41)[:length]
    result = build_evidence(algorithm, parameters_applied={"value_field": value})
    assert result["parameters_applied"]["value_field"] == value[:160]


def test_real_remote_sensing_quantile_evidence_keeps_array_type():
    from app.tools.remote_sensing import register_rs_tools

    reg = ToolRegistry()
    register_rs_tools(reg)
    result = asyncio.run(reg._tools["robust_normalize"](
        bands={"red": np.arange(36, dtype=float).reshape(6, 6).tolist()},
        method="percentile_stretch",
    ))
    assert result["success"] is True
    parameters = result["scientific_evidence"]["parameters_applied"]
    assert parameters == {
        "method": "percentile_stretch", "quantiles": [2.0, 98.0], "has_reference": False,
    }
    assert json.loads(json.dumps(parameters))["quantiles"] == [2.0, 98.0]


def test_real_terrain_coordinate_evidence_keeps_array_type(tmp_path, monkeypatch):
    import rasterio
    from rasterio.transform import Affine

    from app.tools import _terrain_support
    from app.tools.terrain_measurements import _register_measurement_tools

    # Permit the fixture's temporary path; raster reading and LOS computation
    # use the real implementations, not a synthetic scientific return value.
    monkeypatch.setattr(_terrain_support, "validate_data_path", lambda p: str(p))
    path = tmp_path / "plane.tif"
    dem = 0.5 * np.add.outer(np.arange(12), np.arange(12)).astype(float)
    with rasterio.open(
        path, "w", driver="GTiff", height=12, width=12, count=1,
        dtype="float64", crs="EPSG:32650", transform=Affine(1, 0, 0, 0, -1, 12),
    ) as dst:
        dst.write(dem, 1)
    reg = ToolRegistry()
    _register_measurement_tools(reg)
    result = reg._tools["line_of_sight_analysis"](
        str(path), 1.5, 0.5, 9.5, 0.5, observer_height=5, target_height=2,
    )
    assert result["success"] is True
    assert result["visible"] is True
    parameters = result["scientific_evidence"]["parameters_applied"]
    assert parameters["observer"] == [1.5, 0.5]
    assert parameters["target"] == [9.5, 0.5]
    assert parameters["observer_height"] == 5.0
    assert parameters["target_height"] == 2.0
    assert json.loads(json.dumps(parameters))["observer"] == [1.5, 0.5]
