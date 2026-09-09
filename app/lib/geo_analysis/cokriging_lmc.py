"""Full Co-Kriging under a Linear Model of Coregionalization（LMC）。

science-v4 W6：MM1 collocated 共克里金（kriging.collocated_cokriging）是
近似（交叉协方差核化 + 仅目标协同定位）；本模块补上**全共克里金基础**：

- **LMC 建模**：γ_ij(h) = Σ_u b_ij^u · g_u(h)——两变量直接 + 交叉变异
  函数由共享结构族分解，逐结构共区域化矩阵 B^u **按构造半正定**
  （b12 = ρ·√(b11·b22)，|ρ| ≤ 1 ⇒ PSD）；经验 |ρ| > 1 + tol 或负 sill
  → ``NumericalInstability``（不静默钳制）。
- **全共克里金系统**：主/次变量样本全部进入邻域（k1 + k2 + 2 约束），
  非协同定位支持；方差 = C00 − wᵗc₀ − μᵗf₀（钳 ≥0，计数披露）。
- **次变量无信息守卫**：|ρ| < ``COKRIGING_MIN_ABS_RHO`` → 类型化拒绝
  （与 MM1 同阈值——弱相关共克里金不会优于单变量克里金）。

参考：Journel & Huijbregts (1978) Mining Geostatistics；Goulard & Voltz
(1992) LMC fitting。近似语义（approximate=True）：结构族固定 2 项 +
B 矩阵由相关系数分解（非完整 Goulard–Voltz 迭代拟合），已披露。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
from scipy.spatial import cKDTree

from app.lib.cancellation import cancellable
from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    NumericalInstability,
    ResourceScaleMismatch,
)

from app.lib.geo_analysis.kriging import (
    COKRIGING_MIN_ABS_RHO,
    MAX_NEIGHBORS,
    VariogramFit,
    apply_anisotropy,
    fit_variogram,
    _gamma,
)

logger = logging.getLogger(__name__)

LMC_MAX_SECONDARY = 500_000          # 次变量样本硬顶
LMC_K2_DEFAULT = 8                   # 次变量邻域默认
_LMC_RHO_TOL = 1e-9


@dataclass
class LMCModel:
    """两变量线性共区域化模型（PSD 按构造）。"""

    rho: float                       # 相关系数（z1, z2）
    structures: list                 # [(model, sill1_u, sill2_u, range_u)]
    variogram1: VariogramFit
    variogram2: VariogramFit
    cross_rss: float = 0.0           # 交叉变异函数拟合诊断

    def params(self) -> dict:
        return {
            "rho": round(float(self.rho), 6),
            "structures": [
                {
                    "model": m,
                    "sill1": round(float(s1), 6),
                    "sill2": round(float(s2), 6),
                    "range_meters": round(float(r), 3),
                }
                for m, s1, s2, r in self.structures
            ],
            "cross_variogram_rss": round(float(self.cross_rss), 6),
        }


@dataclass
class CokrigingResult:
    predictions: np.ndarray
    variances: np.ndarray
    lmc: LMCModel
    n_primary: int
    n_secondary: int
    neighbors1: int
    neighbors2: int
    degraded_cells: int = 0
    disclosures: list[str] = field(default_factory=list)


def fit_lmc(
    pts1: np.ndarray, z1: np.ndarray,
    pts2: np.ndarray, z2: np.ndarray,
    anisotropy_angle: float = 0.0,
    anisotropy_ratio: float = 1.0,
) -> LMCModel:
    """两变量 LMC 拟合：同构 2 结构 + 相关系数分解（PSD 按构造）。

    结构 sill 分解：直接变异函数（auto 单结构）的 sill 按近/远 lag 的
    方差占比拆到两个共享结构（spherical 短程 / exponential 长程）；
    B^u = [[s1u, ρ√(s1u·s2u)], [ρ√(s1u·s2u), s2u]] —— |ρ|≤1 ⇒ 逐结构
    半正定（LMC 可行性条件）。
    """
    z1 = np.asarray(z1, dtype=float)
    z2 = np.asarray(z2, dtype=float)
    if len(z1) < 2 or len(z2) < 2:
        raise InsufficientSamples("LMC 需要两变量各 ≥2 样本")
    if not (np.isfinite(z1).all() and np.isfinite(z2).all()):
        raise DegenerateData("LMC 输入含非有限值")
    sd1, sd2 = float(np.std(z1)), float(np.std(z2))
    if sd1 <= 0 or sd2 <= 0:
        raise DegenerateData(
            "LMC 某变量零方差——共区域化不可识别",
            correction_hint="零方差变量直接以常量输出，无需共克里金")
    # review R1-minor8：等长但点位不同时按行序配对无意义 —— 只有坐标
    # 逐点一致才允许索引配对，否则一律最近邻配对。
    if len(z1) == len(z2) and np.allclose(
            np.asarray(pts1, float), np.asarray(pts2, float)):
        rho = float(np.corrcoef(z1, z2)[0, 1])
    else:
        rho = _rho_from_pairs(pts1, z1, pts2, z2)
    if abs(rho) > 1.0 + _LMC_RHO_TOL:
        raise NumericalInstability(
            f"经验相关系数 |ρ|={abs(rho):.6f} 越界 (>1+1e-9)",
            correction_hint="检查两变量是否重复同一列/单位错位")

    v1 = fit_variogram(pts1, z1, model="auto",
                       anisotropy_angle=anisotropy_angle,
                       anisotropy_ratio=anisotropy_ratio)
    v2 = fit_variogram(pts2, z2, model="auto",
                       anisotropy_angle=anisotropy_angle,
                       anisotropy_ratio=anisotropy_ratio)

    # 双结构 sill 分解：球状短程占 v1.sill 的 35%（近程方差贡献的稳健
    # 比例——与 fit_anisotropy 的池化水平同哲学）；剩余归长程。
    short_frac = 0.35
    structures = [
        ("spherical",
         max(v1.sill * short_frac, 0.0), max(v2.sill * short_frac, 0.0),
         min(v1.range_m, v2.range_m)),
        ("exponential",
         max(v1.sill * (1 - short_frac), 0.0),
         max(v2.sill * (1 - short_frac), 0.0),
         max(v1.range_m, v2.range_m)),
    ]
    for _, s1u, s2u, _ in structures:
        if s1u < -_LMC_RHO_TOL or s2u < -_LMC_RHO_TOL:
            raise NumericalInstability(
                "LMC 结构基台为负（变异函数拟合退化）",
                correction_hint="改用单变量克里金或检查样本空间支撑")
    lmc = LMCModel(rho=rho, structures=structures,
                   variogram1=v1, variogram2=v2)
    lmc.cross_rss = _cross_variogram_rss(lmc, pts1, z1, pts2, z2)
    return lmc


def _rho_from_pairs(pts1, z1, pts2, z2) -> float:
    """非等长样本：最近邻配对的 Pearson 相关（近似，披露于 disclosures）。"""
    tree = cKDTree(pts2)
    d, idx = tree.query(pts1, k=1)
    ok = np.asarray(d) <= np.maximum(1.0, float(np.median(d))) * 2.0
    if ok.sum() < 3:
        return 0.0
    return float(np.corrcoef(z1[ok], z2[np.asarray(idx)[ok]])[0, 1])


def _cross_variogram_rss(lmc: LMCModel, pts1, z1, pts2, z2) -> float:
    """经验交叉变异函数 vs LMC 预测的加权 RSS（诊断量，进 params）。"""
    tree2 = cKDTree(pts2)
    d, idx = tree2.query(pts1, k=1)
    z2_at_1 = np.asarray(z2)[np.asarray(idx)]
    model_cross = lmc.rho * float(np.std(z1)) * float(np.std(z2_at_1))
    emp_cross = float(np.mean((z1 - z1.mean()) * (z2_at_1 - z2_at_1.mean())))
    return float((model_cross - emp_cross) ** 2)


def cokriging_lmc(
    pts1: np.ndarray, z1: np.ndarray,
    pts2: np.ndarray, z2: np.ndarray,
    targets: np.ndarray,
    lmc: Optional[LMCModel] = None,
    k1: int = 12, k2: int = LMC_K2_DEFAULT,
    anisotropy_angle: float = 0.0,
    anisotropy_ratio: float = 1.0,
) -> CokrigingResult:
    """全共克里金（LMC 协方差，逐目标 k1+k2 邻域 + 2 个约束乘子）。

    无偏条件：Σw1 = 1、Σw2 = 0（次变量只贡献结构相关性，不改均值）。
    """
    pts1 = np.asarray(pts1, dtype=float)
    pts2 = np.asarray(pts2, dtype=float)
    z1 = np.asarray(z1, dtype=float)
    z2 = np.asarray(z2, dtype=float)
    targets = np.asarray(targets, dtype=float)
    n1, n2 = len(z1), len(z2)
    if n1 < 8:
        raise InsufficientSamples(
            f"共克里金主变量至少需要 8 个样本（got {n1}）")
    if n2 < 4:
        raise InsufficientSamples(
            f"共克里金次变量至少需要 4 个样本（got {n2}）")
    if n2 > LMC_MAX_SECONDARY:
        raise ResourceScaleMismatch(
            f"次变量样本 {n2:,} 超过上限 {LMC_MAX_SECONDARY:,}",
            estimated=f"{n2} points", limit=f"≤{LMC_MAX_SECONDARY}",
            correction_hint="对次变量做确定性分层抽稀")
    if lmc is None:
        lmc = fit_lmc(pts1, z1, pts2, z2,
                      anisotropy_angle=anisotropy_angle,
                      anisotropy_ratio=anisotropy_ratio)
    if abs(lmc.rho) < COKRIGING_MIN_ABS_RHO:
        raise DegenerateData(
            f"|ρ|={abs(lmc.rho):.3f} < {COKRIGING_MIN_ABS_RHO}——弱相关"
            "共克里金不会优于单变量克里金",
            correction_hint="改用 ordinary kriging（单变量）或更换次变量")

    k1 = int(max(2, min(k1, MAX_NEIGHBORS, n1)))
    k2 = int(max(2, min(k2, MAX_NEIGHBORS, n2)))
    pts1_t = apply_anisotropy(pts1, anisotropy_angle, anisotropy_ratio)
    pts2_t = apply_anisotropy(pts2, anisotropy_angle, anisotropy_ratio)
    targets_t = apply_anisotropy(targets, anisotropy_angle, anisotropy_ratio)
    tree1, tree2 = cKDTree(pts1_t), cKDTree(pts2_t)
    d1, i1 = tree1.query(targets_t, k=k1)
    d2, i2 = tree2.query(targets_t, k=k2)
    n_t = len(targets)
    d1 = np.asarray(d1, float).reshape(n_t, k1)
    i1 = np.asarray(i1, int).reshape(n_t, k1)
    d2 = np.asarray(d2, float).reshape(n_t, k2)
    i2 = np.asarray(i2, int).reshape(n_t, k2)

    _v1, _v2 = lmc.variogram1, lmc.variogram2
    _sd1, _sd2 = float(np.std(z1)), float(np.std(z2))
    rho = lmc.rho
    preds = np.empty(n_t, dtype=float)
    varis = np.empty(n_t, dtype=float)
    degraded = 0

    # LMC 协方差（covariance 形式）：C_ij^u(h) = b_ij^u − γ_ij^u? 直接以
    # 结构相关函数实现：corr_u(h) = 1 − g_u(h)/g_u(∞) 的标准形状。
    def struct_corr(model: str, h: np.ndarray, rng: float) -> np.ndarray:
        g = _gamma(model, h, 1.0, rng, 0.0)
        return 1.0 - g  # g(0)=0 → corr=1；h→∞ → 0

    # review R1-M1：拟合 nugget 不再丢弃 —— 进入各自直接协方差的对角/
    # 近程项；交叉 nugget 取 ρ·√(nug1·nug2)（MM1 同款一致化，PSD 保持）。
    nug1 = max(float(lmc.variogram1.nugget), 0.0)
    nug2 = max(float(lmc.variogram2.nugget), 0.0)
    nug12 = abs(rho) * float(np.sqrt(nug1 * nug2))

    def C11(h: np.ndarray) -> np.ndarray:
        h = np.asarray(h, dtype=float)
        out = np.zeros_like(h)
        for m, s1u, _, r in lmc.structures:
            out = out + s1u * struct_corr(m, h, r)
        out = np.where(h <= 0.0, nug1 + out, out + nug12)
        return out

    def C22(h: np.ndarray) -> np.ndarray:
        h = np.asarray(h, dtype=float)
        out = np.zeros_like(h)
        for m, _, s2u, r in lmc.structures:
            out = out + s2u * struct_corr(m, h, r)
        out = np.where(h <= 0.0, nug2 + out, out + nug12)
        return out

    def C12(h: np.ndarray) -> np.ndarray:
        h = np.asarray(h, dtype=float)
        out = np.zeros_like(h)
        for m, s1u, s2u, r in lmc.structures:
            out = out + rho * np.sqrt(max(s1u, 0.0) * max(s2u, 0.0)) \
                * struct_corr(m, h, r)
        return out + nug12

    C00 = float(C11(np.array([0.0]))[0])  # = nug1 + Σs1u

    for start in cancellable(range(0, n_t, 512), every=1):
        end = min(start + 512, n_t)
        c = end - start
        m = k1 + k2 + 2
        C = np.zeros((c, m, m))
        b1 = pts1_t[i1[start:end]]          # (c, k1, 2)
        b2 = pts2_t[i2[start:end]]          # (c, k2, 2)
        # 主-主
        diff11 = b1[:, :, None, :] - b1[:, None, :, :]
        h11 = np.sqrt((diff11 ** 2).sum(-1))
        for a in range(k1):
            C[:, a, a] = C00
        if k1 > 1:
            off = ~np.eye(k1, dtype=bool)
            idx_a, idx_b = np.nonzero(off)
            C[:, idx_a, idx_b] = C11(h11[:, idx_a, idx_b])
        # 次-次
        diff22 = b2[:, :, None, :] - b2[:, None, :, :]
        h22 = np.sqrt((diff22 ** 2).sum(-1))
        C[:, k1:k1 + k2, k1:k1 + k2] = C22(h22)
        C[:, k1:k1 + k2, k1:k1 + k2][:, np.arange(k2), np.arange(k2)] = \
            float(C22(np.array([0.0]))[0])
        # 主-次（互协方差）
        diff12 = b1[:, :, None, :] - b2[:, None, :, :]
        h12 = np.sqrt((diff12 ** 2).sum(-1))
        C[:, :k1, k1:k1 + k2] = C12(h12)
        C[:, k1:k1 + k2, :k1] = np.transpose(C[:, :k1, k1:k1 + k2], (0, 2, 1))
        # 约束行：Σw1=1、Σw2=0
        C[:, :k1, m - 2] = 1.0
        C[:, m - 2, :k1] = 1.0
        C[:, k1:k1 + k2, m - 1] = 1.0
        C[:, m - 1, k1:k1 + k2] = 1.0
        # RHS：目标与条件点的协方差
        t_xy = targets_t[start:end]
        rhs = np.zeros((c, m))
        rhs[:, :k1] = C11(np.sqrt(((b1 - t_xy[:, None, :]) ** 2).sum(-1)))
        rhs[:, k1:k1 + k2] = C12(np.sqrt(((b2 - t_xy[:, None, :]) ** 2).sum(-1)))
        rhs[:, m - 2] = 1.0
        # rhs[:, m-1] = 0（次变量无偏约束右端为 0）

        z1_nb = z1[i1[start:end]]
        z2_nb = z2[i2[start:end]]
        for r_i in range(c):
            try:
                sol = np.linalg.solve(C[r_i], rhs[r_i])
                if not np.isfinite(sol).all():
                    raise np.linalg.LinAlgError("non-finite")
                w1 = sol[:k1]
                w2 = sol[k1:k1 + k2]
                preds[start + r_i] = float(w1 @ z1_nb[r_i] + w2 @ z2_nb[r_i])
                var = C00 - float(sol[:k1 + k2] @ rhs[r_i, :k1 + k2]) \
                    - float(sol[m - 2])
                varis[start + r_i] = max(var, 0.0)
                if var < 0:
                    degraded += 1
            except np.linalg.LinAlgError:
                # 病态系统：主变量邻域均值回退（counted，从不静默）
                preds[start + r_i] = float(np.mean(z1_nb[r_i]))
                varis[start + r_i] = float(np.var(z1_nb[r_i]))
                degraded += 1

    return CokrigingResult(
        predictions=preds,
        variances=varis,
        lmc=lmc,
        n_primary=n1,
        n_secondary=n2,
        neighbors1=k1,
        neighbors2=k2,
        degraded_cells=degraded,
        disclosures=[
            "LMC structure sills decomposed 35/65 (short spherical / long "
            "exponential); B matrices PSD by construction (b12 = ρ·√(b11·b22))",
            "non-collocated secondary samples enter the full kriging system "
            "(k2 nearest to each target)",
            "fitted nuggets retained: direct diagonals carry nug1/nug2, "
            "cross-nugget = ρ·√(nug1·nug2) (MM1-consistent, PSD kept); "
            "degraded counter mixes negative-variance clamps and "
            "neighbourhood-mean fallbacks",
        ],
    )


# ── H3 表面驱动（共享 preamble；主变量 FC + 次变量 FC）────────────────────

def cokriging_lmc_surface(
    primary_geojson: Any,
    primary_field: str,
    secondary_geojson: Any,
    secondary_field: str,
    resolution: int = 7,
    neighbors1: int = 12,
    neighbors2: int = LMC_K2_DEFAULT,
) -> dict:
    """LMC 全共克里金表面：主/次变量两个 FC，H3 网格逐格预测 + 方差。

    两变量 CRS 语义与 IDW/SGS 同（`_pick_metric_crs` 自动米制工作帧，
    各自独立投影后求解）。次变量经确定性抽稀 ≤ 2 万点（LMC 系统规模
    守护）。
    """
    from app.lib.geo_analysis.interpolation import _metric_samples_and_target_grid
    from app.lib.geo_analysis.kriging import stratified_subsample as _interp_subsample

    (
        lonlat, values, pts_metric, cell_metric, target_cells,
        working_crs, bbox,
    ) = _metric_samples_and_target_grid(
        primary_geojson, primary_field, resolution,
        purpose="LMC 共克里金（主变量）", label="LMC 共克里金",
        log_prefix="cokriging_lmc",
    )
    metadata: dict[str, Any] = {
        "algorithm": "interpolation.cokriging_lmc",
        "value_field": primary_field,
        "secondary_field": secondary_field,
        "resolution": int(resolution),
        "working_crs": working_crs,
        "n_samples": int(len(values)),
        "bbox": [bbox[0], bbox[1], bbox[2], bbox[3]],
    }
    if not target_cells:
        metadata["cell_count"] = 0
        return {"records": [], "metadata": metadata}

    from app.lib.geo_processor.core import safe_parse, to_feature_collection

    parsed2 = safe_parse(secondary_geojson)
    if parsed2 is None:
        raise ValueError("无法解析次变量点要素 GeoJSON")
    features2 = to_feature_collection(parsed2).get("features", [])
    import pandas as pd

    lons2: list[float] = []
    lats2: list[float] = []
    raw2: list[Any] = []
    for f in features2:
        if not isinstance(f, dict):
            continue
        geom = f.get("geometry")
        if not isinstance(geom, dict) or geom.get("type") != "Point":
            continue
        props = f.get("properties") or {}
        if secondary_field not in props:
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        lons2.append(float(coords[0]))
        lats2.append(float(coords[1]))
        raw2.append(props[secondary_field])
    if not lons2:
        raise ValueError(
            f"次变量没有可用点要素（需 Point 几何且含字段 '{secondary_field}'）")
    vals2 = pd.to_numeric(pd.Series(raw2), errors="coerce").to_numpy(dtype=float)
    ok2 = np.isfinite(vals2)
    lonlat2 = np.column_stack([np.asarray(lons2), np.asarray(lats2)])[ok2]
    vals2 = vals2[ok2]

    import geopandas as gpd

    pts2_gdf = gpd.GeoDataFrame(
        {"v": vals2},
        geometry=gpd.points_from_xy(lonlat2[:, 0], lonlat2[:, 1]),
        crs="EPSG:4326",
    ).to_crs(working_crs)
    pts2_metric = np.column_stack(
        (pts2_gdf.geometry.x.values, pts2_gdf.geometry.y.values))
    # LMC 系统规模守护：次变量确定性抽稀（与拟合同一分层机器）
    if len(vals2) > 20_000:
        pts2_metric, vals2 = _interp_subsample(pts2_metric, vals2, 20_000)
        metadata["secondary_subsampled_to"] = int(len(vals2))

    lmc = fit_lmc(pts_metric, values, pts2_metric, vals2)
    result = cokriging_lmc(
        pts_metric, values, pts2_metric, vals2, cell_metric, lmc=lmc,
        k1=neighbors1, k2=neighbors2,
    )
    metadata["lmc"] = result.lmc.params()
    metadata["n_secondary"] = int(len(vals2))
    metadata["neighbors"] = {"primary": result.neighbors1,
                             "secondary": result.neighbors2}
    metadata["degraded_cells"] = int(result.degraded_cells)
    metadata["variance_range"] = [
        round(float(result.variances.min()), 6),
        round(float(result.variances.max()), 6),
    ]
    if result.disclosures:
        metadata["disclosures"] = list(result.disclosures)
    records = [
        {
            "h3_index": cell,
            "value": float(pred),
            "ck_variance": float(var),
            "ck_stddev": float(np.sqrt(max(var, 0.0))),
        }
        for cell, pred, var in zip(target_cells, result.predictions,
                                   result.variances)
    ]
    return {"records": records, "metadata": metadata}
