'use client';

import React, { useState } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';
import { STitle, SField, SButton } from '@/components/shared/section-title';
import { Check } from 'lucide-react';

const CRS_OPTIONS = [
  { code: 'EPSG:4326', label: 'WGS 84' },
  { code: 'EPSG:3857', label: 'Web Mercator' },
  { code: 'EPSG:4490', label: 'CGCS2000' },
];

export function MapConfig() {
  const baseLayer = useHudStore((s) => s.baseLayer);
  const setBaseLayer = useHudStore((s) => s.setBaseLayer);
  const mapStyles = useHudStore((s) => s.mapStyles);
  const setMapStyles = useHudStore((s) => s.setMapStyles);

  const [crs, setCrs] = useState('EPSG:3857');
  const [newName, setNewName] = useState('');
  const [newUrl, setNewUrl] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const [added, setAdded] = useState(false);

  const handleAddBasemap = () => {
    if (!newName.trim() || !newUrl.trim()) return;
    const nextId =
      mapStyles.length > 0
        ? Math.max(...mapStyles.map((s) => s.id)) + 1
        : 0;
    setMapStyles([
      ...mapStyles,
      { id: nextId, name: newName.trim(), desc: newDesc.trim() || 'Custom', url: newUrl.trim() },
    ]);
    setNewName('');
    setNewUrl('');
    setNewDesc('');
    setAdded(true);
    setTimeout(() => setAdded(false), 2000);
  };

  return (
    <div className="flex flex-col gap-5">
      <STitle title="地图配置" sub="Map Configuration" />

      {/* Basemap style cards */}
      <div>
        <div className="text-heading uppercase tracking-wider text-ink-muted font-semibold mb-3">
          Basemap Style
        </div>
        <div className="grid grid-cols-3 gap-2">
          {mapStyles.map((style) => {
            const isActive = baseLayer === style.name;
            return (
              <button
                key={style.id}
                onClick={() => setBaseLayer(style.name)}
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
                  {style.name}
                </span>
                <span className="text-body leading-tight text-ink-muted">
                  {style.desc}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Add custom basemap */}
      <div>
        <div className="text-heading uppercase tracking-wider text-ink-muted font-semibold mb-3">
          Add Custom Basemap
        </div>
        <div className="flex flex-col gap-2.5 rounded-md border border-edge-subtle bg-surface-raised px-4 py-3">
          <SField
            label="Name"
            value={newName}
            onChange={setNewName}
            placeholder="My Custom Map"
          />
          <SField
            label="Tile URL"
            value={newUrl}
            onChange={setNewUrl}
            placeholder="https://tiles.example.com/{z}/{x}/{y}.png"
          />
          <SField
            label="Description"
            value={newDesc}
            onChange={setNewDesc}
            placeholder="Custom tile layer"
          />
          <div className="pt-1">
            <SButton saved={added} onClick={handleAddBasemap}>
              {added ? 'Added' : 'Add Basemap'}
            </SButton>
          </div>
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
