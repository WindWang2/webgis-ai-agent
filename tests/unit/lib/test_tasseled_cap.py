"""Tasseled Cap 冠层变换 + EVI2 conformance 测试（Foundation V2 · A6）。

覆盖：

- tasseled_cap（app/lib/geo_analysis/tasseled_cap.py）：三传感器
  单像元手算黄金（brightness/greenness/wetness = Σ coef·band，
  系数行即注册表事实源、公式在测试内独立复算，精确 1e-12）；
  NaN/哨兵传播（任一角色无效 → 三轴全 NaN）；逐分量贡献证据；
- 类型化拒绝：未知传感器 → UnsupportedMethod（提示显式注册）；
  缺语义角色 → UnsupportedBandSemantics（绝不按位置猜测）；
  reflectance_domain 披露与校验；
- spectral +evi2（app/lib/geo_analysis/spectral.py additive）：
  EVI2 = 2.5·(NIR−RED)/(NIR+2.4·RED+1) 手算黄金、全零波段 → NaN
  （与 evi 同 nodata 语义）、出处 huete1988（SAVI/EVI 谱系）、
  INDEX_FAMILY 计数 11、missing role → UnsupportedBandSemantics、
  确定性。
"""
import numpy as np
import pytest

from app.lib.geo_analysis.spectral import INDEX_FAMILY, compute_spectral_index
from app.lib.geo_analysis.tasseled_cap import (
    TASSELED_CAP_COMPONENTS,
    TASSELED_CAP_COEFFICIENTS,
    tasseled_cap,
)
from app.lib.gis.scientific_errors import (
    UnsupportedBandSemantics,
    UnsupportedMethod,
)

pytestmark = pytest.mark.unit

_ROLES = ("blue", "green", "red", "nir", "swir1", "swir2")
# 单像元手算用反射率向量（0.1-0.6，反射率域内）
_VECTOR = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6)


def _bands_from_vector(vector, shape=(1, 1)):
    return {role: np.full(shape, v) for role, v in zip(_ROLES, vector)}


def test_tasseled_cap_hand_golden_all_sensors():
    bands = _bands_from_vector(_VECTOR)
    for sensor in TASSELED_CAP_COEFFICIENTS:
        res = tasseled_cap(bands, sensor=sensor)
        # 黄金：每轴 = Σ coef·band（注册表系数行 × 测试内独立求和）
        for comp in TASSELED_CAP_COMPONENTS:
            expected = sum(
                coef * v for coef, v in
                zip(TASSELED_CAP_COEFFICIENTS[sensor][comp], _VECTOR))
            got = res["components"][comp][0, 0]
            assert got == pytest.approx(expected, abs=1e-12), (sensor, comp)
        # 系数证据（分量 → 角色 → 系数）与出处披露
        for comp in TASSELED_CAP_COMPONENTS:
            assert set(res["coefficients"][comp]) == set(_ROLES)
        assert res["meta"]["sensor"] == sensor
        assert res["roles_used"] == list(_ROLES)

    # NaN / 哨兵传播：任一角色无效 → 三轴全 NaN（不角色级稀释）
    bands_nan = _bands_from_vector(_VECTOR)
    bands_nan["nir"] = np.array([[np.nan]])
    res_nan = tasseled_cap(bands_nan, sensor="landsat5_tm")
    for comp in TASSELED_CAP_COMPONENTS:
        assert np.isnan(res_nan["components"][comp][0, 0])

    bands_sentinel = _bands_from_vector(_VECTOR, shape=(1, 2))
    bands_sentinel["swir2"] = np.array([[0.6, -9999.0]])
    res_sent = tasseled_cap(bands_sentinel, sensor="sentinel2",
                            nodata=-9999.0)
    for comp in TASSELED_CAP_COMPONENTS:
        assert np.isfinite(res_sent["components"][comp][0, 0])
        assert np.isnan(res_sent["components"][comp][0, 1])

    # 逐分量贡献证据：|贡献| 占比和为 1（有界摘要）
    contrib = res_sent["contribution"]
    for comp in TASSELED_CAP_COMPONENTS:
        assert set(contrib[comp]) == set(_ROLES)
        assert sum(contrib[comp].values()) == pytest.approx(1.0, abs=1e-4)

    # 大网格广播正确性：3×3 网格 = 系数行 × 波段（向量化黄金）
    grid = _bands_from_vector(_VECTOR, shape=(3, 3))
    res_grid = tasseled_cap(grid, sensor="landsat8_oli")
    for comp in TASSELED_CAP_COMPONENTS:
        expected = sum(
            coef * v for coef, v in
            zip(TASSELED_CAP_COEFFICIENTS["landsat8_oli"][comp], _VECTOR))
        np.testing.assert_allclose(
            res_grid["components"][comp], np.full((3, 3), expected),
            atol=1e-12)


def test_tasseled_cap_unknown_sensor_and_missing_role():
    bands = _bands_from_vector(_VECTOR)

    # 未知传感器 → UnsupportedMethod（不默认套用他传感器系数）
    with pytest.raises(UnsupportedMethod) as exc_info:
        tasseled_cap(bands, sensor="landsat2029")
    assert "注册" in str(exc_info.value) or "landsat2029" in str(exc_info.value)

    # 缺角色 → UnsupportedBandSemantics（列出缺失；拒绝位置猜测）
    missing_swir = {k: v for k, v in bands.items() if k != "swir1"}
    with pytest.raises(UnsupportedBandSemantics) as role_err:
        tasseled_cap(missing_swir, sensor="landsat5_tm")
    assert "swir1" in str(role_err.value)
    assert "位置" in str(role_err.value)

    # 只有 6 个数值波段但无语义命名 → 同样拒绝（位置猜测禁令）
    with pytest.raises(UnsupportedBandSemantics):
        tasseled_cap({"band_1": bands["blue"], "band_2": bands["green"],
                      "band_3": bands["red"], "band_4": bands["nir"],
                      "band_5": bands["swir1"], "band_6": bands["swir2"]},
                     sensor="sentinel2")

    # reflectance_domain：枚举外 → ValueError；合法值进披露
    with pytest.raises(ValueError, match="reflectance_domain"):
        tasseled_cap(bands, sensor="landsat5_tm", reflectance_domain="TOA_rad")
    res = tasseled_cap(bands, sensor="landsat5_tm",
                       reflectance_domain="surface")
    assert res["meta"]["reflectance_domain"] == "surface"
    assert "at-satellite" in res["meta"]["disclosure"]

    # 确定性
    a = tasseled_cap(bands, sensor="landsat8_oli")
    b = tasseled_cap(bands, sensor="landsat8_oli")
    for comp in TASSELED_CAP_COMPONENTS:
        np.testing.assert_array_equal(a["components"][comp],
                                      b["components"][comp])


# ── spectral +evi2（Foundation V2 additive；EVI2 = 两波段 EVI）───────

def test_spectral_evi2_hand_golden():
    # 手算：EVI2 = 2.5·(NIR − Red) / (NIR + 2.4·Red + 1)
    # red=0.2、nir=0.5 → 2.5·0.3 / (0.5 + 0.48 + 1) = 0.75 / 1.98
    res = compute_spectral_index(
        {"red": np.array([[0.2]]), "nir": np.array([[0.5]])}, "evi2")
    expected = 2.5 * 0.3 / (0.5 + 2.4 * 0.2 + 1.0)
    assert res["array"][0, 0] == pytest.approx(expected, abs=1e-12)
    assert res["array"][0, 0] == pytest.approx(0.75 / 1.98, abs=1e-12)
    # 出处：huete1988（SAVI/EVI 谱系；EVI2 本体 Huete 1997 不在词表——
    # 公式串披露谱系，不伪托独立条目）
    assert res["reference"] == "huete1988"
    assert "EVI2" in res["formula"]
    # 独立 numpy 复算（网格）
    rng = np.random.RandomState(0)
    red = rng.uniform(0, 1, (4, 4))
    nir = rng.uniform(0, 1, (4, 4))
    res_grid = compute_spectral_index({"red": red, "nir": nir}, "evi2")
    ref = 2.5 * (nir - red) / (nir + 2.4 * red + 1.0)
    np.testing.assert_allclose(res_grid["array"], ref, rtol=0, atol=1e-12)

    # 全零波段（S2 L2A nodata 惯例）→ NaN（与 evi 同语义，不产伪 0）
    zero = compute_spectral_index(
        {"red": np.array([[0.0]]), "nir": np.array([[0.0]])}, "evi2")
    assert np.isnan(zero["array"][0, 0])

    # 族计数 13（11 + NDWI 拆名对 ndwi_gao/ndwi_water，审计 §3.2）；
    # 所需角色 = (red, nir)
    assert len(INDEX_FAMILY) == 13
    assert INDEX_FAMILY["evi2"].required_roles == ("red", "nir")
    assert INDEX_FAMILY["evi2"].valid_range == (-1.0, 2.5)
    for existing in ("ndvi", "savi", "evi", "nbr", "msavi"):
        assert existing in INDEX_FAMILY

    # 缺角色 → 类型化拒绝；确定性
    with pytest.raises(UnsupportedBandSemantics):
        compute_spectral_index({"red": np.array([[0.2]])}, "evi2")
    again = compute_spectral_index({"red": red, "nir": nir}, "evi2")
    np.testing.assert_array_equal(again["array"], res_grid["array"])
