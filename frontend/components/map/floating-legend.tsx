'use client';

import { useMemo } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';
import type { Layer } from '@/lib/types/layer';
import { LegendCard, formatLegendValue } from './legends/legend-card';

interface FloatingLegendProps {
  className?: string;
}

// #679 单一色源（退化兜底）：层上无 legend_spec 时的旧固定色带，与 adapter 无
// paint 兜底同款。热力层正常都带后端 legend_spec（heatmap_data 挂
// palette_colors=NATIVE_HEATMAP_COLORS 同源色），此路径仅为无 spec 的退化场景。
const FALLBACK_COLORS = ['#0ff0ff', '#00ff41', '#ffff00', '#ff5f00', '#ff2d55'];
const LABELS = ['极低', '低', '中', '高', '极高'];

// #740: class labels must map 1:1 onto the actual ramp length — a fixed
// 5-label row against an N-color palette pointed 极低/极高 at the wrong
// stops (misleading density reading).
function labelsFor(colorCount: number): string[] {
  if (colorCount === LABELS.length) return LABELS;
  if (colorCount <= 1) return colorCount === 1 ? [LABELS[0]] : [];
  if (colorCount < LABELS.length) {
    return [LABELS[0], ...new Array(colorCount - 2).fill(''), LABELS[LABELS.length - 1]].slice(0, colorCount);
  }
  // more colors than labels: spread labels, middle classes unlabeled
  const out = new Array(colorCount).fill('');
  out[0] = LABELS[0];
  out[colorCount - 1] = LABELS[LABELS.length - 1];
  const mid = Math.floor(colorCount / 2);
  if (mid > 0 && mid < colorCount - 1) out[mid] = '中';
  return out;
}

function legendColorsFor(layer: Layer | undefined): string[] {
  const spec = layer?.legend_spec;
  if (spec && (spec.type === 'continuous' || spec.type === 'divergent')) {
    if (spec.palette_colors && spec.palette_colors.length >= 2) {
      return spec.palette_colors;
    }
  }
  return FALLBACK_COLORS;
}

/**
 * #998：热力层 legend_spec（continuous/divergent）的量化刻度。后端已随
 * legend_spec 下发 min/max/unit，此前被整段丢弃——用户只能看到『极高』
 * 这类定性形容词，不知对应多少密度。带 min/max 时渲染 formatLegendValue
 * 数值行（与专题图例 / MapSpec 色条同一格式化器）；缺失时返回 null，
 * 上层退回定性标签。
 */
function quantRangeFor(
  layer: Layer | undefined,
): { min: number; max: number; unit?: string } | null {
  const spec = layer?.legend_spec;
  if (!spec || (spec.type !== 'continuous' && spec.type !== 'divergent')) return null;
  const { min, max, unit } = spec;
  if (
    typeof min !== 'number' || !Number.isFinite(min) ||
    typeof max !== 'number' || !Number.isFinite(max)
  ) {
    return null;
  }
  return typeof unit === 'string' && unit ? { min, max, unit } : { min, max };
}

/**
 * 热力图图例（#679 后为热力层的唯一图例 —— map-panel 的 ThematicLegend 列表
 * 不再渲染 type=heatmap 层，消灭同屏双图例互相矛盾）。
 *
 * UI V4：改用与四种专题图例相同的 LegendCard 容器 —— 之前它是地图 chrome 里
 * 唯一一套独立视觉系统（硬编码 rgba、blur(20px)、12.5px JetBrains Mono 标题），
 * 其标题色在浅色下只有约 2.4:1，而它就贴在三个已收敛的图例旁边。
 * 色带来源（#679 单一色源）：层的 legend_spec.palette_colors（后端
 * NATIVE_HEATMAP_COLORS，与地图渲染所读 layer.paint 同源）→ 旧固定色带兜底。
 */
export function FloatingLegend({ className }: FloatingLegendProps) {
  const layers = useHudStore((s) => s.layers);
  const visibleHeatLayer = layers.find((l) => l.visible && l.type === 'heatmap');
  const colors = useMemo(() => legendColorsFor(visibleHeatLayer), [visibleHeatLayer]);
  const quantRange = useMemo(() => quantRangeFor(visibleHeatLayer), [visibleHeatLayer]);

  return (
    <div
      className={className}
      style={{
        transform: visibleHeatLayer ? 'translateY(0)' : 'translateY(20px)',
        opacity: visibleHeatLayer ? 1 : 0,
        transition: 'transform 0.25s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.25s cubic-bezier(0.4, 0, 0.2, 1)',
        pointerEvents: visibleHeatLayer ? 'auto' : 'none',
      }}
      aria-hidden={!visibleHeatLayer}
    >
      <LegendCard field={visibleHeatLayer?.name} kind="热力密度渲染">
        <div aria-hidden className="mb-1 flex h-2 overflow-hidden rounded-xs ring-1 ring-inset ring-map-chrome-border">
          {colors.map((color, i) => (
            <div key={`${color}-${i}`} className="flex-1" style={{ backgroundColor: color }} />
          ))}
        </div>
        {quantRange ? (
          /* #998：带 min/max 的 continuous/divergent spec 渲染量化刻度行——
             formatLegendValue 与专题图例/色条同源（千分位、M/k 压缩、非零
             不打印零），同一地图各处读数一致。 */
          <div className="flex items-baseline justify-between gap-2 text-micro tabular-nums text-map-chrome-ink">
            <span>{formatLegendValue(quantRange.min)}</span>
            {quantRange.unit && (
              <span className="min-w-0 truncate text-map-chrome-ink-muted" title={quantRange.unit}>
                {quantRange.unit}
              </span>
            )}
            <span>{formatLegendValue(quantRange.max)}</span>
          </div>
        ) : (
          /* 无 spec 的退化兜底：定性标签 */
          <div className="flex justify-between text-micro text-map-chrome-ink">
            {labelsFor(colors.length).map((label, i) => (
              <span key={`${label}-${i}`}>{label}</span>
            ))}
          </div>
        )}
      </LegendCard>
    </div>
  );
}

export default FloatingLegend;
