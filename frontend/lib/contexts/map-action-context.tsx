'use client';

import React, { createContext, useContext, useState, useCallback } from 'react';

export interface MapActionPayload {
  command: 'add_layer' | 'remove_layer' | 'fly_to';
  params: {
    layerId?: string;
    type?: 'fill' | 'line' | 'circle' | 'symbol';
    geojson?: any;
    style?: any;
    flyTo?: boolean;
    center?: [number, number];
    zoom?: number;
  };
}

export interface MapActionContextType {
  action: MapActionPayload | null;
  dispatchAction: (action: MapActionPayload) => void;
  clearAction: () => void;
}

export const MapActionContext = createContext<MapActionContextType | undefined>(undefined);

export function MapActionProvider({ children }: { children: React.ReactNode }) {
  const [action, setAction] = useState<MapActionPayload | null>(null);

  const dispatchAction = useCallback((newAction: MapActionPayload) => {
    console. log('[MapAction] Dispatching:', newAction);
    setAction(newAction);
  }, []);

  const clearAction = useCallback(() => {
    setAction(null);
  }, []);

  return (
    <MapActionContext.Provider value={{ action, dispatchAction, clearAction }}>
      {children}
    </MapActionContext.Provider>
  );
}

export default MapActionProvider;

export function useMapAction() {
  const context = useContext(MapActionContext);
  if (context === undefined) {
    throw new Error('useMapAction must be used within a MapActionProvider');
  }
  return context;
}