'use client';
import { useEffect } from 'react';
import { useMapAction } from '@/lib/contexts/map-action-context';
import { useHudStore } from '@/lib/store/useHudStore';

import { useMap } from 'react-map-gl/maplibre';

import { COMMAND_CATALOGUE } from '@/lib/map-commands/catalogue';
import type { CommandEntry, MapCommandContext } from '@/lib/map-commands/types';
import { ensureAnnotationLayers, refreshAnnotations } from '@/lib/map-commands/annotationHelpers';

import { devOnly } from "@/lib/utils/logger";

export function MapActionHandler() {
  const { actions, popAction, setSelectedBaseLayer } = useMapAction();
  const mapContext = useMap();
  const mapInstance = mapContext.default;
  const annotations = useHudStore((s) => s.annotations);
  const action = actions[0];

  // Refresh annotation layers on map when annotations change in Zustand store
  useEffect(() => {
    if (!mapInstance) return;
    const map = mapInstance.getMap();
    if (!map) return;
    ensureAnnotationLayers(map);
    refreshAnnotations(map);
  }, [mapInstance, annotations]);

  useEffect(() => {
    if (!action) return;

    if (!mapInstance) {
      devOnly.warn('[MapActionHandler] No map instance found! (Is MapProvider missing or Map ID mismatch?)');
      return;
    }

    const map = mapInstance.getMap();
    if (!map) return;

    // F5: 默认在 finally 同步 popAction；某些 case 走异步 map.once 回调，
    // 把 deferredPop 设为 true，由 case 自己负责出队。
    let deferredPop = false;
    // 审计 F24：export_map 的 deferred pop 在 map.once('render') 回调里，
    // 期间若 mapInstance 因 base layer 切换而变化，effect 会重跑 + cleanup
    // 不取消 once 监听 -> 两次 popAction（队列下溢）。用 poppedRef 保证只 pop 一次。
    let poppedRef = false;
    const safePop = () => {
      if (poppedRef) return;
      poppedRef = true;
      popAction();
    };

    const ctx: MapCommandContext = {
      map,
      popAction,
      setDeferredPop: (v: boolean) => { deferredPop = v; },
      safePop,
      getHudState: () => useHudStore.getState(),
      setSelectedBaseLayer,
      command: action.command,
      params: action.params || {},
    };

    try {
      const entry = (COMMAND_CATALOGUE as Record<string, CommandEntry>)[action.command.toLowerCase()];
      if (entry) {
        entry.run(ctx);
      } else {
        devOnly.warn('[MapActionHandler] Unknown command:', action.command);
      }
    } catch (error) {
      // /review C10: surface AI command failures to the user via system message
      // instead of swallowing in console — otherwise user sees nothing and
      // assumes the AI lied about what it was doing.
      const msg = error instanceof Error ? error.message : String(error);
      devOnly.error('[MapActionHandler] Error executing action:', error);
      try {
        useHudStore.getState().setPendingSystemMessage(
          `[系统通知] 地图命令 ${action.command} 执行失败: ${msg}`
        );
      } catch {
        /* defensive: store unavailable */
      }
    } finally {
      if (!deferredPop) safePop();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [action, mapInstance, popAction]);

  return null;
}

export default MapActionHandler;
