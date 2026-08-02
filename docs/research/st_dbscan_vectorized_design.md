# Research Findings & Architectural Design: ST-DBSCAN Vectorized NumPy Implementation & GeoAnalysisResult Seam

## 1. Executive Summary & Design Overview

Wayfinder Ticket #223 requires adding a native, vectorized **ST-DBSCAN** (Spatio-Temporal Density-Based Spatial Clustering of Applications with Noise) implementation to the WebGIS AI Agent core analysis library (`app/lib/geo_analysis/statistics.py`) and exposing it via the `SpatialAnalyzer` operator seam (`app/services/spatial_analyzer.py`).

### Key Architectural Invariants:
1. **Zero External C-Dependencies**: Uses standard NumPy, SciPy (`cKDTree`, `pdist`, `sparse`), and Scikit-Learn (`DBSCAN`). No third-party C extensions or specialized `st-dbscan` PyPI packages.
2. **Unified Seam Contract**: Returns standard `GeoAnalysisResult` object (ADR-0009 / ADR-0029), seamlessly supported by `@spatial_operator` and ChatEngine LLM responses.
3. **Automatic CRS & Projected Coordinates**: Integrates with `to_utm_gdf()` to project WGS84 spatial coordinates into local meters before distance evaluation.

---

## 2. Spatio-Temporal Distance Vectorization Proof

In theoretical ST-DBSCAN (Birant & Kut, 2007), two points $p_i = (x_i, y_i, t_i)$ and $p_j = (x_j, y_j, t_j)$ belong to each other's spatio-temporal $\epsilon$-neighborhood if and only if **both** distance constraints are satisfied:
$$D_{\text{spatial}}(p_i, p_j) \le \epsilon_1 \quad \text{AND} \quad D_{\text{temporal}}(p_i, p_j) \le \epsilon_2$$

### Vectorization Proof via Normalized $L_\infty$ Distance Matrix:
To execute ST-DBSCAN using Scikit-Learn's native `DBSCAN(metric="precomputed", eps=1.0)`:

1. Let $D_{\text{spatial}}(i, j)$ be the Euclidean distance (in meters) between UTM projected coordinates $(x_i, y_i)$ and $(x_j, y_j)$.
2. Let $D_{\text{temporal}}(i, j) = |t_i - t_j|$ be the absolute temporal difference (in seconds) between timestamps.
3. Construct a combined normalized distance matrix $D_{\text{combined}}$:
   $$D_{\text{combined}}(i, j) = \max \left( \frac{D_{\text{spatial}}(i, j)}{\epsilon_1}, \frac{D_{\text{temporal}}(i, j)}{\epsilon_2} \right)$$

### Equivalence Proof:
$$D_{\text{combined}}(i, j) \le 1.0 \iff \left( \frac{D_{\text{spatial}}(i, j)}{\epsilon_1} \le 1.0 \quad \text{and} \quad \frac{D_{\text{temporal}}(i, j)}{\epsilon_2} \le 1.0 \right) \iff \left( D_{\text{spatial}}(i, j) \le \epsilon_1 \quad \text{and} \quad D_{\text{temporal}}(i, j) \le \epsilon_2 \right)$$

Thus, executing `sklearn.cluster.DBSCAN(eps=1.0, min_samples=min_samples, metric="precomputed")` on $D_{\text{combined}}$ yields the **exact theoretical ST-DBSCAN clusters**, density-reachable points, and noise classification in vectorized $O(N^2)$ NumPy time.

---

## 3. Input & Output Contract Specifications

### Input Contract:
- `geojson: dict` — GeoJSON FeatureCollection or feature list containing point geometries and ISO-8601 string or numeric Epoch timestamp properties.
- `eps1_spatial_meters: float` — Maximum spatial distance threshold in meters (e.g., 500.0).
- `eps2_temporal_seconds: float` — Maximum temporal difference threshold in seconds (e.g., 3600.0).
- `min_samples: int` — Minimum neighborhood points to form a core density cluster (default: 5).
- `timestamp_field: str` — Name of property key containing timestamp (default `"timestamp"`, with fallback auto-detection).

### Output Contract (`GeoAnalysisResult`):
- `success: bool` — `True` if completed successfully, `False` on error.
- `data: dict` — GeoJSON FeatureCollection dictionary containing `"cluster_id": int` (`-1` for noise, `0..N` for clusters) and summary `"stats"`.
- `summary: str` — Narrative summary describing the discovered spatio-temporal clusters.
