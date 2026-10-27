/**
 * G3D05（ADR-0275 D5）：场景控制器 —— 2D/2.5D/3D 人机同语义操作面。
 *
 * 真相纪律：
 * - 显示态读 committed MapSpec.scene（单一真相）；本组件不持有场景状态；
 * - 用户操作经 commitUserScene 写同一 SetSceneIntent 事务管线（与 Agent
 *   set_map_scene 汇合），同时联动 hud is3D（地形/相机效果的实际闸门）；
 * - 无 raster-dem 证据时夸张控制禁用并解释（不伪装可调）；
 * - reduced-motion：场景切换 transition_ms 置 0（无动画跳变）。
 */
'use client';

import { useCallback, useRef, useState, useSyncExternalStore } from 'react';
import { Boxes, ChevronDown } from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import {
  getCommittedMapSpec,
  getMapSpecLiveGeneration,
  subscribeMapSpecLive,
} from '@/lib/mapspec/session-cursor';
import { commitUserScene, type UserScenePayload } from '@/lib/mapspec/scene-mutation';
import { useT } from '@/lib/i18n/useT';

type SceneMode = '2d' | '2.5d' | '3d';

const MODES: SceneMode[] = ['2d', '2.5d', '3d'];
const EXAGGERATION_MIN = 0.25;
const EXAGGERATION_MAX = 10;
const EXAGGERATION_STEP = 0.25;
const PITCH_MAX = 85;
const PITCH_STEP = 5;
const BEARING_STEP = 15;

interface CommittedScene {
  mode?: SceneMode;
  terrain?: {
    source?: string;
    exaggeration?: number;
    vertical_unit?: string;
    elevation_ref?: string;
  } | null;
  camera?: { pitch?: number; bearing?: number };
}

function readScene(): CommittedScene | null {
  const spec = getCommittedMapSpec() as { scene?: CommittedScene } | null;
  return spec?.scene ?? null;
}

function terrainEvidence(): { ready: boolean; sourceId: string | null } {
  // R1-P2（评审修复）：证据与源 id 同源解析 —— 滑杆提交必须带
  // terrain.source（MapSceneConfig 必填），此前只判证据不解析源 id，
  // 无 terrain 块时提交 {exaggeration} 必 422。
  const spec = getCommittedMapSpec() as
    | { sources?: Record<string, { type?: string }> }
    | null;
  const sources = spec?.sources;
  if (!sources || typeof sources !== 'object') {
    return { ready: false, sourceId: null };
  }
  const entry = Object.entries(sources).find(([, s]) => s?.type === 'raster-dem');
  return entry ? { ready: true, sourceId: entry[0] } : { ready: false, sourceId: null };
}

function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return false;
  }
}

export function SceneController() {
  const t = useT('map');
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  // liveGeneration 触发重渲染；场景读 committed（单一真相）。
  useSyncExternalStore(
    subscribeMapSpecLive,
    getMapSpecLiveGeneration,
    getMapSpecLiveGeneration,
  );
  const scene = readScene();
  // 每渲染直读（sources 扫描廉价；committed 变更由 liveGeneration 驱动重渲染）。
  const terrain = terrainEvidence();
  const mode: SceneMode =
    scene?.mode === '2.5d' || scene?.mode === '3d' ? scene.mode : '2d';
  const exaggeration = scene?.terrain?.exaggeration ?? 1;
  const pitch = scene?.camera?.pitch ?? 0;
  const bearing = scene?.camera?.bearing ?? 0;

  const apply = useCallback(async (next: UserScenePayload | null) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    try {
      const outcome = await commitUserScene(next);
      if (outcome.outcome === 'failed') {
        // R1-P2：失败可见（此前仅 dev 日志 —— 滑杆回弹零解释）。
        try {
          const { useToastStore } = await import('@/components/ui/toast');
          useToastStore.getState().addToast(
            t('sceneController.sceneApplyFailed'), 'warning');
        } catch { /* toast 不可用不影响状态收敛 */
        }
      }
      // superseded：committed 真相随响应回灌 → 显示态自动对齐（无本地
      // 乐观场景状态需要回滚），不额外打扰。
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }, [t]);

  const setMode = useCallback(
    (next: SceneMode) => {
      if (next === mode) return;
      useHudStore.getState().setIs3D(next !== '2d');
      // 平面 pitch（0/缺席）→ 进 3D 用安全默认 50°（scene_planning
      // SAFE_PITCH_3D 同值）；已有倾斜则保留用户/Agent 相机。
      const prevPitch = scene?.camera?.pitch;
      const nextPitch =
        next === '2d' ? 0 : prevPitch && prevPitch > 0 ? prevPitch : 50;
      const nextBearing = next === '2d' ? 0 : scene?.camera?.bearing ?? 0;
      void apply({
        ...(scene ?? {}),
        mode: next,
        camera: {
          pitch: nextPitch,
          bearing: nextBearing,
          transition_ms: prefersReducedMotion() ? 0 : undefined,
        },
      });
    },
    [apply, mode, scene],
  );

  const setExaggeration = useCallback(
    (value: number) => {
      if (!terrain.ready) return;
      // terrain.source 必填：committed scene 有 terrain 用其 source，
      // 否则用证据解析出的 raster-dem 源 id。
      const source = scene?.terrain?.source ?? terrain.sourceId ?? undefined;
      void apply({
        ...(scene ?? { mode: '3d' as SceneMode }),
        terrain: { ...(scene?.terrain ?? {}), ...(source ? { source } : {}), exaggeration: value },
      });
    },
    [apply, scene, terrain],
  );

  const nudgeCamera = useCallback(
    (field: 'pitch' | 'bearing', delta: number) => {
      const nextPitch =
        field === 'pitch'
          ? Math.max(0, Math.min(PITCH_MAX, Math.round((pitch + delta) * 10) / 10))
          : pitch;
      const nextBearing =
        field === 'bearing'
          ? Math.max(-180, Math.min(180, Math.round((bearing + delta) * 10) / 10))
          : bearing;
      useHudStore.getState().setIs3D(nextPitch > 0 || mode !== '2d');
      void apply({
        ...(scene ?? {}),
        mode,
        camera: { pitch: nextPitch, bearing: nextBearing },
      });
    },
    [apply, bearing, mode, pitch, scene],
  );

  const elevationRef = scene?.terrain?.elevation_ref;
  const verticalUnit = scene?.terrain?.vertical_unit;

  return (
    <div className="pointer-events-auto absolute right-2 top-14 z-20 print:hidden">
      <button
        type="button"
        aria-label={t('sceneController.title')}
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((v) => !v)}
        className={`flex h-8 items-center gap-1 rounded-sm px-2 text-xs transition-colors ${
          mode !== '2d'
            ? 'bg-status-accent-soft font-bold text-status-accent'
            : 'bg-surface-panel text-ink-secondary hover:bg-surface-hover hover:text-ink'
        }`}
      >
        <Boxes className="h-4 w-4" aria-hidden />
        <span>{t('sceneController.trigger')}</span>
        <ChevronDown
          className={`h-3 w-3 transition-transform ${open ? 'rotate-180' : ''}`}
          aria-hidden
        />
      </button>
      {open && (
        <div
          role="dialog"
          aria-label={t('sceneController.title')}
          className="mt-1 w-64 rounded-md border border-edge-subtle bg-surface-panel p-3 text-xs text-ink shadow-lg"
        >
          {/* 模式分段 */}
          <div role="radiogroup" aria-label={t('sceneController.mode')} className="mb-3 flex gap-1">
            {MODES.map((m) => (
              <button
                key={m}
                type="button"
                role="radio"
                aria-checked={m === mode}
                disabled={busy}
                onClick={() => setMode(m)}
                className={`flex-1 rounded-sm border px-2 py-1 transition-colors ${
                  m === mode
                    ? 'border-status-accent bg-status-accent-soft font-bold text-status-accent'
                    : 'border-edge-subtle text-ink-secondary hover:bg-surface-hover'
                }`}
              >
                {t(`sceneController.mode_${m === '2.5d' ? '2_5d' : m}`)}
              </button>
            ))}
          </div>

          {/* 夸张系数（无证据禁用） */}
          <label className="mb-1 block text-ink-secondary" htmlFor="scene-exaggeration">
            {t('sceneController.exaggeration')}
            <span className="ml-1 font-mono text-ink">{exaggeration}×</span>
          </label>
          <input
            id="scene-exaggeration"
            type="range"
            min={EXAGGERATION_MIN}
            max={EXAGGERATION_MAX}
            step={EXAGGERATION_STEP}
            value={exaggeration}
            disabled={!terrain.ready || busy}
            onChange={(e) => setExaggeration(Number(e.target.value))}
            className="w-full accent-[color:var(--agent-accent)]"
            aria-describedby="scene-exaggeration-note"
          />
          <p id="scene-exaggeration-note" className="mb-3 text-[11px] leading-4 text-ink-secondary">
            {terrain.ready
              ? t('sceneController.exaggerationNote')
              : t('sceneController.exaggerationDisabled')}
          </p>

          {/* 相机 */}
          <div className="mb-3 grid grid-cols-2 gap-2">
            <div>
              <span className="block text-ink-secondary">{t('sceneController.pitch')}</span>
              <div className="flex items-center gap-1">
                <button
                  type="button" aria-label={t('sceneController.pitchDown')}
                  disabled={busy} onClick={() => nudgeCamera('pitch', -PITCH_STEP)}
                  className="rounded-sm border border-edge-subtle px-1.5 hover:bg-surface-hover"
                >−</button>
                <span className="flex-1 text-center font-mono">{pitch}°</span>
                <button
                  type="button" aria-label={t('sceneController.pitchUp')}
                  disabled={busy} onClick={() => nudgeCamera('pitch', PITCH_STEP)}
                  className="rounded-sm border border-edge-subtle px-1.5 hover:bg-surface-hover"
                >+</button>
              </div>
            </div>
            <div>
              <span className="block text-ink-secondary">{t('sceneController.bearing')}</span>
              <div className="flex items-center gap-1">
                <button
                  type="button" aria-label={t('sceneController.bearingDown')}
                  disabled={busy} onClick={() => nudgeCamera('bearing', -BEARING_STEP)}
                  className="rounded-sm border border-edge-subtle px-1.5 hover:bg-surface-hover"
                >−</button>
                <span className="flex-1 text-center font-mono">{bearing}°</span>
                <button
                  type="button" aria-label={t('sceneController.bearingUp')}
                  disabled={busy} onClick={() => nudgeCamera('bearing', BEARING_STEP)}
                  className="rounded-sm border border-edge-subtle px-1.5 hover:bg-surface-hover"
                >+</button>
              </div>
            </div>
          </div>

          {/* 垂直语义披露行 */}
          <p className="border-t border-edge-subtle pt-2 text-[11px] leading-4 text-ink-secondary">
            {t('sceneController.verticalDatumLabel')}
            {elevationRef || t('sceneController.datumUndeclared')}
            {verticalUnit ? t('sceneController.unitSuffix', { unit: verticalUnit }) : ''}
          </p>
          <p className="mt-1 text-[11px] leading-4 text-ink-secondary">
            {prefersReducedMotion()
              ? t('sceneController.reducedMotionOn')
              : t('sceneController.reducedMotionHint')}
          </p>
        </div>
      )}
    </div>
  );
}
