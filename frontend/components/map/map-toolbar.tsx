"use client";

import {
  ZoomIn,
  ZoomOut,
  RotateCcw,
  Crosshair,
  Box,
  Download,
} from "lucide-react";
import { useHudStore } from "@/lib/store/useHudStore";

interface MapToolbarProps {
  sidebarOpen: boolean;
  onZoomIn?: () => void;
  onZoomOut?: () => void;
  onHome?: () => void;
  onLocate?: () => void;
  onExport?: () => void;
}

export default function MapToolbar({
  sidebarOpen,
  onZoomIn,
  onZoomOut,
  onHome,
  onLocate,
  onExport,
}: MapToolbarProps) {
  const is3D = useHudStore((s) => s.is3D);
  const setIs3D = useHudStore((s) => s.setIs3D);

  const btnBase =
    "flex items-center justify-center w-[27px] h-[27px] rounded-md " +
    "text-slate-500 hover:text-slate-700 hover:bg-slate-100/80 " +
    "active:bg-slate-200/80 transition-colors";

  return (
    <div
      className="absolute top-1/2 -translate-y-1/2 z-40
                 bg-white/85 backdrop-blur-[20px] border border-white/90
                 shadow-agent-md rounded-xl p-0.5
                 flex flex-col items-center gap-0.5
                 transition-all duration-300 ease-in-out"
      style={{ left: sidebarOpen ? "calc(330px + 10px)" : "10px" }}
    >
      <button className={btnBase} onClick={onZoomIn} title="放大">
        <ZoomIn size={14} />
      </button>

      <button className={btnBase} onClick={onZoomOut} title="缩小">
        <ZoomOut size={14} />
      </button>

      <button className={btnBase} onClick={onHome} title="复位">
        <RotateCcw size={14} />
      </button>

      <button className={btnBase} onClick={onLocate} title="定位">
        <Crosshair size={14} />
      </button>

      {/* divider */}
      <span className="w-4 h-px bg-slate-200 my-0.5" />

      <button
        className={`${btnBase} ${is3D ? "on text-green-600 bg-green-50 hover:bg-green-100/80" : ""}`}
        onClick={() => setIs3D(!is3D)}
        title={is3D ? "切换 2D" : "切换 3D"}
      >
        <Box size={14} />
      </button>

      <button className={btnBase} onClick={onExport} title="导出地图">
        <Download size={14} />
      </button>
    </div>
  );
}
