"""遥感 V3 批次 —— 波段栈科学算法族（Foundation V3 · Remote Sensing V3）。

13 个算法，输入约定与 ``raster_pca.py`` 一致（2D 波段栈：3D (k,H,W)、
同形 2D 数组序列，或 ``{角色: 2D 数组}`` 字典——字典按插入序进入，
波段序在 meta ``band_order`` 披露）：

- ``mnf``             —— 最小噪声分数变换（Green et al. 1988）
- ``ica``             —— FastICA 独立成分分析（Hyvärinen 1999；sklearn）
- ``spectral_angle_mapper``          —— 光谱角制图（Kruse 1993）
- ``spectral_information_divergence`` —— 光谱信息散度（Chang 2000）
- ``matched_filter``  —— 匹配滤波目标检测（Boardman 1995）
- ``rx_anomaly``      —— RX 全局异常检测（Reed & Xiaoli 1990）
- ``mad_change``      —— MAD / IR-MAD 变化检测（Nielsen 1998）
- ``segment_image``   —— k-means 分割基座（Lloyd 1982；非 SLIC，披露）
- ``extract_endmembers_vca`` —— 端元提取 VCA（Nascimento & Dias 2005，EXPERIMENTAL）
- ``fcls_unmix``      —— FCLS 全约束线性光谱解混（Heinz & Chang 2001）
- ``band_correlation_table`` —— 波段×波段 Pearson 相关表（公共有效掩膜）
- ``temporal_features`` —— 逐像元时序特征（单周期谐波 + 线性去趋势，披露）
- ``robust_normalize`` —— 稳健跨波段/跨场景归一化（2-98 分位，披露）
- ``cloud_qc_basic``  —— 亮度百分比阈值云咨询掩膜（EXPERIMENTAL，非 Fmask）

诚实性契约（与 raster_pca.py 同约定）：

- **公共有效掩膜**：任一波段无效（NaN/Inf/哨兵）的像元整行剔除
  （非 pairwise-complete）；0 个公共有效像元 → ``NoValidObservations``；
- **规模守卫（先估算后分配）**：n_bands·H·W ≤ 16M 像元总量，超限抛
  ``ResourceScaleMismatch``（本迭代无分块/流式实现，诚实拒绝）；
- **确定性**：全部随机经 ``np.random.default_rng(seed)`` 或 sklearn
  ``random_state=42``（模块级 ``_FIXED_SEED``）；无 wall-clock 依赖；
- **近似披露**：估计量/简化实现全部写进 meta 披露（中文），绝不静默；
- 统计全部 nan-aware；输出栅格 NaN 回填无效像元。
"""
from __future__ import annotations

import warnings
from typing import Dict, List, Mapping, Optional, Sequence, Tuple, Union

import numpy as np

from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    NoValidObservations,
    ResourceScaleMismatch,
    UnsupportedMethod,
)

__all__ = [
    "RS_SCALE_LIMIT_CELLS",
    "RS_FIXED_SEED",
    "mnf",
    "mnf_inverse",
    "ica",
    "spectral_angle_mapper",
    "spectral_information_divergence",
    "matched_filter",
    "rx_anomaly",
    "mad_change",
    "segment_image",
    "extract_endmembers_vca",
    "fcls_unmix",
    "band_correlation_table",
    "temporal_features",
    "robust_normalize",
    "cloud_qc_basic",
]

RS_SCALE_LIMIT_CELLS = 16_777_216      # n_bands·H·W（16M 像元总量，同 raster_pca）
RS_FIXED_SEED = 42                     # 模块级固定种子（确定性披露）
_COMMON_VALID_WARN_FRACTION = 0.5
_MAD_RHO_CLAMP = 1e-12                 # ρ ≤ 1−clamp → 方差下界，防 0/0
_IRMAD_WEIGHT_FLOOR = 1e-4             # IR-MAD 权重下限（防单像元权重爆炸）
_IRMAD_CONVERGED_DELTA = 1e-6


# ── 输入归一 / 公共掩膜 / 规模守卫（raster_pca 同约定）─────────────────

StackInput = Union[np.ndarray, Sequence[np.ndarray], Mapping[str, np.ndarray]]


def _as_stack(bands: StackInput) -> Tuple[np.ndarray, List[str]]:
    """输入归一为 3D (n_bands, H, W)；dict 按插入序，名单随 meta 披露。"""
    if isinstance(bands, Mapping):
        if not bands:
            raise ValueError("bands 字典不能为空")
        names: List[str] = []
        arrays: List[np.ndarray] = []
        shape = None
        for role, data in bands.items():
            arr = np.asarray(data, dtype=float)
            if arr.ndim != 2:
                raise ValueError(
                    f"bands[{role!r}] 必须是 2D 数组，got ndim={arr.ndim}")
            if shape is None:
                shape = arr.shape
            elif arr.shape != shape:
                raise ValueError(
                    f"bands[{role!r}] 形状 {arr.shape} 与首波段 {shape} 不一致")
            names.append(str(role))
            arrays.append(arr)
        return np.stack(arrays, axis=0), names
    if isinstance(bands, np.ndarray):
        stack = np.asarray(bands, dtype=float)
        if stack.ndim != 3:
            raise ValueError(
                f"波段栈必须是 3D (n_bands, H, W)，got ndim={stack.ndim}")
        return stack, [f"band_{i}" for i in range(stack.shape[0])]
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
    return np.stack(arrays, axis=0), [f"band_{i}" for i in range(len(arrays))]


def _check_scale(stack: np.ndarray, what: str) -> None:
    """规模守卫：n_bands·H·W ≤ 16M 像元（先估算后分配；无流式实现）。"""
    n_b, height, width = stack.shape
    total = n_b * int(height) * int(width)
    if total > RS_SCALE_LIMIT_CELLS:
        raise ResourceScaleMismatch(
            f"{what} 波段栈规模超限：{n_b}×{height}×{width}={total} 像元"
            f"（≤{RS_SCALE_LIMIT_CELLS}；无流式实现，先拒绝）",
            estimated=f"{total * 8 / 1e6:.1f} MB float64 栈 + 线性代数工作面",
            limit=f"n_bands·H·W≤{RS_SCALE_LIMIT_CELLS}",
            correction_hint="降采样/分块聚合后重试，或减少波段数",
        )


def _common_valid(
    stack: np.ndarray, nodata: Optional[float] = None, what: str = "",
) -> Tuple[np.ndarray, int, float, List[str]]:
    """公共有效掩膜：任一波段无效 → 整像元剔除；0 → NoValidObservations。"""
    valid = np.isfinite(stack)
    if nodata is not None:
        valid &= stack != float(nodata)
    common = valid.all(axis=0)
    n_valid = int(common.sum())
    fraction = n_valid / max(1, common.size)
    warnings_list: List[str] = []
    if n_valid == 0:
        raise NoValidObservations(
            f"无跨波段公共有效像元——{what or '本算法'}需要≥1 个全波段有效像元",
            correction_hint="检查各波段 nodata/对齐；或放宽 nodata 设置")
    if fraction < _COMMON_VALID_WARN_FRACTION:
        warnings_list.append(
            f"common_valid_fraction={fraction:.3f} < 0.5——公共有效像元占比"
            "过低，样本代表性退化（结果慎读）")
    return common, n_valid, fraction, warnings_list


def _cov(x: np.ndarray) -> np.ndarray:
    """样本协方差（ddof=1，scikit-learn 惯例）；单波段输入升 2D。"""
    return np.atleast_2d(np.cov(x, rowvar=False, ddof=1))


def _require_samples(n_valid: int, need: int, what: str) -> None:
    if n_valid < need:
        raise InsufficientSamples(
            f"{what}: 公共有效像元 {n_valid} < 方法学下限 {need}",
            correction_hint="扩大切片范围或降低波段数后重试")


def _backfill(
    values: np.ndarray, common: np.ndarray, height: int, width: int,
) -> np.ndarray:
    """一列像元值 → 2D 栅格（无效像元 NaN 回填）。"""
    plane = np.full(int(height) * int(width), np.nan, dtype=float)
    plane[common.ravel()] = values
    return plane.reshape(height, width)


# ── 1. MNF（Green et al. 1988）────────────────────────────────────────

def _noise_covariance_local_diff(stack: np.ndarray, common: np.ndarray) -> Tuple[np.ndarray, Dict[str, int]]:
    """局部差分噪声协方差估计（披露估计量）。

    水平/垂直一阶差分各自在「两端像元均有效」的差分向量上取样本协方差
    （ddof=1）；估计量 = 两方向协方差的均值再除以 2 —— 差分是两个独立
    噪声样本之和（方差加倍），除 2 还原噪声协方差，使白化空间噪声
    方差=1、SNR=λ−1 语义成立。
    """
    dh = np.diff(stack, axis=2)
    mask_h = common[:, 1:] & common[:, :-1]
    dv = np.diff(stack, axis=1)
    mask_v = common[1:, :] & common[:-1, :]
    n_h = int(mask_h.sum())
    n_v = int(mask_v.sum())
    _require_samples(min(n_h, n_v), 2, "MNF 局部差分噪声估计")
    covs: List[np.ndarray] = []
    for diff, mask in ((dh, mask_h), (dv, mask_v)):
        vectors = diff[:, mask].T              # (n, k)
        covs.append(_cov(vectors))
    c_noise = (covs[0] + covs[1]) / 4.0        # 均值后再除 2（差分加倍校正）
    c_noise = (c_noise + c_noise.T) / 2.0      # 对称化（数值卫生）
    return c_noise, {"horizontal_diffs": n_h, "vertical_diffs": n_v}


def mnf(
    stack: StackInput,
    n_components: Optional[int] = None,
    *,
    noise_estimation: str = "local_diff",
    standardize: bool = False,
    nodata: Optional[float] = None,
) -> Dict[str, object]:
    """最小噪声分数变换（MNF，Green et al. 1988）。

    流程：局部差分噪声协方差 Σ_n → 特征分解 Σ_n = U D Uᵀ →
    白化 W = D^{-1/2} Uᵀ → 白化数据 SVD（噪声空间 PCA）→ 分量按 SNR
    降序。白化空间内噪声方差=1，故 SNR_i = λ_i − 1（λ 为白化 PCA 特征
    值，样本方差 ddof=1）。载荷（原始空间滤波器）A = Wᵀ·V。

    Args:
        stack: 3D (k,H,W) 栈 / 同形 2D 列表 / {角色: 2D 数组}。
        n_components: 输出分量栅格数（≤ n_bands；缺省 = n_bands）。
        noise_estimation: 仅支持 "local_diff"（局部差分估计，披露）。
        standardize: True → 逐波段 z-score 后再变换（相关矩阵语义）。
        nodata: 标量哨兵；NaN/Inf 自动视为无效。

    Returns:
        dict: component_rasters（前 k 分量，NaN 回填）、eigenvalues（白化
        空间方差，降序）、snr（λ−1）、loadings_original（k×k，列=原始
        空间滤波器）、noise_covariance、whiten_matrix、mean、
        common_valid_fraction、n_valid_pixels、warnings、meta。
    """
    if noise_estimation != "local_diff":
        raise UnsupportedMethod(
            f"未知噪声估计方式 {noise_estimation!r}——本实现仅支持 "
            "'local_diff'（局部一阶差分，披露估计量）",
            correction_hint="noise_estimation='local_diff'，或先扩展实现")
    arr, band_names = _as_stack(stack)
    _check_scale(arr, "MNF")
    n_bands, height, width = arr.shape
    k = int(n_components) if n_components is not None else n_bands
    if not (1 <= k <= n_bands):
        raise ValueError(
            f"n_components 必须在 [1, n_bands={n_bands}] 内，got {k!r}")

    common, n_valid, fraction, warn = _common_valid(arr, nodata, "MNF")
    _require_samples(n_valid, max(8, n_bands + 1), "MNF")
    x = arr[:, common].T                          # (n, k)
    mean = x.mean(axis=0)
    xc = x - mean
    if standardize:
        std = x.std(axis=0, ddof=1)
        if (std <= 1e-15).any():
            raise DegenerateData(
                f"standardize=True 但波段 {np.where(std <= 1e-15)[0].tolist()}"
                "方差为 0——z-score 无定义",
                correction_hint="剔除常量波段，或改用 standardize=False")
        xc = xc / std

    c_noise, n_diffs = _noise_covariance_local_diff(arr, common)
    d_vals, u_vecs = np.linalg.eigh(c_noise)      # 升序
    # 奇异判定：相对 + 绝对双闸（纯梯度栈的差分协方差整体≈数值 0，
    # 仅相对闸会漏检——用数据方差尺度兜底）。
    data_scale = max(float(np.trace(_cov(xc))), 1e-300)
    noise_floor = max(float(d_vals.max()) * 1e-12, data_scale * 1e-15)
    if float(d_vals.min()) <= noise_floor:
        raise DegenerateData(
            "MNF 噪声协方差奇异（最小噪声特征值≈0）——存在无噪声/常量或"
            "共线波段，白化无定义",
            correction_hint="剔除常量/完全共线波段，或叠加真实噪声后重试")
    whiten = (u_vecs / np.sqrt(d_vals)) @ u_vecs.T      # D^{-1/2} Uᵀ（对称形）
    y = xc @ whiten.T                             # 白化数据 (n, k)
    u_s, s_vals, vt = np.linalg.svd(y, full_matrices=False)
    eigenvalues = (s_vals ** 2) / max(1, n_valid - 1)   # ddof=1（披露）
    snr = eigenvalues - 1.0
    loadings_original = whiten.T @ vt.T                 # k×k，列=原始空间滤波器
    # Xc = scores·R；Y = Xc·Wᵀ 的逆：W^{-1} = U·D^{1/2}·Uᵀ（对称）
    # → R = Vt·U·D^{1/2}·Uᵀ（全分量重建精确；截断为最小二乘近似）
    reconstruction_matrix = vt @ (u_vecs * np.sqrt(
        np.clip(d_vals, 0.0, None))) @ u_vecs.T
    scores = u_s * s_vals                               # (n, n_comps)

    k_out = min(k, int(vt.shape[0]))
    component_rasters = [
        _backfill(scores[:, c], common, height, width) for c in range(k_out)
    ]

    meta: Dict[str, object] = {
        "n_bands": n_bands,
        "band_order": band_names,
        "grid": [int(height), int(width)],
        "n_valid_pixels": n_valid,
        "common_valid_fraction": fraction,
        "noise_estimation": "local_diff",
        "noise_covariance_formula": (
            "Σ_noise = (C_h + C_v)/4：水平/垂直一阶差分样本协方差均值 ÷2"
            "（差分使独立噪声方差加倍，除 2 还原；白化空间噪声方差=1）"),
        "noise_diff_counts": n_diffs,
        "standardize": bool(standardize),
        "variance_ddof": 1,
        "n_components_rasters": k_out,
        "snr_definition": "SNR_i = λ_i − 1（白化空间噪声方差=1；λ 为白化 PCA 特征值）",
        "streaming": "none（本迭代无流式实现；规模守卫先拒绝）",
        "disclosure": (
            "MNF 按 Green et al. (1988) 噪声白化 PCA 实现；载荷/分量代数符号"
            "依 LAPACK 约定（同一构建内稳定）；公共有效掩膜（非 pairwise-"
            "complete）；噪声估计量为局部差分近似（非逐波段独立噪声真值）"),
    }
    return {
        "component_rasters": component_rasters,
        "eigenvalues": eigenvalues[:k_out].tolist(),
        "snr": snr[:k_out].tolist(),
        "loadings_original": loadings_original,
        "reconstruction_matrix": reconstruction_matrix,
        "noise_covariance": c_noise,
        "whiten_matrix": whiten,
        "mean": mean,
        "scores": scores,
        "n_valid_pixels": n_valid,
        "common_valid_fraction": fraction,
        "warnings": warn,
        "meta": meta,
    }


def mnf_inverse(
    mnf_result: Dict[str, object],
    n_components: Optional[int] = None,
) -> np.ndarray:
    """MNF 逆变换 / 去噪重建：X̂ = μ + Σ_c score_c · r_c（前 k 分量）。

    r_c 为重建矩阵行（= V·U·D^{1/2} 的行；Y = Xc·Wᵀ 的精确逆为
    W^{-1} = U·D^{1/2}，全分量重建精确）。``n_components`` < 全部分量
    时即 MNF 去噪（截断重建，最小二乘意义近似——披露）；NaN 分量像元
    保持 NaN。
    """
    rasters: List[np.ndarray] = list(mnf_result["component_rasters"])  # type: ignore[arg-type]
    recon_matrix = np.asarray(mnf_result["reconstruction_matrix"], dtype=float)
    mean = np.asarray(mnf_result["mean"], dtype=float)
    k_out = int(mnf_result["meta"]["n_components_rasters"])  # type: ignore[index]
    k = int(n_components) if n_components is not None else k_out
    if not (1 <= k <= k_out):
        raise ValueError(
            f"n_components 必须在 [1, {k_out}] 内，got {k!r}")
    cube = np.stack(rasters[:k], axis=0)              # (k, H, W)
    height, width = cube.shape[1], cube.shape[2]
    valid = np.isfinite(cube).all(axis=0)
    score_rows = cube[:, valid].T                      # (n, k)
    recon = mean[None, :] + score_rows @ recon_matrix[:k, :]   # (n, n_bands)
    planes = np.full((recon.shape[1], height * width), np.nan, dtype=float)
    planes[:, valid.ravel()] = recon.T
    return planes.reshape(recon.shape[1], height, width)


# ── 2. FastICA（Hyvärinen 1999；sklearn）──────────────────────────────

def ica(
    stack: StackInput,
    n_components: Optional[int] = None,
    *,
    max_iter: int = 200,
    tol: float = 1e-4,
    nodata: Optional[float] = None,
) -> Dict[str, object]:
    """FastICA 独立成分分析（sklearn.decomposition.FastICA）。

    ``random_state=42`` + ``whiten="unit-variance"``（确定性、单位方差
    白化，披露）。收敛性诚实披露：sklearn 以 ``ConvergenceWarning`` 报告
    未收敛——捕获后 ``converged=False`` 进 meta/warnings（不静默、也不
    假装成功）。
    """
    from sklearn.decomposition import FastICA
    from sklearn.exceptions import ConvergenceWarning

    arr, band_names = _as_stack(stack)
    _check_scale(arr, "ICA")
    n_bands, height, width = arr.shape
    k = int(n_components) if n_components is not None else n_bands
    if not (1 <= k <= n_bands):
        raise ValueError(
            f"n_components 必须在 [1, n_bands={n_bands}] 内，got {k!r}")
    if not (np.isfinite(tol) and tol > 0):
        raise ValueError(f"tol 必须为正有限数，got {tol!r}")
    if int(max_iter) < 1:
        raise ValueError(f"max_iter 必须 ≥1，got {max_iter!r}")

    common, n_valid, fraction, warn = _common_valid(arr, nodata, "ICA")
    _require_samples(n_valid, max(8, k + 2), "ICA")
    x = arr[:, common].T                          # (n, k)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        model = FastICA(
            n_components=k, random_state=RS_FIXED_SEED,
            whiten="unit-variance", max_iter=int(max_iter), tol=float(tol),
        )
        sources = model.fit_transform(x)          # (n, k)
    converged = not any(
        issubclass(w.category, ConvergenceWarning) for w in caught)
    n_iter = int(np.asarray(model.n_iter_).max()) if np.ndim(
        model.n_iter_) else int(model.n_iter_)    # type: ignore[arg-type]
    if not converged:
        warn.append(
            f"FastICA 未在 max_iter={int(max_iter)} 内收敛"
            f"（n_iter={n_iter}）——分量非稳定估计，结果慎读（诚实披露，"
            "未静默）")

    mixing = np.asarray(model.mixing_, dtype=float)   # (k, k)
    comp_rasters = [
        _backfill(sources[:, c], common, height, width) for c in range(k)
    ]
    meta: Dict[str, object] = {
        "n_bands": n_bands,
        "band_order": band_names,
        "grid": [int(height), int(width)],
        "n_valid_pixels": n_valid,
        "common_valid_fraction": fraction,
        "whiten": "unit-variance",
        "random_state": RS_FIXED_SEED,
        "max_iter": int(max_iter),
        "tol": float(tol),
        "n_iter": n_iter,
        "converged": bool(converged),
        "disclosure": (
            "FastICA（sklearn）实现；白化=unit-variance；随机固定 "
            "random_state=42；收敛性显式披露（converged/n_iter）——"
            "ICA 分量序与符号不唯一（算法固有），跨运行比较需固定实现版本"),
    }
    return {
        "component_rasters": comp_rasters,
        "mixing_matrix": mixing,
        "mean": np.asarray(model.mean_, dtype=float),
        "n_iter": n_iter,
        "converged": bool(converged),
        "n_valid_pixels": n_valid,
        "common_valid_fraction": fraction,
        "warnings": warn,
        "meta": meta,
    }


# ── 3. SAM（Kruse 1993）───────────────────────────────────────────────

def _as_endmembers(
    endmembers: Union[Mapping[str, Sequence[float]], np.ndarray, Sequence[float]],
    n_bands: int,
) -> Tuple[List[str], np.ndarray]:
    """端元归一为 (m, k) 矩阵 + 名称表（dict 插入序 / 数组序披露）。"""
    if isinstance(endmembers, Mapping):
        names = [str(n) for n in endmembers.keys()]
        rows = [np.asarray(v, dtype=float).ravel() for v in endmembers.values()]
        if not names:
            raise ValueError("endmembers 不能为空")
    else:
        arr = np.asarray(endmembers, dtype=float)
        if arr.ndim == 1:
            arr = arr[None, :]
        if arr.ndim != 2:
            raise ValueError(
                f"endmembers 必须是 (m, k) 数组或 {n_bands} 维向量，"
                f"got ndim={arr.ndim}")
        names = [f"endmember_{i}" for i in range(arr.shape[0])]
        rows = [arr[i] for i in range(arr.shape[0])]
    if not rows:
        raise ValueError("endmembers 不能为空")
    for i, v in enumerate(rows):
        if v.shape != (n_bands,):
            raise ValueError(
                f"endmembers[{names[i]!r}] 长度 {v.shape[0]} != 波段数 "
                f"{n_bands}（端元向量必须与波段栈逐波段对齐）")
    return names, np.stack(rows, axis=0)


def spectral_angle_mapper(
    stack: StackInput,
    endmembers: Union[Mapping[str, Sequence[float]], np.ndarray, Sequence[float]],
    *,
    nodata: Optional[float] = None,
) -> Dict[str, object]:
    """光谱角制图（SAM，Kruse 1993）：逐像元光谱角（弧度）。

    θ(x, e) = arccos( ⟨x, e⟩ / (‖x‖·‖e‖) )；零范数像元（‖x‖=0，含全零
    亮度）→ NaN（诚实披露，不伪造 0 角）；零范数端元 → 该端元角度全
    NaN 并披露，argmin 在其余有限角度上取。输出逐端元角度栅格 + argmin
    类别栅格（仅有效且亮度>0 像元有值）。
    """
    arr, band_names = _as_stack(stack)
    _check_scale(arr, "SAM")
    n_bands, height, width = arr.shape
    names, em = _as_endmembers(endmembers, n_bands)
    common, n_valid, fraction, warn = _common_valid(arr, nodata, "SAM")
    x = arr[:, common].T                          # (n, k)
    x_norm = np.linalg.norm(x, axis=1)
    zero_cell = x_norm <= 0.0

    em_norm = np.linalg.norm(em, axis=1)
    zero_em = em_norm <= 0.0
    if zero_em.any():
        warn.append(
            f"零范数端元 {[_names_at(names, i) for i in np.where(zero_em)[0]]}"
            "——其光谱角无定义（全 NaN），argmin 在其余端元上取（披露）")

    cos = np.empty((n_valid, len(names)), dtype=float)
    for j in range(len(names)):
        if zero_em[j]:
            cos[:, j] = np.nan
            continue
        dot = x @ em[j]
        cos[:, j] = dot / np.where(zero_cell, np.inf, x_norm * em_norm[j])
    cos = np.clip(cos, -1.0, 1.0)
    angles = np.arccos(cos)                       # (n, m) 弧度
    angles[zero_cell] = np.nan

    angle_rasters: Dict[str, np.ndarray] = {}
    for j, name in enumerate(names):
        angle_rasters[name] = _backfill(angles[:, j], common, height, width)

    finite = np.isfinite(angles)
    any_finite = finite.any(axis=1)
    class_vals = np.full(n_valid, np.nan, dtype=float)
    if len(names) > 0:
        masked = np.where(finite, angles, np.inf)
        cls = np.argmin(masked, axis=1).astype(float)
        class_vals[any_finite] = cls[any_finite]
    class_raster = _backfill(class_vals, common, height, width)

    zero_fraction = float(np.sum(zero_cell) / n_valid)
    meta: Dict[str, object] = {
        "n_bands": n_bands,
        "band_order": band_names,
        "endmember_names": names,
        "grid": [int(height), int(width)],
        "n_valid_pixels": n_valid,
        "common_valid_fraction": fraction,
        "zero_norm_fraction": zero_fraction,
        "formula": "θ = arccos(⟨x, e⟩/(‖x‖·‖e‖))（弧度；零范数 → NaN）",
        "reference": "kruse1993",
        "disclosure": (
            "SAM 只度量光谱形状（对亮度增益不变），不区分亮度差异；"
            "端元向量必须与波段序逐波段对齐（band_order 披露）；"
            "零范数像元不产伪 0 角"),
    }
    return {
        "names": names,
        "angles": angle_rasters,
        "class_raster": class_raster,
        "zero_norm_fraction": zero_fraction,
        "n_valid_pixels": n_valid,
        "common_valid_fraction": fraction,
        "warnings": warn,
        "meta": meta,
    }


def _names_at(names: List[str], indices: np.ndarray) -> List[str]:
    return [names[int(i)] for i in np.atleast_1d(indices)]


# ── 4. SID（Chang 2000）───────────────────────────────────────────────

def spectral_information_divergence(
    stack: StackInput,
    endmembers: Union[Mapping[str, Sequence[float]], np.ndarray, Sequence[float]],
    *,
    nodata: Optional[float] = None,
) -> Dict[str, object]:
    """光谱信息散度（SID，Chang 2000，对称形式）。

    p = x/Σx、q = e/Σe（逐像元归一化概率）；
    D(x,e) = Σ p·ln(p/q) + Σ q·ln(q/p)。和 ≤ 0 或出现非正分量 → NaN
    （typed disclosure：信息熵在非正测度上无定义，不伪造）。
    """
    arr, band_names = _as_stack(stack)
    _check_scale(arr, "SID")
    n_bands, height, width = arr.shape
    names, em = _as_endmembers(endmembers, n_bands)
    common, n_valid, fraction, warn = _common_valid(arr, nodata, "SID")
    x = arr[:, common].T                          # (n, k)
    sum_x = x.sum(axis=1)
    cell_ok = np.isfinite(sum_x) & (sum_x > 0)
    # 非正分量（负反射率等）→ p 非正 → 散度无定义
    cell_ok &= (x > 0).all(axis=1)

    p = x / np.where(cell_ok[:, None], sum_x[:, None], 1.0)   # (n, k)
    sum_em = em.sum(axis=1)
    em_ok = sum_em > 0
    if (~em_ok).any():
        warn.append(
            "非正和端元 "
            f"{[names[int(i)] for i in np.where(~em_ok)[0]]}"
            "——其散度全 NaN（typed disclosure，不伪造）")
    q = em / np.where(em_ok, sum_em, 1.0)[:, None]            # (m, k)

    div = np.full((n_valid, len(names)), np.nan, dtype=float)
    for j in range(len(names)):
        if not em_ok[j] or not (q[j] > 0).all():
            continue
        ok = cell_ok
        if not ok.any():
            continue
        pp = p[ok]
        qq = q[j][None, :]
        with np.errstate(divide="ignore", invalid="ignore"):
            t1 = pp * (np.log(pp) - np.log(qq))
            t2 = qq * (np.log(qq) - np.log(pp))
        d = t1.sum(axis=1) + t2.sum(axis=1)
        d = np.where(np.isfinite(d), d, np.nan)
        div[ok, j] = d

    div_rasters: Dict[str, np.ndarray] = {}
    for j, name in enumerate(names):
        div_rasters[name] = _backfill(div[:, j], common, height, width)

    nonpositive_fraction = float(np.sum(~cell_ok) / n_valid)
    meta: Dict[str, object] = {
        "n_bands": n_bands,
        "band_order": band_names,
        "endmember_names": names,
        "grid": [int(height), int(width)],
        "n_valid_pixels": n_valid,
        "common_valid_fraction": fraction,
        "nonpositive_fraction": nonpositive_fraction,
        "formula": "D(x,e) = Σ p·ln(p/q) + Σ q·ln(q/p)，p=x/Σx、q=e/Σe（对称）",
        "reference": "chang2000",
        "disclosure": (
            "SID 为对称信息散度（自动度量与匹配项都计）；像元或端元出现"
            "非正分量/非正和 → NaN（信息熵在非正测度上无定义；typed "
            "disclosure nonpositive_fraction）；要求反射率类正值输入"),
    }
    return {
        "names": names,
        "divergence": div_rasters,
        "nonpositive_fraction": nonpositive_fraction,
        "n_valid_pixels": n_valid,
        "common_valid_fraction": fraction,
        "warnings": warn,
        "meta": meta,
    }


# ── 5. 匹配滤波（Boardman 1995）───────────────────────────────────────

def matched_filter(
    stack: StackInput,
    target_signal: Sequence[float],
    *,
    center: bool = True,
    nodata: Optional[float] = None,
) -> Dict[str, object]:
    """Boardman 匹配滤波：全局协方差白化下的目标投影得分。

    score(x) = tᵀ Σ⁻¹ (x−μ) / (tᵀ Σ⁻¹ t)（center=True，默认；
    center=False 时 μ=0）。目标丰度式得分对纯目标像元 ≈ 1。零方差波段
    从滤波器中剔除并披露（Σ⁻¹ 无定义）；伪逆 pinv 用于数值稳定（披露）。
    """
    arr, band_names = _as_stack(stack)
    _check_scale(arr, "匹配滤波")
    n_bands, height, width = arr.shape
    t = np.asarray(target_signal, dtype=float).ravel()
    if t.shape != (n_bands,):
        raise ValueError(
            f"target_signal 长度 {t.shape[0]} != 波段数 {n_bands}（逐波段对齐）")
    common, n_valid, fraction, warn = _common_valid(arr, nodata, "匹配滤波")
    _require_samples(n_valid, max(8, n_bands + 1), "匹配滤波")
    x = arr[:, common].T                          # (n, k)

    std = x.std(axis=0, ddof=1)
    keep = std > 1e-15
    dropped = [int(i) for i in np.where(~keep)[0]]
    if dropped:
        warn.append(
            f"零方差波段 {dropped} 从滤波器中剔除（Σ⁻¹ 无定义；披露）——"
            "target_signal 对应分量一并忽略")
    if not keep.any():
        raise DegenerateData(
            "所有波段方差为 0——匹配滤波无定义",
            correction_hint="检查输入是否为常量场")
    x_r = x[:, keep]
    t_r = t[keep]
    mu = x_r.mean(axis=0) if center else np.zeros(x_r.shape[1])
    sigma = _cov(x_r - mu) if center else _cov(x_r)
    inv = np.linalg.pinv(sigma)                   # 数值稳定（披露）
    denom = float(t_r @ inv @ t_r)
    if not np.isfinite(denom) or abs(denom) <= 1e-300:
        raise DegenerateData(
            "目标向量在白化空间退化（tᵀΣ⁻¹t≈0）——目标与背景共线",
            correction_hint="更换目标光谱或检查输入波段")
    resid = (x_r - mu[None, :]) if center else x_r
    score_vals = (resid @ (inv @ t_r)) / denom    # (n,)

    score = _backfill(score_vals, common, height, width)
    meta: Dict[str, object] = {
        "n_bands": n_bands,
        "band_order": band_names,
        "grid": [int(height), int(width)],
        "n_valid_pixels": n_valid,
        "common_valid_fraction": fraction,
        "dropped_bands": dropped,
        "center": bool(center),
        "formula": "score(x) = tᵀΣ⁻¹(x−μ)/(tᵀΣ⁻¹t)；μ 由公共有效像元估计",
        "reference": "boardman1995",
        "inverse_backend": "numpy pinv（数值稳定；非迭代解）",
        "disclosure": (
            "全局协方差白化匹配滤波（Boardman 惯用法）；纯目标像元得分≈1、"
            "背景≈0（丰度式解读）；Σ 为全场景协方差——目标与背景统计同"
            "场景假设；零方差波段剔除披露（dropped_bands）"),
    }
    return {
        "score": score,
        "mean": mu,
        "covariance": sigma,
        "dropped_bands": dropped,
        "n_valid_pixels": n_valid,
        "common_valid_fraction": fraction,
        "warnings": warn,
        "meta": meta,
    }


# ── 6. RX 异常检测（Reed & Xiaoli 1990）───────────────────────────────

def rx_anomaly(
    stack: StackInput,
    *,
    regularize: float = 1e-6,
    threshold_sigma: float = 3.0,
    nodata: Optional[float] = None,
) -> Dict[str, object]:
    """Reed-Xiaoli 全局 RX 异常检测：逐像元 Mahalanobis 距离。

    δ(x) = √((x−μ)ᵀ Σ_r⁻¹ (x−μ))，Σ_r = Σ + regularize·(tr Σ/k)·I
    （尺度不变岭正则，披露）。阈值建议 = mean(δ)+threshold_sigma·σ(δ)
    （启发式披露，非假设检验）。全零方差（常量场）→ δ 全 0、无异常、
    零方差事实披露（不伪造 Mahalanobis）。
    """
    if not (np.isfinite(regularize) and regularize >= 0):
        raise ValueError(f"regularize 必须 ≥0，got {regularize!r}")
    if not (np.isfinite(threshold_sigma) and threshold_sigma > 0):
        raise ValueError(f"threshold_sigma 必须 >0，got {threshold_sigma!r}")
    arr, band_names = _as_stack(stack)
    _check_scale(arr, "RX 异常检测")
    n_bands, height, width = arr.shape
    common, n_valid, fraction, warn = _common_valid(arr, nodata, "RX")
    _require_samples(n_valid, max(8, n_bands + 1), "RX")
    x = arr[:, common].T
    mean = x.mean(axis=0)
    resid = x - mean
    sigma = _cov(resid)
    total_var = float(np.trace(sigma))

    if total_var <= 0.0:
        delta_vals = np.zeros(n_valid, dtype=float)
        threshold = 0.0
        sigma_reg = sigma
        zero_variance = True
        warn.append(
            "输入为常量场（全零方差）——Mahalanobis 距离无定义，δ 全 0、"
            "无异常（诚实披露，不伪造统计量）")
    else:
        zero_variance = False
        ridge = float(regularize) * total_var / n_bands
        sigma_reg = sigma + ridge * np.eye(n_bands)
        inv = np.linalg.pinv(sigma_reg)
        d2 = np.einsum("ij,jk,ik->i", resid, inv, resid)
        delta_vals = np.sqrt(np.clip(d2, 0.0, None))
        d_mean = float(delta_vals.mean())
        d_std = float(delta_vals.std(ddof=0))
        threshold = d_mean + float(threshold_sigma) * d_std

    delta = _backfill(delta_vals, common, height, width)
    anomaly = np.isfinite(delta) & (delta > threshold) if not zero_variance \
        else np.zeros_like(delta, dtype=bool)
    anomaly_fraction = float(np.sum(anomaly) / max(1, delta.size))
    meta: Dict[str, object] = {
        "n_bands": n_bands,
        "band_order": band_names,
        "grid": [int(height), int(width)],
        "n_valid_pixels": n_valid,
        "common_valid_fraction": fraction,
        "regularize": float(regularize),
        "ridge_formula": "Σ_r = Σ + regularize·(tr Σ/k)·I（尺度不变岭正则）",
        "threshold": float(threshold),
        "threshold_rule": (
            f"mean(δ)+{float(threshold_sigma)}·σ(δ)（启发式建议值，非假设"
            "检验；σ 为总体标准差 ddof=0）"),
        "zero_variance": bool(zero_variance),
        "anomaly_fraction": anomaly_fraction,
        "reference": "reed1990",
        "disclosure": (
            "全局 RX（单高斯背景假设）——局部 RX / 核 RX 未实现（披露）；"
            "阈值 mean+k·σ 为启发式建议（k 显式参数），非显著性水平；"
            "常量场诚实退化为 δ=0"),
    }
    return {
        "delta": delta,
        "anomaly_mask": anomaly,
        "threshold": float(threshold),
        "anomaly_fraction": anomaly_fraction,
        "zero_variance": bool(zero_variance),
        "n_valid_pixels": n_valid,
        "common_valid_fraction": fraction,
        "warnings": warn,
        "meta": meta,
    }


# ── 7. MAD / IR-MAD 变化检测（Nielsen 1998）───────────────────────────

def _weighted_cca(
    xc: np.ndarray, yc: np.ndarray, w: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """标准化场景间的 CCA（SVD 联合协方差实现；返回升序 ρ 与变体系数）。

    M = Σ11^{-1/2}·Σ12·Σ22^{-1/2}，SVD M = U S Vᵀ；
    a = Σ11^{-1/2}U、b = Σ22^{-1/2}V；ρ 升序排列（noisiest first）。
    """
    if w is None:
        sw = float(xc.shape[0])
        c11 = _cov(xc)
        c22 = _cov(yc)
        c12 = (xc.T @ yc) / max(1, xc.shape[0] - 1)
    else:
        sw = float(w.sum())
        c11 = (xc.T * w) @ xc / max(1.0, sw - 1.0)
        c22 = (yc.T * w) @ yc / max(1.0, sw - 1.0)
        c12 = ((xc.T * w) @ yc) / max(1.0, sw - 1.0)

    def _inv_sqrt(mat: np.ndarray, what: str) -> np.ndarray:
        d_vals, u_vecs = np.linalg.eigh(mat)
        d_max = max(float(d_vals.max()), 1e-300)
        if float(d_vals.min()) <= d_max * 1e-12:
            raise DegenerateData(
                f"{what} 协方差奇异（波段共线或零方差）——MAD 无定义",
                correction_hint="剔除共线/常量波段后重试")
        return (u_vecs / np.sqrt(d_vals)) @ u_vecs.T

    a11 = _inv_sqrt(c11, "T1 场景")
    a22 = _inv_sqrt(c22, "T2 场景")
    m_mat = a11 @ c12 @ a22
    u_s, s_vals, vt = np.linalg.svd(m_mat)
    a_coef = a11 @ u_s                              # (k, k)
    b_coef = a22 @ vt.T                             # (k, k)
    order = np.argsort(s_vals)                      # 升序（noisiest first）
    return a_coef[:, order], b_coef[:, order], s_vals[order]


def _standardize(x: np.ndarray, w: Optional[np.ndarray] = None) -> np.ndarray:
    if w is None:
        mean = x.mean(axis=0)
        std = x.std(axis=0, ddof=1)
    else:
        sw = w.sum()
        mean = (x * w[:, None]).sum(axis=0) / sw
        var = ((x - mean) ** 2 * w[:, None]).sum(axis=0) / max(1.0, sw - 1.0)
        std = np.sqrt(np.clip(var, 0.0, None))
    if (std <= 1e-15).any():
        raise DegenerateData(
            f"波段 {np.where(std <= 1e-15)[0].tolist()} 方差为 0——"
            "标准化无定义（MAD 要求两期各自非零方差）",
            correction_hint="剔除常量波段")
    return (x - mean) / std


def mad_change(
    stack_a: StackInput,
    stack_b: StackInput,
    n_iterms: int = 0,
    *,
    nodata_a: Optional[float] = None,
    nodata_b: Optional[float] = None,
) -> Dict[str, object]:
    """多变量变化检测 MAD / IR-MAD（Nielsen 1998）。

    两期栈各自标准化 → SVD-CCA → MAD 变分量 MAD_i = a_i·X − b_i·Y
    （按规范相关 ρ 升序——noisiest first，Nielsen 约定）。变分量方差
    （理论值 2(1−ρ)，随实测经验方差一并在输出披露）。χ² 栅格 =
    Σ_i MAD_i²/Var_i，自由度 k=n_bands（k 个标准化变分量，每个方差
    2(1−ρ_i) → 每分量 1 自由度；Nielsen 1998 / Canty IR-MAD 惯例
    χ²_k）；ρ 钳制 ≤1−1e-12（防恒等场景 0/0）。

    ``n_iterms`` > 0 → IR-MAD 迭代重加权：w = 1/χ²（均值归一、下限
    1e-4，披露），加权均值/协方差重做 CCA；迭代上限 10，报告收敛增量
    （max|Δw|，<1e-6 视为收敛）。完整 IR-MAD 为固定点迭代（披露）。
    """
    n_it = int(n_iterms)
    if n_it < 0 or n_it > 10:
        raise ValueError(
            f"n_iterms 必须在 [0, 10] 内（IR-MAD 固定点迭代上限 10），got {n_it!r}")
    arr_a, names_a = _as_stack(stack_a)
    arr_b, names_b = _as_stack(stack_b)
    if arr_a.shape != arr_b.shape:
        raise ValueError(
            f"两期栈形状不一致：A={arr_a.shape} vs B={arr_b.shape}"
            "（需同波段数同网格，先对齐）")
    _check_scale(arr_a, "MAD 变化检测（两期合计 ×2）")
    n_bands, height, width = arr_a.shape
    common_a, _, frac_a, warn = _common_valid(arr_a, nodata_a, "MAD(T1)")
    common_b, _, frac_b, warn2 = _common_valid(arr_b, nodata_b, "MAD(T2)")
    warn.extend(warn2)
    common = common_a & common_b
    n_valid = int(common.sum())
    fraction = n_valid / max(1, common.size)
    if n_valid == 0:
        raise NoValidObservations(
            "两期无公共有效像元——MAD 需要≥1 个双期全波段有效像元",
            correction_hint="检查两期 nodata/对齐")
    if fraction < _COMMON_VALID_WARN_FRACTION:
        warn.append(
            f"common_valid_fraction={fraction:.3f} < 0.5——双期公共有效像元"
            "占比过低（结果慎读）")
    _require_samples(n_valid, max(8, n_bands + 1), "MAD")

    xa = arr_a[:, common].T
    xb = arr_b[:, common].T
    w: Optional[np.ndarray] = None
    rho = np.zeros(n_bands)
    mads = np.zeros((n_valid, n_bands))
    iterations_ran = 0
    delta: Optional[float] = None
    converged: Optional[bool] = None

    for it in range(n_it + 1):
        zs_a = _standardize(xa, w)
        zs_b = _standardize(xb, w)
        a_coef, b_coef, rho = _weighted_cca(zs_a, zs_b, w)
        mads = zs_a @ a_coef - zs_b @ b_coef       # (n, k)，升序 ρ
        iterations_ran = it
        if it == n_it:
            break
        # IR-MAD 重加权（固定点迭代；权重下限防单像元爆炸，披露）
        rho_c = np.minimum(rho, 1.0 - _MAD_RHO_CLAMP)
        var_theo_it = 2.0 * (1.0 - rho_c)
        chi2_it = np.sum(mads ** 2 / var_theo_it[None, :], axis=1)
        w_new = 1.0 / np.clip(chi2_it, 1e-10, None)
        w_new = w_new / w_new.mean()
        w_new = np.clip(w_new, _IRMAD_WEIGHT_FLOOR, None)
        delta = float(np.max(np.abs(w_new - w))) if w is not None else float("inf")
        w = w_new
        iterations_ran = it + 1
        converged = delta < _IRMAD_CONVERGED_DELTA
        if converged:
            break
    if converged is None:
        converged = True                            # n_iterms=0：无迭代，视为收敛

    rho_clamped = np.minimum(rho, 1.0 - _MAD_RHO_CLAMP)
    var_theo = 2.0 * (1.0 - rho_clamped)
    chi2_vals = np.sum(mads ** 2 / var_theo[None, :], axis=1)
    var_emp = mads.var(axis=0, ddof=1)

    mad_rasters = [
        _backfill(mads[:, c], common, height, width) for c in range(n_bands)
    ]
    chi2_raster = _backfill(chi2_vals, common, height, width)
    weights_raster = (
        _backfill(w, common, height, width) if w is not None else None)

    meta: Dict[str, object] = {
        "n_bands": n_bands,
        "band_order": names_a,
        "band_order_b": names_b,
        "grid": [int(height), int(width)],
        "n_valid_pixels": n_valid,
        "common_valid_fraction": fraction,
        "n_iterms_requested": n_it,
        "iterations_ran": int(iterations_ran),
        "convergence_delta": delta,
        "converged": converged,
        "chi2_dof": n_bands,
        "variate_variance_theory": "Var(MAD_i) = 2(1−ρ_i)（标准化场景）",
        "chi2_dof_derivation": (
            "χ² 自由度 = k = n_bands：k 个标准化变分量每个方差 2(1−ρ_i)，"
            "标准化后每分量贡献 1 个自由度（Nielsen 1998 / Canty IR-MAD "
            "惯例 χ²_k）；历史实现曾用 2k（本版修正）"),
        "rho_clamp": _MAD_RHO_CLAMP,
        "weight_floor": _IRMAD_WEIGHT_FLOOR if w is not None else None,
        "disclosure": (
            "MAD 按 Nielsen (1998)：SVD-CCA + 变分量按 ρ 升序（noisiest "
            "first）；χ² 栅格自由度 = k（标准化变分量方差 2(1−ρ) → 每分量 "
            "1 dof，Nielsen/Canty χ²_k 惯例）；ρ 钳制 ≤1−1e-12（恒等"
            "场景 χ²≈0 而非 0/0）；IR-MAD 为固定点重加权迭代（w=1/χ²，"
            "均值归一 + 下限 1e-4）——完整学术实现含 no-change 概率优化，"
            "未在本迭代实现（披露）"),
    }
    return {
        "mad_rasters": mad_rasters,
        "canonical_correlations": rho.tolist(),
        "variate_variances": var_emp.tolist(),
        "variate_variances_theoretical": var_theo.tolist(),
        "chi2_raster": chi2_raster,
        "weights_raster": weights_raster,
        "iterations_ran": int(iterations_ran),
        "convergence_delta": delta,
        "converged": converged,
        "n_valid_pixels": n_valid,
        "common_valid_fraction": fraction,
        "warnings": warn,
        "meta": meta,
    }


# ── 8. k-means 分割基座（Lloyd 1982；非 SLIC）─────────────────────────

def segment_image(
    stack: StackInput,
    n_segments: int = 50,
    *,
    spatial_weight: float = 0.5,
    compactness: float = 0.5,
    nodata: Optional[float] = None,
) -> Dict[str, object]:
    """图像分割基座：标准化光谱特征 + 加权归一化坐标特征的 k-means。

    特征 = [逐波段 z-score（常量波段剔除并披露）] + [x_norm, y_norm] ·
    spatial_weight·compactness。KMeans(random_state=42, n_init=10)
    （Lloyd 1982 惯用法；确定性）。

    **诚实边界（进 meta/描述符）**：flat-color k-means 基座，**不是**
    SLIC 超像素——compactness 只作为空间特征权重乘子，无几何紧致约束、
    无 watershed 精化。``n_segments`` > 有效像元数时钳制到像元数并披露
    （realized < requested）。
    """
    from sklearn.cluster import KMeans

    for name, val in (("spatial_weight", spatial_weight),
                      ("compactness", compactness)):
        if not (np.isfinite(val) and 0.0 <= val <= 1.0):
            raise ValueError(f"{name} 必须在 [0, 1] 内，got {val!r}")
    if int(n_segments) < 2:
        raise ValueError(f"n_segments 必须 ≥2，got {n_segments!r}")
    arr, band_names = _as_stack(stack)
    _check_scale(arr, "图像分割")
    n_bands, height, width = arr.shape
    common, n_valid, fraction, warn = _common_valid(arr, nodata, "图像分割")
    x = arr[:, common].T                          # (n, k)

    std = x.std(axis=0, ddof=1)
    spectral_keep = std > 1e-15
    dropped = [int(i) for i in np.where(~spectral_keep)[0]]
    if dropped:
        warn.append(
            f"常量波段 {dropped} 未进入分割特征（零方差；披露）")
    features_spectral: List[np.ndarray] = []
    if spectral_keep.any():
        xs = x[:, spectral_keep]
        features_spectral.append(
            (xs - xs.mean(axis=0)) / std[spectral_keep][None, :])
    if not features_spectral:
        warn.append("无可用光谱特征（全部常量）——仅按空间坐标分割（披露）")

    rows, cols = np.nonzero(common)
    x_norm = (cols + 0.5) / width
    y_norm = (rows + 0.5) / height
    spatial_scale = float(spatial_weight) * float(compactness)
    features = np.hstack([
        *features_spectral,
        (x_norm * spatial_scale)[:, None],
        (y_norm * spatial_scale)[:, None],
    ])

    realized = min(int(n_segments), n_valid)
    if realized < int(n_segments):
        warn.append(
            f"n_segments={int(n_segments)} > 有效像元数 {n_valid}——钳制为 "
            f"{realized}（披露 realized < requested）")
    km = KMeans(
        n_clusters=realized, random_state=RS_FIXED_SEED, n_init=10,
    )
    labels_flat = km.fit_predict(features).astype(float)

    label_raster = np.full(height * width, np.nan, dtype=float)
    label_raster[common.ravel()] = labels_flat
    label_raster = label_raster.reshape(height, width)

    spectra: List[List[float]] = []
    for c in range(realized):
        mask = labels_flat == c
        if mask.any():
            spectra.append(x[mask].mean(axis=0).round(8).tolist())
        else:                                     # 空簇（理论不出现，诚实兜底）
            spectra.append([float("nan")] * n_bands)

    meta: Dict[str, object] = {
        "n_bands": n_bands,
        "band_order": band_names,
        "grid": [int(height), int(width)],
        "n_valid_pixels": n_valid,
        "common_valid_fraction": fraction,
        "n_segments_requested": int(n_segments),
        "n_segments_realized": int(realized),
        "spatial_weight": float(spatial_weight),
        "compactness": float(compactness),
        "spatial_feature_scale": spatial_scale,
        "dropped_constant_bands": dropped,
        "random_state": RS_FIXED_SEED,
        "n_init": 10,
        "disclosure": (
            "flat-color k-means 分割基座（Lloyd 1982；sklearn，"
            "random_state=42 + n_init=10 确定性）——不是 SLIC 超像素："
            "compactness 仅作为空间特征权重乘子（无几何紧致约束），无 "
            "watershed 精化（诚实边界）；段数按有效像元钳制并披露"),
    }
    return {
        "label_raster": label_raster,
        "segment_mean_spectra": spectra,
        "n_segments_realized": int(realized),
        "n_valid_pixels": n_valid,
        "common_valid_fraction": fraction,
        "warnings": warn,
        "meta": meta,
    }


# ── 9. VCA 端元提取（Nascimento & Dias 2005；EXPERIMENTAL）────────────

def _unfound_mask(n: int, found_idx: List[int]) -> np.ndarray:
    """True = 尚未选中的像元（末顶点带符号极值搜索用）。"""
    mask = np.ones(n, dtype=bool)
    mask[np.asarray(found_idx, dtype=int)] = False
    return mask


def extract_endmembers_vca(
    stack: StackInput,
    n_endmembers: int,
    *,
    seed: int = RS_FIXED_SEED,
    nodata: Optional[float] = None,
) -> Dict[str, object]:
    """顶点成分分析 VCA 端元提取（EXPERIMENTAL）。

    SVD 降维（均值正交补空间取前 m−1 维）+ 随机投影逐顶点选择（rng =
    ``default_rng(seed)``）：每步取随机方向、投影掉已找到端元方向、
    |投影| 最大的**未选**像元为下一端元；末顶点用已选顶点仿射包法向。
    纯像元假设下恢复端元光谱 = 原像元光谱（EXPERIMENTAL：简化确定性
    变体，非论文完整实现——披露）。守卫：n_endmembers < n_bands。
    """
    m = int(n_endmembers)
    arr, band_names = _as_stack(stack)
    _check_scale(arr, "VCA 端元提取")
    n_bands, height, width = arr.shape
    if m < 2:
        raise ValueError(
            f"n_endmembers 必须 ≥2（单端元无单纯形），got {m!r}")
    if m >= n_bands:
        raise ValueError(
            f"n_endmembers={m} 必须 < n_bands={n_bands}"
            "（VCA 降维到 m−1 维的守卫；EXPERIMENTAL 实现约定）")
    common, n_valid, fraction, warn = _common_valid(arr, nodata, "VCA")
    _require_samples(n_valid, max(8, m + 1), "VCA")
    x = arr[:, common].T                          # (n, k)
    rng = np.random.default_rng(int(seed))

    mean = x.mean(axis=0)
    mu_norm = float(np.linalg.norm(mean))
    if mu_norm <= 0.0:
        raise DegenerateData(
            "数据均值为零向量——VCA 均值正交补投影无定义",
            correction_hint="检查输入是否全零/对称抵消")
    u_mu = mean / mu_norm
    r = x - (x @ u_mu)[:, None] * u_mu[None, :]   # 均值正交补（n, k）
    p = m - 1
    _, _, vt = np.linalg.svd(r, full_matrices=False)
    v = vt[:p].T                                  # (k, p)
    y = r @ v                                     # (n, p)

    found_idx: List[int] = []
    found_basis = np.zeros((p, 0), dtype=float)   # 已选端元方向的规范正交基
    for step in range(m):
        if found_basis.shape[1] < p:
            a = rng.standard_normal(p)
            if found_basis.shape[1]:
                a = a - found_basis @ (found_basis.T @ a)
            norm_a = float(np.linalg.norm(a))
            if norm_a <= 1e-12:
                a = rng.standard_normal(p)        # 极小概率退化：重抽（确定性序列）
                if found_basis.shape[1]:
                    a = a - found_basis @ (found_basis.T @ a)
                norm_a = float(np.linalg.norm(a))
            w_dir = a / max(norm_a, 1e-300)
        else:
            # 末顶点：已选顶点仿射包的法向（p 个顶点张成 p−1 维仿射包；
            # 法向 = m_mat 零空间 = 最小奇异值对应的左奇异向量）
            m_mat = found_basis[:, 1:] - found_basis[:, :1]
            if m_mat.shape[1] == 0:
                w_dir = np.ones(1)                # p=1：唯一轴（另一极端）
            else:
                u_mat, _, _ = np.linalg.svd(m_mat, full_matrices=True)
                w_dir = u_mat[:, -1]
        candidates = np.abs(y @ w_dir)
        if found_basis.shape[1] >= p and p >= 1:
            # 末顶点（法向分支）：单纯形顶点必在已选顶点仿射包法向的
            # 一侧（凸性——混合像元投影介于两者之间）。比较带符号两端
            # 极值到「已选顶点投影值」的距离，取更远一侧（精确，不取巧）。
            signed = y @ w_dir
            found_proj = float(np.mean(signed[np.asarray(found_idx, dtype=int)]))
            hi = int(np.argmax(np.where(_unfound_mask(n_valid, found_idx), signed, -np.inf)))
            lo = int(np.argmin(np.where(_unfound_mask(n_valid, found_idx), signed, np.inf)))
            d_hi = float(signed[hi] - found_proj)
            d_lo = float(found_proj - signed[lo])
            idx = hi if d_hi >= d_lo else lo
        else:
            # 随机方向步：|投影| 极值必为顶点（线性函数在单纯形顶点取极值；
            # 凸性保证混合像元不会超过端点）
            candidates = candidates.copy()
            if found_idx:
                candidates[np.asarray(found_idx, dtype=int)] = -1.0
            idx = int(np.argmax(candidates))
        found_idx.append(idx)
        vec = y[idx]
        vec_norm = float(np.linalg.norm(vec))
        if vec_norm > 1e-12:
            u_new = vec / vec_norm
            # 规范正交化进基（Gram-Schmidt 一列）
            basis = found_basis
            proj = u_new - basis @ (basis.T @ u_new) if basis.shape[1] else u_new
            proj_norm = float(np.linalg.norm(proj))
            if proj_norm > 1e-12:
                found_basis = np.hstack([basis, (proj / proj_norm)[:, None]])

    endmembers = x[found_idx, :]                  # 纯像元假设：原光谱即端元
    rows, cols = np.nonzero(common)
    locations = [(int(rows[i]), int(cols[i])) for i in found_idx]
    meta: Dict[str, object] = {
        "n_bands": n_bands,
        "band_order": band_names,
        "n_endmembers": m,
        "reduced_dim": p,
        "seed": int(seed),
        "n_valid_pixels": n_valid,
        "common_valid_fraction": fraction,
        "scientific_status": "EXPERIMENTAL",
        "disclosure": (
            "VCA（Nascimento & Dias 2005）的简化确定性变体：SVD 降维 + "
            "随机投影逐顶点选择 + 末顶点仿射包法向（非论文完整实现）；"
            "纯像元假设——恢复端元=原始像元光谱；随机固定 seed=42；"
            "EXPERIMENTAL：结果需人工核验后使用"),
    }
    return {
        "endmembers": endmembers,
        "locations": locations,
        "valid_indices": found_idx,
        "n_valid_pixels": n_valid,
        "common_valid_fraction": fraction,
        "warnings": warn,
        "meta": meta,
    }


# ── 9b. FCLS 线性光谱解混（Heinz & Chang 2001）────────────────────────

# δ 增广行权重（和一约束的罚强度；在按端元列范数归一的系统中取值，
# 端元谱 O(1) 时残差 O(1) → 和一违背 ~O(1/δ)，边界像元经 scipy NNLS
# 求解。值为实现常数、进 meta 披露）。
_FCLS_SUM_TO_ONE_WEIGHT = 1e6
# 闭合式解的数值负容忍（|x_i| ≤ tol 视为 0，不触发逐像元 NNLS 回退）。
_FCLS_NEG_TOL = 1e-12


def _validate_endmember_matrix(E: np.ndarray, n_bands: int) -> np.ndarray:
    """端元矩阵校验：2D (k, m)、波段数一致、有限、列满秩（秩亏拒绝）。"""
    E = np.asarray(E, dtype=float)
    if E.ndim != 2:
        raise ValueError(
            f"endmembers 必须是 2D 矩阵 [n_bands][n_endmembers]，got ndim={E.ndim}")
    k, m = E.shape
    if k != n_bands:
        raise ValueError(
            f"endmembers 波段维 {k} 与栈波段数 {n_bands} 不一致"
            "（端元光谱必须逐波段对齐）")
    if m < 2:
        raise ValueError(f"endmembers 必须 ≥2 个端元（单端元无混合），got {m!r}")
    if not np.isfinite(E).all():
        raise ValueError("endmembers 含 NaN/Inf——端元光谱必须有限")
    norms = np.linalg.norm(E, axis=0)
    if (norms <= 1e-12).any():
        raise DegenerateData(
            f"端元 {np.where(norms <= 1e-12)[0].tolist()} 为零向量——解混无定义",
            correction_hint="检查端元光谱提取是否失败")
    # 列满秩：秩亏（含 m > k 的欠定）→ EᵀE 奇异，FCLS 无良定义唯一解
    rank = int(np.linalg.matrix_rank(E))
    if rank < m:
        raise DegenerateData(
            f"端元矩阵秩亏（rank={rank} < m={m}，共线或端元数>波段数）——"
            "FCLS 约束解不唯一",
            correction_hint="剔除共线端元或减少端元数（m ≤ 波段数）")
    return E


# review R2-4：边界像元逐像元 NNLS 的耗时面预算（超过仅披露，不拒绝
# —— 闭式解主路径不受影响，预算用于警示批处理时长）。
_FCLS_NNLS_BUDGET = 200_000


def fcls_unmix(
    stack: StackInput,
    endmembers: StackInput,
    *,
    sum_to_one_weight: float = _FCLS_SUM_TO_ONE_WEIGHT,
    nodata: Optional[float] = None,
) -> Dict[str, object]:
    """全约束最小二乘线性光谱解混 FCLS（Heinz & Chang 2001）。

    逐像元求解 ``min ‖E·x − f‖²  s.t.  x ≥ 0, Σx = 1``，E 为 k 波段 ×
    m 端元矩阵。输出 m 个丰度面（值域 [0,1]）+ 逐像元 RMS 残差面
    ``‖E·x − f‖₂/√k``（波段均方根，不确定性摘要——field_uncertainty）。

    实现（与 Heinz & Chang 2001 的增广最小二乘框架一致）：

    - 全体像元先做向量化**和一约束闭式解**（Lagrange 乘子法，论文
      eq.14–16 路线）：x_c = R⁻¹(Eᵀf − λ1)，λ=(1ᵀR⁻¹Eᵀf − 1)/(1ᵀR⁻¹1)，
      R=EᵀE；解在单纯形内部时即精确约束最优（KKT 成立）；
    - 出现负分量的边界像元逐像元回退 **δ-增广 NNLS**（[E; δ·1ᵀ]x =
      [f; δ]，δ=``sum_to_one_weight``（默认 1e6）、按端元列范数归一的
      尺度下）：scipy NNLS 给出增广问题的精确非负解，δ→∞ 收敛到真
      FCLS（和一违背 ~O(1/δ)，披露）；
    - 端元矩阵列满秩守卫（秩亏/端元数>波段数 → DegenerateData）；
    - 无随机成分（deterministic）；公共有效掩膜 + NaN 回填同本模块约定。
    """
    E = np.asarray(endmembers, dtype=float)
    arr, band_names = _as_stack(stack)
    _check_scale(arr, "FCLS 线性光谱解混")
    n_bands, height, width = arr.shape
    E = _validate_endmember_matrix(E, n_bands)
    k, m = E.shape
    delta = float(sum_to_one_weight)
    if not (np.isfinite(delta) and delta >= 1.0):
        raise ValueError(
            f"sum_to_one_weight（δ 增广权重）必须为 ≥1 的有限数，got {delta!r}")

    common, n_valid, fraction, warn = _common_valid(arr, nodata, "FCLS 解混")
    # 逐像元独立 LS：无全局统计估计，样本下限仅要求 ≥1 个有效场像元
    # （本模块统一走 InsufficientSamples 语义，取 2 作最低场规模）。
    _require_samples(n_valid, 2, "FCLS 解混")
    f_pixels = arr[:, common].T                   # (n, k)

    # 端元尺度归一（整体缩放不改变丰度解；δ 在该尺度下固定）。
    col_norms = np.linalg.norm(E, axis=0)
    scale = float(col_norms.max())
    En = E / scale
    fn = f_pixels / scale

    # ── 阶段 A：向量化和一约束闭式解（Lagrange；单纯形内部 = 精确）──
    r_mat = En.T @ En                             # (m, m)
    r_inv = np.linalg.pinv(r_mat)
    u_vec = r_inv @ np.ones(m)                    # R⁻¹1
    denom = float(np.ones(m) @ u_vec)             # 1ᵀR⁻¹1（满秩 > 0）
    x_ls = fn @ En @ r_inv                        # (n, m) 无约束 LS（R⁻¹Eᵀfᵀ）
    lag = (np.ones(m) @ x_ls.T - 1.0) / denom     # (n,) λ（论文 eq.15-16）
    x_con = x_ls - lag[:, None] * u_vec[None, :]
    interior = x_con.min(axis=1) >= -_FCLS_NEG_TOL
    x_con[interior[:, None] & (x_con < 0.0)] = 0.0   # 数值尘埃（|x|≤1e-12）归零

    # ── 阶段 B：边界像元 δ-增广 NNLS（scipy 精确非负解，披露路径）──
    n_boundary = int((~interior).sum())
    x_all = x_con
    if n_boundary:
        from scipy.optimize import nnls as _nnls

        e_aug = np.vstack([En, np.full((1, m), delta)])
        x_bnd = np.empty((n_boundary, m), dtype=float)
        for i, f_row in enumerate(fn[~interior]):
            b_aug = np.append(f_row, delta)
            x_bnd[i], _ = _nnls(e_aug, b_aug)
        x_all[~interior] = x_bnd

    # 逐像元 RMS 残差（原始端元尺度；field_uncertainty 摘要）。
    resid = f_pixels - x_all @ E.T
    rms_vals = np.sqrt(np.mean(resid ** 2, axis=1))

    abundances = [
        _backfill(x_all[:, c], common, height, width) for c in range(m)
    ]
    rms_raster = _backfill(rms_vals, common, height, width)

    meta: Dict[str, object] = {
        "n_bands": n_bands,
        "band_order": band_names,
        "n_endmembers": m,
        "n_valid_pixels": n_valid,
        "common_valid_fraction": fraction,
        "n_boundary_pixels_nnls": n_boundary,
        # review R2-4：边界像元走逐像元 NNLS（Python 层），给出耗时面
        # 预算口径 —— 超预算时披露（不静默）。
        "nnls_budget_pixels": _FCLS_NNLS_BUDGET,
        "nnls_budget_exceeded": bool(n_boundary > _FCLS_NNLS_BUDGET),
        "sum_to_one_weight": delta,
        "method": "fcls",
        "disclosure": (
            "FCLS（Heinz & Chang 2001）：min‖Ex−f‖² s.t. x≥0, Σx=1；"
            "单纯形内部像元走和一约束闭式解（精确），"
            f"{n_boundary} 个边界像元走 δ-增广 NNLS（δ={delta:g} 归一尺度，"
            "和一违背 ~O(1/δ)）；丰度值域 [0,1]，RMS 残差为波段均方根"
            "（重建不确定性摘要）；端元矩阵列满秩守卫"),
    }
    return {
        "abundances": abundances,
        "rms_residual": rms_raster,
        "n_valid_pixels": n_valid,
        "common_valid_fraction": fraction,
        "warnings": warn,
        "meta": meta,
    }


# ── 10. 波段×波段相关表（公共有效掩膜，披露）──────────────────────────

def band_correlation_table(
    stack: StackInput,
    *,
    standardize: bool = False,
    nodata: Optional[float] = None,
) -> Dict[str, object]:
    """波段×波段 Pearson 相关矩阵 + 逐对样本数（stats_table 形 dict of lists）。

    **公共有效掩膜**（任一波段无效 → 整行剔除；非 pairwise-complete，
    披露）。Pearson 相关对线性变换不变——``standardize=True`` 只改变
    内部表征、不改变 r（诚实披露，不是隐藏的第二个结果）。零方差波段
    行/列 → NaN（不伪造）。
    """
    arr, band_names = _as_stack(stack)
    _check_scale(arr, "波段相关表")
    n_bands, _, _ = arr.shape
    if n_bands < 2:
        raise ValueError(
            f"波段相关表需要 ≥2 个波段，got {n_bands}")
    common, n_valid, fraction, warn = _common_valid(arr, nodata, "波段相关表")
    _require_samples(n_valid, 3, "波段相关表")
    x = arr[:, common].T
    if standardize:
        std = x.std(axis=0, ddof=1)
        safe = np.where(std > 1e-15, std, 1.0)
        x = (x - x.mean(axis=0)) / safe
        warn.append(
            "standardize=True：Pearson 相关对线性变换不变，相关值与 "
            "standardize=False 相同（披露，非两个结果）")
    cov = _cov(x)
    d = np.sqrt(np.clip(np.diag(cov), 0.0, None))
    zero_var = d <= 1e-15
    if zero_var.any():
        warn.append(
            f"零方差波段 {np.where(zero_var)[0].tolist()}——相关未定义 "
            "（NaN，不伪造）")
    denom = np.where(zero_var[:, None] | zero_var[None, :], np.nan,
                     d[:, None] * d[None, :])
    corr = cov / denom
    n_matrix = np.full((n_bands, n_bands), n_valid, dtype=int)
    n_matrix[zero_var, :] = 0
    n_matrix[:, zero_var] = 0
    meta: Dict[str, object] = {
        "n_bands": n_bands,
        "band_order": band_names,
        "n_valid_pixels": n_valid,
        "common_valid_fraction": fraction,
        "pairing": "common_valid（任一波段无效 → 整像元剔除；非 pairwise-complete）",
        "coefficient": "pearson（ddof=1 协方差）",
        "standardize": bool(standardize),
        "zero_variance_bands": [int(i) for i in np.where(zero_var)[0]],
        "disclosure": (
            "逐对样本数恒等于公共有效像元数（公共掩膜约定，非逐对独立"
            "计数）；Pearson 线性相关不捕获非线性关联"),
    }
    return {
        "columns": list(band_names),
        "rows": list(band_names),
        "correlation": corr.tolist(),
        "n_observations": n_matrix.tolist(),
        "n_valid_pixels": n_valid,
        "common_valid_fraction": fraction,
        "warnings": warn,
        "meta": meta,
    }


# ── 11. 时序特征（线性去趋势 + 单周期谐波，披露）──────────────────────

def temporal_features(
    stack: StackInput,
    *,
    nodata: Optional[float] = None,
) -> Dict[str, object]:
    """逐像元时序特征（栈第 0 轴 = 时间序）。

    min/max/mean/std（总体 ddof=0，nan-aware）、amplitude = max−min、
    first_last_diff = first − last（负值=上升趋势；首尾任一无效 → NaN）、
    谐波：完整序列联合 LS 拟合 [1, t, sin(2πt), cos(2πt)]（t 归一 [0,1]，
    单周期=栈跨度；线性趋势与谐波联合估计——分步去趋势会泄漏），输出
    harmonic_amplitude/phase。

    **诚实边界**：无物候模型拟合（无双谐波、无 Savitzky-Golay、无
    物候期提取）——超出线性趋势 + 单周期谐波的部分不做（披露）。
    谐波要求全序列有效且设计矩阵满秩（T≥4），否则 NaN。
    """
    arr, band_names = _as_stack(stack)
    _check_scale(arr, "时序特征")
    n_t, height, width = arr.shape
    if n_t < 2:
        raise ValueError(
            f"时序特征需要 ≥2 个时间切片，got {n_t}")
    arr = np.asarray(arr, dtype=float)
    invalid = ~np.isfinite(arr)
    if nodata is not None:
        invalid |= arr == float(nodata)
    values = np.where(invalid, np.nan, arr)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        v_min = np.nanmin(values, axis=0)
        v_max = np.nanmax(values, axis=0)
        v_mean = np.nanmean(values, axis=0)
        v_std = np.nanstd(values, axis=0, ddof=0)
    n_finite = (~invalid).sum(axis=0)              # 每像元有限切片数
    no_data = n_finite == 0

    amplitude = v_max - v_min
    first = values[0]
    last = values[-1]
    first_last = first - last
    complete = n_finite == n_t

    harmonic_amp = np.full((height, width), np.nan, dtype=float)
    harmonic_phase = np.full((height, width), np.nan, dtype=float)
    harmonic_note = "T<4 → 谐波不做（NaN）"
    if n_t >= 4:
        t_grid = np.arange(n_t, dtype=float) / float(n_t - 1)
        # 线性趋势 + 单周期谐波联合 LS（分步去趋势会让趋势泄漏进谐波幅值）
        design = np.stack([
            np.ones(n_t), t_grid,
            np.sin(2.0 * np.pi * t_grid), np.cos(2.0 * np.pi * t_grid),
        ], axis=1)                                          # (T, 4)
        if np.linalg.matrix_rank(design) == 4:
            flat = values.reshape(n_t, -1)
            ok = complete.reshape(-1)
            if ok.any():
                series = flat[:, ok]                        # (T, n_ok)
                coef, *_ = np.linalg.lstsq(design, series, rcond=None)  # (4, n)
                amp = np.hypot(coef[2], coef[3])
                phase = np.arctan2(coef[3], coef[2])
                harmonic_amp.reshape(-1)[ok] = amp
                harmonic_phase.reshape(-1)[ok] = phase
            harmonic_note = (
                "谐波要求完整序列（任一切片无效 → NaN）与满秩设计")
        else:
            harmonic_note = "设计矩阵秩亏 → 谐波 NaN"

    v_min = np.where(no_data, np.nan, v_min)
    v_max = np.where(no_data, np.nan, v_max)
    v_mean = np.where(no_data, np.nan, v_mean)
    v_std = np.where(no_data, np.nan, v_std)
    amplitude = np.where(no_data, np.nan, amplitude)

    features: Dict[str, np.ndarray] = {
        "min": v_min, "max": v_max, "mean": v_mean, "std": v_std,
        "amplitude": amplitude, "first_last_diff": first_last,
        "harmonic_amplitude": harmonic_amp, "harmonic_phase": harmonic_phase,
    }
    meta: Dict[str, object] = {
        "time_slices": int(n_t),
        "grid": [int(height), int(width)],
        "band_order": band_names,
        "std_ddof": 0,
        "first_last_definition": "first − last（负值 = 时间上升趋势）",
        "harmonic_definition": (
            "联合 LS 拟合 [1, t, sin(2πt), cos(2πt)]（t 归一 [0,1]，单周期"
            " = 栈跨度；趋势与谐波联合估计）；amplitude=√(c²+d²)、"
            "phase=atan2(d,c)"),
        "harmonic_note": harmonic_note,
        "disclosure": (
            "无物候模型拟合（无双谐波/Savitzky-Golay/物候期提取）——"
            "仅线性去趋势 + 单周期谐波（诚实边界）；std 为总体标准差"
            "（ddof=0）；min/max/mean 对有限切片 nan-aware"),
    }
    return {
        "features": features,
        "complete_series_fraction": float(np.sum(complete) / complete.size),
        "meta": meta,
    }


# ── 12. 稳健归一化（2-98 分位拉伸 / 参考场景匹配）─────────────────────

def robust_normalize(
    stack: StackInput,
    *,
    method: str = "percentile_match",
    reference: Optional[StackInput] = None,
    lower_percentile: float = 2.0,
    upper_percentile: float = 98.0,
    nodata: Optional[float] = None,
) -> Dict[str, object]:
    """稳健跨波段/跨场景归一化（NaN-aware 分位；披露逐波段所用分位）。

    - ``percentile_stretch``：逐波段 [lo, hi] 分位线性拉伸到 [0, 1]（钳端点）；
    - ``percentile_match``：源分位拉伸后重缩放到**参考栈**同序波段的
      [lo, hi] 分位区间（reference 必需；波段数必须一致，网格可不同）。

    线性不变性：B = a·A + b 经 match(ref=A) 与 A 自匹配结果逐位一致
    （测试锁定）。常量波段（hi−lo≈0）→ DegenerateData（诚实拒绝）。
    """
    if method not in ("percentile_stretch", "percentile_match"):
        raise ValueError(
            f"method 必须是 'percentile_stretch'/'percentile_match'，got {method!r}")
    lo_q, hi_q = float(lower_percentile), float(upper_percentile)
    if not (np.isfinite(lo_q) and np.isfinite(hi_q)
            and 0.0 <= lo_q < hi_q <= 100.0):
        raise ValueError(
            f"分位需 0 ≤ lower < upper ≤ 100，got ({lo_q}, {hi_q})")
    arr, band_names = _as_stack(stack)
    _check_scale(arr, "稳健归一化")
    n_bands, height, width = arr.shape
    invalid = ~np.isfinite(arr)
    if nodata is not None:
        invalid |= arr == float(nodata)
    values = np.where(invalid, np.nan, arr)
    if not np.isfinite(values).any():
        raise NoValidObservations(
            "无有效像元——归一化需要 ≥1 个有效值",
            correction_hint="检查 nodata 设置")

    ref_lo = ref_hi = None
    if method == "percentile_match":
        if reference is None:
            raise ValueError(
                "method='percentile_match' 需要 reference 参考栈"
                "（缺省请改用 method='percentile_stretch'）")
        ref, _ = _as_stack(reference)
        if ref.shape[0] != n_bands:
            raise ValueError(
                f"reference 波段数 {ref.shape[0]} != 栈波段数 {n_bands}"
                "（逐波段同序匹配）")
        ref_invalid = ~np.isfinite(ref)
        ref_values = np.where(ref_invalid, np.nan, ref)
        ref_lo = np.nanpercentile(ref_values, lo_q, axis=(1, 2))
        ref_hi = np.nanpercentile(ref_values, hi_q, axis=(1, 2))
        degenerate = (ref_hi - ref_lo) <= 1e-15
        if degenerate.any():
            raise DegenerateData(
                f"reference 波段 {np.where(degenerate)[0].tolist()} 分位"
                "区间为 0（常量场）——匹配无定义",
                correction_hint="更换参考场景或剔除常量波段")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        src_lo = np.nanpercentile(values, lo_q, axis=(1, 2))
        src_hi = np.nanpercentile(values, hi_q, axis=(1, 2))
    degenerate = (src_hi - src_lo) <= 1e-15
    if degenerate.any():
        raise DegenerateData(
            f"波段 {np.where(degenerate)[0].tolist()} 分位区间为 0"
            "（常量场）——拉伸无定义",
            correction_hint="剔除常量波段")
    stretched = np.clip(
        (values - src_lo[:, None, None])
        / (src_hi - src_lo)[:, None, None], 0.0, 1.0)
    if method == "percentile_match" and ref_lo is not None and ref_hi is not None:
        normalized = stretched * (ref_hi - ref_lo)[:, None, None] \
            + ref_lo[:, None, None]
    else:
        normalized = stretched

    percentiles_used: Dict[str, Dict[str, object]] = {}
    for i, name in enumerate(band_names):
        entry: Dict[str, object] = {
            "source": [round(float(src_lo[i]), 8), round(float(src_hi[i]), 8)],
            "quantiles": [lo_q, hi_q],
        }
        if ref_lo is not None and ref_hi is not None:
            entry["reference"] = [
                round(float(ref_lo[i]), 8), round(float(ref_hi[i]), 8)]
        percentiles_used[name] = entry

    meta: Dict[str, object] = {
        "method": method,
        "n_bands": n_bands,
        "band_order": band_names,
        "grid": [int(height), int(width)],
        "quantiles": [lo_q, hi_q],
        "percentiles_used": percentiles_used,
        "disclosure": (
            "NaN-aware 分位（np.nanpercentile 线性插值）；源值钳制到"
            f" [{lo_q}, {hi_q}] 分位区间（越界钳端点，披露）；"
            "percentile_match 把源区间线性重缩放到参考栈同序波段区间"
            "（对线性增益/偏移不变，逐波段所用分位在 percentiles_used "
            "完整披露）"),
    }
    return {
        "normalized": normalized,
        "method": method,
        "percentiles_used": percentiles_used,
        "meta": meta,
    }


# ── 13. 云 QC 基础咨询掩膜（EXPERIMENTAL；非 Fmask）───────────────────

def cloud_qc_basic(
    red: np.ndarray,
    nir: np.ndarray,
    *,
    brightness_thresholds: Optional[float] = None,
    brightness_percentile: float = 97.5,
    ndvi_max_abs: Optional[float] = None,
) -> Dict[str, object]:
    """亮度阈值云咨询掩膜（EXPERIMENTAL——不是云概率产品）。

    brightness = (red+nir)/2（两波段均值——云在红/近红外均高反射）。
    阈值：显式 ``brightness_thresholds``（绝对阈值）优先，缺省按场景
    ``brightness_percentile``（默认 97.5 百分位，nan-aware）。可选
    ``ndvi_max_abs``：追加 |NDVI| ≤ 阈值条件（云光谱平坦 ≈ 0）；NDVI
    无法计算（零分母）的像元不进入该条件（保守不标记，披露）。

    **强披露**：非 Fmask/cloud-probability——无热红外云检测、无卷云
    波段检验、无视差/时间合成检验；亮地物（屋顶/沙地/雪）会误报。
    仅作 advisory。
    """
    red_arr = np.asarray(red, dtype=float)
    nir_arr = np.asarray(nir, dtype=float)
    if red_arr.shape != nir_arr.shape:
        raise ValueError(
            f"red/nir 形状不一致：{red_arr.shape} vs {nir_arr.shape}")
    if red_arr.ndim != 2:
        raise ValueError(f"red/nir 必须是 2D 数组，got ndim={red_arr.ndim}")
    total = 2 * red_arr.size
    if total > RS_SCALE_LIMIT_CELLS:
        raise ResourceScaleMismatch(
            f"cloud_qc 双波段规模超限：2×{red_arr.shape[0]}×{red_arr.shape[1]}"
            f"={total} 像元（≤{RS_SCALE_LIMIT_CELLS}）",
            estimated=f"{total * 8 / 1e6:.1f} MB float64",
            limit=f"≤{RS_SCALE_LIMIT_CELLS}",
            correction_hint="分块处理或走栅格工件路径",
        )
    if brightness_thresholds is not None:
        if not (np.isfinite(brightness_thresholds)):
            raise ValueError(
                f"brightness_thresholds 必须为有限数，got {brightness_thresholds!r}")
        threshold = float(brightness_thresholds)
        threshold_source = "explicit"
    else:
        if not (np.isfinite(brightness_percentile)
                and 50.0 <= brightness_percentile <= 100.0):
            raise ValueError(
                f"brightness_percentile 必须在 [50, 100]，got {brightness_percentile!r}")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            threshold = float(np.nanpercentile(
                (red_arr + nir_arr) / 2.0, brightness_percentile))
        threshold_source = f"p{float(brightness_percentile)}"

    brightness = (red_arr + nir_arr) / 2.0
    suspect = np.isfinite(brightness) & (brightness > threshold)
    ndvi_condition = "off"
    if ndvi_max_abs is not None:
        if not (np.isfinite(ndvi_max_abs) and ndvi_max_abs >= 0):
            raise ValueError(
                f"ndvi_max_abs 必须 ≥0，got {ndvi_max_abs!r}")
        denom = nir_arr + red_arr
        with np.errstate(divide="ignore", invalid="ignore"):
            ndvi = (nir_arr - red_arr) / denom
        ndvi_ok = np.isfinite(ndvi) & (np.abs(ndvi) <= float(ndvi_max_abs))
        suspect = suspect & ndvi_ok
        ndvi_condition = f"|NDVI| ≤ {float(ndvi_max_abs)}"

    finite_bright = np.isfinite(brightness)
    suspect_fraction = float(np.sum(suspect) / max(1, int(finite_bright.sum())))
    meta: Dict[str, object] = {
        "grid": [int(red_arr.shape[0]), int(red_arr.shape[1])],
        "brightness_formula": "(red + nir)/2（云在红/近红外均高反射）",
        "threshold": float(threshold),
        "threshold_source": threshold_source,
        "brightness_percentile": (
            float(brightness_percentile)
            if brightness_thresholds is None else None),
        "ndvi_condition": ndvi_condition,
        "suspect_fraction": suspect_fraction,
        "scientific_status": "EXPERIMENTAL",
        "disclosure": (
            "强披露：非 Fmask/云概率产品——无热红外云检测、无卷云波段、"
            "无视差/多时相合成检验；亮地物（屋顶/沙地/雪/旱地）会误报；"
            "输出仅为 brightness 阈值咨询掩膜（qc_mask=True=疑似云），"
            "EXPERIMENTAL，不作为云剔除的唯一依据"),
    }
    return {
        "qc_mask": suspect,
        "brightness": brightness,
        "threshold": float(threshold),
        "suspect_fraction": suspect_fraction,
        "ndvi_condition": ndvi_condition,
        "meta": meta,
    }
