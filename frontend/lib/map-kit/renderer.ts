import maplibregl from 'maplibre-gl';

/**
 * Safely adds or updates a GeoJSON source.
 */
export function addGeoJsonSource(map: any, id: string, data: any) {
  const source = map.getSource(id) as maplibregl.GeoJSONSource;
  if (source) {
    source.setData(data);
  } else {
    map.addSource(id, {
      type: 'geojson',
      data
    });
  }
}
