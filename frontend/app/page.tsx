"use client"

import { ChatPanel } from "@/components/chat/chat-panel"
import { MapPanel } from "@/components/map/map-panel"
import { ResultsPanel } from "@/components/panel/results-panel"

export default function Home() {
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
          <ResultsPanel />
        </div>
      </div>
    </div>
  )
}
