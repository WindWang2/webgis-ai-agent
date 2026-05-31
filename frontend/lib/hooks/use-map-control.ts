'use client';

import { useCallback } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';

export function useMapControl(mounted: boolean) {
  const { setViewport, pushOpLog } = useHudStore();

  const handleZoomIn = useCallback(() => {
    const { viewport } = useHudStore.getState();
    setViewport(viewport.center, Math.min(viewport.zoom + 1, 22), viewport.bearing, viewport.pitch);
  }, [setViewport]);

  const handleZoomOut = useCallback(() => {
    const { viewport } = useHudStore.getState();
    setViewport(viewport.center, Math.max(viewport.zoom - 1, 1), viewport.bearing, viewport.pitch);
  }, [setViewport]);

  const handleHome = useCallback(() => {
    setViewport([116.4074, 39.9042], 4.0, 0, 0);
  }, [setViewport]);

  const handleLocate = useCallback(() => {
    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          setViewport([pos.coords.longitude, pos.coords.latitude], 12.0, 0, 0);
          pushOpLog({
            id: Date.now().toString(),
            type: 'flyto',
            label: '飞到 — 当前位置',
            time: mounted ? new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '',
            detail: `[${pos.coords.longitude.toFixed(5)}, ${pos.coords.latitude.toFixed(5)}]`,
          });
        },
        () => {
          setViewport([116.4074, 39.9042], 10.0, 0, 0);
        }
      );
    }
  }, [setViewport, pushOpLog, mounted]);

  return {
    handleZoomIn,
    handleZoomOut,
    handleHome,
    handleLocate,
  };
}
