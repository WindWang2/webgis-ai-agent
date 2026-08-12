'use client';

import { useHudStore } from '@/lib/store/useHudStore';
import { LegendCard } from './legends/legend-card';

interface FloatingLegendProps {
  className?: string;
}

const COLORS = ['#0ff0ff', '#00ff41', '#ffff00', '#ff5f00', '#ff2d55'];
const LABELS = ['极低', '低', '中', '高', '极高'];

/**
 * 热力图图例。
 *
 * UI V4：改用与四种专题图例相同的 LegendCard 容器 —— 之前它是地图 chrome 里
 * 唯一一套独立视觉系统（硬编码 rgba、blur(20px)、12.5px JetBrains Mono 标题），
 * 其标题色在浅色下只有约 2.4:1，而它就贴在三个已收敛的图例旁边。
 * 色带本身是热力图渲染器的固定配色，属于数据编码，保持原样。
 */
export function FloatingLegend({ className }: FloatingLegendProps) {
  const layers = useHudStore((s) => s.layers);
  const visibleHeatLayer = layers.find((l) => l.visible && l.type === 'heatmap');

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
          {COLORS.map((color) => (
            <div key={color} className="flex-1" style={{ backgroundColor: color }} />
          ))}
        </div>
        <div className="flex justify-between text-micro text-map-chrome-ink">
          {LABELS.map((label) => (
            <span key={label}>{label}</span>
          ))}
        </div>
      </LegendCard>
    </div>
  );
}

export default FloatingLegend;
