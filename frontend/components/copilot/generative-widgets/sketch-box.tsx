'use client';

/**
 * SketchBoxWidget — 草图框选请求卡（ADR-0194 kind=sketch_box）。
 * CTA 激活 copilot 框选工具；高亮环完成后自动回传几何（双向绑定闭环）。
 */
import { useEffect, useRef } from 'react';
import { useT } from '@/lib/i18n/useT';
import { useHudStore } from '@/lib/store/useHudStore';
import type { WidgetSpec } from '@/lib/copilot/affordance';
import { ringToPolygon } from '@/lib/copilot/affordance';
import { sendWidgetReply } from '@/lib/copilot/widget-binding';

interface SketchBoxPayload {
  prompt: string;
  baseline_geometry?: { type: string; coordinates: unknown };
  constraints?: { min_area_m2?: number; max_area_m2?: number };
  layer_ref?: string;
}

export function SketchBoxWidget({
  spec,
  onReply,
}: {
  spec: WidgetSpec;
  onReply?: (value: unknown) => void;
}) {
  const payload = spec.payload as unknown as SketchBoxPayload;
  const armedRef = useRef(false);
  const setCopilotTool = useHudStore((s) => s.setCopilotTool);
  const t = useT('copilot');
  const highlight = useHudStore((s) => s.copilotHighlight);

  const arm = () => {
    armedRef.current = true;
    setCopilotTool('box_select');
  };

  useEffect(() => {
    if (!armedRef.current || !highlight?.lnglatRing) return;
    armedRef.current = false;
    const geometry = ringToPolygon(highlight.lnglatRing);
    if (!geometry) return;
    setCopilotTool(null);
    sendWidgetReply(spec, { geometry }, onReply);
  }, [highlight, spec, onReply, setCopilotTool]);

  const constraintHint = payload.constraints
    ? [
        payload.constraints.min_area_m2 != null
          ? `≥${payload.constraints.min_area_m2}m²`
          : null,
        payload.constraints.max_area_m2 != null
          ? `≤${payload.constraints.max_area_m2}m²`
          : null,
      ]
        .filter(Boolean)
        .join(' ')
    : '';

  return (
    <div data-testid="sketch-box-body">
      <p className="text-xs text-ink">{payload.prompt}</p>
      {constraintHint && (
        <p className="mt-1 text-[10px] text-ink-muted">{t('constraintHint', { hint: constraintHint })}</p>
      )}
      <button
        type="button"
        data-testid="sketch-box-start"
        onClick={arm}
        className="mt-2 w-full rounded-md bg-sky-600 px-2 py-1 text-xs text-white hover:bg-sky-500"
      >
        {t('startSketchBox')}
      </button>
    </div>
  );
}
