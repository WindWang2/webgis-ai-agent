import numpy as np
import pandas as pd
import geopandas as gpd
from scipy import sparse
from shapely.geometry import Point, Polygon, mapping
from scipy.spatial import distance_matrix
from scipy.stats import norm
from app.lib.geo_processor.core import GeoAnalysisResult
from app.lib.geo_processor.core import to_utm_gdf, safe_parse

def _build_weights(gdf, k=8):
    """Build spatial weights matrix using KNN via cKDTree.
    
    Returns a sparse COO matrix (n×n) with 1.0 for K-nearest neighbors.
    Uses O(n log n) cKDTree query instead of O(n²) distance_matrix.
    """
    from scipy.spatial import cKDTree
    coords = np.column_stack((gdf.centroid.x.values, gdf.centroid.y.values))
    n = len(coords)
    if n == 0:
        return sparse.coo_matrix((0, 0))
    k_actual = min(k, n - 1) if n > 1 else 1
    tree = cKDTree(coords)
    # query returns k+1 neighbors including self at index 0
    _, idx = tree.query(coords, k=k_actual + 1)
    # Build sparse matrix: skip self (column 0)
    rows = np.repeat(np.arange(n), k_actual)
    cols = idx[:, 1:].ravel()
    data = np.ones(len(rows), dtype=float)
    return sparse.coo_matrix((data, (rows, cols)), shape=(n, n))

def _extract_numeric_values(gdf, value_field):
    """Helper to extract numeric values from a GDF field."""
    if value_field not in gdf.columns:
        return None
    values = gdf[value_field]
    if not np.issubdtype(values.dtype, np.number):
        # Try converting to numeric
        values = pd.to_numeric(values, errors='coerce')
    return values.dropna().values

def calculate_sde(geojson: dict) -> GeoAnalysisResult:
    """
    Calculate the Standard Deviational Ellipse (SDE) for a set of points.
    Returns a GeoAnalysisResult with the ellipse polygon and a directional insight.
    """
    res = to_utm_gdf(geojson)
    if not res:
        return GeoAnalysisResult(False, None, "Invalid input or no features found", error_type="ValueError")
    
    gdf, utm_crs = res
    if len(gdf) < 3:
        return GeoAnalysisResult(False, None, "At least 3 points required", error_type="InsufficientData")

    # Ensure we only work with point geometries for SDE
    points = gdf[gdf.geometry.type == 'Point']
    if len(points) < 3:
        # Try to use centroids if they aren't all points
        coords = np.array([(g.centroid.x, g.centroid.y) for g in gdf.geometry])
    else:
        coords = np.array([(g.x, g.y) for g in points.geometry])
        
    n = len(coords)
    mean_x, mean_y = coords.mean(axis=0)
    x_prime = coords[:, 0] - mean_x
    y_prime = coords[:, 1] - mean_y

    sum_x2 = np.sum(x_prime**2)
    sum_y2 = np.sum(y_prime**2)
    sum_xy = np.sum(x_prime * y_prime)

    # Angle calculation
    delta = sum_x2 - sum_y2
    if delta == 0:
        theta = np.pi / 4 if sum_xy > 0 else 0
    else:
        theta = 0.5 * np.arctan2(2 * sum_xy, delta)

    # Standard deviations along the rotated axes
    sin_t = np.sin(theta)
    cos_t = np.cos(theta)
    
    sigma_x_2 = 2 * np.sum((x_prime * cos_t + y_prime * sin_t)**2) / n
    sigma_y_2 = 2 * np.sum((x_prime * sin_t - y_prime * cos_t)**2) / n
    
    sigma_x = np.sqrt(max(sigma_x_2, 0))
    sigma_y = np.sqrt(max(sigma_y_2, 0))

    # Create ellipse polygon
    t = np.linspace(0, 2*np.pi, 100)
    ell_x = sigma_x * np.cos(t)
    ell_y = sigma_y * np.sin(t)
    
    rot_x = mean_x + ell_x * cos_t - ell_y * sin_t
    rot_y = mean_y + ell_x * sin_t + ell_y * cos_t
    
    ellipse_poly = Polygon(np.column_stack([rot_x, rot_y]))
    if not ellipse_poly.is_valid:
        ellipse_poly = ellipse_poly.buffer(0)
        
    ellipse_wgs84 = gpd.GeoSeries([ellipse_poly], crs=utm_crs).to_crs("EPSG:4326").iloc[0]
    
    deg = np.degrees(theta) % 180
    if 67.5 <= deg < 112.5: 
        direction = "North-South"
    elif 22.5 <= deg < 67.5: 
        direction = "North-East to South-West"
    elif 112.5 <= deg < 157.5: 
        direction = "North-West to South-East"
    else: 
        direction = "East-West"
    
    area_km2 = ellipse_poly.area / 1e6
    summary = f"Directional Insight: The points show a clear {direction} directional trend, covering an area of {area_km2:.2f} sq km."
    
    data_out = {
        "type": "Feature",
        "geometry": mapping(ellipse_wgs84),
        "properties": {
            "center": [float(mean_x), float(mean_y)],
            "sigma_x": float(sigma_x),
            "sigma_y": float(sigma_y),
            "angle_deg": float(deg),
            "area_km2": float(area_km2),
            "direction": direction
        }
    }
    
    return GeoAnalysisResult(True, data_out, summary)

def moran_i_narrated(geojson: dict, value_field: str) -> GeoAnalysisResult:
    """
    Global Moran's I spatial autocorrelation test with narrative summary.
    """
    res = to_utm_gdf(geojson)
    if not res:
        return GeoAnalysisResult(False, None, "Invalid GeoJSON or no features found")
    
    gdf, _ = res
    values = _extract_numeric_values(gdf, value_field)
    if values is None or len(values) == 0:
        return GeoAnalysisResult(False, None, f"Field '{value_field}' missing or non-numeric")
    
    n = len(values)
    if n < 3:
        return GeoAnalysisResult(False, None, "At least 3 features required for Moran's I")
    
    w = _build_weights(gdf, k=min(8, n-1))
    w_sum = float(w.sum())
    if w_sum == 0:
        return GeoAnalysisResult(False, None, "Spatial weights matrix is empty")
    
    z = values - values.mean()
    s0 = w_sum
    # _build_weights returns a sparse COO matrix; convert to dense for
    # element-wise multiplication with the outer product (Moran's I formula).
    w_dense = w.toarray() if sparse.issparse(w) else w
    numerator = float(np.sum(w_dense * np.outer(z, z)))
    denominator = np.sum(z**2)
    
    moran_i_val = (n / s0) * (numerator / denominator) if denominator > 0 else 0
    expected_i = -1.0 / (n - 1)
    
    # Simplified permutation test for p-value
    rng = np.random.default_rng(42)
    perms = 99
    perm_is = []
    for _ in range(perms):
        pv = rng.permutation(values)
        pz = pv - pv.mean()
        p_num = np.sum(w * np.outer(pz, pz))
        p_den = np.sum(pz**2)
        perm_is.append((n / s0) * (p_num / p_den) if p_den > 0 else 0)
    
    p_value = float(np.mean(np.abs(np.array(perm_is) - expected_i) >= np.abs(moran_i_val - expected_i)))
    
    if p_value < 0.05:
        pattern = "clustering" if moran_i_val > expected_i else "dispersion"
    else:
        pattern = "random"
        
    if pattern == "clustering":
        narrative = f"There is a statistically significant clustering of {value_field} values (Moran's I: {moran_i_val:.4f}, p = {p_value:.4f}). Similar values tend to be near each other."
    elif pattern == "dispersion":
        narrative = f"There is a statistically significant spatial dispersion of {value_field} values (Moran's I: {moran_i_val:.4f}, p = {p_value:.4f}). High and low values tend to be alternated."
    else:
        narrative = f"The distribution of {value_field} appears to be spatially random (Moran's I: {moran_i_val:.4f}, p = {p_value:.4f}). No clear spatial pattern was detected."
    
    data_out = {
        "moran_i": float(moran_i_val),
        "expected_i": float(expected_i),
        "p_value": float(p_value),
        "pattern": pattern,
        "n_features": n
    }
    
    return GeoAnalysisResult(True, data_out, narrative)

def hotspot_narrated(geojson: dict, value_field: str, distance_band: float = 0) -> GeoAnalysisResult:
    """
    Getis-Ord Gi* local spatial autocorrelation (hotspot analysis) with narrative summary.
    """
    res = to_utm_gdf(geojson)
    if not res:
        return GeoAnalysisResult(False, None, "Invalid GeoJSON or no features found")
    
    gdf, utm_crs = res
    values = _extract_numeric_values(gdf, value_field)
    if values is None or len(values) == 0:
        return GeoAnalysisResult(False, None, f"Field '{value_field}' missing or non-numeric")
    
    n = len(values)
    if n < 3:
        return GeoAnalysisResult(False, None, "At least 3 features required for hotspot analysis")
    
    coords = np.column_stack((gdf.centroid.x.values, gdf.centroid.y.values))
    
    if distance_band <= 0:
        # Auto-calculate distance band using cKDTree (O(n log n) instead of O(n²))
        from scipy.spatial import cKDTree
        tree = cKDTree(coords)
        nn_dist, _ = tree.query(coords, k=2)
        bw = float(nn_dist[:, 1].mean())
        if bw <= 0:
            bw = 1.0
    else:
        bw = distance_band
    
    # Build binary weights matrix using cKDTree sparse distance matrix
    from scipy.spatial import cKDTree
    tree = cKDTree(coords)
    w_sparse = tree.sparse_distance_matrix(tree, max_distance=bw, output_type="coo_matrix")
    w = np.zeros((n, n))
    w[w_sparse.row, w_sparse.col] = 1.0
    np.fill_diagonal(w, 0)
    
    x_bar = values.mean()
    s = values.std(ddof=0)
    if s == 0:
        return GeoAnalysisResult(False, None, "All values are identical, cannot perform hotspot analysis")
    
    # Vectorized Gi* computation (audit S40: O(n) instead of O(n) Python loop)
    sum_wi = w.sum(axis=1)
    sum_wi2 = (w ** 2).sum(axis=1)
    numerators = w @ values - x_bar * sum_wi
    denom_inners = (n * sum_wi2 - sum_wi**2) / (n - 1)
    denominators = np.where(denom_inners > 0, s * np.sqrt(denom_inners), 0)
    gi_stars = np.where(denominators != 0, numerators / denominators, 0)
    p_vals = 2 * (1 - norm.cdf(np.abs(gi_stars)))
    
    hot_count = int(np.sum((p_vals < 0.05) & (gi_stars > 0)))
    cold_count = int(np.sum((p_vals < 0.05) & (gi_stars < 0)))
    
    # Batch reproject once (audit S40: O(1) instead of O(n) CRS transforms)
    gdf_wgs84 = gdf.to_crs("EPSG:4326")
    
    features = []
    for i in range(len(gdf)):
        gi_star = float(gi_stars[i])
        p_val = float(p_vals[i])
        
        h_type = "Not Significant"
        confidence = "Not Significant"
        
        if p_val < 0.05:
            h_type = "Hot Spot" if gi_star > 0 else "Cold Spot"
            confidence = "99%" if p_val < 0.01 else "95%"
        elif p_val < 0.1:
            h_type = "Hot Spot" if gi_star > 0 else "Cold Spot"
            confidence = "90%"
            
        geom_wgs84 = gdf_wgs84.geometry.iloc[i]
        row = gdf.iloc[i]
        props = {k: v for k, v in row.items() if k != "geometry"}
        props.update({
            "gi_star": round(gi_star, 4),
            "p_value": round(p_val, 6),
            "hotspot_type": h_type,
            "confidence": confidence
        })
        
        features.append({
            "type": "Feature",
            "geometry": mapping(geom_wgs84),
            "properties": props
        })
        
    summary = f"Hotspot analysis identified {hot_count} statistically significant hot spots and {cold_count} cold spots."
    if hot_count > 0 or cold_count > 0:
        summary += f" Significant clusters of high/low values were detected using a distance band of {bw:.1f} meters."
    else:
        summary += " No significant hotspots were detected at the 90% confidence level."
        
    data_out = {
        "type": "FeatureCollection",
        "features": features,
        "hot_spots_count": hot_count,
        "cold_spots_count": cold_count,
        "distance_band_m": round(bw, 2)
    }
    
    return GeoAnalysisResult(True, data_out, summary)

def calculate_nearest(geojson: dict) -> GeoAnalysisResult:
    """Nearest neighbor analysis with narrative summary (O(n log n) via cKDTree)."""
    from scipy.spatial import cKDTree
    res = to_utm_gdf(geojson)
    if not res:
        return GeoAnalysisResult(False, None, "Invalid input or no features found")
    
    gdf, _ = res
    if len(gdf) < 2:
        return GeoAnalysisResult(False, None, "At least 2 points required for nearest neighbor analysis")
    
    coords = np.column_stack((gdf.centroid.x.values, gdf.centroid.y.values))
    tree = cKDTree(coords)
    nn_dist, _ = tree.query(coords, k=2)  # k=1 is self (dist=0), k=2 is true nearest neighbor
    nn_dist = nn_dist[:, 1]
    
    mean_dist = float(nn_dist.mean())
    std_dist = float(nn_dist.std())
    
    # Simple pattern recognition
    # Expected mean distance for random distribution (Poisson process)
    # R = Observed / Expected
    # Expected = 0.5 * sqrt(Area / N)
    xmin, ymin, xmax, ymax = gdf.total_bounds
    area = (xmax - xmin) * (ymax - ymin)
    expected_mean = 0.5 * np.sqrt(area / len(gdf))
    r_ratio = mean_dist / expected_mean if expected_mean > 0 else 1
    
    pattern = "random"
    if r_ratio < 0.7: pattern = "clustered"
    elif r_ratio > 1.3: pattern = "dispersed"
    
    summary = f"Nearest Neighbor Insight: The mean distance to the nearest neighbor is {mean_dist:.2f} meters. The distribution pattern appears to be {pattern} (R ratio: {r_ratio:.2f})."
    
    data = {
        "mean_distance": mean_dist,
        "std_distance": std_dist,
        "min_distance": float(nn_dist.min()),
        "max_distance": float(nn_dist.max()),
        "r_ratio": r_ratio,
        "pattern": pattern
    }
    return GeoAnalysisResult(True, data, summary)

def calculate_central_feature(geojson: dict, method: str = "mean_center") -> GeoAnalysisResult:
    """Find the central feature or mean center."""
    res = to_utm_gdf(geojson)
    if not res:
        return GeoAnalysisResult(False, None, "Invalid input or no features found")
    
    gdf, utm_crs = res
    coords = np.array([(g.centroid.x, g.centroid.y) for g in gdf.geometry])
    
    if method == "mean_center":
        mc = coords.mean(axis=0)
        center_pt = Point(mc[0], mc[1])
        summary = f"Mean Center: The average geographic center is at {mc[0]:.2f}, {mc[1]:.2f} (UTM)."
    else:
        # Central Feature: point with minimum total distance to all other points
        # Use batched cKDTree queries to avoid O(n²) memory allocation.
        from scipy.spatial import cKDTree
        n = len(coords)
        # Guard: central_feature requires all-pairs distances; cap at 5000 features
        if n > 5000:
            return GeoAnalysisResult(
                False, None,
                f"Too many features ({n}) for central_feature analysis (max 5000). Use mean_center instead.",
                error_type="InsufficientData",
            )
        tree = cKDTree(coords)
        # Batch query: avoid allocating full n×n distance matrix at once
        batch_size = 500
        dist_sums = np.zeros(n)
        for start in range(0, n, batch_size):
            end = min(start + batch_size, n)
            dists, _ = tree.query(coords[start:end], k=n)
            # Zero self-distances (first column per row)
            for offset, i in enumerate(range(start, end)):
                dists[offset, i] = 0.0
            dist_sums[start:end] = dists.sum(axis=1)
        idx = int(np.argmin(dist_sums))
        center_pt = gdf.geometry.iloc[idx]
        summary = f"Central Feature: The feature at index {idx} is identified as the central feature (minimum total distance to others)."
        
    center_wgs84 = gpd.GeoSeries([center_pt], crs=utm_crs).to_crs("EPSG:4326").iloc[0]
    data = {
        "type": "Feature",
        "geometry": mapping(center_wgs84),
        "properties": {"method": method, "summary": summary}
    }
    return GeoAnalysisResult(True, data, summary)

def cluster_narrated(
    geojson: dict,
    method: str = "dbscan",
    n_clusters: int = 5,
    eps: float = 1000,
    min_samples: int = 5,
    value_field: str = ""
) -> GeoAnalysisResult:
    """
    Perform spatial clustering (DBSCAN or K-Means) with narrative summary.
    """
    try:
        from sklearn.cluster import DBSCAN, KMeans
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        return GeoAnalysisResult(False, None, "scikit-learn not installed")

    res = to_utm_gdf(geojson)
    if not res:
        return GeoAnalysisResult(False, None, "Invalid input or no features found")
    
    gdf, utm_crs = res
    if len(gdf) < 3:
        return GeoAnalysisResult(False, None, "At least 3 features required for clustering")

    coords = np.array([(g.centroid.x, g.centroid.y) for g in gdf.geometry])

    if value_field:
        vals = _extract_numeric_values(gdf, value_field)
        if vals is None:
            return GeoAnalysisResult(False, None, f"Field '{value_field}' is not numeric")
        scaler = StandardScaler()
        vals_scaled = scaler.fit_transform(vals.reshape(-1, 1))
        features = np.column_stack([coords, vals_scaled])
    else:
        features = coords

    if method == "kmeans":
        model = KMeans(n_clusters=min(n_clusters, len(gdf)), random_state=42, n_init=10)
        labels = model.fit_predict(features)
        summary = f"K-Means clustering identified {len(set(labels))} groups."
    else:
        model = DBSCAN(eps=eps, min_samples=min_samples)
        labels = model.fit_predict(features)
        n_clusters_found = len(set(labels)) - (1 if -1 in labels else 0)
        n_noise = list(labels).count(-1)
        summary = f"DBSCAN identified {n_clusters_found} clusters and {n_noise} noise points."

    # Batch reproject once (audit S40)
    gdf_wgs84 = gdf.to_crs("EPSG:4326")
    
    out_features = []
    for i in range(len(gdf)):
        geom_wgs84 = gdf_wgs84.geometry.iloc[i]
        row = gdf.iloc[i]
        props = {k: v for k, v in row.items() if k != "geometry"}
        props["cluster_id"] = int(labels[i])
        out_features.append({
            "type": "Feature",
            "geometry": mapping(geom_wgs84),
            "properties": props,
        })

    cluster_counts = dict(zip(*np.unique(labels, return_counts=True)))

    data_out = {
        "type": "FeatureCollection",
        "features": out_features,
        "cluster_stats": cluster_counts,
        "method": method,
        "n_clusters": len(set(labels)) - (1 if -1 in labels else 0),
    }

    return GeoAnalysisResult(True, data_out, summary)

def h3_lisa(h3_geojson: dict, value_field: str) -> GeoAnalysisResult:
    """
    Calculate Local Indicators of Spatial Association (LISA) for H3 hex bins.
    """
    try:
        from libpysal.weights import Queen
        from esda.moran import Moran_Local
    except ImportError:
        return GeoAnalysisResult(False, None, "libpysal or esda not installed", error_type="ImportError")

    res = to_utm_gdf(h3_geojson)
    if not res:
        return GeoAnalysisResult(False, None, "Invalid GeoJSON or no features found", error_type="ValueError")
    
    gdf, utm_crs = res
    values = _extract_numeric_values(gdf, value_field)
    if values is None or len(values) == 0:
        return GeoAnalysisResult(False, None, f"Field '{value_field}' missing or non-numeric", error_type="ValueError")
    
    if len(values) < 3:
        return GeoAnalysisResult(False, None, "At least 3 features required for LISA", error_type="InsufficientData")

    # Use original geometries (hexagons) to build weights
    # We must ensure there is no index duplication
    gdf = gdf.reset_index(drop=True)
    w = Queen.from_dataframe(gdf)
    w.transform = 'r'
    
    # Calculate LISA
    lisa = Moran_Local(values, w)
    
    # Assign clusters
    clusters = []
    cluster_counts = {"HH": 0, "LL": 0, "HL": 0, "LH": 0, "NS": 0}
    for i, p in enumerate(lisa.p_sim):
        if p < 0.05:
            q = lisa.q[i]
            if q == 1:
                c = "HH"
            elif q == 2:
                c = "LH"
            elif q == 3:
                c = "LL"
            elif q == 4:
                c = "HL"
            else:
                c = "NS"
        else:
            c = "NS"
        clusters.append(c)
        cluster_counts[c] += 1
        
    # Batch reproject once (audit S40)
    gdf_wgs84 = gdf.to_crs("EPSG:4326")
    
    out_features = []
    for i in range(len(gdf)):
        geom_wgs84 = gdf_wgs84.geometry.iloc[i]
        row = gdf.iloc[i]
        props = {k: v for k, v in row.items() if k != "geometry"}
        props["lisa_cluster"] = clusters[i]
        out_features.append({
            "type": "Feature",
            "geometry": mapping(geom_wgs84),
            "properties": props,
        })
        
    summary_parts = []
    if cluster_counts["HH"] > 0:
        summary_parts.append(f"{cluster_counts['HH']} High-High hotspots")
    if cluster_counts["LL"] > 0:
        summary_parts.append(f"{cluster_counts['LL']} Low-Low coldspots")
    if cluster_counts["HL"] > 0:
        summary_parts.append(f"{cluster_counts['HL']} High-Low spatial outliers")
    if cluster_counts["LH"] > 0:
        summary_parts.append(f"{cluster_counts['LH']} Low-High spatial outliers")
        
    if summary_parts:
        summary = "Found " + ", ".join(summary_parts) + "."
        
        # Determine dominant pattern
        # Excluding NS
        sig_counts = {k: v for k, v in cluster_counts.items() if k != "NS" and v > 0}
        if sig_counts:
            dominant = max(sig_counts, key=sig_counts.get)
            dom_name = {"HH": "High-High clustering", "LL": "Low-Low clustering", "HL": "High-Low outliers", "LH": "Low-High outliers"}
            summary += f" Dominant pattern is {dom_name[dominant]}."
    else:
        summary = "No significant local spatial autocorrelation found."
        
    data_out = {
        "type": "FeatureCollection",
        "features": out_features,
        "cluster_stats": cluster_counts
    }
    
    return GeoAnalysisResult(True, data_out, summary)
