'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import Map, { Layer, Source, NavigationControl, FullscreenControl } from 'react-map-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { Layer as LayerType } from '../layer/layer-list';

interface MapWithLayersProps {
  layers?: LayerType[];
  onLayerSelect?: (layerId: string) => void;
  selectedLayerId?: string;
  analysisResultLayerId?: string;
  onViewChange?: (view: { zoom: number; center: [number, number] }) => void;
}

interface GeoJSONFeature {
  type: 'Feature';
  geometry: unknown;
  properties: Record<string, unknown>;
}

interface GeoJSON {
  type: 'FeatureCollection';
  features: GeoJSONFeature[];
}

const INITIAL_VIEW_STATE = {
  latitude: 35.8617,
  longitude: 104.1954,
  zoom: 4,
};

export function MapWithLayers({
  layers = [],
  onLayerSelect,
  selectedLayerId,
  analysisResultLayerId,
  onViewChange,
}: MapWithLayersProps) {
  const mapRef = useRef<Map | null>(null);
  const [mapLoaded, setMapLoaded] = useState(false);
  const [hoveredFeature, setHoveredFeature] = useState<GeoJSONFeature | null>(null);
  const [hoverCoords, setHoverCoords] = useState<[number, number] | null>(null);

  // Fetch layer data when layer changes
  const fetchLayerData = useCallback(async (layerId: string): Promise<GeoJSON | null> => {
    try {
      const response = await fetch(`/api/layers/${layerId}/data`);
      if (!response.ok) return null;
      return response.json();
    } catch (error) {
      console.error(`Failed to fetch layer ${layerId}:`, error);
      return null;
    }
  }, []);

  // Map state for all layers
  const [layerData, setLayerData] = useState<Record<string, GeoJSON | null>>({});

  // Fetch layer data when layers change
  useEffect(() => {
    const fetchAllLayers = async () => {
      const data: Record<string, GeoJSON | null> = {};
      for (const layer of layers) {
        if (layer.isVisible) {
          data[layer.id] = await fetchLayerData(layer.id);
        }
      }
      setLayerData(data);
    };

    if (layers.length > 0) {
      fetchAllLayers();
    }
  }, [layers, fetchLayerData]);

  // Zoom to layer bounds when selected
  useEffect(() => {
    if (selectedLayerId && mapRef.current) {
      const layer = layers.find(l => l.id === selectedLayerId);
      if (layer?.bounds) {
        const [minX, minY, maxX, maxY] = layer.bounds;
        mapRef.current.fitBounds(
          [[minX, minY], [maxX, maxY]],
          { padding: 50, duration: 1000 }
        );
      }
    }
  }, [selectedLayerId, layers]);

  // Zoom to analysis result
  useEffect(() => {
    if (analysisResultLayerId && mapRef.current) {
      const resultLayer = layers.find(l => l.id === analysisResultLayerId);
      if (resultLayer?.bounds) {
        const [minX, minY, maxX, maxY] = resultLayer.bounds;
        mapRef.current.fitBounds(
          [[minX, minY], [maxX, maxY]],
          { padding: 50, duration: 1000 }
        );
      }
    }
  }, [analysisResultLayerId, layers]);

  const handleMoveEnd = () => {
    if (mapRef.current && onViewChange) {
      const center = mapRef.current.getCenter();
      const zoom = mapRef.current.getZoom();
      onViewChange({ zoom, center: [center.lng, center.lat] });
    }
  };

  const handleMouseEnter = (e: unknown) => {
    const event = e as { features?: Array<{ properties: Record<string, unknown>; geometry: unknown }> };
    if (event.features && event.features.length > 0) {
      setHoveredFeature(event.features[0]);
      setHoverCoords(event.lngLat as [number, number]);
    }
  };

  const handleMouseLeave = () => {
    setHoveredFeature(null);
    setHoverCoords(null);
  };

  // Generate layer styles based on feature type
  const getLayerStyle = (layerId: string, opacity: number) => {
    const isResultLayer = layerId === analysisResultLayerId;
    const baseColor = isResultLayer ? '#ef4444' : '#3b82f6';

    return {
      id: layerId,
      source: layerId,
      type: 'fill' as const,
      paint: {
        'fill-color': baseColor,
        'fill-opacity': ['interpolate', ['linear'], ['get', 'opacity'], 0, opacity, 1, opacity],
        'fill-outline-color': baseColor,
      },
      filter: ['==', '$type', 'Polygon'],
    };
  };

  const getLineLayerStyle = (layerId: string, opacity: number) => ({
    id: `${layerId}-line`,
    source: layerId,
    type: 'line' as const,
    paint: {
      'line-color': '#3b82f6',
      'line-width': 2,
      'line-opacity': opacity,
    },
    filter: ['==', '$type', 'LineString'],
  });

  const getPointLayerStyle = (layerId: string) => ({
    id: `${layerId}-point`,
    source: layerId,
    type: 'circle' as const,
    paint: {
      'circle-radius': 8,
      'circle-color': '#3b82f6',
      'circle-stroke-width': 2,
      'circle-stroke-color': '#ffffff',
    },
    filter: ['==', '$type', 'Point'],
  });

  return (
    <div className="w-full h-full relative">
      <Map
        ref={mapRef as unknown as React.LegacyRef<Map>}
        initialViewState={INITIAL_VIEW_STATE}
        style={{ width: '100%', height: '100%' }}
        mapStyle="https://demotiles.maplibre.org/style.json"
        onLoad={() => setMapLoaded(true)}
        onMoveEnd={handleMoveEnd}
        interactiveLayerIds={layers.map(l => l.id)}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
      >
        <NavigationControl position="top-right" />
        <FullscreenControl position="top-right" />

        {/* Render visible layers */}
        {layers.filter(l => l.isVisible).map(layer => {
          const data = layerData[layer.id];
          if (!data) return null;

          const opacity = layer.opacity ?? 1;

          return (
            <React.Fragment key={layer.id}>
              {data.features.some(f => f.geometry?.type === 'Polygon') && (
                <Layer
                  {...getLayerStyle(layer.id, opacity)}
                  onHover={handleMouseEnter}
                />
              )}
              {data.features.some(f => f.geometry?.type === 'LineString') && (
                <Layer {...getLineLayerStyle(layer.id, opacity)} />
              )}
              {data.features.some(f => f.geometry?.type === 'Point') && (
                <Layer {...getPointLayerStyle(layer.id)} />
              )}
            </React.Fragment>
          );
        })}
      </Map>

      {/* Hover Tooltip */}
      {hoveredFeature && hoverCoords && (
        <div
          className="absolute z-10 bg-white border border-gray-200 rounded-lg shadow-lg p-3 max-w-xs text-xs"
          style={{
            left: hoverCoords[0] + 20,
            top: hoverCoords[1] + 20,
          }}
        >
          <h4 className="font-semibold mb-2">属性信息</h4>
          <div className="space-y-1">
            {Object.entries(hoveredFeature.properties || {}).slice(0, 5).map(([key, value]) => (
              <div key={key} className="flex justify-between gap-4">
                <span className="text-gray-500">{key}:</span>
                <span className="text-gray-900 truncate">{String(value)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Loading Overlay */}
      {!mapLoaded && (
        <div className="absolute inset-0 bg-gray-100 flex items-center justify-center">
          <div className="text-center">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mx-auto mb-4" />
            <p className="text-gray-600">加载地图...</p>
          </div>
        </div>
      )}
    </div>
  );
}

import React from 'react';
