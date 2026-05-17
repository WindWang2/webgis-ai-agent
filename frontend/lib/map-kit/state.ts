import type { Map, PointLike } from 'maplibre-gl';
import type { GeoAnalysisResult } from './types';

/**
 * Queries rendered features at a specific point on the map.
 * Returns a summary of the features found.
 */
export function queryFeaturesAt(
  map: Map,
  point: PointLike,
  layers?: string[]
): GeoAnalysisResult {
  const features = map.queryRenderedFeatures(point, { layers });

  if (!features || features.length === 0) {
    return {
      success: true,
      data: [],
      summary: "No features found at this location."
    };
  }

  const featureSummaries = features.map(f => {
    const name = f.properties?.name || f.properties?.title || f.properties?.label || `Feature ${f.id ?? ''}`;
    return `'${name}'`;
  });

  const uniqueSummaries = Array.from(new Set(featureSummaries));
  const summary = `Found ${features.length} feature(s): ${uniqueSummaries.join(', ')}.`;

  return {
    success: true,
    data: features,
    summary
  };
}
