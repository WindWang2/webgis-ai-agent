"""P2 修复回归：app/tools/remote_sensing.py（review-swarm P2-00）。

- 总规模闸：_bands_to_arrays 除单角色 ≤4M 外，全部角色合计 ≤16M 值；
- sar_calibrate 入射角 LUT：工具层校验 ndim/同形，超限抛
  ResourceScaleMismatch（不再裸 ValueError）；
- raster_pca / mnf_transform / ica_transform 的 component_rasters 走
  _bounded_sample 有界抽样（全幅载荷走栅格工件路径）。
"""
import numpy as np
import pytest

from app.tools.registry import ToolRegistry


@pytest.fixture()
def rs_tools():
    from app.tools.remote_sensing import register_rs_tools

    reg = ToolRegistry()
    register_rs_tools(reg)
    return reg._tools


# ── 发现 1：_bands_to_arrays 缺总量闸（单角色 4M 可被多角色累加绕过）─────


def test_bands_to_arrays_rejects_multi_role_total_over_limit():
    from app.lib.gis.scientific_errors import ResourceScaleMismatch
    from app.tools.remote_sensing import _bands_to_arrays

    # 每角色 1000×1000=1M（远低于单角色 4M），17 个角色合计 17M > 16M。
    # broadcast_to 是 0-stride 视图，避免测试本身分配上百 MB。
    plane = np.broadcast_to(np.zeros((1, 1), dtype=float), (1000, 1000))
    bands = {f"b{i}": plane for i in range(17)}
    with pytest.raises(ResourceScaleMismatch, match="总量上界"):
        _bands_to_arrays(bands)


def test_bands_to_arrays_allows_total_at_limit():
    from app.tools.remote_sensing import _bands_to_arrays

    # 恰好 16×1M = 16M：边界不得误伤。
    plane = np.broadcast_to(np.zeros((1, 1), dtype=float), (1000, 1000))
    arrays = _bands_to_arrays({f"b{i}": plane for i in range(16)})
    assert len(arrays) == 16


# ── 发现 3：sar_calibrate 入射角 LUT 形状/规模校验 ────────────────────────


def test_sar_calibrate_incidence_lut_oversize_is_typed(rs_tools):
    from app.lib.gis.scientific_errors import ResourceScaleMismatch

    # 2001×2000=4.002M > 4M；broadcast_to 避免真实分配 ~32MB。
    lut = np.broadcast_to(np.full((1, 1), 30.0), (2001, 2000))
    with pytest.raises(ResourceScaleMismatch, match="入射角 LUT"):
        rs_tools["sar_calibrate"](
            array=[[1.0, 2.0], [3.0, 4.0]],
            calibration_constant=2.0,
            incidence_lut=lut,
        )


@pytest.mark.parametrize("lut", ([[30.0, 31.0]], [[30.0], [31.0]]))
def test_sar_calibrate_incidence_lut_shape_mismatch_rejected_even_for_beta0(
        rs_tools, lut):
    # beta0 不用入射角，此前错误形状的 LUT 会被静默接受；契约要求同形 2D。
    with pytest.raises(ValueError, match="形状"):
        rs_tools["sar_calibrate"](
            array=[[1.0, 2.0], [3.0, 4.0]],
            calibration_constant=2.0,
            incidence_lut=lut,
            output_product="beta0",
        )


def test_sar_calibrate_incidence_lut_same_shape_still_accepted(rs_tools):
    out = rs_tools["sar_calibrate"](
        array=[[1.0, 2.0], [3.0, 4.0]],
        calibration_constant=2.0,
        incidence_lut=[[30.0, 31.0], [32.0, 33.0]],
    )
    assert out["success"] is True
    assert out["incidence_lut_source"] == "incidence_lut"


# ── 发现 4：component_rasters 全幅 .tolist() → 有界抽样 ────────────────────


def _stack(n_bands: int, size: int, seed: int):
    rng = np.random.default_rng(seed)
    return {
        f"b{i}": rng.uniform(0.0, 1.0, size=(size, size)).round(6).tolist()
        for i in range(n_bands)
    }


@pytest.mark.parametrize("tool", ("raster_pca", "mnf_transform", "ica_transform"))
def test_component_rasters_are_bounded_samples(rs_tools, tool):
    bands = _stack(3, 80, seed=7)
    if tool == "raster_pca":
        bands = list(bands.values())
    out = rs_tools[tool](bands=bands, n_components=2)
    assert out["success"] is True
    assert len(out["component_rasters"]) == 2
    # 80×80 全幅会被抽样到 step=2 → 40×40；不得再返回全幅平面。
    for raster in out["component_rasters"]:
        assert len(raster) <= 64
        assert max(len(row) for row in raster) <= 64
