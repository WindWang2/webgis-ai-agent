"use client"

import { useState } from "react"
import { ChatPanel } from "@/components/chat/chat-panel"
import { MapPanel } from "@/components/map/map-panel"
import { ResultsPanel } from "@/components/panel/results-panel"

export default function Home() {
  // TODO: 集成任务管理系统后，从任务状态中获取真实taskId
  // 当前使用模拟数据便于测试报告功能
  const [currentTaskId] = useState<number | null>(1) // 模拟任务ID
  
  return (
    <div className="h-screen w-screen overflow-hidden bg-background">
      <div className="flex h-full w-full">
        {/* Left Panel - Chat */}
        <div className="w-80 flex-shrink-0 border-r border-border">
          <ChatPanel />
        </div>

        {/* Center Panel - Map */}
        <div className="flex-1 min-w-0">
          <MapPanel />
        </div>

        {/* Right Panel - Results */}
        <div className="w-80 flex-shrink-0 border-l border-border">
          <ResultsPanel taskId={currentTaskId} />
        </div>
      </div>
    </div>
  )
}
