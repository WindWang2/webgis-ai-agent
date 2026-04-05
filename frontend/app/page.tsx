"use client"
import { useState } from "react"
import { ChatPanel } from "@/components/chat/chat-panel"
import { MapPanel } from "@/components/map/map-panel"
import { ResultsPanel } from "@/components/panel/results-panel"

// Types for inter-component communication
interface AnalysisResult {
  geoJson?: any
  center?: [number, number]
  zoom?: number
  markerPoints?: Array<{ lng: number; lat: number; label?: string }>
}

interface ResultItem {
  id: string
  title: string
  type: "text" | "chart" | "map" | "table"
  content: string
  timestamp: Date
}

export default function Home() {
  const [analysisTrigger, setAnalysisTrigger] = useState<string>("")
  
  // Demo analysis results - would come from backend in production
  const mockMapAnalysis: AnalysisResult = {
    center: [116.4074, 39.9042],
    zoom: 12,
    markerPoints: [
      { lng: 116.3974, lat: 39.9092, label: "景点A" },
      { lng: 116.4174, lat: 39.9012, label: "景点B" },
    ],
  }

  const handleAnalysisRequest = (message: string, attachments?: any[]) => {
    // In production: send to backend API, receive results
    console.log("Analysis request:", message, attachments)
    
    // Trigger demo response - simulate backend processing
    setAnalysisTrigger(message)
  }

  return (
    <div className="h-screen w-screen overflow-hidden bg-background">
      <div className="flex h-full w-full">
        {/* Left Panel - Chat */}
        <div className="w-80 flex-shrink-0 border-r border-border">
          <ChatPanel onAnalysisRequest={handleAnalysisRequest} />
        </div>

        {/* Center Panel - Map */}
        <div className="flex-1 min-w-0">
          <MapPanel analysisResult={mockMapAnalysis} />
        </div>

        {/* Right Panel - Results */}
        <div className="w-80 flex-shrink-0 border-l border-border">
          <ResultsPanel onGenerateReport={() => console.log("Generate report triggered")} />
        </div>
      </div>
    </div>
  )
}