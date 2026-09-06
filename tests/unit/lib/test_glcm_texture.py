"""GLCM 纹理特征 conformance 测试（Foundation V2 · A6）。

覆盖（app/lib/geo_analysis/glcm.py，纯 numpy 手工实现）：

- 4×4 量化矩阵 → 单窗 GLCM 计数手算黄金矩阵（P+Pᵀ 对称约定，对角
  元加倍——scikit-image symmetric=True 同约定，docstring 声明）；
- 从黄金计数出发独立复算全部 9 属性（contrast/dissimilarity/
  homogeneity/asm/energy/entropy/mean/variance/correlation），
  与 glcm_texture 全图输出逐像元一致（window=5 覆盖 4×4 全图 →
  每个窗口 = 同一单窗 GLCM）；
- 常数场窗口：correlation → NaN（零方差诚实披露）、contrast=0、
  energy=1、entropy=0（不伪造）；
- 对称性（c == cᵀ、±d 同线等价）、确定性（重复调用逐位一致）、
  方向参数（"0"/"90" 各向异性差异、"all4"=NaN 感知均值）；
- 类型化/参数错误 + 规模闸（monkeypatched GLCM_OPS_LIMIT，
  ResourceScaleMismatch 先拒绝）+ nodata 剔除。
"""
import numpy as np
import pytest

from app.lib.geo_analysis import glcm as glcm_mod
from app.lib.geo_analysis.glcm import glcm_texture, window_glcm_counts
from app.lib.gis.scientific_errors import ResourceScaleMismatch

pytestmark = pytest.mark.unit

# 4×4 量化矩阵（值 = 量化档 id，levels=8 下两值 {0,7} 的量化为恒等：
# p2=0、p98=7 → q = floor(x/7·8) ∈ {0,7}，见 test_glcm_scale_guard_and_quantization）
M = np.array([
    [0, 0, 7, 7],
    [0, 0, 7, 7],
    [0, 7, 7, 7],
    [7, 7, 7, 0],
])

# 手算水平 (0,1) 共生计数（未对称）：
# 行0: (0,0),(0,7),(7,7)；行1: (0,0),(0,7),(7,7)
# 行2: (0,7),(7,7),(7,7)；行3: (7,7),(7,7),(7,0)
# → 未对称：(0,0)=2、(0,7)=3、(7,7)=6、(7,0)=1
# 对称化（P+Pᵀ，对角加倍；scikit-image symmetric=True 同约定）：
# c[0,0]=4、c[0,7]=c[7,0]=3+1=4、c[7,7]=12（总 32 = 2×12 对）
# levels=8：量化档 id ∈ {0,7} → 8×8 计数矩阵。
EXPECTED_COUNTS = np.zeros((8, 8), dtype=np.int64)
EXPECTED_COUNTS[0, 0] = 4
EXPECTED_COUNTS[0, 7] = 4
EXPECTED_COUNTS[7, 0] = 4
EXPECTED_COUNTS[7, 7] = 12


def test_glcm_hand_window_counts_golden():
    counts = window_glcm_counts(M, levels=8, dy=0, dx=1)
    np.testing.assert_array_equal(counts, EXPECTED_COUNTS)
    # 对称约定
    assert (counts == counts.T).all()
    assert counts.sum() == 24    # 2 × 12 未对称对

    # 从黄金计数独立复算全部属性（测试内独立 numpy 管线）
    p_mat = counts / counts.sum()
    ii, jj = np.mgrid[0:8, 0:8]
    diff = ii - jj
    props = {
        "contrast": (p_mat * diff ** 2).sum(),
        "dissimilarity": (p_mat * np.abs(diff)).sum(),
        "homogeneity": (p_mat / (1.0 + diff ** 2)).sum(),
        "asm": (p_mat ** 2).sum(),
        "entropy": -(p_mat[p_mat > 0] * np.log(p_mat[p_mat > 0])).sum(),
        "mean": (p_mat * ii).sum(),
        "variance": (p_mat * (ii - (p_mat * ii).sum()) ** 2).sum(),
    }
    mean = props["mean"]
    props["correlation"] = (
        (p_mat * ii * jj).sum() - mean ** 2) / props["variance"]
    props["energy"] = np.sqrt(props["asm"])

    # window=5 在 4×4 图上：中心 4 像元 (1,1),(1,2),(2,1),(2,2) 的窗口
    # 覆盖全部有效像元 → 其窗口 GLCM == 全图单窗 GLCM（黄金值一致）；
    # 角像元窗口只覆盖部分有效区（值不同，只断言有限）。
    res = glcm_texture(M.astype(float), window=5, levels=8,
                       directions="0")
    for name, expected in props.items():
        plane = res["properties"][name]
        for (i, j) in ((1, 1), (1, 2), (2, 1), (2, 2)):
            assert plane[i, j] == pytest.approx(
                expected, rel=1e-12, abs=1e-12), (name, i, j)
        assert np.isfinite(plane).all(), name
    # 披露在场（对称约定/量化/自然对数）
    disc = res["meta"]["disclosure"]
    for token in ("P+Pᵀ", "2-98", "自然对数", "numpy"):
        assert token in disc, token


def test_glcm_constant_window_correlation_nan():
    res = glcm_texture(np.full((6, 6), 3.0), window=3, levels=8)
    props = res["properties"]
    assert np.isnan(props["correlation"]).all()      # 零方差 → NaN
    np.testing.assert_allclose(props["contrast"], 0.0, atol=1e-12)
    np.testing.assert_allclose(props["energy"], 1.0, atol=1e-12)
    np.testing.assert_allclose(props["entropy"], 0.0, atol=1e-12)
    np.testing.assert_allclose(props["asm"], 1.0, atol=1e-12)
    np.testing.assert_allclose(props["homogeneity"], 1.0, atol=1e-12)
    assert np.isfinite(props["mean"]).all()
    assert "零方差" in res["meta"]["disclosure"]
    assert "NaN" in res["meta"]["disclosure"]


def test_glcm_symmetry_determinism_and_directions():
    # ±d 同线等价（P+Pᵀ 对称约定 ⇒ 方向符号不敏感）
    c_pos = window_glcm_counts(M, 8, 1, -1)
    c_neg = window_glcm_counts(M, 8, -1, 1)
    np.testing.assert_array_equal(c_pos, c_neg)

    # 确定性：重复调用逐位一致
    r1 = glcm_texture(M.astype(float), window=3, levels=8)
    r2 = glcm_texture(M.astype(float), window=3, levels=8)
    for name in r1["properties"]:
        np.testing.assert_array_equal(
            r1["properties"][name], r2["properties"][name])

    # 各向异性：竖条纹上水平/垂直方向的 contrast 不同
    stripes = np.tile(np.array([0.0, 7.0]).repeat(2), (8, 4))
    h = glcm_texture(stripes, window=3, levels=8, directions="0")
    v = glcm_texture(stripes, window=3, levels=8, directions="90")
    assert not np.allclose(
        h["properties"]["contrast"], v["properties"]["contrast"])
    # all4 = NaN 感知均值（介于各单方向值之间）
    a4 = glcm_texture(stripes, window=3, levels=8, directions="all4")
    h_c = h["properties"]["contrast"][2:-2, 2:-2].mean()
    v_c = v["properties"]["contrast"][2:-2, 2:-2].mean()
    a4_c = a4["properties"]["contrast"][2:-2, 2:-2].mean()
    assert min(h_c, v_c) - 1e-9 <= a4_c <= max(h_c, v_c) + 1e-9


def test_glcm_scale_guard_and_quantization(monkeypatch):
    # 规模闸（先估算后分配）：monkeypatch 上界 → 6×6×9×1 > 100 拒绝
    monkeypatch.setattr(glcm_mod, "GLCM_OPS_LIMIT", 100)
    with pytest.raises(ResourceScaleMismatch) as exc_info:
        glcm_texture(np.zeros((6, 6)), window=3, levels=8)
    err = exc_info.value
    assert err.estimated is not None and err.limit is not None
    assert "1296" in str(err)          # 6·6·3²·4 方向 操作估算
    monkeypatch.undo()

    # 参数守卫
    with pytest.raises(ValueError, match="window"):
        glcm_texture(np.zeros((6, 6)), window=4)
    with pytest.raises(ValueError, match="levels"):
        glcm_texture(np.zeros((6, 6)), levels=4)
    with pytest.raises(ValueError, match="directions"):
        glcm_texture(np.zeros((6, 6)), directions="all8")
    with pytest.raises(ValueError, match="properties"):
        glcm_texture(np.zeros((6, 6)), properties="haralick999")
    subset = glcm_texture(np.zeros((6, 6)), properties="contrast,energy")
    assert set(subset["properties"]) == {"contrast", "energy"}

    # 量化披露：两值 {0,7} 在 levels=8 下 2-98 分位拉伸为恒等映射
    res = glcm_texture(M.astype(float), window=5, levels=8)
    lo, hi = res["quantiles"]
    assert lo == pytest.approx(0.0, abs=1e-9)
    assert hi == pytest.approx(7.0, abs=1e-9)

    # nodata：任一像元无效的共生对被剔除 → 全无效区窗口 NaN
    masked = M.astype(float).copy()
    masked[:, 2:] = np.nan
    res_nd = glcm_texture(masked, window=3, levels=8)
    assert np.isnan(res_nd["properties"]["contrast"][:, 3]).all()
    assert np.isfinite(res_nd["properties"]["contrast"][:, :1]).all()
