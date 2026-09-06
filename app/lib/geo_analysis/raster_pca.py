"""波段栈 PCA —— SVD 主成分分析（Foundation V2 · A6）。

多波段栅格的主成分变换（Karakasis 类波段降维/去相关惯用法）：

- **SVD 实现**：X (n_valid_pixels × n_bands) 中心化后
  ``np.linalg.svd(Xc, full_matrices=False)``；explained_variance =
  S²/(n−1)（样本方差 ddof=1，scikit-learn 惯例，披露）；loadings =
  Vᵀᵀ（bands × components，正交）；得分 = Xc·V。
- ``standardize=False``（默认）→ **协方差 PCA**（量纲相同时惯用）；
  ``standardize=True`` → 相关矩阵 PCA（逐波段 z-score；零方差波段
  → DegenerateData 拒绝，不产 NaN 载荷）。
- **nodata 语义（诚实最简）**：跨波段**公共有效掩膜**——任一波段
  无效的像元整行剔除（不做 pairwise-complete 矩阵，其偏半定修正
  不在本实现范围，披露）；common_valid_fraction < 0.5 → 警告披露
  （不拒绝）；0 → NoValidObservations。
- **规模守卫（先估算后分配）**：n_bands·H·W ≤ 16M 像元总量，超限抛
  ``ResourceScaleMismatch``——本迭代**无流式两遍实现**（诚实拒绝，
  不假装可扩展）；SVD 在有效像元矩阵上全量执行。
- 输出：explained_variance_ratio、loadings、前 k 分量栅格
  （NaN 回填无效像元）、得分预览（行数有界，披露）。

出处诚实声明：标准 SVD/PCA 无单一经典出处条目（method_references
词表无对应 id）——``method_references`` 留空，不伪托。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

from app.lib.gis.scientific_errors import (
    DegenerateData,
    NoValidObservations,
    ResourceScaleMismatch,
)

__all__ = [
    "PCA_SCALE_LIMIT_CELLS",
    "PCA_PREVIEW_MAX_ROWS",
    "pca_bands",
]

PCA_SCALE_LIMIT_CELLS = 16_777_216      # n_bands·H·W（16M 像元总量）
PCA_PREVIEW_MAX_ROWS = 10_000           # 得分预览行数上界（披露）
_COMMON_VALID_WARN_FRACTION = 0.5


def _as_stack(
    bands: Union[np.ndarray, Sequence[np.ndarray]],
) -> Tuple[np.ndarray, int]:
    """输入归一为 3D (n_bands, H, W)；列表输入校验同形。"""
    if isinstance(bands, np.ndarray):
        stack = np.asarray(bands, dtype=float)
        if stack.ndim != 3:
            raise ValueError(
                f"波段栈必须是 3D (n_bands, H, W)，got ndim={stack.ndim}")
        return stack, int(stack.shape[0])
    arrays = [np.asarray(b, dtype=float) for b in bands]
    if not arrays:
        raise ValueError("波段列表不能为空")
    shape = arrays[0].shape
    for i, arr in enumerate(arrays):
        if arr.ndim != 2:
            raise ValueError(
                f"bands[{i}] 必须是 2D 数组，got ndim={arr.ndim}")
        if arr.shape != shape:
            raise ValueError(
                f"bands[{i}] 形状 {arr.shape} 与 bands[0] {shape} 不一致")
    return np.stack(arrays, axis=0), len(arrays)


def pca_bands(
    bands: Union[np.ndarray, Sequence[np.ndarray]],
    *,
    standardize: bool = False,
    n_components: Optional[int] = None,
    nodata: Optional[float] = None,
) -> Dict[str, object]:
    """波段栈 PCA（协方差/相关 PCA；公共有效掩膜；SVD 全量实现）。

    Args:
        bands: 3D (n_bands, H, W) 栈或同形 2D 数组列表。
        standardize: True → 相关矩阵 PCA（逐波段 z-score）；
            False（默认）→ 协方差 PCA。
        n_components: 输出分量栅格数 k（≤ n_bands；缺省 = n_bands）。
        nodata: 标量哨兵值；NaN/Inf 自动视为无效。

    Returns:
        dict: explained_variance_ratio（和为 1，降序）、
        explained_variance、loadings（n_bands × n_bands，列=分量）、
        component_rasters（前 k 分量回填 2D，无效像元 NaN）、
        scores_preview（n_preview × n_bands，行数有界披露）、
        common_valid_fraction、n_valid_pixels、warnings、meta。
    """
    stack, n_bands = _as_stack(bands)
    n_b, height, width = stack.shape
    total_cells = n_b * int(height) * int(width)
    if total_cells > PCA_SCALE_LIMIT_CELLS:
        raise ResourceScaleMismatch(
            f"PCA 波段栈规模超限：{n_b}×{height}×{width}={total_cells} 像元"
            f"（≤{PCA_SCALE_LIMIT_CELLS}；无流式实现，先拒绝）",
            estimated=f"{total_cells * 8 / 1e6:.1f} MB float64 栈 "
                      f"+ SVD 工作面",
            limit=f"n_bands·H·W≤{PCA_SCALE_LIMIT_CELLS}",
            correction_hint="降采样/分块聚合后再 PCA，或减少波段数",
        )

    k = int(n_components) if n_components is not None else n_bands
    if not (1 <= k <= n_bands):
        raise ValueError(
            f"n_components 必须在 [1, n_bands={n_bands}] 内，got {k!r}")

    valid = np.isfinite(stack)
    if nodata is not None:
        valid &= stack != float(nodata)
    common = valid.all(axis=0)
    n_valid = int(common.sum())
    common_fraction = n_valid / max(1, common.size)
    warnings: List[str] = []
    if n_valid == 0:
        raise NoValidObservations(
            "无跨波段公共有效像元——PCA 需要≥1 个全波段有效像元",
            correction_hint="检查各波段 nodata/对齐；或放宽 nodata 设置")
    if common_fraction < _COMMON_VALID_WARN_FRACTION:
        warnings.append(
            f"common_valid_fraction={common_fraction:.3f} < 0.5——公共有效"
            "像元占比过低，样本代表性退化（退化数据风险，结果慎读）")

    x = stack[:, common].T                      # (n_valid, n_bands)
    mean = x.mean(axis=0)
    xc = x - mean
    if standardize:
        std = x.std(axis=0, ddof=1)
        if (std <= 1e-15).any():
            raise DegenerateData(
                f"standardize=True 但波段 {np.where(std <= 1e-15)[0].tolist()}"
                "方差为 0——z-score 无定义",
                correction_hint="剔除常量波段，或改用协方差 PCA "
                                "(standardize=False)")
        xc = xc / std

    _, s_vals, vt = np.linalg.svd(xc, full_matrices=False)
    n_comps = int(vt.shape[0])              # ≤ min(n_valid−?, n_bands)
    k_out = min(k, n_comps)
    explained = (s_vals ** 2) / max(1, n_valid - 1)      # ddof=1（披露）
    evr = explained / explained.sum()
    loadings = vt.T                                      # bands × components

    scores = xc @ vt.T                                    # (n_valid, n_comps)
    preview_rows = min(n_valid, PCA_PREVIEW_MAX_ROWS)

    component_rasters: List[np.ndarray] = []
    flat = np.full((int(height) * int(width),), np.nan)
    for c in range(k_out):
        plane = flat.copy()
        plane[common.ravel()] = scores[:n_valid, c]
        component_rasters.append(plane.reshape(height, width))

    meta: Dict[str, object] = {
        "n_bands": n_bands,
        "grid": [int(height), int(width)],
        "n_valid_pixels": n_valid,
        "common_valid_fraction": common_fraction,
        "standardize": bool(standardize),
        "pca_type": "correlation" if standardize else "covariance",
        "variance_ddof": 1,
        "n_components_rasters": k_out,
        "scores_preview_rows": preview_rows,
        "streaming": "none（本迭代无流式实现；规模守卫先拒绝）",
        "disclosure": (
            "SVD 全量实现（确定性）；载荷/分量的代数符号依 LAPACK 约定"
            "（同一构建内稳定，跨构建可能整体翻转）；公共有效掩膜（任一波段无效 → 整行"
            "剔除，非 pairwise-complete）；explained_variance 为样本方差"
            "（ddof=1）；得分预览行数有界（全量得分不在证据内）"),
    }
    return {
        "explained_variance_ratio": evr[:k_out].tolist(),
        "explained_variance": explained[:k_out].tolist(),
        "explained_variance_ratio_all": evr.tolist(),
        "loadings": loadings,
        "component_rasters": component_rasters,
        "scores_preview": scores[:preview_rows],
        "n_valid_pixels": n_valid,
        "common_valid_fraction": common_fraction,
        "warnings": warnings,
        "meta": meta,
    }
