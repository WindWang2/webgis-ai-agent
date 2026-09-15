'use client';

/**
 * SwipeCompareWidget — 双方案卷帘对比卡（ADR-0194 kind=swipe_compare）。
 * 滑块控制分割位置；选定一侧 → widget_reply {selected}。
 */
import { useState } from 'react';
import { useT } from '@/lib/i18n/useT';
import type { WidgetSpec } from '@/lib/copilot/affordance';
import { sendWidgetReply } from '@/lib/copilot/widget-binding';

interface SwipePayload {
  left: { label: string; layer_ref: string };
  right: { label: string; layer_ref: string };
}

export function SwipeCompareWidget({
  spec,
  onReply,
}: {
  spec: WidgetSpec;
  onReply?: (value: unknown) => void;
}) {
  const payload = spec.payload as unknown as SwipePayload;
  const [position, setPosition] = useState(50);
  const t = useT('copilot');

  return (
    <div data-testid="swipe-compare-body">
      <div className="relative h-16 overflow-hidden rounded-md border border-edge">
        <div
          data-testid="swipe-pane-left"
          className="absolute inset-y-0 left-0 flex items-center justify-center bg-sky-500/20 text-xs text-ink"
          style={{ width: `${position}%` }}
        >
          {payload.left.label}
        </div>
        <div
          data-testid="swipe-pane-right"
          className="absolute inset-y-0 right-0 flex items-center justify-center bg-emerald-500/20 text-xs text-ink"
          style={{ width: `${100 - position}%` }}
        >
          {payload.right.label}
        </div>
        <div
          className="absolute inset-y-0 w-px bg-amber-400"
          style={{ left: `${position}%` }}
        />
      </div>
      <input
        type="range"
        data-testid="swipe-position"
        aria-label={t('positionAria')}
        min={0}
        max={100}
        value={position}
        onChange={(e) => setPosition(Number(e.target.value))}
        className="mt-2 w-full accent-sky-500"
      />
      <div className="mt-2 flex gap-2">
        <button
          type="button"
          data-testid="swipe-choose-left"
          onClick={() => sendWidgetReply(spec, { selected: 'left' }, onReply)}
          className="flex-1 rounded-md border border-edge px-2 py-1 text-xs text-ink hover:bg-surface-hover"
        >
          {t('useChoice', { label: payload.left.label })}
        </button>
        <button
          type="button"
          data-testid="swipe-choose-right"
          onClick={() => sendWidgetReply(spec, { selected: 'right' }, onReply)}
          className="flex-1 rounded-md border border-edge px-2 py-1 text-xs text-ink hover:bg-surface-hover"
        >
          {t('useChoice', { label: payload.right.label })}
        </button>
      </div>
    </div>
  );
}
