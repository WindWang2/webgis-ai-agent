"""SAR 辐射定标 V2 conformance 测试（Foundation V2 · A6）。

覆盖（app/lib/geo_analysis/sar_calibration.py）：

- 手算黄金值：amplitude 2、K=1 → β⁰=4；θ=30° → σ⁰=4·sin30°=2.0、
  γ⁰=4·tan30°（math.sin/tan 独立复算，精确一致）；
- intensity 域直通（不平方）、dB 转换 10·log₁₀ golden、output_product
  =all/beta0 的产品裁剪；
- 类型化错误：缺定标常数 → MissingRequiredField（LUT 提示在场）、
  负 DN → UnsupportedMethod、入射角越界 → InvalidUnits、
  σ⁰/γ⁰ 缺入射角 → MissingRequiredField；
- 逐像元入射角平面（形状一致 + (0,90) 全体有效）golden；
- nodata → NaN、确定性、诚实披露（LUT/热噪声未实现）在场。
"""
import math

import numpy as np
import pytest

from app.lib.gis.scientific_errors import (
    InvalidUnits,
    MissingRequiredField,
    UnsupportedMethod,
)
from app.lib.geo_analysis.sar_calibration import calibrate_sar

pytestmark = pytest.mark.unit


def test_calibration_hand_golden():
    # amplitude 2、K=1：I = 2² = 4 → β⁰ = 4；θ=30° → σ⁰ = 4·sin30° = 2.0
    res = calibrate_sar(
        np.array([[2.0]]), calibration_constant=1.0, incidence_deg=30.0,
        input_domain="dn_amplitude", output_product="all")
    beta0 = res["products"]["beta0"][0, 0]
    sigma0 = res["products"]["sigma0"][0, 0]
    gamma0 = res["products"]["gamma0"][0, 0]
    assert beta0 == pytest.approx(4.0, abs=1e-12)
    assert sigma0 == pytest.approx(4.0 * math.sin(math.radians(30.0)),
                                   abs=1e-12)
    assert sigma0 == pytest.approx(2.0, abs=1e-12)
    assert gamma0 == pytest.approx(4.0 * math.tan(math.radians(30.0)),
                                   abs=1e-12)
    # 振幅域平方披露在场
    assert res["meta"]["input_domain"] == "dn_amplitude"
    assert "平方" in res["meta"]["disclosure"]

    # 全 2×2 网格逐像元复算（振幅域，K=0.5）
    dn = np.array([[1.0, 2.0], [3.0, 4.0]])
    res2 = calibrate_sar(
        dn, calibration_constant=0.5, incidence_deg=40.0,
        input_domain="dn_amplitude", output_product="all")
    for i in range(2):
        for j in range(2):
            expect_beta = (dn[i, j] ** 2) / 0.5
            assert res2["products"]["beta0"][i, j] == pytest.approx(
                expect_beta, abs=1e-12)
            assert res2["products"]["sigma0"][i, j] == pytest.approx(
                expect_beta * math.sin(math.radians(40.0)), abs=1e-12)


def test_calibration_intensity_db_and_all_products():
    # intensity 域直通（不平方）：I=4、K=2 → β⁰=2
    res = calibrate_sar(
        np.array([[4.0]]), calibration_constant=2.0,
        input_domain="dn_intensity", output_product="beta0")
    assert res["products"]["beta0"][0, 0] == pytest.approx(2.0, abs=1e-12)
    assert res["products"]["sigma0"] is None
    assert res["products"]["gamma0"] is None
    assert res["meta"]["primary_product"] == "beta0"

    # dB 转换：10·log₁₀(2) golden
    res_db = calibrate_sar(
        np.array([[4.0]]), calibration_constant=2.0,
        input_domain="dn_intensity", output_product="beta0", to_db=True)
    assert res_db["products"]["beta0"][0, 0] == pytest.approx(
        10.0 * math.log10(2.0), abs=1e-12)
    assert res_db["meta"]["to_db"] is True

    # all：三产品齐出，主产品为 sigma0（请求序首位）
    res_all = calibrate_sar(
        np.array([[4.0]]), calibration_constant=2.0, incidence_deg=30.0,
        input_domain="dn_intensity", output_product="all")
    assert res_all["meta"]["products_computed"] == [
        "sigma0", "beta0", "gamma0"]
    for p in ("sigma0", "beta0", "gamma0"):
        assert res_all["products"][p] is not None

    # sigma0/gamma0 需要入射角 → MissingRequiredField
    with pytest.raises(MissingRequiredField):
        calibrate_sar(np.array([[4.0]]), calibration_constant=2.0,
                      output_product="sigma0")

    # 确定性
    again = calibrate_sar(
        np.array([[4.0]]), calibration_constant=2.0, incidence_deg=30.0,
        input_domain="dn_intensity", output_product="all")
    np.testing.assert_array_equal(
        again["products"]["sigma0"], res_all["products"]["sigma0"])


def test_calibration_missing_constant_typed_error():
    # 缺定标常数 → MissingRequiredField（绝不虚构 K；LUT 未实现提示在场）
    with pytest.raises(MissingRequiredField) as exc_info:
        calibrate_sar(np.array([[2.0]]), calibration_constant=None)
    assert "定标常数" in str(exc_info.value)
    assert "LUT" in exc_info.value.correction_hint

    # K 非正/非有限 → ValueError（普通参数校验）
    for bad in (0.0, -1.0, float("nan")):
        with pytest.raises(ValueError):
            calibrate_sar(np.array([[2.0]]), calibration_constant=bad)

    # 未知枚举 → ValueError
    with pytest.raises(ValueError, match="input_domain"):
        calibrate_sar(np.array([[2.0]]), calibration_constant=1.0,
                      input_domain="dB")
    with pytest.raises(ValueError, match="output_product"):
        calibrate_sar(np.array([[2.0]]), calibration_constant=1.0,
                      output_product="gamma_0")


def test_calibration_negative_dn_and_incidence_guards():
    # 负 DN：振幅/强度均非负 → UnsupportedMethod
    neg = np.array([[2.0, -0.5], [1.0, 3.0]])
    for domain in ("dn_amplitude", "dn_intensity"):
        with pytest.raises(UnsupportedMethod) as neg_err:
            calibrate_sar(neg, calibration_constant=1.0, input_domain=domain)
        assert "非负" in str(neg_err.value)

    # 入射角开区间 (0,90)：越界 → InvalidUnits
    for bad_angle in (0.0, 90.0, -5.0, 120.0):
        with pytest.raises(InvalidUnits):
            calibrate_sar(np.array([[2.0]]), calibration_constant=1.0,
                          incidence_deg=bad_angle, output_product="sigma0")

    # 逐像元入射角平面：形状不一致 → ValueError；含越界值 → InvalidUnits
    with pytest.raises(ValueError, match="形状"):
        calibrate_sar(np.zeros((2, 2)), calibration_constant=1.0,
                      incidence_map=np.full((3, 3), 30.0),
                      output_product="sigma0")
    bad_map = np.full((2, 2), 30.0)
    bad_map[0, 0] = 95.0
    with pytest.raises(InvalidUnits):
        calibrate_sar(np.zeros((2, 2)), calibration_constant=1.0,
                      incidence_map=bad_map, output_product="sigma0")

    # 逐像元平面 golden：σ⁰ = β⁰·sin(θ_ij) 逐像元精确
    dn = np.array([[2.0, 4.0]])
    inc = np.array([[30.0, 45.0]])
    res = calibrate_sar(dn, calibration_constant=2.0, incidence_map=inc,
                        input_domain="dn_amplitude", output_product="all")
    assert res["meta"]["incidence_mode"] == "per_pixel"
    assert res["products"]["sigma0"][0, 0] == pytest.approx(
        (4.0 / 2.0) * math.sin(math.radians(30.0)), abs=1e-12)
    assert res["products"]["sigma0"][0, 1] == pytest.approx(
        (16.0 / 2.0) * math.sin(math.radians(45.0)), abs=1e-12)

    # 标量与平面互斥
    with pytest.raises(ValueError, match="二选一"):
        calibrate_sar(dn, calibration_constant=2.0, incidence_deg=30.0,
                      incidence_map=inc, output_product="sigma0")

    # nodata → NaN（不参与、不稀释）；诚实披露（LUT/热噪声）在场
    nodata_arr = np.array([[2.0, -9999.0]])
    res_nd = calibrate_sar(
        nodata_arr, calibration_constant=1.0, incidence_deg=30.0,
        input_domain="dn_amplitude", output_product="all", nodata=-9999.0)
    assert res_nd["products"]["beta0"][0, 0] == pytest.approx(4.0, abs=1e-12)
    assert np.isnan(res_nd["products"]["beta0"][0, 1])
    assert "热噪声" in res_nd["meta"]["disclosure"]
    assert "LUT" in res_nd["meta"]["disclosure"]
