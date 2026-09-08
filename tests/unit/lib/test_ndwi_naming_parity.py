"""NDWI 同名异式拆名（审计 §3.2）跨路径 parity 锁定。

历史状态：裸名 ``ndwi`` 在在线/本地路径（band_math / raster_windowed）
指 McFeeters (1996) 开放水体 (green−nir)/(green+nir)，在 typed 层
（spectral.INDEX_FAMILY）却指 Gao (1996) 植被水分 (nir−swir1)/(nir+swir1)
——同名异式，跨路径不可比。

拆名后的锁定目标：
- 裸 ``ndwi`` 三路径统一 = McFeeters 开放水体（同名跨路径公式一致）；
- Gao 式统一叫 ``ndwi_gao``；
- ``ndwi_water`` 是 McFeeters 版的显式别名（逐位一致）；
- 两条公式的 description 均声明「不可互换」。
"""
import numpy as np
import pytest

from app.lib.geo_analysis.raster_windowed import (
    INDEX_BAND_ROLES as WINDOWED_ROLES,
    INDEX_VALID_RANGE as WINDOWED_RANGE,
)
from app.lib.geo_analysis.spectral import INDEX_FAMILY, compute_spectral_index
from app.lib.gis.method_references import METHOD_REFERENCES
from app.services.rs.band_math import (
    INDEX_DESCRIPTIONS,
    INDEX_FORMULAS,
    compute_index_array,
)

pytestmark = pytest.mark.unit

GREEN = np.array([[0.30, 0.20], [0.00, 0.60]])
NIR = np.array([[0.50, 0.10], [0.00, 0.40]])
SWIR1 = np.array([[0.10, 0.05], [0.30, 0.40]])


def test_ndwi_same_name_cross_path_formula_parity():
    """同名跨路径公式一致性：ndwi 在 band_math / raster_windowed / typed 层
    三路径同角色同公式（含零分母 → NaN 的 golden 语义）。"""
    # 波段角色契约一致（band_math 与 typed 层逐元素相等）
    assert tuple(INDEX_FORMULAS["ndwi"][0]) == ("green", "nir")
    assert WINDOWED_ROLES["ndwi"] == ("green", "nir")
    assert INDEX_FAMILY["ndwi"].required_roles == ("green", "nir")

    # 数值 parity：在线公式 vs typed 层（同一合成场，含零分母像元）
    via_band_math = compute_index_array("ndwi", green=GREEN, nir=NIR)
    via_typed = compute_spectral_index(
        {"green": GREEN, "nir": NIR}, "ndwi")["array"]
    np.testing.assert_allclose(via_band_math, via_typed, rtol=0, atol=0)
    # McFeeters 手算锚：green=0.3, nir=0.5 → (0.3−0.5)/(0.3+0.5) = −0.25
    assert via_typed[0, 0] == pytest.approx(-0.25, abs=1e-12)
    assert not np.isnan(via_typed[0, 1])  # 分母 0.3>0 正常

    # 零分母像元（全 0 波段，S2 L2A nodata 惯例）→ NaN（两路径一致）
    assert np.isnan(via_band_math[1, 0])
    assert np.isnan(via_typed[1, 0])


def test_ndwi_gao_fixture_anchor_exact():
    """ndwi_gao（Gao 1996）数值锚：typed 层与在线路径逐位一致。
    nir=0.5, swir1=0.1 → (0.5−0.1)/(0.5+0.1) = 2/3；
    与 McFeeters ndwi（同场 −0.25）方向相反——不可互换的数值证据。"""
    res = compute_spectral_index({"nir": NIR, "swir1": SWIR1}, "ndwi_gao")
    arr = res["array"]
    assert arr[0, 0] == pytest.approx(2.0 / 3.0, abs=1e-12)
    assert arr[0, 1] == pytest.approx((0.10 - 0.05) / (0.10 + 0.05), abs=1e-12)
    assert arr[1, 0] == pytest.approx((0.00 - 0.30) / (0.00 + 0.30), abs=1e-12)
    assert res["reference"] == "gao1996"
    assert res["roles_used"] == ["nir", "swir1"]

    # 在线路径同式（swir11 = STAC B11/SWIR1 键）与 typed 层逐位一致
    via_band_math = compute_index_array("ndwi_gao", nir=NIR, swir11=SWIR1)
    np.testing.assert_allclose(via_band_math, arr, rtol=0, atol=0)


def test_ndwi_water_alias_bitwise_identity():
    """ndwi_water 是 McFeeters ndwi 的显式别名：三路径公式逐位一致。"""
    assert WINDOWED_ROLES["ndwi_water"] == WINDOWED_ROLES["ndwi"]
    assert INDEX_FAMILY["ndwi_water"].required_roles == \
        INDEX_FAMILY["ndwi"].required_roles

    typed_alias = compute_spectral_index(
        {"green": GREEN, "nir": NIR}, "ndwi_water")["array"]
    typed_ndwi = compute_spectral_index(
        {"green": GREEN, "nir": NIR}, "ndwi")["array"]
    np.testing.assert_array_equal(typed_alias, typed_ndwi)

    band_math_alias = compute_index_array("ndwi_water", green=GREEN, nir=NIR)
    band_math_ndwi = compute_index_array("ndwi", green=GREEN, nir=NIR)
    np.testing.assert_array_equal(band_math_alias, band_math_ndwi)


def test_ndwi_vs_ndwi_gao_not_interchangeable():
    """同一场上 ndwi 与 ndwi_gao 数值不同（角色序都不同），文档声明在场。"""
    ndwi = compute_spectral_index({"green": GREEN, "nir": NIR}, "ndwi")["array"]
    gao = compute_spectral_index({"nir": NIR, "swir1": SWIR1}, "ndwi_gao")["array"]
    assert not np.allclose(ndwi, gao)

    # 两条公式的 description 都声明「不可互换」（band_math 描述表）
    for key in ("ndwi", "ndwi_water", "ndwi_gao"):
        assert "不可互换" in INDEX_DESCRIPTIONS[key], key
    # typed 层公式串同样声明
    assert "不可互换" in INDEX_FAMILY["ndwi"].formula_text
    assert "不可互换" in INDEX_FAMILY["ndwi_gao"].formula_text
    assert "不可互换" in INDEX_FAMILY["ndwi_water"].formula_text


def test_ndwi_gao_windowed_contract_and_reference_ids():
    """本地窗口化路径契约 + 词表出处完整性。"""
    assert WINDOWED_ROLES["ndwi_gao"] == ("nir", "swir1")
    assert WINDOWED_RANGE["ndwi_gao"] == (-1.0, 1.0)
    assert WINDOWED_RANGE["ndwi_water"] == (-1.0, 1.0)
    # 词表出处（题录在 method_references registry，validate() 校验存在性）
    assert "gao1996" in METHOD_REFERENCES
    assert "mcfeeters1996" in METHOD_REFERENCES
    assert INDEX_FAMILY["ndwi_gao"].reference == "gao1996"
    assert INDEX_FAMILY["ndwi"].reference == "mcfeeters1996"
