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
      const collectCoords = (coords: number[][]) => {
        coords.forEach((c: number[]) => { lngs.push(c[0]); lats.push(c[1]) })
      }
      const collectFromGeometry = (geometry: any) => {
        if (!geometry?.coordinates) return
        switch (geometry.type) {
          case "Point":
            lngs.push(geometry.coordinates[0])
            lats.push(geometry.coordinates[1])
            break
          case "MultiPoint":
            collectCoords(geometry.coordinates)
            break
          case "LineString":
            collectCoords(geometry.coordinates)
            break
          case "MultiLineString":
            geometry.coordinates.forEach((ring: number[][]) => collectCoords(ring))
            break
          case "Polygon":
            collectCoords(geometry.coordinates[0] || [])
            break
          case "MultiPolygon":
            geometry.coordinates.forEach((poly: number[][][]) => collectCoords(poly[0] || []))
            break
        }
      }
      result.geojson.features.forEach((f: any) => collectFromGeometry(f.geometry))

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
          // bbox 格式: [west, south, east, north] = [minLng, minLat, maxLng, maxLat]
          center = [(parts[0] + parts[2]) / 2, (parts[1] + parts[3]) / 2]
        }
      }

      if (center) {
        setAnalysisResult({ center, zoom })
      }

      // 确定图层类型：heatmap_raster 返回 type="heatmap"，带 grid 属性的为 vector
      const isRaster = result.type === "heatmap_raster"
      const isGrid = result.geojson?.metadata?.render_type === "grid"
      
      const layerType = isRaster ? "heatmap" : "vector"
      const layerStyle = (isRaster || isGrid)
        ? { color, renderType: isRaster ? "heatmap" : "grid" }
        : { color }

      setLayers(prev => [{
        id: layerId,
        name: result.type === "poi_query" ? `${result.area} - ${result.category}` : (result.type || toolName),
        type: layerType,
        visible: true,
        opacity: 1,
        group: result.group || 'analysis',
        source: result.geojson,
        style: layerStyle,
      }, ...prev])
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
  
  const handleUpdateLayer = useCallback((layerId: string, updates: Partial<Layer>) => {
    setLayers(prev => prev.map(l => 
      l.id === layerId ? { ...l, ...updates } : l
    ))
  }, [])

  const handleReorderLayers = useCallback((newLayers: Layer[]) => {
    setLayers(newLayers)
  }, [])
  
  const handleMapMove = useCallback((center: [number, number], zoom: number) => {
    setAnalysisResult({ center, zoom })
  }, [])

  return (
    <TaskProvider>
      <div className="h-screen w-screen overflow-hidden bg-background relative selection:bg-primary/30 selection:text-foreground">
        {/* 装饰边框 - 古典画框感 */}
        <div className="absolute inset-0 border-[16px] border-border/10 pointer-events-none z-50 pointer-events-none" />
        <div className="absolute inset-[15px] border border-border/20 pointer-events-none z-50 pointer-events-none" />
        
        {/* 背景氛围层 */}
        <div className="absolute inset-0 opacity-5 pointer-events-none bg-[radial-gradient(circle_at_50%_50%,rgba(120,119,198,0.15),transparent_70%)]" />
        <div className="absolute inset-0 bg-gradient-to-tr from-background via-transparent to-background/50 pointer-events-none" />

        <div className="flex h-full w-full relative z-10 p-4 gap-4">
          {/* 左侧对话面板 - 悬浮玻璃感 */}
          <div className="w-85 lg:w-96 flex-shrink-0 flex flex-col overflow-hidden rounded-xl border border-border/50 shadow-2xl bg-card/40 backdrop-blur-md">
            <ChatPanel onToolResult={handleToolResult} />
          </div>

          {/* 中间地图区域 - 核心视窗 */}
          <div className="flex-1 min-w-0 flex flex-col overflow-hidden rounded-xl border border-border/50 shadow-inner bg-background relative group">
            <MapPanel
              layers={layers}
              onRemoveLayer={handleRemoveLayer}
              onToggleLayer={handleToggleLayer}
              onEditLayer={handleEditLayer}
              analysisResult={analysisResult}
            />
            {/* 角标装饰 */}
            <div className="absolute top-4 left-4 h-12 w-12 border-t-2 border-l-2 border-primary/40 pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity" />
            <div className="absolute bottom-4 right-4 h-12 w-12 border-b-2 border-r-2 border-primary/40 pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity" />
          </div>

          {/* 右侧结果面板 - 书卷感 */}
          <div className="w-80 lg:w-85 flex-shrink-0 flex flex-col overflow-hidden rounded-xl border border-border/50 shadow-2xl bg-card/60 backdrop-blur-lg">
            <ResultsPanel 
              layers={layers} 
              onToggleLayer={handleToggleLayer} 
              onMapMove={handleMapMove}
              onRemoveLayer={handleRemoveLayer}
              onUpdateLayer={handleUpdateLayer}
              onReorderLayers={handleReorderLayers}
            />
          </div>
        </div>
      </div>
    </TaskProvider>
  )
}
