"use client";

import { useHudStore } from "@/lib/store/useHudStore";

const BASE_LAYER_LABELS: Record<string, string> = {
  osm: "OpenStreetMap",
  amap: "高德地图",
  tianditu: "天地图",
  satellite: "卫星影像",
  dark: "暗色底图",
};

export default function StatusBar() {
  const viewport = useHudStore((s) => s.viewport);
  const baseLayer = useHudStore((s) => s.baseLayer);

  const lng = viewport.center[0];
  const lat = viewport.center[1];
  const zoom = viewport.zoom;

  const items = [
    { label: "CRS", value: "EPSG:4326" },
    { label: "LNG", value: lng.toFixed(5) },
    { label: "LAT", value: lat.toFixed(5) },
    { label: "ZOOM", value: zoom.toFixed(1) },
    {
      label: "底图",
      value: BASE_LAYER_LABELS[baseLayer] ?? baseLayer,
    },
  ];

  return (
    <div
      className="fixed bottom-0 inset-x-0 z-50 flex items-center h-[24px] px-3 gap-4
                 bg-white/75 backdrop-blur-[20px] border-t border-black/[0.04]"
    >
      <div className="flex items-center gap-4">
        {items.map((item) => (
          <div key={item.label} className="flex items-center gap-1 select-none">
            <span className="text-[9px] uppercase text-slate-400 tracking-wide">
              {item.label}
            </span>
            <span className="text-[10px] font-mono text-slate-600" style={{ fontFamily: "'JetBrains Mono', monospace" }}>
              {item.value}
            </span>
          </div>
        ))}
      </div>

      <div className="flex-1" />

      <span className="text-[9px] text-slate-300 select-none tracking-wide">
        GeoAgent · All is Agent
      </span>
    </div>
  );
}
