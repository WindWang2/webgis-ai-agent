'use client';

/**
 * WidgetHost — 生成式微 UI 挂载点（ADR-0194）。
 *
 * 读 copilotSlice.copilotWidgets（SSE ui_action.mount_widget 经
 * extractMountedWidget 安全校验后入栈），按 kind 注册表渲染卡片。
 * 会话切换由 clearCopilotState 清栈；单卡可手动关闭。
 */
import type { ComponentType } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';
import { useT } from '@/lib/i18n/useT';
import type { WidgetSpec } from '@/lib/copilot/affordance';
import { WIDGET_KINDS } from '@/lib/copilot/affordance';
import { HistogramSliderWidget } from './histogram-slider';
import { SwipeCompareWidget } from './swipe-compare';
import { CandidatePickerWidget } from './candidate-picker';
import { SketchBoxWidget } from './sketch-box';

const WIDGET_REGISTRY: Record<
  WidgetSpec['kind'],
  ComponentType<{ spec: WidgetSpec; onReply?: (value: unknown) => void }>
> = {
  histogram_slider: HistogramSliderWidget,
  swipe_compare: SwipeCompareWidget,
  candidate_picker: CandidatePickerWidget,
  sketch_box: SketchBoxWidget,
};

const KIND_LABEL_KEYS: Record<WidgetSpec['kind'], string> = {
  histogram_slider: 'kindHistogram',
  swipe_compare: 'kindSwipe',
  candidate_picker: 'kindCandidate',
  sketch_box: 'kindSketchBox',
};

function WidgetCard({ spec }: { spec: WidgetSpec }) {
  const dismissCopilotWidget = useHudStore((s) => s.dismissCopilotWidget);
  const t = useT('copilot');
  const Body = WIDGET_REGISTRY[spec.kind] ?? null;
  return (
    <div
      data-testid={`generative-widget-${spec.kind}`}
      className="w-64 rounded-lg border border-edge bg-surface-raised p-3 shadow-lg"
    >
      <header className="mb-2 flex items-center justify-between">
        <span className="text-xs font-medium text-ink">
          {spec.title || t(KIND_LABEL_KEYS[spec.kind])}
        </span>
        <button
          type="button"
          data-testid="widget-dismiss"
          aria-label={t('dismiss')}
          onClick={() => dismissCopilotWidget(spec.widget_id)}
          className="text-ink-muted hover:text-ink"
        >
          ×
        </button>
      </header>
      {Body ? <Body spec={spec} /> : null}
    </div>
  );
}

export function WidgetHost() {
  // ?? []：测试以部分状态 mock store（repo 惯例），挂载点须容忍缺字段。
  const widgets = useHudStore((s) => s.copilotWidgets) ?? [];
  if (widgets.length === 0) return null;
  // 防御：进入渲染面前再过一遍词表（push 时已校验，这里兜底旧持久态）。
  const safe = widgets.filter((w) => WIDGET_KINDS.includes(w.spec.kind));
  if (safe.length === 0) return null;
  return (
    <div
      data-testid="copilot-widget-host"
      className="pointer-events-auto absolute right-3 top-14 z-40 flex flex-col gap-2"
    >
      {safe.map((w) => (
        <WidgetCard key={w.spec.widget_id} spec={w.spec} />
      ))}
    </div>
  );
}
