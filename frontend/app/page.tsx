"use client"
import { useState, useCallback } from "react"
import { ChatPanel } from "@/components/chat/chat-panel"
import { MapPanel } from "@/components/map/map-panel"
import { ResultsPanel } from "@/components/panel/results-panel"
import { TaskProvider } from "@/lib/contexts/task-context"
import type { Layer } from "@/lib/types/layer"
import type { AnalysisResult, ToolResult, GeoJSONGeometry, GeoJSONFeature } from "@/lib/types"
import type { UploadResponse } from "@/lib/api/upload"
import { getUploadGeojson } from "@/lib/api/upload"

export default function Home() {
  const [layers, setLayers] = useState<Layer[]>([])
  const [analysisResult, setAnalysisResult] = useState<AnalysisResult | null>(null)

  const handleToolResult = useCallback(async (toolName: string, result: any, sessionId?: string) => {
    let geojson = result.geojson
    let bbox = result.bbox
    let image = result.image

    // 1. 如果数据被脱敏 (只有引用 ID)，则异步拉取完整数据
    if (!geojson && result.geojson_ref && typeof result.geojson_ref === 'string' && result.geojson_ref.startsWith('ref:') && sessionId) {
      try {
        const resp = await fetch(`http://localhost:8001/api/v1/layers/data/${result.geojson_ref}?session_id=${sessionId}`)
        if (resp.ok) {
          const fullData = await resp.json()
          // 根据数据类型恢复字段
          if (fullData.type === "FeatureCollection" || (typeof fullData === 'object' && 'features' in fullData)) {
            geojson = fullData
          } else if (fullData.image) {
            image = fullData.image
            bbox = fullData.bbox
          }
        }
      } catch (e) {
        console.error("Fetch layer data failed:", e)
      }
    }

    // 2. 处理栅格热力图
    if ((result?.type === "heatmap_raster" || image) && (result?.image || image) && (result.bbox || bbox)) {
      const layerId = `heatmap_raster-${Date.now()}`
      const finalBbox = bbox || result.bbox
      const finalImage = image || result.image
      const [west, south, east, north] = finalBbox
      const center: [number, number] = [(west + east) / 2, (south + north) / 2]
      setAnalysisResult({ center, zoom: 10 })
      setLayers(prev => [...prev, {
        id: layerId,
        name: "热力图（栅格）",
        type: "heatmap",
        visible: true,
        opacity: 0.85,
        source: { image: finalImage, bbox: finalBbox },
        style: {},
      }])
      return
    }

    // 3. 处理矢量数据
    if (geojson && geojson.features?.length > 0) {
      const layerId = `${toolName}-${Date.now()}`
      const colors = ["#3b82f6", "#ef4444", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899"]
      const color = colors[Math.floor(Math.random() * colors.length)]

      // 计算 GeoJSON 的中心点用于地图定位
      const lngs: number[] = []
      const lats: number[] = []
      const collectCoords = (coords: number[][]) => {
        coords.forEach((c: number[]) => { lngs.push(c[0]); lats.push(c[1]) })
      }
      const collectFromGeometry = (geometry: GeoJSONGeometry) => {
        const c = geometry.coordinates as number[] | number[][]
        if (!c) return
        switch (geometry.type) {
          case "Point":
            lngs.push((c as number[])[0])
            lats.push((c as number[])[1])
            break
          case "MultiPoint":
            collectCoords(c as number[][])
            break
          case "LineString":
            collectCoords(c as number[][])
            break
          case "MultiLineString":
            (c as number[][][]).forEach((ring: number[][]) => collectCoords(ring))
            break
          case "Polygon":
            collectCoords((c as number[][][])[0] || [])
            break
          case "MultiPolygon":
            (c as number[][][][]).forEach((poly: number[][][]) => collectCoords(poly[0] || []))
            break
        }
      }
      geojson.features.forEach((f: GeoJSONFeature) => collectFromGeometry(f.geometry!))

      let center: [number, number] | undefined
      let zoom = 12
      
      // 优先从要素计算中心
      if (lngs.length > 0) {
        center = [
          lngs.reduce((a, b) => a + b, 0) / lngs.length,
          lats.reduce((a, b) => a + b, 0) / lats.length,
        ]
        const count = geojson.features.length
        if (count > 100) zoom = 10
        else if (count > 50) zoom = 11
        else if (count < 10) zoom = 13
      } 
      // 备选从 bbox 计算中心 (支持脱敏后的导航)
      else if (bbox || result.bbox) {
        const b = bbox || result.bbox
        const parts = typeof b === 'string' ? b.split(",").map(Number) : b
        if (parts.length === 4) {
          // bbox: [min_lat, min_lng, max_lat, max_lng] (backend internal) or [west, south, east, north] (frontend standard)
          // 这里需要小心格式转换，通常 OSM 出来的 bbox string 是 s,w,n,e
          if (typeof b === 'string') {
            center = [(parts[1] + parts[3]) / 2, (parts[0] + parts[2]) / 2]
          } else {
            center = [(parts[0] + parts[2]) / 2, (parts[1] + parts[3]) / 2]
          }
        }
      }

      if (center) {
        setAnalysisResult({ center, zoom })
      }

      // 再次确认：如果 result 本身就是脱敏后的 FeatureCollection 根对象
      if (!geojson && result.type === "FeatureCollection" && result.geojson_ref) {
        // 这种情况会进入下方的 fetch 逻辑
      }
      // 如果 result 本身就是完整的 FeatureCollection
      if (!geojson && result.type === "FeatureCollection" && result.features) {
        geojson = result
      }
      
      if (geojson && geojson.features?.length > 0) {
        const layerId = `${toolName}-${Date.now()}`
        const colors = ["#3b82f6", "#ef4444", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899"]
        const color = colors[Math.floor(Math.random() * colors.length)]
        // 确定图层类型
        const isGrid = geojson?.metadata?.render_type === "grid"
        const isNative = geojson?.metadata?.render_type === "native"
        const layerType = (isGrid || isNative) ? "heatmap" : "vector"
        const layerStyle = isGrid ? { color, renderType: "grid" } : (isNative ? { color, renderType: "heatmap" } : { color })

        setLayers(prev => [{
          id: layerId,
          name: isNative ? "原生热力图" : (isGrid ? "格网热力分析" : (result.type === "poi_query" ? `${result.area} - ${result.category}` : (result.type || toolName))),
          type: layerType,
          visible: true,
          opacity: 1,
          group: result.group || 'analysis',
          source: geojson,
          style: layerStyle,
        }, ...prev])
      }
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

  // 上传成功后自动加载到地图
  const handleUploadSuccess = useCallback(async (result: UploadResponse) => {
    if (result.file_type === "vector" && result.bbox) {
      try {
        const geojson = await getUploadGeojson(result.id)
        if (geojson.features?.length > 0) {
          const layerId = `upload-${result.id}`
          const colors = ["#3b82f6", "#ef4444", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899"]
          const color = colors[result.id % colors.length]
          const [west, south, east, north] = result.bbox
          const center: [number, number] = [(west + east) / 2, (south + north) / 2]
          const zoom = result.feature_count > 100 ? 10 : result.feature_count > 20 ? 11 : 12

          setAnalysisResult({ center, zoom })
          setLayers(prev => [{
            id: layerId,
            name: result.original_name,
            type: "vector",
            visible: true,
            opacity: 1,
            group: "reference",
            source: geojson,
            style: { color },
          }, ...prev])
        }
      } catch (e) {
        console.error("加载上传数据到地图失败:", e)
      }
    } else if (result.file_type === "raster" && result.bbox) {
      // 栅格数据：定位到范围
      const [west, south, east, north] = result.bbox
      const center: [number, number] = [(west + east) / 2, (south + north) / 2]
      setAnalysisResult({ center, zoom: 10 })
    }
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
            <ChatPanel onToolResult={handleToolResult} onUploadSuccess={handleUploadSuccess} />
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
