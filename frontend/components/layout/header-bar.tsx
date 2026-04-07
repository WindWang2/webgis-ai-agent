"use client"
import { Satellite, Activity } from "lucide-react"

interface HeaderBarProps {
  status?: "online" | "analyzing" | "idle"
}

export function HeaderBar({ status = "idle" }: HeaderBarProps) {
  return (
    <header className="h-14 flex items-center justify-between border-b border-cyber-cyan/20 bg-cyber-black/80 backdrop-blur-md px-4">
      {/* Left: Logo + Project Name */}
      <div className="flex items-center gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-cyber-cyan/10 border border-cyber-cyan/30">
          <Satellite className="h-5 w-5 text-cyber-cyan" />
        </div>
        <div>
          <h1 className="font-semibold text-white tracking-wide">WebGIS AI Agent</h1>
          <p className="text-xs text-gray-500">智能地理空间分析系统</p>
        </div>
      </div>

      {/* Right: Status Indicator */}
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          {status === "online" && (
            <div className="flex items-center gap-2">
              <div className="h-2.5 w-2.5 rounded-full bg-emerald-500 animate-pulse-led" style={{ color: "#10b981" }} />
              <span className="text-sm text-gray-400">在线</span>
            </div>
          )}
          {status === "analyzing" && (
            <div className="flex items-center gap-2">
              <div className="h-2.5 w-2.5 rounded-full bg-amber-500 animate-pulse-led" style={{ color: "#f59e0b" }} />
              <span className="text-sm text-amber-400">分析中</span>
              <Activity className="h-4 w-4 text-amber-400 animate-spin" />
            </div>
          )}
          {status === "idle" && (
            <div className="flex items-center gap-2">
              <div className="h-2.5 w-2.5 rounded-full bg-gray-500" />
              <span className="text-sm text-gray-500">待机</span>
            </div>
          )}
        </div>
      </div>
    </header>
  )
}