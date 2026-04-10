"use client"
import { useState, useCallback } from "react"
import { ChatPanel } from "@/components/chat/chat-panel"
import { MapPanel } from "@/components/map/map-panel"
import { ResultsPanel } from "@/components/panel/results-panel"
import { TaskProvider } from "@/lib/contexts/task-context"
import type { Layer } from "@/lib/types/layer"

export default function Home() {
  const [layers, setLayers] = useState<Layer[]>([])
  const [analysisResult, setAnalysisResult] = useState<any>(null)

  const handleToolResult = useCallback((toolName: string, result: any) => {
    // 栅格热力图（base64 PNG + bbox）
    if (result?.type === "heatmap_raster" && result?.image && result?.bbox) {
      const layerId = `heatmap_raster-${Date.now()}`
      const [west, south, east, north] = result.bbox
      const center: [number, number] = [(west + east) / 2, (south + north) / 2]
      setAnalysisResult({ center, zoom: 10 })
      setLayers(prev => [...prev, {
        id: layerId,
        name: "热力图（栅格）",
        type: "heatmap",
        visible: true,
        opacity: 0.85,
        source: { image: result.image, bbox: result.bbox },
        style: {},
      }])
      return
    }

    if (result?.geojson && result.geojson.features?.length > 0) {
      const layerId = `${toolName}-${Date.now()}`
      const colors = ["#3b82f6", "#ef4444", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899"]
      const color = colors[Math.floor(Math.random() * colors.length)]

      // 计算 GeoJSON 的中心点用于地图定位
      const lngs: number[] = []
      const lats: number[] = []
      result.geojson.features.forEach((f: any) => {
        if (f.geometry?.coordinates) {
          if (f.geometry.type === "Point") {
            lngs.push(f.geometry.coordinates[0])
            lats.push(f.geometry.coordinates[1])
          } else if (f.geometry.type === "LineString") {
            f.geometry.coordinates.forEach((c: number[]) => { lngs.push(c[0]); lats.push(c[1]) })
          } else if (f.geometry.type === "Polygon") {
            f.geometry.coordinates[0]?.forEach((c: number[]) => { lngs.push(c[0]); lats.push(c[1]) })
          }
        }
      })

      let center: [number, number] | undefined
      let zoom = 12
      if (lngs.length > 0) {
        center = [
          lngs.reduce((a, b) => a + b, 0) / lngs.length,
          lats.reduce((a, b) => a + b, 0) / lats.length,
        ]
        const count = result.geojson.features.length
        if (count > 100) zoom = 10
        else if (count > 50) zoom = 11
        else if (count < 10) zoom = 13
      } else if (result.bbox) {
        const parts = result.bbox.split(",").map(Number)
        if (parts.length === 4) {
          center = [(parts[1] + parts[3]) / 2, (parts[0] + parts[2]) / 2]
        }
      }

      if (center) {
        setAnalysisResult({ center, zoom })
      }

      // 确定图层类型：heatmap_data 返回 type="heatmap"，其他默认为 vector
      const layerType = result.type === "heatmap" ? "heatmap" : "vector"
      const layerStyle = result.type === "heatmap"
        ? { color, renderType: "heatmap" }
        : { color }

      setLayers(prev => [...prev, {
        id: layerId,
        name: result.type === "poi_query" ? `${result.area} - ${result.category}` : (result.type || toolName),
        type: layerType,
        visible: true,
        opacity: 1,
        source: result.geojson,
        style: layerStyle,
      }])
    }
  }, [])

  const handleRemoveLayer = useCallback((layerId: string) => {
    setLayers(prev => prev.filter(l => l.id !== layerId))
  }, [])

  const handleToggleLayer = useCallback((layerId: string) => {
    setLayers(prev => prev.map(l =>
      l.id === layerId ? { ...l, visible: !l.visible } : l
    ))
  }, [])

  const handleEditLayer = useCallback((_layer: Layer) => {}, [])

  return (
    <TaskProvider>
      <div className="h-screen w-screen overflow-hidden bg-background relative">
        {/* 装饰边框 - 内敛色调 */}
        <div className="absolute inset-4 border border-border-light/30 rounded-lg pointer-events-none" />

        <div className="flex h-full w-full relative z-10">
          <div className="w-80 flex-shrink-0 border-r border-border overflow-hidden">
            <ChatPanel onToolResult={handleToolResult} />
          </div>
        <div className="flex-1 min-w-0 overflow-hidden">
          <MapPanel
            layers={layers}
            onRemoveLayer={handleRemoveLayer}
            onToggleLayer={handleToggleLayer}
            onEditLayer={handleEditLayer}
            analysisResult={analysisResult}
          />
        </div>
        <div className="w-80 flex-shrink-0 border-l border-border overflow-hidden bg-card">
          <ResultsPanel layers={layers} onGenerateReport={() => {}} onToggleLayer={handleToggleLayer} />
        </div>
      </div>
    </div>
    </TaskProvider>
  )
}
