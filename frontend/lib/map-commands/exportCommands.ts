import type { CommandEntry } from './types';
import type { ExportRequest } from '@/lib/map-kit/exporter';
import { devOnly } from '@/lib/utils/logger';

/**
 * export_map command — thin dispatch arm.
 *
 * Owns only the dispatch-level concerns: deferred pop (the component's finally
 * block must skip the synchronous pop), the `map.once('render')` lifecycle, and
 * the re-entrancy guard (`safePop`).
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
    run(ctx) {
      const { map, params, getHudState, setDeferredPop, safePop } = ctx;

      // F5: 异步 export 必须等 map.once('render') 真正回调完再 popAction，
      // 否则连续触发 export 会让后一次在前一次还没合成完时覆盖 canvas。
      // 标记该 case 自己负责 popAction，外层 finally 跳过。
      setDeferredPop(true);

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
        } finally {
          // F5: 真正合成完才出队，杜绝重入
          // 审计 F24：用 safePop 防止 base layer 切换重入导致 double-pop
          safePop();
        }
      });
      map.triggerRepaint();
    },
  },
};
