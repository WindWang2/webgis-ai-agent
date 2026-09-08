"""medoid 时序合成（Flood 2013）conformance 测试。

断言三类：
- 数值锚：手工 3 时相小栈 → medoid 入选**真实清洁切片**（index 栅格
  精确），云污染切片永不入选；与逐波段 median 的拼接观测对照；
- NaN 语义：任一波段无效的切片整条剔除；全无效像元 → NaN + index −1；
- 守卫：ndim≠4 / T=1 / T 超限 / T·H·W 超预算 → 类型化拒绝。
"""
import numpy as np
import pytest

from app.lib.geo_analysis.sar_temporal import medoid_composite
from app.lib.gis.scientific_errors import ResourceScaleMismatch

pytestmark = pytest.mark.unit


def _scene() -> np.ndarray:
    """3 时相 × 2 波段 2×2 场：slice0/1 清洁且互相接近，slice2 云污染。

    像元 (0,0) 特意让两波段的逐波段中位数落在**不同切片**上
    （band0 中位=0.22 来自 slice1、band1 中位=0.62 来自 slice0）——
    逐波段 median 拼出 (0.22, 0.62) 这一不存在观测，与 medoid 对照。
    """
    t = np.zeros((3, 2, 2, 2), dtype=float)
    t[0, :, 0, 0] = [0.20, 0.62]
    t[0, :, 1, 1] = [0.30, 0.50]
    t[1, :, 0, 0] = [0.22, 0.41]
    t[1, :, 1, 1] = [0.31, 0.52]
    t[2, :, 0, 0] = [0.60, 0.80]      # 云污染：整体抬亮
    t[2, :, 1, 1] = [0.65, 0.85]
    # 交叉像元补齐（保持每切片全像元有值）
    t[0, :, 0, 1] = [0.25, 0.45]
    t[0, :, 1, 0] = [0.28, 0.48]
    t[1, :, 0, 1] = [0.24, 0.44]
    t[1, :, 1, 0] = [0.27, 0.47]
    t[2, :, 0, 1] = [0.62, 0.82]
    t[2, :, 1, 0] = [0.63, 0.83]
    return t


def test_medoid_selects_real_clean_observation():
    """medoid 入选真实清洁切片；与逐波段 median 的拼接观测对照。"""
    t = _scene()
    res = medoid_composite(t)
    idx = res["medoid_index"]
    # 云污染切片（slice 2）永不入选
    assert not (idx == 2).any()
    assert set(np.unique(idx)) <= {0, 1}
    # 输出必须逐位等于入选真实切片的光谱（跨波段一致性）
    for i in range(2):
        for j in range(2):
            np.testing.assert_array_equal(
                res["array"][:, i, j], t[idx[i, j], :, i, j])
    # 对照：逐波段 median 会产生不存在于任何切片的拼接观测
    from app.lib.geo_analysis.sar_temporal import temporal_composite

    band_median = np.stack([
        temporal_composite(t[:, b], method="median")["array"] for b in range(2)])
    assert not np.array_equal(band_median, res["array"])
    assert "Flood 2013" in res["meta"]["disclosure"]
    assert res["meta"]["method"] == "medoid"


def test_medoid_nan_and_guards():
    """NaN 切片整条剔除；全无效像元 → NaN + index −1；规模守卫类型化拒绝。"""
    t = _scene()
    t[0, 0, 0, 0] = np.nan                 # slice0 在像元 (0,0) 整条剔除
    res = medoid_composite(t)
    assert res["medoid_index"][0, 0] != 0  # 只能从 slice 1/2 选
    assert res["medoid_index"][1, 1] in (0, 1)

    # nodata 哨兵同 NaN 语义
    t2 = _scene()
    t2[1, 1, 0, 0] = -9999.0
    res2 = medoid_composite(t2, nodata=-9999.0)
    assert res2["medoid_index"][0, 0] != 1

    # 全无效像元 → NaN + index −1 + 计数披露
    t3 = _scene()
    t3[:, :, 1, 1] = np.nan
    res3 = medoid_composite(t3)
    assert np.isnan(res3["array"][:, 1, 1]).all()
    assert res3["medoid_index"][1, 1] == -1
    assert res3["meta"]["pixels_all_invalid"] == 1

    # 守卫：ndim ≠ 4
    with pytest.raises(ValueError, match="4D"):
        medoid_composite(np.zeros((3, 2, 2)))
    # T=1（需要 ≥2 个时相才有「到其余观测的距离」）
    with pytest.raises(ResourceScaleMismatch, match="medoid"):
        medoid_composite(np.zeros((1, 2, 2, 2)))
    # T 超限（≤24）
    with pytest.raises(ResourceScaleMismatch, match="medoid"):
        medoid_composite(np.zeros((25, 1, 2, 2)))
    # T·H·W 超预算（32M）
    with pytest.raises(ResourceScaleMismatch, match="预算"):
        medoid_composite(np.zeros((24, 1, 2048, 704)))


def test_medoid_tool_wrapper():
    """工具面：4D 内联 payload + 证据块 + 4M 内联守卫。"""
    import asyncio

    from app.tools.registry import ToolRegistry
    from app.tools.remote_sensing import register_rs_tools

    registry = ToolRegistry()
    register_rs_tools(registry)
    assert "medoid_composite" in set(registry.list_tools())
    tool_fn = registry._tools["medoid_composite"]

    t = _scene()
    payload = asyncio.run(tool_fn(stack=t.tolist()))
    assert payload["success"] is True
    assert payload["time_slices"] == 3
    assert payload["scientific_evidence"]["algorithm"] == \
        "remote.medoid_composite"
    idx = np.asarray(payload["medoid_index"])
    assert not (idx == 2).any()

    # 4M 内联守卫
    big = np.zeros((2, 1, 1500, 1400))     # 4.2M 值 > 4M
    with pytest.raises(ResourceScaleMismatch):
        asyncio.run(tool_fn(stack=big.tolist()))
