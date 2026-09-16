'use client';

/**
 * Cockpit 共享小件：状态徽章 / KV 行 / 诚实空态 / 虚拟化列表骨架。
 *
 * 纪律：状态文案一律「颜色 + 文字」双通道（色盲安全）；空态说明数据为何
 * 缺席（服务端没给 ≠ 前端猜）；列表一律走 useVirtualRows 窗口化。
 */
import type { ReactNode } from 'react';
import { useVirtualRows } from '@/lib/hooks/use-virtual-rows';
import { useT } from '@/lib/i18n/useT';

export type Tone = 'neutral' | 'active' | 'warn' | 'bad' | 'good';

const TONE_CLASS: Record<Tone, string> = {
  neutral: 'bg-status-neutral-soft text-ink-secondary',
  active: 'bg-status-accent-soft text-status-accent',
  warn: 'bg-status-warning-soft text-status-warning',
  bad: 'bg-status-critical-soft text-status-critical',
  good: 'bg-status-success-soft text-status-success',
};

/** Mission/Swarm/Claim 状态 → 色调（仅视觉通道；文字仍由投影原文给出）。 */
export function stateTone(state: string): Tone {
  switch (state) {
    case 'running':
    case 'RUNNING':
    case 'recovering':
      return 'active';
    case 'suspended':
    case 'waiting_dependency':
    case 'partially_complete':
    case 'PENDING':
    case 'READY':
      return 'warn';
    case 'failed':
    case 'FAILED':
    case 'contradicted':
    case 'UNRESOLVED':
      return 'bad';
    case 'complete':
    case 'COMPLETE':
    case 'SUCCEEDED':
    case 'supported':
      return 'good';
    default:
      return 'neutral';
  }
}

export function StatusBadge({ state, label }: { state: string; label: string }) {
  return (
    <span
      data-testid={`cockpit-badge-${state}`}
      className={`inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-meta font-medium ${TONE_CLASS[stateTone(state)]}`}
    >
      {label}
    </span>
  );
}

export function Kv({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline gap-2 text-meta">
      <span className="shrink-0 text-ink-muted">{label}</span>
      <span className="min-w-0 break-words text-ink">{children}</span>
    </div>
  );
}

export function EmptyState({ testId, note, children }: { testId: string; note?: string; children: ReactNode }) {
  return (
    <div
      data-testid={testId}
      className="flex flex-col items-center justify-center gap-1 rounded-sm border border-dashed border-edge-subtle px-3 py-6 text-center"
    >
      <p className="text-meta text-ink-secondary">{children}</p>
      {note ? <p className="text-meta text-ink-muted">{note}</p> : null}
    </div>
  );
}

export interface VirtualListProps<T> {
  items: T[];
  rowHeight: number;
  rowTestId: string;
  renderRow: (item: T, index: number) => ReactNode;
  ariaLabel: string;
  testId?: string;
}

/** 固定行高窗口化列表（复用 useVirtualRows；10k 行不 O(N) 渲染）。 */
export function VirtualList<T>({
  items, rowHeight, rowTestId, renderRow, ariaLabel, testId,
}: VirtualListProps<T>) {
  const { scrollRef, start, end, totalHeight, offsetY, onScroll } = useVirtualRows(
    items.length,
    rowHeight,
  );
  return (
    <div
      ref={scrollRef}
      onScroll={onScroll}
      data-testid={testId}
      role="list"
      aria-label={ariaLabel}
      className="min-h-0 flex-1 overflow-y-auto"
    >
      <div style={{ height: totalHeight, position: 'relative' }}>
        <div style={{ transform: `translateY(${offsetY}px)` }}>
          {items.slice(start, end).map((item, i) => (
            <div role="listitem" data-testid={rowTestId} key={start + i} style={{ height: rowHeight }}>
              {renderRow(item, start + i)}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/** 彩色圆点 + 文案的活跃指示（reduced-motion 下无脉冲动画）。 */
export function LiveDot({ active, reducedMotion }: { active: boolean; reducedMotion: boolean }) {
  const t = useT('cockpit');
  return (
    <span className="inline-flex items-center gap-1 text-meta text-ink-muted">
      <span
        aria-hidden
        className={`inline-block h-1.5 w-1.5 rounded-full ${active ? 'bg-status-accent' : 'bg-ink-tertiary'} ${active && !reducedMotion ? 'animate-pulse' : ''}`}
      />
      {active ? t('mission.state.running') : t('empty.noData')}
    </span>
  );
}
