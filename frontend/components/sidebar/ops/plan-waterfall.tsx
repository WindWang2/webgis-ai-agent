'use client';

/**
 * 阶段瀑布图（P4）—— 从 run 事件流（after_id 游标轮询）推导节点级
 * start→end 区间的纯展示组件。
 *
 * 区间语义：起点 = node_dispatched/node_started 早者；终点 = 终局事件
 * （completed/reused/failed/cancelled/lost）；仅有起点的事件画进行中条。
 * 时间轴相对整窗（最早事件 → 最晚事件），条宽按百分比定位。
 * 投机副本（speculative_dispatch/resolved）与等待资源作为事件注记行。
 */
import type { GeoComputeRunEvent } from '@/lib/api/geocompute';

const TERMINAL_FOR_NODE = new Set([
  'node_completed',
  'node_reused',
  'node_failed',
  'node_cancelled',
  'node_lost',
]);

const STATE_COLOR: Record<string, string> = {
  completed: 'bg-status-success',
  reused: 'bg-status-info',
  failed: 'bg-status-critical',
  cancelled: 'bg-status-neutral-border',
  lost: 'bg-status-warning',
  running: 'bg-status-info/60 animate-pulse motion-reduce:animate-none',
};

interface NodeSpan {
  nodeId: string;
  startMs: number;
  endMs: number | null;
  state: keyof typeof STATE_COLOR;
  attempts: number;
}

export function deriveSpans(events: GeoComputeRunEvent[]): {
  spans: NodeSpan[];
  notes: GeoComputeRunEvent[];
  window: [number, number] | null;
} {
  const byNode = new Map<string, NodeSpan>();
  const notes: GeoComputeRunEvent[] = [];
  let window: [number, number] | null = null;

  for (const ev of events) {
    const ts = ev.created_at ? Date.parse(ev.created_at) : NaN;
    if (!Number.isFinite(ts)) continue;
    if (!window) window = [ts, ts];
    else {
      window[0] = Math.min(window[0], ts);
      window[1] = Math.max(window[1], ts);
    }
    if (!ev.node_id) {
      if (ev.event !== 'run_started') notes.push(ev);
      continue;
    }
    switch (ev.event) {
      case 'node_dispatched':
      case 'node_started': {
        const span = byNode.get(ev.node_id) ?? {
          nodeId: ev.node_id,
          startMs: ts,
          endMs: null,
          state: 'running' as const,
          attempts: 0,
        };
        span.startMs = Math.min(span.startMs, ts);
        if (ev.attempt != null) span.attempts = Math.max(span.attempts, ev.attempt);
        byNode.set(ev.node_id, span);
        break;
      }
      case 'node_output_ready':
      case 'partition_planned':
      case 'waiting_resource':
        notes.push(ev);
        break;
      default:
        if (TERMINAL_FOR_NODE.has(ev.event)) {
          const span =
            byNode.get(ev.node_id) ?? {
              nodeId: ev.node_id,
              startMs: ts,
              endMs: null,
              state: 'running' as const,
              attempts: 0,
            };
          span.endMs = ts;
          span.state =
            ev.event === 'node_completed'
              ? 'completed'
              : ev.event === 'node_reused'
                ? 'reused'
                : ev.event === 'node_failed'
                  ? 'failed'
                  : ev.event === 'node_lost'
                    ? 'lost'
                    : 'cancelled';
          if (ev.attempt != null) span.attempts = Math.max(span.attempts, ev.attempt);
          byNode.set(ev.node_id, span);
        } else if (ev.event.startsWith('speculative_') || ev.event === 'straggler_detected' || ev.event === 'gpu_fallback' || ev.event === 'poison_quarantined') {
          notes.push(ev);
        }
    }
  }

  return { spans: [...byNode.values()].sort((a, b) => a.startMs - b.startMs), notes, window };
}

export function PlanWaterfall({
  events,
  runId,
}: {
  events: GeoComputeRunEvent[];
  runId: string;
}) {
  const { spans, notes, window } = deriveSpans(events);
  const lo = window?.[0] ?? 0;
  const hi = Math.max(window?.[1] ?? 1, lo + 1);

  if (events.length === 0) {
    return (
      <p className="px-2 py-3 text-center text-meta text-ink-muted" role="status">
        暂无事件 —— {runId}（等待游标轮询首页）
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-1" data-testid="ops-waterfall" aria-label={`run ${runId} 阶段瀑布`}>
      {spans.map((span) => {
        const leftPct = ((span.startMs - lo) / (hi - lo)) * 100;
        const endMs = span.endMs ?? Date.now();
        const widthPct = Math.max(1.5, ((endMs - span.startMs) / (hi - lo)) * 100);
        return (
          <div key={span.nodeId} className="flex items-center gap-2">
            <span className="w-24 shrink-0 truncate text-right font-mono text-micro text-ink-secondary" title={span.nodeId}>
              {span.nodeId}
            </span>
            <div className="relative h-3 flex-1 rounded-sm bg-surface-sunken">
              <div
                className={`absolute inset-y-0 rounded-sm ${STATE_COLOR[span.state] ?? STATE_COLOR.running}`}
                style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
                role="img"
                aria-label={`${span.nodeId} ${span.state}${span.attempts > 0 ? `（尝试 ${span.attempts} 次）` : ''}`}
              />
            </div>
            <span className="w-14 shrink-0 text-micro text-ink-muted">
              {span.state === 'running' ? '进行中' : span.state}
            </span>
          </div>
        );
      })}
      {notes.length > 0 && (
        <details className="mt-1 rounded-sm border border-edge-subtle bg-surface-sunken px-2 py-1">
          <summary className="cursor-pointer text-micro font-medium text-ink-secondary">
            事件注记（{notes.length}）—— 投机/等待/分区/降级
          </summary>
          <ul className="mt-1 flex flex-col gap-0.5">
            {notes.slice(-12).map((ev) => (
              <li key={ev.id} className="text-micro text-ink-muted">
                <span className="font-mono">{ev.event}</span>
                {ev.node_id ? ` @ ${ev.node_id}` : ''}
                {ev.status ? ` · ${ev.status}` : ''}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
