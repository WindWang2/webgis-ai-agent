'use client';

import { useHudStore } from '@/lib/store/useHudStore';

const BASE_LAYER_LABELS: Record<string, string> = {
  osm: 'OpenStreetMap',
  amap: '高德地图',
  tianditu: '天地图',
  satellite: '卫星影像',
  dark: '暗色底图',
};

export default function StatusBar() {
  const viewport = useHudStore((s) => s.viewport);
  const baseLayer = useHudStore((s) => s.baseLayer);
  const layers = useHudStore((s) => s.layers);
  const theme = useHudStore((s) => s.theme);
  const isDark = theme === 'dark';

  const lng = viewport.center[0];
  const lat = viewport.center[1];
  const zoom = viewport.zoom;
  const visibleLayerCount = layers.filter(l => l.visible).length;

  const items = [
    { label: 'CRS', value: 'EPSG:4326' },
    { label: 'LNG', value: lng.toFixed(5) },
    { label: 'LAT', value: lat.toFixed(5) },
    { label: 'ZOOM', value: zoom.toFixed(1) },
    {
      label: '底图',
      value: BASE_LAYER_LABELS[baseLayer] ?? baseLayer,
    },
    { label: 'LAYERS', value: visibleLayerCount.toString() },
  ];

  return (
    <div
      style={{
        position: 'fixed', bottom: 0, left: 0, right: 0, zIndex: 50,
        display: 'flex', alignItems: 'center', height: 24, paddingLeft: 12, paddingRight: 12, gap: 16,
        backgroundColor: isDark ? 'rgba(15,23,42,0.75)' : 'rgba(255,255,255,0.75)',
        backdropFilter: 'blur(20px)', WebkitBackdropFilter: 'blur(20px)',
        borderTopWidth: 1, borderTopStyle: 'solid',
        borderTopColor: isDark ? 'rgba(148,163,184,0.2)' : 'rgba(0,0,0,0.04)'
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        {items.map((item) => (
          <div key={item.label} style={{ display: 'flex', alignItems: 'center', gap: 4, userSelect: 'none' }}>
            <span style={{ fontSize: 9, textTransform: 'uppercase', color: isDark ? '#64748b' : '#94a3b8', letterSpacing: '0.06em' }}>
              {item.label}
            </span>
            <span style={{ fontSize: 10, fontFamily: "'JetBrains Mono', monospace", color: isDark ? '#e2e8f0' : '#475569' }}>
              {item.value}
            </span>
          </div>
        ))}
      </div>

      <div style={{ flex: 1 }} />

      <span style={{ fontSize: 9, color: isDark ? '#475569' : '#cbd5e1', userSelect: 'none', letterSpacing: '0.06em' }}>
        GeoAgent · All is Agent
      </span>
    </div>
  );
}
