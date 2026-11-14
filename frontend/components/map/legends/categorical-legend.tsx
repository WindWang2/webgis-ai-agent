'use client';

import React from 'react';
import type { CategoricalLegendSpec } from '@/lib/map-kit/types';
import { resolveBindingSwatch } from '@/lib/cartographic-assets/legend-swatch';
import { useT } from '@/lib/i18n/useT';
import { LegendCard } from './legend-card';

interface Props {
  spec: CategoricalLegendSpec;
  /** G40：同层符号资产绑定 —— swatch 与地图同一规范形/参数（WP08）。 */
  symbolAsset?: Record<string, unknown> | null;
}

/** 绑定 → 内联 SVG swatch（doc 由 builtin/缓存取；缺席回退色块）。 */
function AssetSwatch({ symbolAsset }: { symbolAsset: Record<string, unknown> }) {
  const html = React.useMemo(() => {
    try {
      const binding = symbolAsset as unknown as Parameters<
        typeof resolveBindingSwatch>[0];
      const svg = resolveBindingSwatch(binding);
      return svg;
    } catch {
      return null;
    }
  }, [symbolAsset]);
  if (!html) return null;
  return (
    <svg
      width="16" height="13" viewBox="0 0 16 13" role="img"
      className="shrink-0 rounded-xs ring-1 ring-inset ring-map-chrome-border"
      data-testid="cat-swatch-asset"
      dangerouslySetInnerHTML={{ __html: `<rect width="16" height="13" fill="#ffffff"/>${html}` }}
    />
  );
}

export function CategoricalLegend({ spec, symbolAsset }: Props) {
  const t = useT();
  const { field, categories } = spec;
  return (
    <LegendCard field={field} kind={t('map.legends.categorical')}>
      <div className="space-y-1">
        {categories.map((c) => (
          <div key={c.key} className="flex items-center gap-2">
            {symbolAsset ? (
              <div
                data-testid="cat-swatch"
                className="shrink-0 rounded-xs ring-1 ring-inset ring-map-chrome-border"
              >
                <AssetSwatch symbolAsset={symbolAsset} />
              </div>
            ) : (
              <div
                data-testid="cat-swatch"
                className="h-icon-sm w-icon-sm shrink-0 rounded-xs ring-1 ring-inset ring-map-chrome-border"
                style={{ backgroundColor: c.color }}
              />
            )}
            <span className="truncate text-meta text-map-chrome-ink" title={c.label}>
              {c.label}
            </span>
          </div>
        ))}
        {spec.default && (
          <div className="flex items-center gap-2 opacity-80" data-testid="cat-other-swatch">
            <div
              className="h-icon-sm w-icon-sm shrink-0 rounded-xs ring-1 ring-dashed ring-inset ring-map-chrome-border"
              style={{ backgroundColor: spec.default.color }}
            />
            <span className="truncate text-meta text-map-chrome-ink italic">
              {spec.default.label || t('map.legends.other')}
            </span>
          </div>
        )}
      </div>
    </LegendCard>
  );
}
