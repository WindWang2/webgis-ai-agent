"""波段栈 PCA conformance 测试（Foundation V2 · A6）。

覆盖（app/lib/geo_analysis/raster_pca.py，SVD 实现）：

- 秩 1 栈（band1=3·t、band2=7·t）：explained_variance_ratio = [1,0,0]
  精确（1e-12）；分量栅格 NaN 回填位置正确；
- 载荷/解释方差与**独立 numpy 参考**（np.cov + np.linalg.eigh，测试内
  复算）一致（符号不唯一 → 取 |.|/符号对齐比较）；
- 正交性（loadingsᵀ·loadings = I）与重建误差随 k 单调下降、
  k=full 时 ≈ 0；
- 公共有效掩膜（任一波段无效 → 整行剔除；占比披露；<50% → 警告；
  全无效 → NoValidObservations）；standardize=True 相关矩阵 PCA 参考
  一致、零方差波段 → DegenerateData；规模闸（monkeypatched cap）；
- 得分预览行数有界（PCA_PREVIEW_MAX_ROWS）+ 确定性。
"""
import numpy as np
import pytest

from app.lib.geo_analysis import raster_pca as pca_mod
from app.lib.geo_analysis.raster_pca import (
    PCA_PREVIEW_MAX_ROWS,
    pca_bands,
)
from app.lib.gis.scientific_errors import (
    DegenerateData,
    NoValidObservations,
    ResourceScaleMismatch,
)

pytestmark = pytest.mark.unit


def test_pca_rank1_explained_variance_exact():
    t = np.linspace(0.0, 1.0, 25).reshape(5, 5)
    stack = np.stack([t, 3.0 * t, 7.0 * t])       # 全部与 t 共线 → 秩 1
    res = pca_bands(stack)
    evr = res["explained_variance_ratio"]
    assert len(evr) == 3
    assert evr[0] == pytest.approx(1.0, abs=1e-12)
    assert evr[1] == pytest.approx(0.0, abs=1e-12)
    assert evr[2] == pytest.approx(0.0, abs=1e-12)
    assert res["meta"]["pca_type"] == "covariance"
    assert res["common_valid_fraction"] == pytest.approx(1.0, abs=1e-12)

    # 第一分量栅格与 t 共线（得分 = Xc·v ∝ t），无效像元 → NaN
    comp0 = res["component_rasters"][0]
    valid = np.isfinite(comp0)
    assert valid.all()
    corr = np.corrcoef(comp0.ravel(), t.ravel())[0, 1]
    assert abs(corr) == pytest.approx(1.0, abs=1e-12)


def test_pca_loadings_match_numpy_eig_reference():
    rng = np.random.RandomState(42)
    a = rng.uniform(0, 1, (6, 5))
    stack = np.stack([a, 0.5 * a + rng.uniform(0, 0.1, (6, 5)),
                      1.0 - a])
    res = pca_bands(stack)

    # 独立参考：样本协方差（ddof=1）+ eigh
    x = stack.reshape(3, -1).T                       # (n, bands)
    cov = np.cov(x, rowvar=False, ddof=1)
    eigvals, eigvecs = np.linalg.eigh(cov)           # 升序
    eigvals = eigvals[::-1]
    eigvecs = eigvecs[:, ::-1]

    explained = np.asarray(res["explained_variance"])
    np.testing.assert_allclose(explained, eigvals, rtol=1e-9, atol=1e-12)

    loadings = np.asarray(res["loadings"])           # bands × components
    # 符号不唯一（SVD 约定）——逐列取与参考符号一致的差
    for c in range(3):
        col = loadings[:, c]
        ref = eigvecs[:, c]
        sign = np.sign(np.dot(col, ref)) or 1.0
        np.testing.assert_allclose(col, sign * ref, rtol=1e-9, atol=1e-12)

    # explained_variance_ratio == eigvals / Σeigvals
    np.testing.assert_allclose(
        np.asarray(res["explained_variance_ratio"]),
        eigvals / eigvals.sum(), rtol=1e-9, atol=1e-15)


def test_pca_orthogonality_and_reconstruction():
    rng = np.random.RandomState(7)
    base = rng.uniform(0, 10, (8, 6))
    stack = np.stack([base, base * 0.3 + 1.0, 5.0 - base,
                      rng.uniform(0, 2, (8, 6))])
    res = pca_bands(stack)
    loadings = np.asarray(res["loadings"])

    # 正交性：Vᵀ·V = I
    gram = loadings.T @ loadings
    np.testing.assert_allclose(gram, np.eye(4), atol=1e-10)

    # 重建误差随 k 单调下降；k = full → ≈ 0
    x = stack.reshape(4, -1).T
    xc = x - x.mean(axis=0)
    scores = xc @ loadings
    errors = []
    for k in range(1, 5):
        recon = scores[:, :k] @ loadings[:, :k].T
        errors.append(np.linalg.norm(xc - recon))
    for e1, e2 in zip(errors, errors[1:]):
        assert e2 <= e1 + 1e-9
    assert errors[-1] == pytest.approx(0.0, abs=1e-8)


def test_pca_common_mask_and_scale_guard(monkeypatch):
    rng = np.random.RandomState(3)
    a = rng.uniform(0, 1, (4, 4))
    b = rng.uniform(0, 1, (4, 4))
    c = rng.uniform(0, 1, (4, 4))
    b[0, 0] = np.nan                       # 一个像元无效 → 整行剔除
    c[1, 1] = -9999.0                      # 哨兵无效
    stack = np.stack([a, b, c])

    res = pca_bands(stack, n_components=2, nodata=-9999.0)
    assert res["n_valid_pixels"] == 14
    assert res["common_valid_fraction"] == pytest.approx(14.0 / 16.0,
                                                         abs=1e-12)
    assert len(res["component_rasters"]) == 2
    for raster in res["component_rasters"]:
        assert np.isnan(raster[0, 0]) and np.isnan(raster[1, 1])
        assert np.isfinite(raster[3, 3])
    assert res["warnings"] == []           # 14/16 > 0.5 → 无退化警告

    # 公共有效 < 50% → 退化风险警告披露（不拒绝）
    b2 = np.full((4, 4), np.nan)
    b2[0, :2] = 0.5                        # 仅 2 像元公共有效 = 12.5%
    res_thin = pca_bands(np.stack([a, b2, c]))
    assert res_thin["common_valid_fraction"] == pytest.approx(0.125,
                                                              abs=1e-12)
    assert any("0.5" in w or "退化" in w for w in res_thin["warnings"])

    # 全无效 → NoValidObservations
    with pytest.raises(NoValidObservations):
        pca_bands(np.stack([a, np.full((4, 4), np.nan), c]))

    # 规模闸（先估算后分配；无流式实现的诚实拒绝）
    monkeypatch.setattr(pca_mod, "PCA_SCALE_LIMIT_CELLS", 10)
    with pytest.raises(ResourceScaleMismatch) as exc_info:
        pca_bands(np.stack([a, b, c]))
    assert exc_info.value.estimated is not None
    assert exc_info.value.limit is not None
    monkeypatch.undo()

    # 参数守卫：k 越界；standardize 遇零方差波段 → DegenerateData
    with pytest.raises(ValueError, match="n_components"):
        pca_bands(stack, n_components=5, nodata=-9999.0)
    const_band = np.stack([a, np.full((4, 4), 2.0)])
    with pytest.raises(DegenerateData):
        pca_bands(const_band, standardize=True)


def test_pca_preview_bounded_and_determinism():
    t = np.linspace(0.0, 1.0, 101 * 101).reshape(101, 101)   # 10201 有效像元
    stack = np.stack([t, 2.0 * t])
    res = pca_bands(stack)
    assert res["scores_preview"].shape[0] == PCA_PREVIEW_MAX_ROWS
    assert res["n_valid_pixels"] == 101 * 101
    assert res["meta"]["scores_preview_rows"] == PCA_PREVIEW_MAX_ROWS

    again = pca_bands(stack)
    np.testing.assert_array_equal(np.asarray(res["loadings"]),
                                  np.asarray(again["loadings"]))
    assert res["explained_variance_ratio"] == again["explained_variance_ratio"]
