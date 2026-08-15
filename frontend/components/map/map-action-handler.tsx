'use client';
import React, { useEffect } from 'react';
import { useMapAction } from '@/lib/contexts/map-action-context';
import { useHudStore } from '@/lib/store/useHudStore';

import { useMap } from 'react-map-gl/maplibre';

import { COMMAND_CATALOGUE } from '@/lib/map-commands/catalogue';
import type { CommandEntry, MapCommandContext, MapCommandResult } from '@/lib/map-commands/types';
import type { MapActionTerminalStatus } from '@/lib/types';
import { ensureAnnotationLayers, refreshAnnotations } from '@/lib/map-commands/annotationHelpers';

import { devOnly } from "@/lib/utils/logger";

export const MapActionHandler = React.memo(function MapActionHandler() {
  const { actions, popAction, setSelectedBaseLayer, reportTerminal } = useMapAction();
  const mapContext = useMap();
  const mapInstance = mapContext.default;
  const annotations = useHudStore((s) => s.annotations);
  const action = actions[0];

  // V3: re-run guard. The effect re-runs when mapInstance identity changes
  // mid-flight (e.g. MapProvider remounts while an action is at the queue head);
  // without this guard it would re-execute run() for the same head action. The
  // first run owns the settle; the ref is only overwritten when a *different*
  // action takes the head. Keep the per-action pop guard (popAction is id-guarded).
  const runningActionIdRef = React.useRef<string | null | undefined>(null);

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

    // Skip re-execution when this action is already being run by an earlier
    // effect pass (the first run owns the settle). Production actions always
    // carry an id (backend `ma-*` or client `fe-*` fallback); the ref is
    // initialized to null so the very first run always proceeds.
    if (runningActionIdRef.current === action.action_id) return;

    if (!mapInstance) {
      devOnly.warn('[MapActionHandler] No map instance found! (Is MapProvider missing or Map ID mismatch?)');
      return;
    }

    const map = mapInstance.getMap();
    if (!map) return;

    runningActionIdRef.current = action.action_id;

    // V3 (design §6): the handler owns dequeuing and terminal reporting. Every
    // action settles exactly once: queued → running → terminal (succeeded /
    // failed / cancelled / superseded), reported through reportTerminal →
    // context ack sink, then popped. Commands never pop themselves anymore;
    // setDeferredPop / safePop / popAction stay in MapCommandContext only for
    // backward compat (export_map's promise-returning run replaces the old
    // deferred-pop machinery).
    const startedAt = new Date().toISOString();
    const startedAtMs = performance.now();

    const ctx: MapCommandContext = {
      map,
      // Compat no-ops — the per-action settle guard in this effect is the single
      // dequeue point (replaces the old per-run poppedRef/deferredPop).
      popAction: () => {},
      setDeferredPop: () => {},
      safePop: () => {},
      getHudState: () => useHudStore.getState(),
      setSelectedBaseLayer,
      command: action.command,
      actionId: action.action_id || '',
      params: action.params || {},
    };

    const finish = (status: MapActionTerminalStatus, extras?: { error?: string; actual?: unknown }) => {
      reportTerminal(action, status, {
        error: extras?.error,
        actual: extras?.actual,
        startedAt,
        finishedAt: new Date().toISOString(),
        durationMs: performance.now() - startedAtMs,
      });
      // Pop guarded by action_id: a settled action only dequeues when it is
      // still the head — an effect re-run settling the same action twice must
      // not drop the next queued action (design §6 per-action settle guard).
      popAction(action.action_id);
    };

    (async () => {
      const entry = (COMMAND_CATALOGUE as Record<string, CommandEntry>)[action.command.toLowerCase()];
      if (!entry) {
        // V3: unknown commands reach a terminal state too (was: warn + silent pop).
        devOnly.warn('[MapActionHandler] Unknown command:', action.command);
        finish('failed', { error: 'unknown_command' });
        return;
      }
      // V3: requiredParams now gates the SSE path as well (spec requires terminal
      // states for param failures).
      if (!entry.requiredParams(action.params || {})) {
        finish('failed', { error: 'invalid_params' });
        return;
      }

      let result: void | MapCommandResult;
      try {
        // run() contract (design §6): void → succeeded; MapCommandResult → honored;
        // Promise → awaited, then popped by the settle guard below.
        result = await entry.run(ctx);
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
        finish('failed', { error: msg });
        return;
      }

      // Issue #393: every catalogue command now returns an explicit
      // MapCommandResult (post-state verification is the FIX-B contract), so a
      // void return is no longer evidence of success — it means the command
      // forgot to report, or an unverifiable code path slipped through. Fail
      // honestly instead of acking `succeeded` for a mutation that may never
      // have landed (the old default silently converted the broken
      // apply_layer_filter / heatmap commands into fake successes).
      if (result === undefined || result === null) {
        finish('failed', { error: 'no_result' });
        return;
      }
      if (result.status === 'failed') {
        // 用户手势打断的相机动画按 cancelled 上报（设计 §6）；其余失败照实上报。
        const status: MapActionTerminalStatus = result.error === 'superseded_by_user'
          ? 'cancelled'
          : 'failed';
        finish(status, { error: result.error, actual: result.result });
        return;
      }
      finish('succeeded', { actual: result.result });
    })();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [action, mapInstance, popAction, reportTerminal]);

  return null;
});

export default MapActionHandler;
