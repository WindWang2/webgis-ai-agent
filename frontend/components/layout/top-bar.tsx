"use client";

import { useEffect, useState } from "react";
import {
  PanelLeftClose,
  Menu,
  Compass,
  Plus,
  History,
  Settings,
} from "lucide-react";
import { useHudStore } from "@/lib/store/useHudStore";

interface TopBarProps {
  sessionName?: string;
  onNewSession?: () => void;
}

const STATUS_CONFIG: Record<
  string,
  { label: string; color: string; bg: string }
> = {
  idle: { label: "就绪", color: "bg-slate-400", bg: "bg-slate-50" },
  thinking: { label: "感知中", color: "bg-blue-500", bg: "bg-blue-50" },
  acting: { label: "执行中", color: "bg-blue-500", bg: "bg-blue-50" },
  done: { label: "完成", color: "bg-green-600", bg: "bg-green-50" },
  error: { label: "异常", color: "bg-red-500", bg: "bg-red-50" },
};

export default function TopBar({ sessionName = "未命名", onNewSession }: TopBarProps) {
  const leftPanelOpen = useHudStore((s) => s.leftPanelOpen);
  const toggleLeftPanel = useHudStore((s) => s.toggleLeftPanel);
  const aiStatus = useHudStore((s) => s.aiStatus);
  const setSettingsOpen = useHudStore((s) => s.setSettingsOpen);
  const setHistoryOpen = useHudStore((s) => s.setHistoryOpen);

  const isActive = aiStatus === "thinking" || aiStatus === "acting";
  const status = STATUS_CONFIG[aiStatus] ?? STATUS_CONFIG.idle;

  /* scan-line position 0-100% */
  const [scanX, setScanX] = useState(0);
  useEffect(() => {
    if (!isActive) return;
    let frame: number;
    let start: number | null = null;
    const DURATION = 2000;
    const tick = (ts: number) => {
      if (start === null) start = ts;
      const progress = ((ts - start) % DURATION) / DURATION;
      setScanX(progress * 100);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [isActive]);

  return (
    <div
      className="fixed top-0 inset-x-0 z-50 flex items-center h-[42px] px-2 gap-2
                 bg-white/75 backdrop-blur-[20px] border-b transition-colors duration-300"
      style={{
        borderBottomColor: isActive
          ? "rgba(22,163,74,0.35)"
          : "rgba(0,0,0,0.06)",
      }}
    >
      {/* heartbeat scan line */}
      {isActive && (
        <div className="absolute top-0 inset-x-0 h-[2px] overflow-hidden pointer-events-none">
          <div
            className="h-full animate-hb-scan"
            style={{
              background:
                "linear-gradient(90deg, transparent 0%, rgba(22,163,74,0.6) 50%, transparent 100%)",
              width: "40%",
              transform: `translateX(${scanX * 2.5}%)`,
            }}
          />
        </div>
      )}

      {/* sidebar toggle */}
      <button
        onClick={toggleLeftPanel}
        className="flex items-center justify-center w-7 h-7 rounded-md
                   hover:bg-slate-100 active:bg-slate-200 transition-colors text-slate-600"
        title={leftPanelOpen ? "收起侧栏" : "展开侧栏"}
      >
        {leftPanelOpen ? <PanelLeftClose size={15} /> : <Menu size={15} />}
      </button>

      {/* logo */}
      <div className="flex items-center gap-1.5 select-none">
        <span
          className="flex items-center justify-center w-6 h-6 rounded-[5px]"
          style={{
            background: "linear-gradient(135deg, #16a34a, #22c55e)",
          }}
        >
          <Compass size={13} className="text-white" />
        </span>
        <div className="leading-none">
          <span className="text-[13px] font-semibold text-slate-800">
            GeoAgent
          </span>
          <span className="text-[9px] text-slate-400 ml-1">All is Agent</span>
        </div>
      </div>

      {/* session name pill */}
      <span
        className="ml-1 px-2 py-0.5 rounded-full bg-slate-50 text-[10px] text-slate-500
                    border border-slate-100 select-none max-w-[180px] truncate"
      >
        会话 / {sessionName}
      </span>

      {/* spacer */}
      <div className="flex-1" />

      {/* agent status badge */}
      <span
        className={`flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium ${status.bg}`}
      >
        <span
          className={`w-1.5 h-1.5 rounded-full ${status.color} ${
            isActive ? "animate-spulse" : ""
          }`}
        />
        <span className="text-slate-600">{status.label}</span>
      </span>

      {/* right actions */}
      <div className="flex items-center gap-0.5">
        <button
          onClick={onNewSession}
          className="flex items-center justify-center w-7 h-7 rounded-md
                     hover:bg-slate-100 active:bg-slate-200 transition-colors text-slate-500"
          title="新建会话"
        >
          <Plus size={15} />
        </button>

        <button
          onClick={() => setHistoryOpen(true)}
          className="flex items-center justify-center w-7 h-7 rounded-md
                     hover:bg-slate-100 active:bg-slate-200 transition-colors text-slate-500"
          title="历史记录"
        >
          <History size={15} />
        </button>

        <span className="mx-1 h-4 w-px bg-slate-200" />

        <button
          onClick={() => setSettingsOpen(true)}
          className="flex items-center justify-center w-7 h-7 rounded-md
                     hover:bg-slate-100 active:bg-slate-200 transition-colors text-slate-500"
          title="设置"
        >
          <Settings size={15} />
        </button>
      </div>
    </div>
  );
}
