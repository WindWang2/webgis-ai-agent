# Design Doc: Spatial Statistics & Pattern Discovery (Insight Ready)

**Topic:** Spatial Statistics Implementation
**Date:** 2026-05-19
**Status:** Approved

## Overview
Implement `app/lib/geoprocessing/statistics.py` providing LLM-optimized spatial analysis tools: Standard Deviational Ellipse (SDE), Moran's I, and Getis-Ord Gi* Hotspot Analysis. These tools return `GeoAnalysisResult` with narrative summaries suitable for LLM consumption.

## Requirements
1. **`calculate_sde(geojson)`**:
   - Measures spatial dispersion and trend.
   - Returns a `GeoAnalysisResult` containing the ellipse polygon.
   - Summary includes a "Directional Insight".

2. **`moran_i_narrated(geojson, value_field)`**:
   - Global spatial autocorrelation.
   - Returns statistical metrics and a narrative summary explaining the distribution pattern.

3. **`hotspot_narrated(geojson, value_field)`**:
   - Local hotspot analysis (Getis-Ord Gi*).
   - Returns a FeatureCollection with Gi* scores.
   - Summary highlights the number of significant hot/cold spots.

## Architecture & Logic

### 1. Standard Deviational Ellipse (SDE)
- **Coordinate Conversion**: Use `to_utm_gdf` to ensure calculations are in meters.
- **Mean Center**: $(\bar{x}, \bar{y})$.
- **Rotation Angle** $\theta$:
  $$ \tan(2\theta) = \frac{2 \sum x'_i y'_i}{(\sum {x'_i}^2 - \sum {y'_i}^2)} $$
- **Standard Deviations**:
  $$ \sigma_x = \sqrt{\frac{2 \sum (x'_i \cos\theta - y'_i \sin\theta)^2}{n}} $$
  $$ \sigma_y = \sqrt{\frac{2 \sum (x'_i \sin\theta + y'_i \cos\theta)^2}{n}} $$
- **Insight Generation**: Map $\theta$ to compass directions and compare $\sigma_x / \sigma_y$ for "elongation" intensity.

### 2. Moran's I
- **Weights Matrix**: KNN (k=8) weights.
- **Null Hypothesis**: Spatial randomness.
- **Narrative**:
  - If $p < 0.05$ and $I > E[I]$: "Statistically significant clustering of similar values."
  - If $p < 0.05$ and $I < E[I]$: "Statistically significant spatial dispersion (dissimilar values are adjacent)."
  - Else: "No significant spatial pattern detected; distribution appears random."

### 3. Hotspot Analysis (Getis-Ord Gi*)
- **Weights Matrix**: Distance-band weights.
- **Metrics**: $Z$-score and $p$-value for each feature.
- **Classification**:
  - $Z > 1.96$ ($95\%$ confidence): Hot Spot.
  - $Z < -1.96$ ($95\%$ confidence): Cold Spot.

## Testing Strategy
- **File**: `tests/unit/geoprocessing/test_statistics.py`.
- **Cases**:
  - SDE: Points along a N-S line should produce a N-S oriented ellipse.
  - Moran's I: Artificial clustered data (all high values in one corner).
  - Hotspot: Group of high values surrounded by low values.
