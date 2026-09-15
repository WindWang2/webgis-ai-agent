'use client';

/**
 * HistogramSliderWidget — 参数直方图滑动断点调节器（ADR-0194 kind=histogram_slider）。
 * 声明式 payload：bins 直方 + 可拖动断点；应用 → widget_reply {breaks}。
 */
import { useMemo, useState } from 'react';
import { useT } from '@/lib/i18n/useT';
import type { WidgetSpec } from '@/lib/copilot/affordance';
import { sendWidgetReply } from '@/lib/copilot/widget-binding';

interface HistogramPayload {
  field: string;
  bins: Array<{ lo: number; hi: number; count: number }>;
  breaks: number[];
  min: number;
  max: number;
  unit?: string;
  layer_ref?: string;
}

export function HistogramSliderWidget({
  spec,
  onReply,
}: {
  spec: WidgetSpec;
  onReply?: (value: unknown) => void;
}) {
  const payload = spec.payload as unknown as HistogramPayload;
  const [breaks, setBreaks] = useState<number[]>(payload.breaks ?? []);
  const t = useT('copilot');
  const maxCount = useMemo(
    () => Math.max(1, ...payload.bins.map((b) => b.count)),
    [payload.bins],
  );
  const range = payload.max - payload.min || 1;

  const setBreak = (index: number, value: number) => {
    setBreaks((prev) => prev.map((b, i) => (i === index ? value : b)));
  };

  return (
    <div data-testid="histogram-slider-body">
      <svg data-testid="histogram-bars" width="100%" height={64} role="img" aria-label={t('histogramAria', { field: payload.field })}>
        {payload.bins.map((b, i) => {
          const width = 100 / payload.bins.length;
          const height = (b.count / maxCount) * 60;
          return (
            <rect
              key={i}
              x={`${i * width}%`}
              y={62 - height}
              width={`${width - 0.5}%`}
              height={height}
              className="fill-sky-400/60"
            />
          );
        })}
        {breaks.map((brk) => (
          <line
            key={brk}
            x1={`${((brk - payload.min) / range) * 100}%`}
            x2={`${((brk - payload.min) / range) * 100}%`}
            y1={0}
            y2={62}
            className="stroke-amber-400"
            strokeWidth={2}
            strokeDasharray="4 2"
          />
        ))}
      </svg>
      <div className="mt-2 flex items-center gap-2">
        {breaks.map((brk, i) => (
          <input
            key={i}
            type="range"
            data-testid={`break-slider-${i}`}
            aria-label={t('breakLabel', { index: i + 1 })}
            min={payload.min}
            max={payload.max}
            step={range / 100}
            value={brk}
            onChange={(e) => setBreak(i, Number(e.target.value))}
            className="w-full accent-sky-500"
          />
        ))}
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-ink-muted">
        <span>{payload.min}{payload.unit ?? ''}</span>
        <span>{payload.max}{payload.unit ?? ''}</span>
      </div>
      <button
        type="button"
        data-testid="histogram-apply"
        onClick={() => sendWidgetReply(spec, { breaks }, onReply)}
        className="mt-2 w-full rounded-md bg-sky-600 px-2 py-1 text-xs text-white hover:bg-sky-500"
      >
        {t('applyBreaks')}
      </button>
    </div>
  );
}
