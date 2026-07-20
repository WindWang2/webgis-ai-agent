'use client';

import React, { createContext, useContext, useState, useCallback, useRef } from 'react';
import type { MapActionPayload } from '@/lib/types';
import { useHudStore } from '@/lib/store/useHudStore';

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
  // 审计 F34：lazy init 从持久化的 useHudStore.baseLayer name 反查 index，
  // 防刷新后 index 重置为 1 与持久化 name 不一致 -> 底图闪烁。
  const [selectedBaseLayer, setSelectedBaseLayer] = useState<number>(() => {
    try {
      const persistedName = useHudStore.getState().baseLayer;
      // 动态 import 避免 circular dep；TILE_PROVIDERS 在 providers.ts
      const { TILE_PROVIDERS } = require('@/lib/providers');
      const idx = TILE_PROVIDERS.findIndex(
        (p: any) => p.name === persistedName || p.name === 'Carto 深色'
      );
      return idx >= 0 ? idx : 1;
    } catch {
      return 1;
    }
  });
  const snapshotFnRef = useRef<(() => MapSnapshot) | null>(null);

  // Last fly_to tracking for physical throttling.
  // 审计 F21：之前对每个命令都用 JSON.stringify 做去重，问题：
  //   (1) key 顺序不同 → 同义动作漏过；
  //   (2) export_map 等大 payload 每次都要序列化，浪费；
  //   (3) AI 连续两次同义指令被静默丢弃（可能本意是 refresh）。
  // 现在：仅对 fly_to 做 2 秒节流（最常见的中途重复），且只比较 center+zoom；
  // 其他命令直接入队，MapActionHandler 本就顺序消费，不需要前端去重。
  const lastFlyToRef = useRef<{
    centerKey: string;
    zoom: number;
    timestamp: number;
  } | null>(null);

  const dispatchAction = useCallback((newAction: MapActionPayload) => {
    if (newAction.command === 'fly_to' && newAction.params) {
      const center = (newAction.params as Record<string, unknown>).center;
      const zoom = (newAction.params as Record<string, unknown>).zoom;
      if (Array.isArray(center) && typeof zoom === 'number') {
        const centerKey = center.join(',');
        const now = Date.now();
        const last = lastFlyToRef.current;
        if (last &&
            last.centerKey === centerKey &&
            last.zoom === zoom &&
            (now - last.timestamp) < 2000) {
          return;  // 节流：2 秒内同地点+同 zoom 的 fly_to 丢弃
        }
        lastFlyToRef.current = { centerKey, zoom, timestamp: now };
      }
    }

    setActions(prev => [...prev, newAction]);
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