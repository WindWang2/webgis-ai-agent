"""General spatial weights construction（ADR-0099 spatial-science VNext）。

``WeightsMatrix`` 把 scipy.sparse 权重矩阵与方法学元数据（scheme /
row_standardized / islands / 方案参数）打包在一起 —— 空间统计结果必须能
披露"权重是怎么来的"，而不是只给一个矩阵。

Builders（全部确定性、无随机成分）：

- ``queen`` / ``rook``：面要素邻接（libpysal），需要 Polygon/MultiPolygon；
- ``knn``：坐标 k 近邻（cKDTree），对称化取并集 —— 与
  ``statistics.moran_i_narrated`` 默认路径的 #1002 语义逐位一致；
- ``distance_band``：距离阈值二值权重（可选 include_self=True 提供 Gi*
  需要的 w_ii=1）；
- ``inverse_distance``：反距离权重（power 幂次 + epsilon 防 0 距离开除）。

科学错误（UnsupportedMethod / ResourceScaleMismatch /
InsufficientSamples）来自 ``app.lib.gis.scientific_errors`` —— 科学性失败
类型化，不再各自发明 ValueError 文案。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional

import numpy as np
from scipy import sparse

from app.lib.gis.scientific_errors import (
    InsufficientSamples,
    ResourceScaleMismatch,
    UnsupportedMethod,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    import geopandas as gpd

#: 支持的权重方案（封闭词表；统计实现与工具描述共用）。
WEIGHT_SCHEMES = ("knn", "queen", "rook", "distance_band")

# inverse_distance 是完整 O(n²) 稠密构造 —— n 超过该上限时诚实拒绝，
# 不静默吃内存（ResourceScaleMismatch 先于 OOM）。
_MAX_IDW_OBSERVATIONS = 2000


def _row_standardize_csr(m: sparse.csr_matrix) -> sparse.csr_matrix:
    """Row-standardize a sparse matrix in place-safe fashion.

    Island rows (zero row-sum) stay zero — row standardization must not
    fabricate neighbours. This mirrors the exact op sequence the default
    (knn) path of ``moran_i_narrated`` has used since #1002, so the default
    behaviour stays bit-comparable.
    """
    row_sums = np.asarray(m.sum(axis=1)).ravel()
    inv_row = np.zeros_like(row_sums)
    np.divide(1.0, row_sums, out=inv_row, where=row_sums > 0)
    return sparse.diags(inv_row) @ m


def _island_indices(m: sparse.csr_matrix) -> List[int]:
    """Rows with no neighbour at all (zero row sums after construction)."""
    row_sums = np.asarray(m.sum(axis=1)).ravel()
    return [int(i) for i in np.flatnonzero(row_sums == 0)]


@dataclass
class WeightsMatrix:
    """A spatial weights matrix plus the metadata a result must disclose."""

    scheme: str                       # queen|rook|knn|distance_band|inverse_distance
    matrix: sparse.csr_matrix         # n×n; diagonal entries only when include_self
    n: int
    row_standardized: bool = False
    islands: List[int] = field(default_factory=list)
    k: Optional[int] = None           # knn: neighbours per observation
    threshold: Optional[float] = None  # distance_band / inverse_distance cut (m)
    include_self: bool = False        # True → w_ii = 1 (Gi* semantics)
    power: Optional[float] = None     # inverse_distance exponent

    @property
    def s0(self) -> float:
        """S0 = Σᵢⱼ wᵢⱼ（全局统计量的权重总和）."""
        return float(self.matrix.sum())

    def row_standardize(self) -> "WeightsMatrix":
        """Return a row-standardized copy (islands remain zero rows)."""
        if self.row_standardized:
            return self
        std = _row_standardize_csr(self.matrix.tocsr())
        return WeightsMatrix(
            scheme=self.scheme, matrix=std, n=self.n, row_standardized=True,
            islands=list(self.islands), k=self.k, threshold=self.threshold,
            include_self=self.include_self, power=self.power,
        )

    def metadata(self) -> dict:
        """Bounded, JSON-safe disclosure block for result payloads."""
        meta = {
            "scheme": self.scheme,
            "row_standardized": self.row_standardized,
            "n": self.n,
            "islands": len(self.islands),
        }
        if self.k is not None:
            meta["k"] = self.k
        if self.threshold is not None:
            meta["threshold_m"] = round(float(self.threshold), 4)
        if self.include_self:
            meta["include_self"] = True
        if self.power is not None:
            meta["power"] = self.power
        return meta


# ── queen / rook（面邻接）───────────────────────────────────────────

def build_contiguity_weights(
    gdf: "gpd.GeoDataFrame",
    scheme: str = "queen",
    *,
    row_standardized: bool = True,
) -> WeightsMatrix:
    """Queen/Rook contiguity weights from polygon geometries (libpysal).

    Raises ``UnsupportedMethod`` for non-polygon input — contiguity is a
    areal-unit concept; point clouds need knn/distance_band (or an explicit
    tessellation first).
    """
    if scheme not in ("queen", "rook"):
        raise ValueError(f"scheme must be 'queen' or 'rook' (got {scheme!r})")
    try:
        from libpysal.weights import Queen, Rook
    except ImportError as exc:  # pragma: no cover - dependency pinned in venv
        raise UnsupportedMethod(
            "libpysal is not installed; queen/rook contiguity unavailable",
            correction_hint="use weights_scheme='knn' or 'distance_band' instead",
        ) from exc

    geom_types = set(gdf.geometry.geom_type)
    if not geom_types <= {"Polygon", "MultiPolygon"}:
        raise UnsupportedMethod(
            f"{scheme} contiguity requires polygonal geometries "
            f"(found {sorted(geom_types)})",
            correction_hint=(
                "pass polygonal units, tessellate points first (e.g. Voronoi), "
                "or use weights_scheme='knn'/'distance_band'"
            ),
        )

    gdf = gdf.reset_index(drop=True)
    builder = Queen if scheme == "queen" else Rook
    # silence_warnings: connectivity is disclosed through the islands metadata
    # (bounded, part of the result payload) rather than a stderr UserWarning
    # that the tool layer cannot route.
    w = builder.from_dataframe(gdf, use_index=False, silence_warnings=True)
    islands = [int(i) for i in getattr(w, "islands", []) or []]
    # libpysal sparse: no diagonal, symmetric by construction.
    m = sparse.csr_matrix(w.sparse.tocsr())
    if row_standardized:
        m = _row_standardize_csr(m)
    return WeightsMatrix(
        scheme=scheme, matrix=m, n=len(gdf), row_standardized=row_standardized,
        islands=islands,
    )


# ── knn（坐标 k 近邻，对称并集）────────────────────────────────────

def build_knn_weights(
    coords: np.ndarray,
    k: int = 8,
    *,
    row_standardized: bool = True,
    symmetrize: bool = True,
) -> WeightsMatrix:
    """Binary k-nearest-neighbour weights via cKDTree, symmetrized by union.

    Mirrors the #1002 semantics of ``statistics._build_weights`` +
    ``moran_i_narrated``: directed kNN is union-symmetrized
    (``w | wᵀ``, elementwise maximum) so global autocorrelation statistics
    get symmetric weights, then rows are standardized. Self-exclusion is
    explicit (E-4): the actual self column is dropped per row, so duplicate
    coordinates can never inject a self-loop.
    """
    from scipy.spatial import cKDTree

    coords = np.asarray(coords, dtype=float)
    n = len(coords)
    if n == 0:
        raise InsufficientSamples("knn weights need at least 1 observation")
    if n == 1:
        m = sparse.csr_matrix((1, 1))
        return WeightsMatrix(
            scheme="knn", matrix=m, n=1, row_standardized=False, islands=[0], k=0,
        )
    k_actual = max(1, min(int(k), n - 1))
    tree = cKDTree(coords)
    # Query one extra neighbour so dropping self *usually* leaves k_actual.
    # 重合点簇（>k+1 个重合点）：tie-break 可能把 self 排出 k+1 邻域，
    # 行贡献数不再恒为 k_actual —— rows 必须从逐行计数派生（评审
    # MAJOR-1：此前 np.repeat(·, k_actual) 在该场景引发 COO 长度失配）。
    _, idx = tree.query(coords, k=k_actual + 1)
    mask = idx != np.arange(n)[:, None]
    cols = idx[mask]
    per_row = mask.sum(axis=1)
    rows = np.repeat(np.arange(n), per_row)
    data = np.ones(len(rows), dtype=float)
    w = sparse.coo_matrix((data, (rows, cols)), shape=(n, n)).tocsr()
    if symmetrize:
        w = w.maximum(w.transpose())
    if row_standardized:
        w = _row_standardize_csr(w)
    return WeightsMatrix(
        scheme="knn", matrix=w.tocsr(), n=n,
        row_standardized=row_standardized, islands=_island_indices(w),
        k=k_actual,
    )


# ── distance_band（二值距离阈值）───────────────────────────────────

def build_distance_band_weights(
    coords: np.ndarray,
    threshold: float,
    *,
    include_self: bool = False,
    row_standardized: bool = False,
) -> WeightsMatrix:
    """Binary distance-band weights (w_ij = 1 iff d_ij ≤ threshold).

    ``include_self=True`` keeps the (i,i) diagonal at distance 0 — the
    w_ii=1 form Getis-Ord Gi* requires. Symmetric by construction.
    """
    from scipy.spatial import cKDTree

    coords = np.asarray(coords, dtype=float)
    n = len(coords)
    if n == 0:
        raise InsufficientSamples("distance-band weights need at least 1 observation")
    threshold = float(threshold)
    if not np.isfinite(threshold) or threshold <= 0:
        raise ValueError(f"threshold must be a positive finite distance (got {threshold})")
    tree = cKDTree(coords)
    coo = tree.sparse_distance_matrix(
        tree, max_distance=threshold, output_type="coo_matrix")
    # B1（科学评审）：include_self 是在非对角项**之上**加 w_ii=1，
    # 不是只留对角 —— 此前的条件表达式把 Gi* 权重变成了单位阵。
    keep = (coo.row != coo.col) | \
        (include_self & (coo.row == coo.col))
    m = sparse.csr_matrix(
        (np.ones(int(keep.sum()), dtype=float), (coo.row[keep], coo.col[keep])),
        shape=(n, n),
    )
    if row_standardized:
        m = _row_standardize_csr(m)
    return WeightsMatrix(
        scheme="distance_band", matrix=m, n=n,
        row_standardized=row_standardized, islands=_island_indices(m),
        threshold=threshold, include_self=include_self,
    )


# ── inverse_distance（反距离）───────────────────────────────────────

def build_inverse_distance_weights(
    coords: np.ndarray,
    power: float = 1.0,
    *,
    epsilon: float = 1e-9,
    row_standardized: bool = True,
) -> WeightsMatrix:
    """Inverse-distance weights w_ij = 1/(d_ij + ε)^power (self excluded).

    Full pairwise construction is O(n²) memory — honest refusal beyond
    ``_MAX_IDW_OBSERVATIONS`` (ResourceScaleMismatch) instead of an OOM.
    """
    from scipy.spatial.distance import cdist

    coords = np.asarray(coords, dtype=float)
    n = len(coords)
    if n == 0:
        raise InsufficientSamples("inverse-distance weights need at least 1 observation")
    if n > _MAX_IDW_OBSERVATIONS:
        raise ResourceScaleMismatch(
            f"inverse-distance weights need the full {n}×{n} pair matrix",
            estimated=f"{n * n} pairs",
            limit=f"{_MAX_IDW_OBSERVATIONS}×{_MAX_IDW_OBSERVATIONS} pairs",
            correction_hint="reduce the observation count or use knn/distance_band weights",
        )
    # MINOR-6（数值评审）：power 上限与 IDW 契约一致 —— 极端幂次在
    # ε=1e-9 下溢出 → w=0 → 全岛矩阵（形式合法但退化）。
    if not 0 < power <= 5:
        from app.lib.gis.scientific_errors import UnsupportedMethod

        raise UnsupportedMethod(
            f"inverse-distance power must be in (0, 5] (got {power})",
            correction_hint="use power 1-2 for typical inverse-distance weights")
    if epsilon <= 0:
        raise ValueError(f"epsilon must be > 0 (got {epsilon})")
    d = cdist(coords, coords)
    np.fill_diagonal(d, np.inf)  # self excluded → weight 0
    w = 1.0 / np.power(d + epsilon, power)
    w[~np.isfinite(w)] = 0.0
    m = sparse.csr_matrix(w)
    if row_standardized:
        m = _row_standardize_csr(m)
    return WeightsMatrix(
        scheme="inverse_distance", matrix=m, n=n,
        row_standardized=row_standardized, islands=_island_indices(m),
        threshold=None, power=float(power),
    )


# ── 自动带宽（E-7 规则共享）────────────────────────────────────────

def auto_band_8nn(coords: np.ndarray, k: int = 8) -> float:
    """Mean k-th nearest-neighbour distance (the E-7 auto-band rule).

    The mean 1st-NN distance leaves ~half the points disconnected on
    clustered data; the 8th-NN band ensures most points have several
    neighbours. Falls back to 1.0 (metres) if the data is degenerate.
    """
    from scipy.spatial import cKDTree

    coords = np.asarray(coords, dtype=float)
    n = len(coords)
    if n < 2:
        return 1.0
    tree = cKDTree(coords)
    k_band = min(int(k), n - 1)
    nn_dist, _ = tree.query(coords, k=k_band + 1)
    bw = float(nn_dist[:, k_band].mean())
    return bw if bw > 0 else 1.0
