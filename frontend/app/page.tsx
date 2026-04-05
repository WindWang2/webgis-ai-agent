"use client"
import { useState, useCallback } from "react"
import { ChatPanel } from "@/components/chat/chat-panel"
import { MapPanel } from "@/components/map/map-panel"
import { ResultsPanel } from "@/components/panel/results-panel"

export interface GeoJsonLayer {
  id: string
  name: string
  geojson: any  // GeoJSON FeatureCollection
  color?: string
  visible?: boolean
}

export default function Home() {
  const [layers, setLayers] = useState<GeoJsonLayer[]>([])

  // Chat panel 返回的工具结果处理
  const handleToolResult = useCallback((toolName: string, result: any) => {
    if (result?.geojson && result.geojson.features?.length > 0) {
      const layerId = `${toolName}-${Date.now()}`
      const colors = ["#3b82f6", "#ef4444", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899"]
      const color = colors[layers.length % colors.length]
      
      setLayers(prev => [...prev, {
        id: layerId,
        name: result.type || toolName,
        geojson: result.geojson,
        color,
        visible: true,
      }])
    }
  }, [layers.length])

  const handleRemoveLayer = useCallback((layerId: string) => {
    setLayers(prev => prev.filter(l => l.id !== layerId))
  }, [])

  const handleToggleLayer = useCallback((layerId: string) => {
    setLayers(prev => prev.map(l => 
      l.id === layerId ? { ...l, visible: !l.visible } : l
    ))
  }, [])

  return (
    <div className="h-screen w-screen overflow-hidden bg-background">
      <div className="flex h-full w-full">
        <div className="w-80 flex-shrink-0 border-r border-border">
          <ChatPanel onToolResult={handleToolResult} />
        </div>
        <div className="flex-1 min-w-0">
          <MapPanel layers={layers} onRemoveLayer={handleRemoveLayer} onToggleLayer={handleToggleLayer} />
        </div>
        <div className="w-80 flex-shrink-0 border-l border-border">
          <ResultsPanel layers={layers} onGenerateReport={() => {}} />
        </div>
      </div>
    </div>
  )
}
