"""Aggregation semantics VNext tests (ADR-0099 aggregation domain).

Covers the explicit-denominator aggregation library
(``aggregate_with_denominator`` in app/lib/geo_analysis/aggregation.py):

- field denominator: exact hand-fixture rates
- area denominator: exact density (m⁻²) via a metric CRS
- count denominator == plain count (count is NOT a rate — labeled honestly)
- zero/negative denominator → rate=None + policy disclosure (never 0/inf)
- NaN numerator values excluded and disclosed
- descriptor honesty: spatial.aggregate.rates / rate_aggregation capability
  disclose the tool-surface gap (count ≠ rate).
"""
import math

import geopandas as gpd
import pytest
from shapely.geometry import Point, box

from app.lib.geo_analysis.aggregation import aggregate_with_denominator
from app.lib.gis.scientific_errors import MissingRequiredField

pytestmark = pytest.mark.unit


# ── fixtures (UTM 50N metric frame: exact areas) ──────────────────────


def _zones():
    """Two 1000 m × 1000 m zones (1e6 m² each) + one 2000 m × 1000 m zone."""
    z1 = box(500000, 4400000, 501000, 4401000)   # 1e6 m²
    z2 = box(501000, 4400000, 502000, 4401000)   # 1e6 m²
    z3 = box(500000, 4401000, 502000, 4402000)   # 2e6 m²
    return gpd.GeoDataFrame(
        {"zid": [1, 2, 3], "pop": [200, 0, 400]},
        geometry=[z1, z2, z3], crs="EPSG:32650")


def _features():
    """4 point features with values; one NaN value (id 3) must be excluded."""
    return gpd.GeoDataFrame(
        {"val": [2.0, 3.0, float("nan"), 5.0], "fid": [1, 2, 3, 4]},
        geometry=[Point(500500, 4400500),   # z1
                  Point(500600, 4400500),   # z1
                  Point(500700, 4400500),   # z1 (NaN value)
                  Point(501500, 4400500)],  # z2
        crs="EPSG:32650")


# ── 9a. field denominator: exact rates ────────────────────────────────


def test_field_denominator_rates_exact():
    """rate = Σ(numerator)/zone_field exactly; NaN excluded; empty zone
    numerator 0 with has_support disclosure."""
    out, ev = aggregate_with_denominator(
        _features(), _zones(), numerator_field="val",
        denominator="pop", denominator_kind="field")

    by_id = {int(r.zid): r for _, r in out.iterrows()}
    # z1: 2+3 (NaN excluded) / 200 = 0.025
    assert by_id[1].numerator == pytest.approx(5.0)
    assert by_id[1].denominator == pytest.approx(200.0)
    assert by_id[1].rate == pytest.approx(0.025)
    assert by_id[1].has_support is True
    # z2: 5 / 0 → denominator present but ≤ 0 → rate None (see policy test)
    assert by_id[2].numerator == pytest.approx(5.0)
    assert by_id[2].denominator == 0.0
    # z3: no features → numerator 0, denominator 400 → true zero rate 0/400
    assert by_id[3].numerator == 0
    assert by_id[3].has_support is False
    assert by_id[3].rate == pytest.approx(0.0)

    assert ev["denominator_kind"] == "field"
    assert ev["denominator_unit"] == "zone_field:pop"
    assert ev["rate_unit"] == "numerator_per_pop"
    assert ev["nan_numerator_excluded"] == 1
    assert ev["join_predicate"] == "intersects"


def test_count_denominator_matches_plain_count():
    """count denominator == plain count: numerator equals the per-zone feature
    count and the denominator equals the same count — and the output is
    honestly labeled a RATIO, not a rate/density (library-level anti-goal)."""
    feats, zones = _features(), _zones()
    plain = gpd.sjoin(feats, zones, how="inner", predicate="intersects")
    plain_counts = plain.groupby("index_right").size()

    out, ev = aggregate_with_denominator(feats, zones, denominator_kind="count")
    for idx, count in plain_counts.items():
        assert int(out.loc[idx, "numerator"]) == int(count)
        assert out.loc[idx, "denominator"] == float(count)
    empty_mask = ~out.index.isin(plain_counts.index)
    assert int(out.loc[empty_mask, "numerator"].sum()) == 0

    assert ev["denominator_kind"] == "count"
    assert ev["rate_unit"] == "count_ratio_not_rate"


# ── 9b. area denominator: exact density ───────────────────────────────


def test_area_denominator_density_exact():
    """area denominator → true m² (metric CRS) and density = count/area."""
    feats, zones = _features(), _zones()
    out, ev = aggregate_with_denominator(feats, zones, denominator_kind="area")

    by_id = {int(r.zid): r for _, r in out.iterrows()}
    assert by_id[1].denominator == pytest.approx(1_000_000.0)
    # default numerator (no numerator_field) = feature count: z1 has 3 points
    assert by_id[1].numerator == 3
    assert by_id[1].rate == pytest.approx(3.0 / 1_000_000.0)   # m⁻² exact
    assert by_id[2].denominator == pytest.approx(1_000_000.0)
    assert by_id[2].rate == pytest.approx(1.0 / 1_000_000.0)
    assert by_id[3].denominator == pytest.approx(2_000_000.0)
    assert by_id[3].rate == pytest.approx(0.0)

    assert ev["denominator_kind"] == "area"
    assert ev["denominator_unit"] == "m2"
    assert ev["rate_unit"] == "per_m2"
    assert ev["area_crs"] == "EPSG:32650"
    assert ev["area_crs_class"] == "projected_local_metric"


def test_area_denominator_geographic_zones_utm():
    """Geographic zones get honest metric areas: the zones are re-projected
    to UTM (Web Mercator would distort), disclosed via area_crs."""
    zones = gpd.GeoDataFrame(
        {"zid": [1]},
        geometry=[box(116.00, 39.70, 116.01, 39.71)], crs="EPSG:4326")
    feats = gpd.GeoDataFrame(
        {"val": [1.0]}, geometry=[Point(116.005, 39.705)], crs="EPSG:4326")
    out, ev = aggregate_with_denominator(feats, zones, denominator_kind="area")
    assert ev["area_crs"].startswith("EPSG:32")
    # 0.01° × 0.01° near lat 39.7 ≈ 1113 m × 855 m ≈ 9.5e5 m² (±2%)
    assert 0.93e6 < float(out["denominator"].iloc[0]) < 0.97e6
    assert float(out["rate"].iloc[0]) == pytest.approx(1.0 / float(out["denominator"].iloc[0]))


# ── 9c. zero/negative denominator policy ──────────────────────────────


def test_zero_and_negative_denominator_rate_none():
    """Zero/negative/missing denominator → rate None (JSON null) + policy
    flag + disclosed count — NEVER 0, NEVER inf."""
    zones = gpd.GeoDataFrame(
        {"zid": [1, 2, 3], "pop": [100, 0, -50]},
        geometry=[box(500000, 4400000, 501000, 4401000),
                  box(501000, 4400000, 502000, 4401000),
                  box(502000, 4400000, 503000, 4401000)],
        crs="EPSG:32650")
    feats = gpd.GeoDataFrame(
        {"val": [4.0, 4.0, 4.0]},
        geometry=[Point(500500, 4400500), Point(501500, 4400500),
                  Point(502500, 4400500)], crs="EPSG:32650")

    out, ev = aggregate_with_denominator(
        feats, zones, numerator_field="val", denominator="pop",
        denominator_kind="field")

    rates = out["rate"].tolist()
    assert rates[0] == pytest.approx(0.04)      # 4/100 fine
    assert rates[1] is None                     # zero denominator → None
    assert rates[2] is None                     # negative denominator → None
    # never fabricated values:
    assert 0.0 not in rates[1:]
    assert not any(isinstance(r, float) and math.isinf(r) for r in rates)
    assert ev["zero_denominator_zones"] == 2
    assert "rate=None" in ev["zero_denominator_policy"]
    # numerators stay real (4.0 each) — only the RATE is undefined
    assert out["numerator"].tolist() == [4.0, 4.0, 4.0]


def test_missing_denominator_column_typed_error():
    """denominator_kind=field without the column → typed MissingRequiredField,
    unknown kind → ValueError (no silent field guessing)."""
    with pytest.raises(MissingRequiredField):
        aggregate_with_denominator(_features(), _zones(), denominator="nope",
                                   denominator_kind="field")
    with pytest.raises(ValueError, match="denominator_kind"):
        aggregate_with_denominator(_features(), _zones(), denominator_kind="perim")


def test_nan_numerator_excluded_and_disclosed():
    """NaN numerator values are excluded from sums and counted in evidence;
    a zone whose features are ALL NaN keeps numerator 0 but has_support=True
    (distinguishing no-support from a true zero)."""
    feats = gpd.GeoDataFrame(
        {"val": [float("nan"), float("nan")], "fid": [1, 2]},
        geometry=[Point(500500, 4400500), Point(500600, 4400500)],
        crs="EPSG:32650")
    zones = _zones().iloc[[0]]  # only z1
    out, ev = aggregate_with_denominator(
        feats, zones, numerator_field="val", denominator="pop",
        denominator_kind="field")
    assert ev["nan_numerator_excluded"] == 2
    assert out["numerator"].tolist() == [0.0]
    assert out["has_support"].tolist() == [True]  # features existed, all-NaN


# ── 10. rate ≠ count honesty (descriptors) ────────────────────────────


def test_rate_descriptors_disclose_tool_surface_gap():
    """spatial.aggregate.rates + rate_aggregation capability exist and
    honestly disclose: explicit denominators, EXPERIMENTAL status, and that
    the tool surface does not yet expose the denominator channel."""
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    from app.lib.gis.capability_registry import get_capability_registry

    algo = get_algorithm_registry().get("spatial.aggregate.rates")
    assert algo is not None
    assert algo.capabilities == ["rate_aggregation"]
    assert algo.tool_candidates == ["spatial_aggregate"]
    assert algo.scientific_status == "EXPERIMENTAL"
    assert algo.algorithm_family == "zonal_aggregation"
    # 2026-09 中央接线闭合了工具面缺口 —— 断言转为正向披露。
    assert any("分母通道已接入 spatial_aggregate 工具" in lim
               for lim in algo.limitations)
    assert any("count 聚合非密度/率" in lim
               for lim in get_algorithm_registry().get("spatial.aggregate.admin").limitations)

    cap = get_capability_registry().get("rate_aggregation")
    assert cap is not None
    assert "count 聚合不是率/密度" in cap.description

    # the parameter contract is registered for future wiring and carries the
    # three denominator params with the exact library signature defaults
    from app.lib.gis.parameter_contracts import get_parameter_contract_registry
    contract = get_parameter_contract_registry().get("aggregate_with_denominator")
    assert contract is not None
    names = {p.name for p in contract.parameters}
    assert names == {"numerator_field", "denominator_kind", "denominator_field"}
    kind = contract.spec("denominator_kind")
    assert kind.enum_values == ["field", "area", "count"]
    assert kind.default == "count"
    # no required params → the tool parity gate stays green pre-wiring
    assert contract.required_names() == []
    assert algo.parameter_contract_ref == "aggregate_with_denominator"


def test_aggregate_with_denominator_contract_applies():
    """apply_contract normalizes the wiring parameters (future tool entry)."""
    from app.lib.gis.parameter_contracts import apply_contract
    out = apply_contract("aggregate_with_denominator", {
        "numerator_field": "cases", "denominator_kind": "field",
        "denominator_field": "pop"})
    assert out == {"numerator_field": "cases", "denominator_kind": "field",
                   "denominator_field": "pop"}
    defaulted = apply_contract("aggregate_with_denominator", {})
    assert defaulted == {"denominator_kind": "count"}


# ── 工具面端到端（spatial_aggregate 分母通道接线）────────────────────
@pytest.mark.asyncio
async def test_spatial_aggregate_tool_denominator_channel():
    """spatial_aggregate 工具 denominator_kind=field → rate 输出 + evidence。"""
    import math
    from app.tools.registry import ToolRegistry
    from app.tools.advanced_spatial import register_advanced_spatial_tools

    reg = ToolRegistry()
    register_advanced_spatial_tools(reg)

    zones = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"name": "A", "pop": 100},
             "geometry": {"type": "Polygon", "coordinates": [[[116.0, 39.8], [116.01, 39.8], [116.01, 39.81], [116.0, 39.81], [116.0, 39.8]]]}},
            {"type": "Feature", "properties": {"name": "B", "pop": 0},
             "geometry": {"type": "Polygon", "coordinates": [[[116.01, 39.8], [116.02, 39.8], [116.02, 39.81], [116.01, 39.81], [116.01, 39.8]]]}},
        ],
    }
    points = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"v": 1},
             "geometry": {"type": "Point", "coordinates": [116.005, 39.805]}},
            {"type": "Feature", "properties": {"v": 2},
             "geometry": {"type": "Point", "coordinates": [116.002, 39.808]}},
        ],
    }
    out = await reg.dispatch("spatial_aggregate", {
        "points": points, "polygons": zones,
        "denominator_kind": "field", "denominator": "pop",
        "numerator_field": "v",
    })
    assert out["success"] is True, out
    feats = out["data"]["features"]
    by_name = {f["properties"]["name"]: f["properties"] for f in feats}
    assert math.isclose(by_name["A"]["rate"], 3 / 100)   # Σv=3 / pop=100
    assert by_name["B"]["rate"] is None          # 零分母 → null，绝不 0
    assert out["scientific_evidence"]["normalization"]["denominator_kind"] == "field"
    # count 路径保持原行为
    out2 = await reg.dispatch("spatial_aggregate", {"points": points, "polygons": zones})
    counts = [f["properties"].get("point_count", f["properties"].get("count"))
              for f in out2["data"]["features"]]
    assert sorted(c for c in counts if c is not None) == [0, 2]
