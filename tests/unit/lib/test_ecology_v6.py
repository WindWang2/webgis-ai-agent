"""Conformance tests for the ecology family (Science V6).

Contract bullets under test (descriptors ``ecology.habitat_suitability``
/ ``ecology.landscape_metrics``):

- HSI: trapezoid closed-form anchors (0 / ramp / 1 / ramp-down / 0),
  weight normalization, geometric aggregation = limiting-factor
  semantics (any zero variable → overall 0), deterministic;
- landscape metrics: uniform raster → SHDI 0; two-class split → SHDI
  ln(2), SIDI 0.5, PLAND 50/50; edge density exact on a half-split
  raster; 4-neighbour patch counting on a hand-built raster;
- typed errors: missing variable field, bad curve params, all-nodata
  raster, too many classes.
"""
import numpy as np
import pytest

from app.lib.geo_analysis.ecology import (
    hsi_score_features,
    landscape_metrics,
)
from app.lib.gis.scientific_errors import (
    DegenerateData,
    MissingRequiredField,
    NoValidObservations,
)

pytestmark = pytest.mark.unit


# ── HSI ──────────────────────────────────────────────────────────────

def _fc(props_list):
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": p, "geometry": None}
        for p in props_list
    ]}


def test_hsi_trapezoid_closed_form_anchors():
    """梯形 [10,15,25,30]：x=5→0, 12→0.4, 20→1, 27.5→0.5, 35→0（闭合式）。"""
    xs = [5.0, 12.0, 20.0, 27.5, 35.0]
    fc = _fc([{"x": v} for v in xs])
    out, meta = hsi_score_features(
        fc, [{"field": "x", "curve": "trapezoid",
              "params": [10, 15, 25, 30]}])
    got = [f["properties"]["hsi_x"] for f in out["features"]]
    assert got == pytest.approx([0.0, 0.4, 1.0, 0.5, 0.0], abs=1e-9)
    assert meta["variables"][0]["weight_normalized"] == 1.0
    # 权重归一化：weight 3 → 1.0（单变量）
    # 确定性
    out2, _ = hsi_score_features(fc, [{"field": "x", "curve": "trapezoid",
                                       "params": [10, 15, 25, 30]}])
    assert out == out2


def test_hsi_geometric_limiting_factor():
    """geometric 聚合：任一变量 0 → 整体 0（限制因子语义）。"""
    fc = _fc([{"temp": 20.0, "rain": 800.0},
              {"temp": 5.0, "rain": 800.0}])  # 第二条 temp 得分 0
    variables = [
        {"field": "temp", "curve": "trapezoid", "params": [10, 15, 25, 30],
         "weight": 2.0},
        {"field": "rain", "curve": "gaussian", "params": [900, 300],
         "weight": 1.0},
    ]
    out, meta = hsi_score_features(fc, variables, aggregation="geometric")
    hs = [f["properties"]["hsi"] for f in out["features"]]
    assert hs[0] > 0
    assert hs[1] == 0.0
    assert meta["aggregation"] == "geometric"
    # 权重归一化：2:1 → 2/3, 1/3
    assert meta["variables"][0]["weight_normalized"] == pytest.approx(2 / 3)


def test_hsi_adversarial_inputs():
    fc = _fc([{"temp": 20.0}])
    with pytest.raises(MissingRequiredField, match="rainfall"):
        hsi_score_features(fc, [{"field": "rainfall",
                                 "curve": "trapezoid",
                                 "params": [0, 1, 2, 3]}])
    with pytest.raises(ValueError, match="a<=b<=c<=d"):
        hsi_score_features(fc, [{"field": "temp", "curve": "trapezoid",
                                 "params": [3, 2, 1, 0]}])
    with pytest.raises(ValueError, match="sigma"):
        hsi_score_features(fc, [{"field": "temp", "curve": "gaussian",
                                 "params": [10, 0]}])
    with pytest.raises(ValueError, match="curve"):
        hsi_score_features(fc, [{"field": "temp", "curve": "sigmoid",
                                 "params": [1, 2]}])
    with pytest.raises(NoValidObservations):
        hsi_score_features(_fc([]), [{"field": "temp",
                                      "curve": "gaussian",
                                      "params": [10, 1]}])


# ── 景观格局指标 ─────────────────────────────────────────────────────

def test_landscape_uniform_and_diversity_anchors():
    """均匀栅格 → SHDI=0；双类对半 → SHDI=ln2、SIDI=0.5、PLAND 50/50。"""
    uni = np.full((4, 4), 3.0)
    _, m_uni = landscape_metrics(uni, cell_size=10.0)
    assert m_uni["shdi"] == 0.0
    assert m_uni["n_classes"] == 1

    half = np.array([[1, 1, 2, 2]] * 4)
    table, m = landscape_metrics(half, cell_size=10.0)
    # meta 数值统一 6 位小数（确定性 meta 契约）
    assert m["shdi"] == pytest.approx(float(np.log(2)), abs=1e-6)
    assert m["sidi"] == pytest.approx(0.5, abs=1e-9)
    plands = sorted(r["pland_pct"] for r in table["classes"])
    assert plands == [50.0, 50.0]
    assert m["pr"] == 2


def test_landscape_patches_and_edge_density():
    """半分栅格 ED=500 m/ha（闭合式）；两块分离斑块 NP=2、LPI 正确。"""
    half = np.array([[1, 1, 2, 2]] * 4)
    table, m = landscape_metrics(half, cell_size=10.0)
    # 物理对比边 4 条 × 10 m = 40 m（类-类边界每条恰计一次）；总面积
    # 16×100 m² = 0.16 ha → 250。逐类视角 ED 仍为 500（对方类视角重复）
    # —— 双口径在 meta["edge_policy"] 披露。
    assert m["edge_density_m_per_ha"] == pytest.approx(250.0, abs=1e-6)
    assert table["classes"][0]["edge_density_m_per_ha"] == pytest.approx(
        500.0, abs=1e-6)

    patchy = np.array([
        [1, 2, 1, 1],
        [1, 2, 1, 1],
        [2, 2, 2, 2],
        [2, 2, 2, 2],
    ])
    t2, _ = landscape_metrics(patchy, cell_size=10.0)
    rows = {r["class_value"]: r for r in t2["classes"]}
    assert rows[1.0]["n_patches"] == 2   # 2 格块 + 4 格块
    assert rows[1.0]["lpi_pct"] == 25.0  # 最大斑块 4/16
    assert rows[2.0]["n_patches"] == 1


def test_landscape_adversarial_inputs():
    with pytest.raises(NoValidObservations, match="all nodata"):
        landscape_metrics(np.full((3, 3), np.nan), cell_size=10.0)
    with pytest.raises(DegenerateData, match="256"):
        big = np.arange(300 * 300, dtype=float).reshape(300, 300) % 300
        landscape_metrics(big, cell_size=1.0)
    with pytest.raises(ValueError, match="cell_size"):
        landscape_metrics(np.ones((3, 3)), cell_size=0)
    # nodata 背景不计面积
    r = np.array([[1, 1, -9999.0], [1, 1, -9999.0]])
    _, m = landscape_metrics(r, cell_size=10.0, nodata=-9999.0)
    assert m["total_area_ha"] == pytest.approx(4 * 100.0 / 10_000.0)
