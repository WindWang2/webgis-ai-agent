import type { CommandEntry, MapCommandContext, MapCommandResult } from './types';
import * as navigation from '@/lib/map-kit/navigation';
import { isUserGesturing, onUserGestureStart, waitForGestureEnd } from './camera-arbitration';
import { checkViewport } from '@/lib/map-product/finalizer';

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
 *   if the wait bound expires with the user STILL gesturing → settle
 *   `superseded_by_user` (never start a camera fight);
 * - map.stop() before starting a new animation (self-interrupt);
 * - resolve succeeded on `moveend` only when the settled viewport actually
 *   reached the requested center+zoom (backend tolerance) AND no map
 *   interaction handler is active AND no user gesture flag is set — the settle
 *   check is deferred one frame so a just-arriving dragstart can be seen first;
 * - a user gesture mid-flight → failed `superseded_by_user` (handler maps to a
 *   cancelled ack — the human took over);
 * - a foreign programmatic move cut the animation short (settled viewport never
 *   reached the target) → failed `interrupted`;
 * - 10s safety timeout → failed `timeout`.
 */

const CAMERA_SAFETY_TIMEOUT_MS = 10_000;
const GESTURE_WAIT_TIMEOUT_MS = 3_000;

// Camera convergence tolerance mirrored from the backend
// (app/lib/harness/pi_agent_harness.py: CAMERA_CENTER_TOL_DEG = 0.001,
// CAMERA_ZOOM_TOL = 0.05, _FLOAT_EPSILON = 1e-9). A camera command only acks
// SUCCEEDED when the settled viewport reached the requested target — a success
// the live map did not earn is a fake ack (ROUND-2 finding).
const CAMERA_CENTER_TOL_DEG = 0.001;
const CAMERA_ZOOM_TOL = 0.05;
// Absorb float64 noise so a delta exactly at the tolerance boundary (e.g.
// 116.001 - 116 = 0.0010000000000012…) keeps the "≤ tolerance" semantics —
// same ε the backend adds.
const FLOAT_EPSILON = 1e-9;

/** Requested center+zoom target used for the post-moveend convergence check. */
interface CameraTarget {
  center: [number, number];
  zoom: number;
}

/** True while ANY map interaction handler is active (a user grab in progress).
 * A grab activates its HandlerManager synchronously, BEFORE the dragstart DOM
 * event lands — so this catches the ROUND-2 camera interrupt race even when the
 * gesture flag has not been set yet. */
function anyInteractionActive(map: any): boolean {
  return !!(
    map?.dragPan?.isActive?.() ||
    map?.scrollZoom?.isActive?.() ||
    map?.dragRotate?.isActive?.() ||
    map?.touchZoomRotate?.isActive?.() ||
    map?.tapZoom?.isActive?.() ||
    map?.keyboard?.isActive?.()
  );
}

/** Whether the settled viewport reached the requested target (backend tolerance). */
function reachedTarget(settled: { center: [number, number]; zoom: number }, target: CameraTarget): boolean {
  return (
    Math.abs(settled.center[0] - target.center[0]) <= CAMERA_CENTER_TOL_DEG + FLOAT_EPSILON &&
    Math.abs(settled.center[1] - target.center[1]) <= CAMERA_CENTER_TOL_DEG + FLOAT_EPSILON &&
    Math.abs(settled.zoom - target.zoom) <= CAMERA_ZOOM_TOL + FLOAT_EPSILON
  );
}

/**
 * Wraps a synchronous camera move so the action settles with a structured result.
 * Always resolves (never rejects): predictable failures like invalid coordinates
 * become a failed MapCommandResult, matching the command's Promise contract.
 */
function runCameraCommand(
  ctx: MapCommandContext,
  execute: (ctx: MapCommandContext) => void,
  target?: CameraTarget,
): Promise<MapCommandResult> {
  return (async () => {
    // V3: a user gesture owns the camera — wait it out (bounded) instead of
    // fighting it. waitForGestureEnd resolves immediately when idle.
    if (isUserGesturing()) {
      await waitForGestureEnd(GESTURE_WAIT_TIMEOUT_MS);
      // ROUND-2: the wait bound can expire with the user STILL gesturing.
      // Starting an animation now (map.stop() + flyTo) would fight the user's
      // active handlers — settle superseded instead, never start a camera fight.
      if (isUserGesturing()) {
        return { status: 'failed', error: 'superseded_by_user' };
      }
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
        frameTimer?: ReturnType<typeof setTimeout>;
        offGesture?: () => void;
      } = {};

      const cleanup = () => {
        if (handles.timer) clearTimeout(handles.timer);
        if (handles.frameTimer) clearTimeout(handles.frameTimer);
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

      // User gesture mid-flight → the human owns the camera now (design §6).
      // Settle superseded FIRST and never call map.stop() ourselves: in real
      // MapLibre stop() fires moveend synchronously, so the once('moveend')
      // settle would win and the action would ack SUCCEEDED — and stop() would
      // also reset the user's active gesture handlers. MapLibre stops the
      // in-flight animation itself when a user gesture begins.
      handles.offGesture = onUserGestureStart(() => {
        settle({ status: 'failed', error: 'superseded_by_user' });
      });

      // Settled viewport = the actual state the backend can verify convergence
      // against (requested center/zoom vs actual within tolerance).
      map.once('moveend', () => {
        // ROUND-2 camera interrupt race: a user grab mid-flyTo fires the
        // interrupt moveend SYNCHRONOUSLY before dragstart lands (in real
        // MapLibre HandlerManager._stop(true) → _afterEase → moveend; dragstart
        // arrives the next frame). The once('moveend') settle would ack
        // SUCCEEDED even though the human just took over. Defer the settle
        // check by one frame so a just-arriving dragstart can set the gesture
        // flag first — and, independently, verify no map interaction handler is
        // active (they activate synchronously with the grab, before the JS
        // dragstart event ever dispatches).
        if (settled) return;
        handles.frameTimer = setTimeout(() => {
          if (isUserGesturing() || anyInteractionActive(map)) {
            settle({ status: 'failed', error: 'superseded_by_user' });
            return;
          }
          const settledVp = settledViewport(map);
          // ROUND-2: a camera command only succeeds when the settled viewport
          // actually reached the requested target — e.g. a foreign programmatic
          // fitBounds that cut the flyTo short means the map did NOT earn the ack.
          if (target && !reachedTarget(settledVp, target)) {
            settle({ status: 'failed', error: 'interrupted' });
            return;
          }
          settle({ status: 'succeeded', result: settledVp });
        }, 0);
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
      return runCameraCommand(
        ctx,
        (c) => {
          const { map, params } = c;
          if (params?.center) {
            navigation.flyTo(map, {
              center: params.center,
              zoom: params?.zoom || 12,
              bearing: params.bearing,
              pitch: params.pitch,
            });
          }
        },
        // Requested target for the post-moveend convergence check — mirror the
        // execute body's zoom default so the comparison is apples-to-apples.
        {
          center: ctx.params?.center as [number, number],
          zoom: (ctx.params?.zoom as number) || 12,
        },
      );
    },
  },

  zoom_to_bbox: {
    requiredParams: (p) => Array.isArray(p.bbox) && p.bbox.length === 4,
    run(ctx) {
      // No explicit center+zoom target (fitBounds computes it internally), so no
      // convergence check — settled == succeeded.
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
    // #534: run body 支持任意组合的部分相机参数（center/zoom/bearing/pitch，
    // 三者皆可选 —— map_view.py 描述与 params 构造也是如此）。former 校验器
    // 只认 center/zoom，把 bearing-only / pitch-only（"倾斜看 3D"/"把北朝上"）
    // 的自然语言请求拒绝成 invalid_params —— 后端成功、前端立刻失败的矛盾。
    // 任一命名维度有效即放行（无有效参数的 no-op 快路径在 run 体内处理）。
    requiredParams: (p) =>
      Array.isArray(p.center) ||
      typeof p.zoom === 'number' ||
      typeof p.bearing === 'number' ||
      typeof p.pitch === 'number',
    run(ctx) {
      const { map, params } = ctx;
      const { center, zoom, bearing, pitch } = params || {};
      const hasCenter = Array.isArray(center) && center.length === 2;
      const hasZoom = typeof zoom === 'number';
      const hasBearing = typeof bearing === 'number';
      const hasPitch = typeof pitch === 'number';
      if (!hasCenter && !hasZoom && !hasBearing && !hasPitch) {
        // No effective camera params at all → nothing would move, so no moveend
        // will ever fire. Settle succeeded immediately with the current camera
        // as actual — otherwise the queue would stall 10s and ack `timeout` for
        // a no-op request.
        return { status: 'succeeded', result: settledViewport(map) };
      }
      // ROUND-2: honor params.center (was dropped — flew to the current center).
      // Merge with the current zoom/bearing/pitch so a partial request moves
      // only the dimensions it names; the merged state is the convergence target.
      const requestedCenter: [number, number] = hasCenter
        ? (center as [number, number])
        : [map.getCenter().lng, map.getCenter().lat];
      const requestedZoom: number = hasZoom ? (zoom as number) : map.getZoom();
      return runCameraCommand(
        ctx,
        (c) => {
          const { map: m } = c;
          if (hasCenter && !hasZoom && !hasBearing && !hasPitch) {
            // Center-only request: an instant jump is the honest no-argument
            // move (was a silent no-op success before ROUND-2). MapLibre jumpTo
            // fires moveend synchronously, so the settle still flows through the
            // one-frame deferred check.
            navigation.jumpTo(m, {
              center: requestedCenter,
              zoom: m.getZoom(),
              bearing: m.getBearing(),
              pitch: m.getPitch(),
            });
            return;
          }
          navigation.flyTo(m, {
            center: requestedCenter,
            zoom: requestedZoom,
            bearing: hasBearing ? (bearing as number) : m.getBearing(),
            pitch: hasPitch ? (pitch as number) : m.getPitch(),
          });
        },
        { center: requestedCenter, zoom: requestedZoom },
      );
    },
  },

  /**
   * ADR-0081：map_finalization 的前端终验命令。后端 Completion Runtime 的
   * 完成态载荷到达时派发 —— 相机真相只在前端，这里做一次有界视口校验：
   * 视口与结果 bbox 相交 → 不动相机；不相交 → fitBounds 一次（用户手势
   * 仲裁经 runCameraCommand 获得）。无 bbox（空结果）→ no-op succeeded。
   */
  map_finalization: {
    requiredParams: (p) => p.status !== undefined,
    run(ctx) {
      const { map, params } = ctx;
      const bbox = (params as { bbox?: unknown }).bbox;
      // 纯校验（共享 helper，与测试同一实现）：相交/无 bbox/未就绪 →
      // 无相机动作（立即结算，不空转队列）。
      const check = checkViewport(map, bbox);
      if (check !== 'repairable') {
        return { status: 'succeeded', result: { viewport: check, repaired: false } };
      }
      // 修复动作放进 runCameraCommand 的 execute —— 用户手势仲裁/自中断
      // 语义对 finalizer 修复同样生效（用户正在拖图时不抢相机）。
      return runCameraCommand(ctx, (c) => {
        navigation.fitBounds(c.map, bbox as [number, number, number, number], 80);
      });
    },
  },
};


