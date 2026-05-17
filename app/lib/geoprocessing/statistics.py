import numpy as np
import geopandas as gpd
from shapely.geometry import Point, Polygon, mapping
from scipy.spatial import distance_matrix
from scipy.stats import norm
from app.lib.geoprocessing.interface import GeoAnalysisResult
from app.tools._geojson_utils import to_utm_gdf, safe_parse_geojson, extract_numeric_values

def _build_weights(gdf, k=8):
    """Build spatial weights matrix using KNN."""
    coords = np.array([(g.centroid.x, g.centroid.y) for g in gdf.geometry])
    n = len(coords)
    dist = distance_matrix(coords, coords)
    w = np.zeros((n, n))
    for i in range(n):
        # Find indices of k nearest neighbors (excluding self)
        idx = np.argsort(dist[i])[1:k+1]
        w[i, idx] = 1.0
    return w

def calculate_sde(geojson: dict) -> GeoAnalysisResult:
    """
    Calculate the Standard Deviational Ellipse (SDE) for a set of points.
    Returns a GeoAnalysisResult with the ellipse polygon and a directional insight.
    """
    data = safe_parse_geojson(geojson)
    res = to_utm_gdf(data)
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
    data = safe_parse_geojson(geojson)
    res = to_utm_gdf(data)
    if not res:
        return GeoAnalysisResult(False, None, "Invalid GeoJSON or no features found")
    
    gdf, _ = res
    values = extract_numeric_values(gdf, value_field)
    if values is None:
        return GeoAnalysisResult(False, None, f"Field '{value_field}' missing or non-numeric")
    
    n = len(values)
    if n < 3:
        return GeoAnalysisResult(False, None, "At least 3 features required for Moran's I")
    
    w = _build_weights(gdf, k=min(8, n-1))
    w_sum = w.sum()
    if w_sum == 0:
        return GeoAnalysisResult(False, None, "Spatial weights matrix is empty")
    
    z = values - values.mean()
    s0 = w_sum
    numerator = np.sum(w * np.outer(z, z))
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
    data = safe_parse_geojson(geojson)
    res = to_utm_gdf(data)
    if not res:
        return GeoAnalysisResult(False, None, "Invalid GeoJSON or no features found")
    
    gdf, utm_crs = res
    values = extract_numeric_values(gdf, value_field)
    if values is None:
        return GeoAnalysisResult(False, None, f"Field '{value_field}' missing or non-numeric")
    
    n = len(values)
    if n < 3:
        return GeoAnalysisResult(False, None, "At least 3 features required for hotspot analysis")
    
    coords = np.array([(g.centroid.x, g.centroid.y) for g in gdf.geometry])
    dist = distance_matrix(coords, coords)
    
    if distance_band <= 0:
        # Auto-calculate distance band: average nearest neighbor distance
        np.fill_diagonal(dist, np.inf)
        bw = float(np.mean(np.min(dist, axis=1)))
        if bw <= 0:
            bw = 1.0
    else:
        bw = distance_band
        
    w = (dist <= bw).astype(float)
    # Getis-Ord Gi* usually includes the feature itself in the local sum (Gi*)
    # So we don't zero the diagonal for Gi*.
    # Actually, Gi* includes i, while Gi doesn't.
    
    x_bar = values.mean()
    s = values.std(ddof=0)
    if s == 0:
        return GeoAnalysisResult(False, None, "All values are identical, cannot perform hotspot analysis")
    
    hot_count = 0
    cold_count = 0
    features = []
    
    for i, row in gdf.iterrows():
        wi = w[i]
        sum_wi = np.sum(wi)
        sum_wi2 = np.sum(wi**2)
        
        # Getis-Ord Gi* formula
        numerator = np.sum(wi * values) - x_bar * sum_wi
        # Standard error term
        denom_inner = (n * sum_wi2 - sum_wi**2) / (n - 1)
        denominator = s * np.sqrt(denom_inner) if denom_inner > 0 else 0
        
        gi_star = float(numerator / denominator) if denominator != 0 else 0
        p_val = 2 * (1 - norm.cdf(abs(gi_star)))
        
        h_type = "Not Significant"
        confidence = "Not Significant"
        
        if p_val < 0.05:
            h_type = "Hot Spot" if gi_star > 0 else "Cold Spot"
            if p_val < 0.01:
                confidence = "99%"
            else:
                confidence = "95%"
        elif p_val < 0.1:
            h_type = "Hot Spot" if gi_star > 0 else "Cold Spot"
            confidence = "90%"
            
        if h_type == "Hot Spot":
            hot_count += 1
        elif h_type == "Cold Spot":
            cold_count += 1
            
        geom_wgs84 = gpd.GeoSeries([row.geometry], crs=utm_crs).to_crs("EPSG:4326").iloc[0]
        
        props = {k: v for k, v in row.items() if k != "geometry"}
        props.update({
            "gi_star": round(gi_star, 4),
            "p_value": round(float(p_val), 6),
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
