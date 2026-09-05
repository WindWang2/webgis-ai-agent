"""Spectral science VNext conformance tests (ADR-0099 remote-sensing pack).

Trusted hand-computed fixtures for the typed band-semantics layer
(app/lib/geo_analysis/spectral.py):

- SAVI ((NIR=0.5, R=0.2, L=0.5) -> ((0.5-0.2)*1.5)/(0.5+0.2+0.5) = 0.375 exact)
- MSAVI2 vs hand value (2 - sqrt(1.6)) / 2
- MNDWI / NDBI / NDMI / GNDVI each one exact hand case
- zero-denominator -> NaN (golden semantics, no inf / fake 0)
- scale_factor=10000 (DN -> reflectance) then NDVI exact
- missing role -> UnsupportedBandSemantics (never positional guessing)
- unscaled DN input -> out_of_range_fraction reported (never clamped)
- determinism across repeated calls.
"""
import math

import numpy as np
import pytest

from app.lib.geo_analysis.spectral import (
    BAND_ROLES,
    INDEX_FAMILY,
    ROLE_ORDER,
    compute_spectral_index,
    validate_band_map,
)
from app.lib.gis.scientific_errors import UnsupportedBandSemantics

pytestmark = pytest.mark.unit


def _run(index_id, **roles):
    return compute_spectral_index(roles, index_id)


# ── 1. Family hand cases (exact) ──────────────────────────────────────

def test_spectral_family_hand_cases_exact():
    # SAVI: ((0.5 − 0.2) · (1 + 0.5)) / (0.5 + 0.2 + 0.5) = 0.45 / 1.2 = 0.375
    res = _run("savi", red=np.array([[0.2]]), nir=np.array([[0.5]]))
    assert res["array"][0, 0] == pytest.approx(0.375, abs=1e-12)
    assert res["formula"] == "((NIR − Red) · (1 + L)) / (NIR + Red + L), L = 0.5"
    assert res["reference"] == "huete1988"

    # MSAVI2: (2·0.5 + 1 − sqrt((2·0.5 + 1)² − 8·(0.5 − 0.2))) / 2
    #       = (2 − sqrt(4 − 2.4)) / 2 = (2 − sqrt(1.6)) / 2
    res = _run("msavi", red=np.array([[0.2]]), nir=np.array([[0.5]]))
    assert res["array"][0, 0] == pytest.approx((2.0 - math.sqrt(1.6)) / 2.0, abs=1e-12)
    # Qi et al. 1994 不在 method_references 词表 → 诚实留空（不伪托 huete1988）
    assert res["reference"] == ""

    # MNDWI (xu2006): (0.3 − 0.1) / (0.3 + 0.1) = 0.5
    res = _run("mndwi", green=np.array([[0.3]]), swir1=np.array([[0.1]]))
    assert res["array"][0, 0] == pytest.approx(0.5, abs=1e-12)
    assert res["reference"] == "xu2006"

    # NDBI (zha_woodcock2003): (0.4 − 0.2) / (0.4 + 0.2) = 1/3
    res = _run("ndbi", nir=np.array([[0.2]]), swir1=np.array([[0.4]]))
    assert res["array"][0, 0] == pytest.approx(1.0 / 3.0, abs=1e-12)
    assert res["reference"] == "zha_woodcock2003"

    # NDMI: (0.5 − 0.2) / (0.5 + 0.2) = 3/7（无词表正典出处 → 留空）
    res = _run("ndmi", nir=np.array([[0.5]]), swir1=np.array([[0.2]]))
    assert res["array"][0, 0] == pytest.approx(3.0 / 7.0, abs=1e-12)
    assert res["reference"] == ""

    # GNDVI: (0.6 − 0.2) / (0.6 + 0.2) = 0.5（Gitelson 1996 不在词表 → 留空）
    res = _run("gndvi", green=np.array([[0.2]]), nir=np.array([[0.6]]))
    assert res["array"][0, 0] == pytest.approx(0.5, abs=1e-12)
    assert res["reference"] == ""


def test_spectral_reference_provenance_registry():
    """出处映射逐指数核对（词表内的用 id，词表外的诚实留空）。"""
    expected_refs = {
        "ndvi": "rouse1974",
        "gndvi": "",
        "savi": "huete1988",
        "msavi": "",
        "ndwi": "gao1996",
        "mndwi": "xu2006",
        "ndbi": "zha_woodcock2003",
        "ndmi": "",
        "nbr": "key_benson2006",
        "evi": "huete1988",
    }
    for index_id, ref in expected_refs.items():
        spec = INDEX_FAMILY[index_id]
        assert spec.reference == ref, index_id
        assert spec.valid_range[0] < spec.valid_range[1]


def test_band_roles_registry_semantics():
    """光学角色 0-1 反射率域；SAR/热红外无封闭值域（dB/亮温语义）。"""
    for role in ("blue", "green", "red", "red_edge", "nir", "swir1", "swir2"):
        assert BAND_ROLES[role].valid_range == (0.0, 1.0), role
        assert BAND_ROLES[role].kind == "optical"
    assert BAND_ROLES["thermal"].valid_range is None
    for role in ("vv", "vh", "hh", "hv"):
        assert BAND_ROLES[role].kind == "sar"
        assert BAND_ROLES[role].valid_range is None
    assert ROLE_ORDER.index("red") < ROLE_ORDER.index("nir")


# ── 2. Zero denominator / linear scale ────────────────────────────────

def test_spectral_zero_denominator_and_scale():
    # 零分母（双波段全 0，S2 L2A nodata 惯例）→ NaN，不产 inf/伪 0
    res = _run("ndvi", red=np.array([[0.0, 0.1]]), nir=np.array([[0.0, 0.3]]))
    assert np.isnan(res["array"][0, 0])
    assert res["array"][0, 1] == pytest.approx(0.5, abs=1e-12)
    # EVI 的 +1 项使全零输入分母非零——显式保持 NaN（#537 golden 语义）
    evi = _run("evi", blue=np.array([[0.0]]), red=np.array([[0.0]]), nir=np.array([[0.0]]))
    assert np.isnan(evi["array"][0, 0])

    # DN (0-10000) 输入：scale_factors 先除定标再进公式 → NDVI = 0.4 精确
    scaled = compute_spectral_index(
        {"red": np.array([[300.0]]), "nir": np.array([[700.0]])},
        "ndvi", scale_factors={"red": 10000.0, "nir": 10000.0})
    assert scaled["array"][0, 0] == pytest.approx(0.4, abs=1e-12)
    assert scaled["scale_factors_applied"] == {"red": 10000.0, "nir": 10000.0}

    # EVI 常数项只在反射率单位成立：DN 定标前后结果不同（#382 语义）
    dn_bands = dict(
        blue=np.array([[500.0]]), red=np.array([[1000.0]]), nir=np.array([[4000.0]]))
    unscaled = compute_spectral_index(dn_bands, "evi")
    refl = {r: a / 10000.0 for r, a in dn_bands.items()}
    scaled_evi = compute_spectral_index(refl, "evi")
    assert abs(unscaled["array"][0, 0] - scaled_evi["array"][0, 0]) > 0.1


# ── 3. Missing role → typed error (never positional guessing) ─────────

def test_spectral_missing_role_typed_error():
    with pytest.raises(UnsupportedBandSemantics) as exc_info:
        validate_band_map("ndvi", {"red": np.zeros((2, 2))})
    assert "nir" in str(exc_info.value)
    assert "band_map" in exc_info.value.correction_hint

    with pytest.raises(UnsupportedBandSemantics):
        compute_spectral_index({"green": np.zeros((2, 2))}, "ndvi")

    with pytest.raises(UnsupportedBandSemantics):
        compute_spectral_index(
            {"red": np.zeros((2, 2)), "nir": np.zeros((2, 2))}, "no_such_index")

    # 多余角色不碍事；缺任一必需角色才拒绝
    ok = compute_spectral_index(
        {"red": np.array([[0.2]]), "nir": np.array([[0.5]]),
         "swir1": np.array([[0.1]])},
        "ndvi")
    assert ok["array"][0, 0] == pytest.approx(0.42857142857, abs=1e-8)


# ── 4. Unscaled DN input → out-of-range reported (never clamped) ──────

def test_spectral_dn_input_out_of_range_reported():
    # 4 像元 NDVI：3 个正常（|NDVI| ≤ 1），1 个因含负偏移 DN（辐射偏移
    # 伪影）得 NDVI = (700−(−800))/(700+(−800)) = −15 → |NDVI| > 1。
    red = np.array([[300.0, 300.0], [-800.0, 300.0]])
    nir = np.array([[700.0, 700.0], [700.0, 700.0]])
    res = compute_spectral_index({"red": red, "nir": nir}, "ndvi")

    vals = res["array"]
    finite = np.isfinite(vals)
    # out_of_range_fraction ≡ 超理论值域的有限像元占比（= |NDVI|>1 占比）
    expected_frac = float(np.sum(finite & (np.abs(vals) > 1.0)) / np.sum(finite))
    assert res["out_of_range_fraction"] == pytest.approx(expected_frac, abs=1e-12)
    assert res["out_of_range_fraction"] == pytest.approx(0.25, abs=1e-12)
    # 只报告不钳制：越界值原样保留（−15），披露文案在场
    assert vals[1, 0] == pytest.approx(-15.0, abs=1e-9)
    assert "未钳制" in res["disclosure"]
    assert res["valid_pixel_fraction"] == pytest.approx(1.0, abs=1e-12)


# ── 5. Nodata mask + determinism ──────────────────────────────────────

def test_spectral_nodata_mask_and_determinism():
    red = np.array([[0.2, 0.2], [0.2, 0.2]])
    nir = np.array([[0.5, 0.5], [0.5, 0.5]])
    nodata = np.array([[False, True], [False, False]])
    res = compute_spectral_index({"red": red, "nir": nir}, "ndvi", nodata=nodata)
    assert np.isnan(res["array"][0, 1])
    assert res["valid_pixel_fraction"] == pytest.approx(0.75, abs=1e-12)

    r1 = compute_spectral_index({"red": red, "nir": nir}, "savi")
    r2 = compute_spectral_index({"red": red, "nir": nir}, "savi")
    np.testing.assert_array_equal(r1["array"], r2["array"])
    assert {k: v for k, v in r1.items() if k != "array"} == \
           {k: v for k, v in r2.items() if k != "array"}
