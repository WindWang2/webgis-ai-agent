'use client';

import React, { useState } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';
import { useMapAction } from '@/lib/contexts/map-action-context';
import { TILE_PROVIDERS } from '@/lib/providers';
import { STitle } from '@/components/shared/section-title';
import { Check } from 'lucide-react';

const CRS_OPTIONS = [
  { code: 'EPSG:4326', label: 'WGS 84' },
  { code: 'EPSG:3857', label: 'Web Mercator' },
  { code: 'EPSG:4490', label: 'CGCS2000' },
];

/** 卡片副标题由 provider 元数据派生（TILE_PROVIDERS 无独立 desc 字段）。 */
function providerDesc(provider: (typeof TILE_PROVIDERS)[number]): string {
  return provider.type === 'vector' ? 'GL 矢量样式' : 'XYZ 栅格瓦片';
}

/**
 * 地图配置 — Basemap 卡片与真正驱动渲染的 TILE_PROVIDERS 同源（#550）。
 *
 * 历史上这里用 DEFAULT_MAP_STYLES（demo 词汇表：'OSM Voyager' 等）渲染卡片，
 * 而实际地图由 TILE_PROVIDERS[selectedBaseLayer] 驱动 —— 两个词汇表零交集，
 * 点击卡片只写了一个任何渲染路径都不认识的 baseLayer 名字，切换是 no-op。
 * 现在：
 *   - 卡片列表直接渲染 TILE_PROVIDERS（唯一事实源，与顶栏切换器同源）；
 *   - 点击双写：setSelectedBaseLayer(idx)（驱动 MAP_STYLES[idx] 真正换底图）
 *     + setBaseLayer(provider.name)（驱动 HUD/env 摘要的规范名），
 *     与 baselayer-switcher.tsx 的 ISSUE-001/002/003 双写模式一致；
 *   - "Add Custom Basemap" 表单已移除：渲染是静态注册表下标驱动的，
 *     表单追加的条目永远无法变成可渲染的 provider（此前是假控件）。
 */
export function MapConfig() {
  const baseLayer = useHudStore((s) => s.baseLayer);
  const setBaseLayer = useHudStore((s) => s.setBaseLayer);
  const { selectedBaseLayer, setSelectedBaseLayer } = useMapAction();

  const [crs, setCrs] = useState('EPSG:3857');

  return (
    <div className="flex flex-col gap-5">
      <STitle title="地图配置" sub="Map Configuration" />

      {/* Basemap style cards — sourced from TILE_PROVIDERS, same vocabulary
          the top-bar switcher and the renderer index use. */}
      <div>
        <div className="text-heading uppercase tracking-wider text-ink-muted font-semibold mb-3">
          Basemap Style
        </div>
        <div className="grid grid-cols-3 gap-2">
          {TILE_PROVIDERS.map((provider, idx) => {
            const isActive = selectedBaseLayer === idx || baseLayer === provider.name;
            return (
              <button
                key={provider.id}
                aria-pressed={isActive}
                onClick={() => {
                  // 双写（与 baselayer-switcher.tsx 相同模式）：两个 state 必须一致，
                  // 否则标签与真实底图再次脱钩（#550 根因）。
                  setSelectedBaseLayer(idx);
                  setBaseLayer(provider.name);
                }}
                className="flex flex-col items-center justify-center gap-1.5 rounded-md border-2 px-2 py-3 transition-all"
                style={{
                  borderColor: isActive
                    ? 'var(--agent-accent, #16a34a)'
                    : 'var(--border-subtle)',
                  backgroundColor: isActive
                    ? 'color-mix(in srgb, var(--agent-accent, #16a34a) 4%, transparent)'
                    : 'var(--surface-raised)',
                }}
              >
                {/* Mini preview */}
                <div
                  className="flex items-center justify-center rounded-md"
                  style={{
                    width: 48,
                    height: 32,
                    background: isActive
                      ? 'linear-gradient(135deg, color-mix(in srgb, var(--agent-accent, #16a34a) 15%, transparent), color-mix(in srgb, var(--agent-accent, #16a34a) 5%, transparent))'
                      : 'linear-gradient(135deg, var(--surface-sunken), var(--surface-raised))',
                    border: isActive
                      ? '1px solid color-mix(in srgb, var(--agent-accent, #16a34a) 20%, transparent)'
                      : '1px solid var(--border-subtle)',
                  }}
                >
                  {isActive && (
                    /* 选中勾是状态图标，accent 作文字 —— 用 text-safe 变体。 */
                    <Check size={16} className="text-agent-accent" />
                  )}
                </div>
                <span
                  className="text-title font-medium leading-tight"
                  style={{
                    color: isActive ? 'var(--agent-accent)' : 'var(--text-secondary)',
                  }}
                >
                  {provider.name}
                </span>
                <span className="text-body leading-tight text-ink-muted">
                  {providerDesc(provider)}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {/* CRS selection */}
      <div>
        <div className="text-heading uppercase tracking-wider text-ink-muted font-semibold mb-3">
          Coordinate Reference System
        </div>
        <div className="flex gap-2">
          {CRS_OPTIONS.map((opt) => {
            const isActive = crs === opt.code;
            return (
              <button
                key={opt.code}
                onClick={() => setCrs(opt.code)}
                className="flex flex-1 flex-col items-center gap-0.5 rounded-md border-2 px-2 py-2 transition-all"
                style={{
                  borderColor: isActive
                    ? 'var(--agent-accent, #16a34a)'
                    : 'var(--border-subtle)',
                  backgroundColor: isActive
                    ? 'color-mix(in srgb, var(--agent-accent, #16a34a) 4%, transparent)'
                    : 'var(--surface-raised)',
                }}
              >
                <span
                  className="text-title font-mono font-semibold"
                  style={{
                    color: isActive ? 'var(--agent-accent)' : 'var(--text-secondary)',
                  }}
                >
                  {opt.code}
                </span>
                <span
                  className="text-body"
                  style={{
                    color: isActive ? 'var(--agent-accent)' : 'var(--text-muted)',
                  }}
                >
                  {opt.label}
                </span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}