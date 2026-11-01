'use client';

/**
 * 地图时序时间轴控制器（GOAL 03 M4）。
 *
 * committed MapSpec.temporal 驱动（agent bind/control 或用户操作经 CAS 提交
 * —— 单一真相）；本组件：
 * - 播放/暂停/步进/seek/倍速/区间选择 + 键盘（Space/←/→，WAI-APG）；
 * - displayedAt 只显示门控后的 displayed 帧（应用 + 观测后才更新）；
 * - 缺期（missing_period_gap）如实提示，不伪造图层；
 * - user_pinned 徽标 + 解除（user-wins 透明化）；
 * - reduced-motion：禁自动播放，仅手动步进；
 * - 会话卸载：停播 + 落定游标提交（settle）+ 清理 live 态。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, Pause, Pin, PinOff, Play } from 'lucide-react';
import { useSyncExternalStore } from 'react';
import { useT } from '@/lib/i18n/useT';
import { usePrefersReducedMotion } from '@/lib/hooks/use-prefers-reduced-motion';
import {
  getCommittedMapSpec,
  subscribeMapSpecLive,
} from '@/lib/mapspec/session-cursor';
import { readTemporalView, type TemporalFrame } from '@/lib/temporal-map/contract';
import { commitUserTemporal } from '@/lib/temporal-map/mutation';
import {
  TemporalPlaybackController,
  activeController,
  getDisplayedTemporalFrame,
  getLiveTemporalFrame,
  markLiveApplied,
  requestLiveFrame,
  resetTemporalLiveState,
  setActiveController,
  subscribeLiveTemporal,
} from '@/lib/temporal-map/live-frame';

const SPEEDS = [1, 2, 4, 8] as const;

function specTemporalIdentity(spec: unknown): string {
  const temporal = (spec as { temporal?: { cursor_frame?: string; user_pinned?: boolean;
    playback?: { playing?: boolean; rate?: number };
    compare?: { enabled?: boolean; frame_id_b?: string } } } | null)?.temporal;
  if (!temporal) return '';
  return [
    temporal.cursor_frame ?? '',
    temporal.user_pinned ? 1 : 0,
    temporal.playback?.playing ? 1 : 0,
    temporal.playback?.rate ?? 1,
    temporal.compare?.enabled ? 1 : 0,
    temporal.compare?.frame_id_b ?? '',
  ].join('|');
}

export function TemporalTimeController() {
  const t = useT('map');
  const reducedMotion = usePrefersReducedMotion();

  // committed temporal（spec 身份选择器：游标/播放/pin 变化才重渲染）。
  const temporalKey = useSyncExternalStore(
    subscribeMapSpecLive,
    () => specTemporalIdentity(getCommittedMapSpec()),
    () => '',
  );
  const liveGeneration = useSyncExternalStore(
    subscribeLiveTemporal,
    () => `${getLiveTemporalFrame()?.id ?? ''}`,
    () => '',
  );
  const view = useMemo(() => readTemporalView(getCommittedMapSpec()), [temporalKey]);
  const displayed = getDisplayedTemporalFrame();
  void liveGeneration;

  const controllerRef = useRef<TemporalPlaybackController | null>(null);
  if (controllerRef.current === null) {
    controllerRef.current = new TemporalPlaybackController();
  }
  const [localPlaying, setLocalPlaying] = useState(false);

  // 控制器生命周期：挂载注册 / 卸载 settle + 清理。
  useEffect(() => {
    const controller = controllerRef.current;
    if (!controller) return;
    setActiveController(controller);
    return () => {
      // settle：把 displayed/live 落定游标提交（用户面 CAS；失败静默 ——
      // 服务端真相为准，重载后以 committed cursor 恢复）。
      const live = getLiveTemporalFrame();
      if (live) {
        void commitUserTemporal({ cursor_frame: live.id, playback: { playing: false } });
      }
      resetTemporalLiveState();
    };
  }, []);

  // 播放启动/停止：spec.playback.playing（agent 或用户提交）为 desired；
  // reduced-motion 拒绝自动播放（诚实：只手动步进）。
  const playing = view?.config.playback?.playing === true || localPlaying;
  useEffect(() => {
    const controller = controllerRef.current;
    if (!controller || !view || !playing) return;
    if (reducedMotion) return;
    controller.start(
      view.config,
      (frame) => requestLiveFrame(frame),
      () => {
        setLocalPlaying(false);
        const last = view.frames[view.frames.length - 1];
        if (last) void commitUserTemporal({ cursor_frame: last.id, playback: { playing: false } });
      },
    );
    return () => controller.stop();
  }, [playing, reducedMotion, temporalKey, view]);

  const settleAndCommit = useCallback((frame: TemporalFrame) => {
    setLocalPlaying(false);
    controllerRef.current?.stop();
    requestLiveFrame(frame);
    void commitUserTemporal({ cursor_frame: frame.id, playback: { playing: false } });
  }, []);

  const onSeek = useCallback((frameId: string) => {
    const v = readTemporalView(getCommittedMapSpec());
    if (!v) return;
    const frame = v.frames.find((f) => f.id === frameId);
    if (frame) settleAndCommit(frame);
  }, [settleAndCommit]);

  const onStep = useCallback((delta: number) => {
    const v = readTemporalView(getCommittedMapSpec());
    if (!v) return;
    const live = getLiveTemporalFrame();
    const baseId = live?.id ?? v.config.cursor_frame;
    const idx = v.frames.findIndex((f) => f.id === baseId);
    const nextIdx = Math.min(v.frames.length - 1, Math.max(0, idx + delta));
    if (nextIdx !== idx) settleAndCommit(v.frames[nextIdx]);
  }, [settleAndCommit]);

  const onTogglePlay = useCallback(() => {
    if (playing) {
      const live = getLiveTemporalFrame();
      setLocalPlaying(false);
      controllerRef.current?.stop();
      const v = readTemporalView(getCommittedMapSpec());
      const settleFrame = live ?? v?.cursorFrame;
      if (v && settleFrame) {
        void commitUserTemporal({ cursor_frame: settleFrame.id, playback: { playing: false } });
      }
    } else {
      setLocalPlaying(true);
      void commitUserTemporal({ playback: { playing: true } });
    }
  }, [playing]);

  const onRate = useCallback((rate: number) => {
    void commitUserTemporal({ playback: { rate } });
  }, []);

  const onUnpin = useCallback(() => {
    void commitUserTemporal({ user_pinned: false });
  }, []);

  if (!view) return null;

  const { config, frames, quality } = view;
  const compareFrame = config.compare?.enabled
    ? frames.find((f) => f.id === config.compare?.frame_id_b)
    : undefined;
  const currentLabel = displayed?.label ?? frames[view.cursorIndex]?.label ?? '';

  return (
    <div
      className="pointer-events-auto absolute bottom-3 left-1/2 z-30 -translate-x-1/2
                 rounded-lg border border-border bg-background/95 p-2 shadow-lg
                 backdrop-blur flex items-center gap-2 text-xs"
      role="group"
      aria-label={t('temporal.aria', { defaultValue: '地图时间轴' })}
      onKeyDown={(e) => {
        if (e.key === ' ') { e.preventDefault(); onTogglePlay(); }
        else if (e.key === 'ArrowLeft') { e.preventDefault(); onStep(-1); }
        else if (e.key === 'ArrowRight') { e.preventDefault(); onStep(1); }
      }}
      tabIndex={0}
      data-testid="temporal-time-controller"
    >
      <button
        type="button"
        onClick={() => onTogglePlay()}
        aria-label={playing ? t('temporal.pause', { defaultValue: '暂停' }) : t('temporal.play', { defaultValue: '播放' })}
        className="rounded p-1 hover:bg-accent"
        data-testid="temporal-play-toggle"
      >
        {playing ? <Pause size={16} /> : <Play size={16} />}
      </button>
      <button
        type="button" onClick={() => onStep(-1)}
        aria-label={t('temporal.stepPrev', { defaultValue: '上一帧' })}
        className="rounded p-1 hover:bg-accent"
      >
        <ChevronLeft size={16} />
      </button>
      <span
        className="min-w-[6rem] text-center font-medium tabular-nums"
        aria-live="polite"
        data-testid="temporal-displayed-at"
      >
        {currentLabel}
      </span>
      <button
        type="button" onClick={() => onStep(1)}
        aria-label={t('temporal.stepNext', { defaultValue: '下一帧' })}
        className="rounded p-1 hover:bg-accent"
      >
        <ChevronRight size={16} />
      </button>
      <input
        type="range"
        min={0}
        max={frames.length - 1}
        value={Math.max(0, frames.findIndex((f) => f.id === (getLiveTemporalFrame()?.id ?? config.cursor_frame)))}
        onChange={(e) => onSeek(frames[Number(e.target.value)].id)}
        aria-label={t('temporal.seek', { defaultValue: '时间轴位置' })}
        className="w-40"
        data-testid="temporal-seek"
      />
      <select
        value={String(config.playback?.rate ?? 1)}
        onChange={(e) => onRate(Number(e.target.value))}
        aria-label={t('temporal.rate', { defaultValue: '播放速度' })}
        className="rounded border border-border bg-background px-1 py-0.5"
        data-testid="temporal-rate"
      >
        {SPEEDS.map((s) => <option key={s} value={s}>{s}×</option>)}
      </select>
      {config.user_pinned ? (
        <button
          type="button" onClick={onUnpin}
          title={t('temporal.pinnedHint', { defaultValue: '用户已固定时间游标，Agent 不可覆盖；点击解除' })}
          aria-label={t('temporal.unpin', { defaultValue: '解除固定' })}
          className="rounded p-1 hover:bg-accent text-amber-600 dark:text-amber-400"
          data-testid="temporal-unpin"
        >
          <PinOff size={14} />
        </button>
      ) : (
        <Pin size={14} className="opacity-30" aria-hidden />
      )}
      {compareFrame ? (
        <span
          className="rounded bg-accent px-1.5 py-0.5"
          data-testid="temporal-compare-badge"
          title={t('temporal.compareHint', { defaultValue: '两期对照（固定分级）' })}
        >
          {t('temporal.compare', { defaultValue: '对照' })} {compareFrame.label}
        </span>
      ) : null}
      {quality.includes('missing_period_gap') ? (
        <span
          className="rounded bg-amber-500/15 px-1.5 py-0.5 text-amber-700 dark:text-amber-400"
          data-testid="temporal-missing-hint"
          title={t('temporal.missingHint', { defaultValue: '数据存在缺期：缺失年份不会内插帧' })}
        >
          {t('temporal.missing', { defaultValue: '含缺期' })}
        </span>
      ) : null}
    </div>
  );
}
