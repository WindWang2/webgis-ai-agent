import type { CommandEntry, MapCommandContext, MapCommandResult } from './types';
import * as navigation from '@/lib/map-kit/navigation';
import { isUserGesturing, onUserGestureStart, waitForGestureEnd } from './camera-arbitration';

/**
 * View commands: camera/navigation.
 *
 * Each `run` body is the verbatim extraction of the corresponding `case` from
 * map-action-handler.tsx, reading from `ctx` instead of the closed-over scope.
 * Validators mirror the old `REQUIRED_PARAMS` table in map-action-renderer.tsx
 * so the renderer gate accepts exactly the same actions as before.
 *
 * V3 (design §6): camera commands return Promise<MapCommandResult> — they own
 * the human-vs-AI arbitration and always settle the action (the queue can never
 * stall on a camera move):
 * - if a user gesture is active when the command starts → wait (≤3s) for it;
 * - map.stop() before starting a new animation (self-interrupt);
 * - resolve succeeded on `moveend` with the *settled* viewport as `actual`;
 * - a user gesture mid-flight → failed `superseded_by_user` (handler maps to a
 *   cancelled ack — the human took over);
 * - 10s safety timeout → failed `timeout`.
 */

const CAMERA_SAFETY_TIMEOUT_MS = 10_000;
const GESTURE_WAIT_TIMEOUT_MS = 3_000;

/**
 * Wraps a synchronous camera move so the action settles with a structured result.
 * Always resolves (never rejects): predictable failures like invalid coordinates
 * become a failed MapCommandResult, matching the command's Promise contract.
 */
function runCameraCommand(
  ctx: MapCommandContext,
  execute: (ctx: MapCommandContext) => void,
): Promise<MapCommandResult> {
  return (async () => {
    // V3: a user gesture owns the camera — wait it out (bounded) instead of
    // fighting it. waitForGestureEnd resolves immediately when idle.
    if (isUserGesturing()) {
      await waitForGestureEnd(GESTURE_WAIT_TIMEOUT_MS);
    }

    const { map } = ctx;
    // Self-interrupt: stop any in-flight AI camera animation before a new one.
    map.stop();

    return new Promise<MapCommandResult>((resolve) => {
      let settled = false;
      // Holder object instead of `let` bindings: `timer`/`offGesture` are assigned
      // after `cleanup` is defined (closures reference them), and a `let` that is
      // only ever assigned once trips `prefer-const` lint.
      const handles: {
        timer?: ReturnType<typeof setTimeout>;
        offGesture?: () => void;
      } = {};

      const cleanup = () => {
        if (handles.timer) clearTimeout(handles.timer);
        handles.offGesture?.();
      };
      const settle = (result: MapCommandResult) => {
        if (settled) return;
        settled = true;
        cleanup();
        resolve(result);
      };

      // Safety timeout: the queue can never stall on a camera command.
      handles.timer = setTimeout(() => {
        settle({ status: 'failed', error: 'timeout' });
      }, CAMERA_SAFETY_TIMEOUT_MS);

      // User gesture mid-flight → the human owns the camera now; MapLibre has
      // already stopped the animation (design §6) — resolve superseded.
      handles.offGesture = onUserGestureStart(() => {
        try {
          map.stop();
        } catch {
          /* defensive */
        }
        settle({ status: 'failed', error: 'superseded_by_user' });
      });

      // Settled viewport = the actual state the backend can verify convergence
      // against (requested center/zoom vs actual within tolerance).
      map.once('moveend', () => {
        settle({
          status: 'succeeded',
          result: settledViewport(map),
        });
      });

      try {
        execute(ctx);
      } catch (e) {
        // Predictable execution failures (e.g. navigation.flyTo rejecting invalid
        // coordinates) become a failed result — the old silent-swallow path.
        settle({ status: 'failed', error: e instanceof Error ? e.message : String(e) });
      }
    });
  })();
}

/** Reads the *settled* camera state off the map (post-moveend). */
function settledViewport(map: any): { center: [number, number]; zoom: number; bearing: number; pitch: number } {
  return {
    center: [map.getCenter().lng, map.getCenter().lat],
    zoom: map.getZoom(),
    bearing: map.getBearing(),
    pitch: map.getPitch(),
  };
}

export const viewCommands: Record<string, CommandEntry> = {
  fly_to: {
    requiredParams: (p) => Array.isArray(p.center) && p.center.length === 2,
    run(ctx) {
      return runCameraCommand(ctx, (c) => {
        const { map, params } = c;
        if (params?.center) {
          navigation.flyTo(map, {
            center: params.center,
            zoom: params?.zoom || 12,
            bearing: params.bearing,
            pitch: params.pitch,
          });
        }
      });
    },
  },

  zoom_to_bbox: {
    requiredParams: (p) => Array.isArray(p.bbox) && p.bbox.length === 4,
    run(ctx) {
      return runCameraCommand(ctx, (c) => {
        const { map, params } = c;
        const bbox = params?.bbox as [number, number, number, number] | undefined;
        const padding = params?.padding ?? 50;
        if (!bbox || bbox.length < 4) {
          throw new Error('invalid bbox');
        }
        navigation.fitBounds(map, bbox, padding);
      });
    },
  },

  set_map_view: {
    requiredParams: (p) => Array.isArray(p.center) || typeof p.zoom === 'number',
    run(ctx) {
      return runCameraCommand(ctx, (c) => {
        const { map, params } = c;
        const { zoom, bearing, pitch } = params || {};
        if (zoom === undefined && bearing === undefined && pitch === undefined) return;
        const center = map.getCenter();
        navigation.flyTo(map, {
          center: [center.lng, center.lat],
          zoom: zoom !== undefined ? zoom : map.getZoom(),
          bearing: bearing !== undefined ? bearing : map.getBearing(),
          pitch: pitch !== undefined ? pitch : map.getPitch(),
        });
      });
    },
  },
};


