'use client';

import { Info } from 'lucide-react';
import type { ReactNode } from 'react';

/**
 * The one legend container.
 *
 * All four legend types previously repeated the same container/​header string by
 * hand and drifted apart anyway: two different number formatters, two different
 * title treatments, no height cap on any of them (a 30-class categorical legend
 * or a 9-break graduated legend simply grew until it reached the top bar), and a
 * divergent legend that printed the continuous legend's footer.
 */

interface LegendCardProps {
  /** The attribute being symbolised; omitted for legends without one. */
  field?: string | null;
  /** Short renderer description shown in the footer, e.g. 「分类专题」. */
  kind: string;
  children: ReactNode;
}

export function LegendCard({ field, kind, children }: LegendCardProps) {
  return (
    <div className="map-chrome flex min-h-0 min-w-[188px] max-w-[260px] flex-col animate-in slide-in-from-right-4 duration-500">
      <div className="flex items-center gap-2 border-b border-map-chrome-border px-panel py-1.5">
        <Info aria-hidden className="h-icon-sm w-icon-sm shrink-0 text-status-accent" />
        <div className="flex min-w-0 flex-col">
          <span className="eyebrow">图例</span>
          {field && (
            <span
              className="truncate text-meta font-semibold text-map-chrome-ink"
              title={field}
            >
              {field}
            </span>
          )}
        </div>
      </div>
      {/* Scrolls instead of growing. The height budget is owned by the stack
          (see the container in map-panel.tsx), not by the card: capping each
          card individually let two legends still overflow the workspace. */}
      <div className="min-h-0 flex-1 overflow-y-auto px-panel py-1.5">{children}</div>
      <div className="border-t border-map-chrome-border px-panel py-1 text-center text-micro text-map-chrome-ink-muted">
        {kind}
      </div>
    </div>
  );
}

/**
 * Shared legend number format: compact for large magnitudes, thousands-grouped
 * otherwise, and stable across legend types (continuous used 1 decimal + M/k
 * while graduated used 0 decimals + M/k for the same data).
 *
 * Fractions keep enough significant digits to stay distinguishable — a fixed
 * single decimal collapsed 0.04 to "0" and -0.04 to "-0", which is exactly the
 * range NDVI, per-capita rates and densities live in.
 */
export function formatLegendValue(n: number): string {
  if (!Number.isFinite(n)) return '—';
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${significant(n / 1_000_000)}M`;
  if (abs >= 10_000) return `${significant(n / 1_000)}k`;
  if (Number.isInteger(n)) return n.toLocaleString('zh-CN');
  return significant(n);
}

/** Up to 3 significant digits, trailing zeros dropped, never rounded to zero. */
function significant(n: number): string {
  const abs = Math.abs(n);
  const decimals = abs >= 100 ? 0 : abs >= 10 ? 1 : abs >= 1 ? 2 : 3;
  const out = Number(n.toFixed(decimals));
  // A non-zero input must never print as zero: fall back to exponential.
  if (out === 0 && n !== 0) return n.toExponential(1);
  return String(out);
}
