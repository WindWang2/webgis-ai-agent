'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ChevronLeft,
  ChevronRight,
  Pause,
  Play,
  X,
} from 'lucide-react';
import type { CubeWindowResult } from '@/lib/api/lakehouse';
import { useHudStore } from '@/lib/store/useHudStore';
import { usePrefersReducedMotion } from '@/lib/hooks/use-prefers-reduced-motion';
import {
  bandStats,
  gridToDataUrl,
  gridToRgba,
  COLOR_RAMP,
  rampColor,
  type ColorRampName,
} from '@/lib/map-kit/raster-canvas';
import { LruCache, formatStepLabel, shouldRenderFrame } from '@/lib/map-kit/raster-timeline';

export interface TimelinePlayerProps {
  result: CubeWindowResult;
  onClose: () => void;
}

const SPEEDS = [1, 2, 4, 8] as const;
/** 基准帧间隔（ms/步，speed=1）。 */
const BASE_INTERVAL_MS = 400;
/** rAF 帧预算（60fps ≈ 16ms；渲染超预算丢帧而非堆积）。 */
const FRAME_BUDGET_MS = 16;
/** 已渲染帧位图缓存（LRU —— 内存有界，长序列回看不重渲染）。 */
const FRAME_CACHE_SIZE = 24;

function bandData(result: CubeWindowResult): { name: string; steps: number[][][] } {
  const [name, data] = Object.entries(result.bands)[0] ?? ['', []];
  return { name, steps: data as number[][][] };
}

/**
 * 动态栅格时序播放器（P7 / README Phase 6 兑现）。
 *
 * - 内存帧源（window 读已含全部 steps）：渲染帧位图进 LRU，播放为纯 blit；
 * - 播放循环走 rAF + shouldRenderFrame 丢帧决策（超预算不堆积任务）；
 * - 键盘：Space 播放/暂停，←/→ 步进（WAI-APG；speed 档位可选）；
 * - reduced-motion：禁自动播放（初始即暂停），仅手动步进；
 * - nodata 掩膜 + min-max 拉伸 + colorbar 图例联动（continuous ramp 与
 *   raster-canvas 同一色带常量）。
 */
export function TimelinePlayer({ result, onClose }: TimelinePlayerProps) {
  const { name, steps } = bandData(result);
  const total = steps.length;
  const reducedMotion = usePrefersReducedMotion();

  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(!reducedMotion && total > 1);
  const [speed, setSpeed] = useState<(typeof SPEEDS)[number]>(1);
  const [range, setRange] = useState<[number, number]>([0, Math.max(0, total - 1)]);
  const [ramp, setRamp] = useState<ColorRampName>('viridis');
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const frameCache = useRef(new LruCache<string, string>(FRAME_CACHE_SIZE));
  const lastRender = useRef({ ms: 0, at: 0 });

  const stats = useMemo(
    () => (total > 0 ? bandStats(steps[0], result.nodata) : null),
    [steps, result.nodata, total],
  );

  // 步进（首尾钳制 + 范围钳制）。
  const step = useCallback(
    (delta: number) => {
      setIndex((i) => Math.min(range[1], Math.max(range[0], i + delta)));
    },
    [range],
  );

  // 播放循环：rAF 驱动；丢帧决策保预算；reduced-motion 恒暂停。
  useEffect(() => {
    if (!playing || reducedMotion || total <= 1) return;
    let rafId = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const interval = BASE_INTERVAL_MS / speed;
      if (now - last >= interval) {
        last = now;
        setIndex((i) => {
          if (i >= range[1]) return range[0]; // 循环回放
          return i + 1;
        });
      }
      rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafId);
  }, [playing, reducedMotion, speed, range, total]);

  // 帧渲染：位图 LRU 命中 → 纯 blit；未命中 → gridToRgba（超预算丢帧保护）。
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || total === 0) return;
    const ctx = canvas.getContext('2d');
    // jsdom / 老浏览器的 2d stub 可能缺 createImageData —— 优雅降级（UI 仍可
    // 步进/播控，仅无帧位图），不抛错。
    if (!ctx || typeof ctx.createImageData !== 'function' || typeof ctx.putImageData !== 'function') {
      return;
    }
    const cacheKey = `${index}|${ramp}`;
    const hit = frameCache.current.get(cacheKey);
    if (hit) {
      const img = new Image();
      img.onload = () => {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0);
      };
      img.src = hit;
      return;
    }
    const t0 = performance.now();
    if (!shouldRenderFrame(lastRender.current.ms, FRAME_BUDGET_MS, t0 - lastRender.current.at)) {
      return; // 丢帧：当前显示保留上一帧内容（不白闪、不堆积）
    }
    const grid = steps[Math.min(total - 1, index)] ?? [];
    const { pixels, width, height } = gridToRgba(grid, {
      nodata: result.nodata,
      domain: stats ? { min: stats.min, max: stats.max } : undefined,
      ramp,
    });
    canvas.width = Math.max(1, width);
    canvas.height = Math.max(1, height);
    const image = ctx.createImageData(width, height);
    image.data.set(pixels);
    ctx.putImageData(image, 0, 0);
    const url = canvas.toDataURL('image/png');
    frameCache.current.put(cacheKey, url);
    lastRender.current = { ms: performance.now() - t0, at: performance.now() };
  }, [index, ramp, steps, stats, result.nodata, total]);

  // 键盘可达（对话框内监听；不与 rail 冲突 —— 挂在容器 div）。
  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === ' ') {
        e.preventDefault();
        if (!reducedMotion) setPlaying((p) => !p);
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault();
        step(-1);
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        step(1);
      }
    },
    [reducedMotion, step],
  );

  const addLayer = useHudStore((s) => s.addLayer);
  const mountCurrentFrame = useCallback(() => {
    if (typeof document === 'undefined' || !result.transform) return;
    const grid = steps[Math.min(total - 1, index)] ?? [];
    const url = gridToDataUrl(grid, { nodata: result.nodata, ramp });
    if (!url) return;
    const [a, , c, , e, f] = result.transform;
    const rows = grid.length;
    const cols = rows > 0 ? grid[0].length : 0;
    if (rows === 0 || cols === 0) return;
    const minx = c;
    const maxx = a * cols + c;
    const maxy = f;
    const miny = e * rows + f;
    addLayer({
      id: `lakehouse-timeline-${Date.now()}`,
      name: `数据湖 · ${name} @ ${result.times[Math.min(total - 1, index)]}`,
      type: 'heatmap',
      visible: true,
      opacity: 0.85,
      group: 'analysis',
      source: { image: url, bbox: [Math.min(minx, maxx), Math.min(miny, maxy), Math.max(minx, maxx), Math.max(miny, maxy)] },
      provenance: { result_ref: 'lakehouse-timeline' },
    });
  }, [addLayer, index, name, ramp, result, steps, total]);

  if (total === 0) {
    return (
      <div className="text-caption text-ink-muted">窗口无时间步，无法播放。</div>
    );
  }

  return (
    <div data-testid="lakehouse-timeline-player" onKeyDown={onKeyDown} tabIndex={-1}>
      <div className="mb-2 flex items-center justify-between gap-2">
        <p className="text-body font-semibold text-ink">
          时序播放 · {name}
          <span className="ml-2 text-caption text-ink-muted">{total} 步</span>
        </p>
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭播放器"
          className="rounded-sm p-1 text-ink-secondary transition-colors hover:bg-surface-hover"
        >
          <X size={14} aria-hidden />
        </button>
      </div>

      <canvas
        ref={canvasRef}
        className="w-full rounded-sm border border-edge-subtle bg-surface-sunken"
        aria-label={`栅格帧 ${index + 1} / ${total}`}
        role="img"
      />

      {/* colorbar 图例联动（与帧渲染同一 ramp 常量） */}
      <div className="mt-1.5 flex items-center gap-2" data-testid="lakehouse-colorbar">
        <span className="font-mono text-micro text-ink-muted">
          {stats ? stats.min.toPrecision(3) : '—'}
        </span>
        <div
          className="h-2 flex-1 rounded-pill"
          style={{
            background: `linear-gradient(to right, ${COLOR_RAMP.map((_, i) => {
              const [r, g, b] = rampColor(i / (COLOR_RAMP.length - 1), ramp);
              return `rgb(${r},${g},${b})`;
            }).join(', ')})`,
          }}
          aria-hidden
        />
        <span className="font-mono text-micro text-ink-muted">
          {stats ? stats.max.toPrecision(3) : '—'}
        </span>
        <select
          value={ramp}
          onChange={(e) => setRamp(e.target.value as ColorRampName)}
          aria-label="色带"
          className="rounded-sm border border-edge-subtle bg-surface-sunken px-1 py-0.5 text-micro text-ink-secondary"
        >
          <option value="viridis">viridis</option>
          <option value="inferno">inferno</option>
          <option value="grayscale">grayscale</option>
        </select>
      </div>

      {/* 时间轴：range slider 双端 + 步进指示 */}
      <div className="mt-2 flex items-center gap-2">
        <input
          type="range"
          min={0}
          max={Math.max(0, total - 1)}
          value={index}
          onChange={(e) => setIndex(Number(e.target.value))}
          aria-label="播放位置"
          aria-valuetext={formatStepLabel(result.times[Math.min(total - 1, index)] ?? '')}
          className="w-full accent-[var(--agent-accent)]"
        />
      </div>
      <div className="mt-1 flex items-center justify-between text-micro text-ink-muted">
        <label className="flex items-center gap-1">
          起
          <input
            type="number"
            min={0}
            max={total - 1}
            value={range[0]}
            onChange={(e) => setRange(([, hi]) => [Math.max(0, Math.min(Number(e.target.value), hi)), hi])}
            aria-label="播放范围起点"
            className="w-14 rounded-sm border border-edge-subtle bg-surface-sunken px-1 text-ink"
          />
        </label>
        <span className="font-mono" data-testid="lakehouse-timeline-current">
          {formatStepLabel(result.times[Math.min(total - 1, index)] ?? String(index))}
        </span>
        <label className="flex items-center gap-1">
          止
          <input
            type="number"
            min={0}
            max={total - 1}
            value={range[1]}
            onChange={(e) => setRange(([lo]) => [lo, Math.min(total - 1, Math.max(Number(e.target.value), lo))])}
            aria-label="播放范围终点"
            className="w-14 rounded-sm border border-edge-subtle bg-surface-sunken px-1 text-ink"
          />
        </label>
      </div>

      {/* 控制：播放/暂停（reduced-motion 禁用）、步进、倍速、上图 */}
      <div className="mt-2 flex items-center justify-center gap-2">
        <button
          type="button"
          onClick={() => step(-1)}
          aria-label="上一步"
          className="rounded-sm bg-surface-sunken p-1.5 text-ink-secondary transition-colors hover:bg-surface-hover"
        >
          <ChevronLeft size={14} aria-hidden />
        </button>
        <button
          type="button"
          onClick={() => !reducedMotion && setPlaying((p) => !p)}
          disabled={reducedMotion}
          aria-label={playing ? '暂停' : '播放'}
          title={reducedMotion ? '系统已开启减弱动态效果 —— 仅手动步进' : undefined}
          data-testid="lakehouse-timeline-play"
          className="flex items-center justify-center gap-1 rounded-sm bg-status-accent px-3 py-1.5 text-caption font-medium text-ink-on-accent transition-opacity hover:opacity-85 disabled:opacity-40"
        >
          {playing ? <Pause size={12} aria-hidden /> : <Play size={12} aria-hidden />}
          {playing ? '暂停' : '播放'}
        </button>
        <button
          type="button"
          onClick={() => step(1)}
          aria-label="下一步"
          className="rounded-sm bg-surface-sunken p-1.5 text-ink-secondary transition-colors hover:bg-surface-hover"
        >
          <ChevronRight size={14} aria-hidden />
        </button>
        <label className="ml-2 flex items-center gap-1 text-caption text-ink-secondary">
          倍速
          <select
            value={speed}
            onChange={(e) => setSpeed(Number(e.target.value) as (typeof SPEEDS)[number])}
            aria-label="播放倍速"
            className="rounded-sm border border-edge-subtle bg-surface-sunken px-1 py-0.5 text-micro text-ink"
          >
            {SPEEDS.map((s) => (
              <option key={s} value={s}>{s}×</option>
            ))}
          </select>
        </label>
        <button
          type="button"
          onClick={mountCurrentFrame}
          className="ml-auto rounded-sm bg-surface-sunken px-2 py-1 text-caption text-ink-secondary transition-colors hover:bg-surface-hover"
        >
          当前帧上图
        </button>
      </div>
    </div>
  );
}
