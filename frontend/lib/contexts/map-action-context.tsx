'use client';

import React, { createContext, useContext, useState, useCallback, useRef } from 'react';
import type { MapActionPayload } from '@/lib/types';

export type { MapActionPayload };

export interface MapSnapshot {
  center: [number, number];
  zoom: number;
  bearing: number;
  pitch: number;
  bounds?: [number, number, number, number];
}

export interface MapActionContextType {
  actions: MapActionPayload[];
  dispatchAction: (action: MapActionPayload) => void;
  popAction: () => void;
  selectedBaseLayer: number;
  setSelectedBaseLayer: (index: number) => void;
  registerSnapshotFn: (fn: () => MapSnapshot) => void;
  getMapSnapshot: () => MapSnapshot | null;
}

export const MapActionContext = createContext<MapActionContextType | undefined>(undefined);

export function MapActionProvider({ children }: { children: React.ReactNode }) {
  const [actions, setActions] = useState<MapActionPayload[]>([]);
  const [selectedBaseLayer, setSelectedBaseLayer] = useState(0);
  const snapshotFnRef = useRef<(() => MapSnapshot) | null>(null);

  // Last action tracking for physical throttling
  const lastDispatchRef = useRef<{
    command: string;
    params: any;
    timestamp: number
  } | null>(null);

  const dispatchAction = useCallback((newAction: MapActionPayload) => {
    const now = Date.now();
    const last = lastDispatchRef.current;
    
    // Physical Throttling: Ignore identical matches within 2 seconds
    if (last &&
        last.command === newAction.command &&
        JSON.stringify(last.params) === JSON.stringify(newAction.params) &&
        (now - last.timestamp) < 2000) {
      return;
    }
    
    lastDispatchRef.current = { 
      command: newAction.command, 
      params: newAction.params, 
      timestamp: now 
    };

    setActions(prev => {
      const isDup = prev.some(a => 
        a.command === newAction.command && 
        JSON.stringify(a.params) === JSON.stringify(newAction.params)
      );
      if (isDup) return prev;
      return [...prev, newAction];
    });
  }, []);

  const popAction = useCallback(() => {
    setActions(prev => prev.slice(1));
  }, []);

  const registerSnapshotFn = useCallback((fn: () => MapSnapshot) => {
    snapshotFnRef.current = fn;
  }, []);

  const getMapSnapshot = useCallback((): MapSnapshot | null => {
    return snapshotFnRef.current?.() ?? null;
  }, []);

  return (
    <MapActionContext.Provider value={{
      actions,
      dispatchAction,
      popAction,
      selectedBaseLayer,
      setSelectedBaseLayer,
      registerSnapshotFn,
      getMapSnapshot,
    }}>
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