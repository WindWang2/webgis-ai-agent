import type { CommandEntry, MapCommandResult } from './types';
import type { ExportRequest } from '@/lib/map-kit/exporter';
import { devOnly } from '@/lib/utils/logger';

/**
 * export_map command — thin dispatch arm.
 *
 * V3 (design §6): `run` returns the real Promise from the render-callback work.
 * The handler awaits it and then pops — replacing the old deferred-pop
 * machinery (`setDeferredPop` / `safePop` stay in MapCommandContext only for
 * backward compat). The action stays at the queue head (running) until the
 * export composition actually finishes, so a second export cannot overwrite the
 * first's canvas.
 *
 * The entire export pipeline — DPI management, canvas preparation, layout,
 * format branching, upload, system messages, error handling — lives in the
 * `MapExporter` deep module (`lib/map-exporter/index.ts`).
 *
 * Perf: ``MapExporterEngine`` (~1300 lines + canvas/layout/svg deps) is loaded
 * only when an export actually runs — via dynamic ``import()`` inside the render
 * callback. This keeps the heavy exporter out of the first-load bundle of any
 * screen that renders the command catalogue (i.e. every map screen).
 */
export const exportCommands: Record<string, CommandEntry> = {
  export_map: {
    requiredParams: () => true,
    run(ctx): Promise<MapCommandResult> {
      const { map, params, getHudState } = ctx;

      return new Promise<MapCommandResult>((resolve) => {
        // F5: 异步 export 必须等 map.once('render') 真正回调完再 settle，否则
        // 连续触发 export 会让后一次在前一次还没合成完时覆盖 canvas。Handler
        // 在 promise settle 后才 popAction（设计 §6）。
        map.once('render', async () => {
          try {
            // Dynamic import: the exporter engine is heavy (canvas composition,
            // DPI/oversample, vector SVG/PDF generation, layout). Load on demand.
            const { MapExporterEngine } = await import('@/lib/map-kit/exporter');
            const outcome = await MapExporterEngine.export(
              { map, getHudState },
              (params || {}) as ExportRequest,
            );
            if (!outcome.ok) {
              devOnly.error('[export_map] Export failed:', outcome.error);
              resolve({ status: 'failed', error: 'export_failed' });
            } else {
              resolve({ status: 'succeeded' });
            }
          } catch (e) {
            devOnly.error('[export_map] Unexpected error:', e);
            try {
              getHudState().setPendingSystemMessage(
                `[系统通知] 专题地图排版合成失败。错误原因: ${e}。请向用户致歉并结束流程。`,
              );
            } catch {
              /* defensive */
            }
            resolve({ status: 'failed', error: 'export_error' });
          }
        });
        map.triggerRepaint();
      });
    },
  },
};
