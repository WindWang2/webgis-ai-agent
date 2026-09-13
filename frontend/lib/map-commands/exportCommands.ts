import type { CommandEntry, MapCommandResult } from './types';
import type { ExportRequest } from '@/lib/map-kit/exporter';
import { idleTimeoutWithinWatchdog } from '@/lib/export/highdpi';
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
const EXPORT_RENDER_TIMEOUT_MS = 30_000;

/**
 * review：引擎 idle 截止按看门狗预算 —— 看门狗在本命令开始时就启动，而高 DPI
 * 引擎的最坏链路（idle 超时 → 降级回退重绘 → 相机 fit）默认预算 30+3+5s 会
 * 超出看门狗，永不 idle 的场景会被队列先 settle 'timeout'，降级成品永远没机会
 * 回传。此处把 idle 截止压到看门狗内（30s − 3s 降级重绘 − 5s fit − 2s 余量
 * = 20s，下限 5s），让降级链路完整落在看门狗之内。静态 import 可接受：
 * lib/export/highdpi 仅类型依赖外部模块，无运行时代价。
 */
const ENGINE_IDLE_TIMEOUT_MS = idleTimeoutWithinWatchdog(EXPORT_RENDER_TIMEOUT_MS);

export const exportCommands: Record<string, CommandEntry> = {
  export_map: {
    requiredParams: () => true,
    run(ctx): Promise<MapCommandResult> {
      const { map, params, getHudState } = ctx;

      return new Promise<MapCommandResult>((resolve) => {
        let settled = false;
        // Holder object instead of `let` bindings (matches runCameraCommand's
        // pattern): `timer` / `onRender` are assigned after `settle` is defined.
        const handles: {
          timer?: ReturnType<typeof setTimeout>;
          onRender?: () => void;
        } = {};

        const settle = (result: MapCommandResult) => {
          if (settled) return;
          settled = true;
          if (handles.timer) clearTimeout(handles.timer);
          if (handles.onRender && typeof map.off === 'function') {
            map.off('render', handles.onRender);
          }
          resolve(result);
        };

        // Safety timeout: if `render` never fires (e.g. the canvas is hidden or
        // the GL context is gone), the queue must not stall forever — same
        // holder pattern as runCameraCommand.
        handles.timer = setTimeout(() => {
          settle({ status: 'failed', error: 'timeout' });
        }, EXPORT_RENDER_TIMEOUT_MS);

        // F5: 异步 export 必须等 map.once('render') 真正回调完再 settle，否则
        // 连续触发 export 会让后一次在前一次还没合成完时覆盖 canvas。Handler
        // 在 promise settle 后才 popAction（设计 §6）。
        // #618: if the timeout already settled, a late render must not run the
        // export pipeline (upload + success system message after a failure ack).
        handles.onRender = async () => {
          if (settled) return;
          try {
            // Dynamic import: the exporter engine is heavy (canvas composition,
            // DPI/oversample, vector SVG/PDF generation, layout). Load on demand.
            const { MapExporterEngine } = await import('@/lib/map-kit/exporter');
            if (settled) return;
            const outcome = await MapExporterEngine.export(
              { map, getHudState, idleTimeoutMs: ENGINE_IDLE_TIMEOUT_MS },
              (params || {}) as ExportRequest,
            );
            if (settled) return;
            if (!outcome.ok) {
              devOnly.error('[export_map] Export failed:', outcome.error);
              settle({ status: 'failed', error: 'export_failed' });
            } else {
              settle({ status: 'succeeded' });
            }
          } catch (e) {
            if (settled) return;
            devOnly.error('[export_map] Unexpected error:', e);
            try {
              getHudState().setPendingSystemMessage(
                `[系统通知] 专题地图排版合成失败。错误原因: ${e}。请向用户致歉并结束流程。`,
              );
            } catch {
              /* defensive */
            }
            settle({ status: 'failed', error: 'export_error' });
          }
        };
        map.once('render', handles.onRender);
        map.triggerRepaint();
      });
    },
  },
};
