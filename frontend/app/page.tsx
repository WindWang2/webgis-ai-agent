"use client"
import { useState, useCallback } from "react"
import { MapPanel } from "@/components/map/map-panel"
import { HudPanel } from "@/components/hud/hud-panel"
import { DynamicIsland } from "@/components/hud/dynamic-island"
import { RagInsightCard } from "@/components/hud/rag-insight-card"
import { ChatHud } from "@/components/chat/chat-panel"
import { DataHud } from "@/components/panel/results-panel"
import { useHudStore } from "@/lib/store/useHudStore"
import { streamChat, SSEEventType } from "@/lib/api/chat"
import { useWebSocket } from "@/lib/hooks/use-websocket"
import type { GeoJSONGeometry, GeoJSONFeature } from "@/lib/types"
import type { UploadResponse } from "@/lib/api/upload"
import { getUploadGeojson } from "@/lib/api/upload"
import { useMapAction } from "@/lib/contexts/map-action-context"
import {
  MessageSquare,
  Activity,
  PanelLeftOpen,
  PanelRightOpen,
} from "lucide-react"

export default function Home() {
  const { dispatchAction } = useMapAction()
  /* ─── Zustand state ─── */
  const {
    layers,
    addLayer,
    removeLayer,
    toggleLayer,
    updateLayer,
    reorderLayers,
    analysisResult,
    setAnalysisResult,
    leftPanelOpen,
    rightPanelOpen,
    toggleLeftPanel,
    toggleRightPanel,
    /* Task actions */
    taskStart,
    stepStart,
    stepResult,
    stepError,
    taskComplete,
    clearTask,
  } = useHudStore()

  /* ─── Chat state ─── */
  const [messages, setMessages] = useState<Array<{ id: string; role: "user" | "assistant"; content: string; timestamp: Date; isThinking?: boolean; charts?: unknown[] }>>([
    {
      id: "1",
      role: "assistant",
      content: "你好！我是空间智能分析系统。请输入空间分析指令，例如「分析北京市学校分布」或「成都市人口密度热力图」",
      timestamp: new Date(),
    },
  ])
  const [isLoading, setIsLoading] = useState(false)
  const [sessionId, setSessionId] = useState<string>()
  const [currentStep, setCurrentStep] = useState<SSEEventType | "error" | null>(null)
  const [showUploadZone, setShowUploadZone] = useState(false)

  // Initialize WebSocket connection
  useWebSocket(sessionId)

  /* ─── Tool result handler ─── */
  const handleToolResult = useCallback(
    async (toolName: string, result: any, sid?: string) => {
      let geojson = result.geojson
      let bbox = result.bbox
      let image = result.image

      // Fetch deferred data via reference
      if (!geojson && result.geojson_ref && typeof result.geojson_ref === "string" && result.geojson_ref.startsWith("ref:") && sid) {
        try {
          const resp = await fetch(`http://localhost:8001/api/v1/layers/data/${result.geojson_ref}?session_id=${sid}`)
          if (resp.ok) {
            const fullData = await resp.json()
            if (fullData.type === "FeatureCollection" || (typeof fullData === "object" && "features" in fullData)) {
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

      // Handle raster heatmap
      if ((result?.type === "heatmap_raster" || image) && (result?.image || image) && (result.bbox || bbox)) {
        const layerId = `heatmap_raster-${Date.now()}`
        const finalBbox = bbox || result.bbox
        const finalImage = image || result.image
        const [west, south, east, north] = finalBbox
        const center: [number, number] = [(west + east) / 2, (south + north) / 2]
        setAnalysisResult({ center, zoom: 10 })
        addLayer({
          id: layerId,
          name: "热力图（栅格）",
          type: "heatmap",
          visible: true,
          opacity: 0.85,
          source: { image: finalImage, bbox: finalBbox },
          style: {},
        })
        return
      }

      // Handle vector data
      if (geojson && geojson.features?.length > 0) {
        const layerId = `${toolName}-${Date.now()}`
        const colors = ["#00f2ff", "#00ff41", "#ff5f00", "#8b5cf6", "#ec4899", "#3b82f6"]
        const color = colors[Math.floor(Math.random() * colors.length)]

        // Calculate center
        const lngs: number[] = []
        const lats: number[] = []
        const collectCoords = (coords: number[][]) => {
          coords.forEach((c: number[]) => { lngs.push(c[0]); lats.push(c[1]) })
        }
        const collectFromGeometry = (geometry: GeoJSONGeometry) => {
          const c = geometry.coordinates
          if (!c) return
          switch (geometry.type) {
            case "Point": { const pt = c as number[]; lngs.push(pt[0]); lats.push(pt[1]); break }
            case "MultiPoint": collectCoords(c as number[][]); break
            case "LineString": collectCoords(c as number[][]); break
            case "MultiLineString": (c as unknown as number[][][]).forEach((ring: number[][]) => collectCoords(ring)); break
            case "Polygon": collectCoords((c as unknown as number[][][])[0] || []); break
            case "MultiPolygon": (c as unknown as number[][][][]).forEach((poly: number[][][]) => collectCoords(poly[0] || [])); break
          }
        }
        geojson.features.forEach((f: GeoJSONFeature) => collectFromGeometry(f.geometry!))

        let center: [number, number] | undefined
        let zoom = 12
        if (lngs.length > 0) {
          center = [lngs.reduce((a: number, b: number) => a + b, 0) / lngs.length, lats.reduce((a: number, b: number) => a + b, 0) / lats.length]
          const count = geojson.features.length
          if (count > 100) zoom = 10
          else if (count > 50) zoom = 11
          else if (count < 10) zoom = 13
        } else if (bbox || result.bbox) {
          const b = bbox || result.bbox
          const parts = typeof b === "string" ? b.split(",").map(Number) : b
          if (parts.length === 4) {
            center = typeof b === "string" ? [(parts[1] + parts[3]) / 2, (parts[0] + parts[2]) / 2] : [(parts[0] + parts[2]) / 2, (parts[1] + parts[3]) / 2]
          }
        }

        if (center) setAnalysisResult({ center, zoom })

        if (!geojson && result.type === "FeatureCollection" && result.features) {
          geojson = result
        }

        if (geojson && geojson.features?.length > 0) {
          const isGrid = geojson?.metadata?.render_type === "grid"
          const isNative = geojson?.metadata?.render_type === "native"
          const layerType = isGrid || isNative ? "heatmap" : "vector"
          const layerStyle = isGrid ? { color, renderType: "grid" as const } : isNative ? { color, renderType: "heatmap" as const } : { color }

          addLayer({
            id: layerId,
            name: isNative ? "原生热力图" : isGrid ? "格网热力分析" : result.type === "poi_query" ? `${result.area} - ${result.category}` : result.type || toolName,
            type: layerType,
            visible: true,
            opacity: 1,
            group: result.group || "analysis",
            source: geojson,
            style: layerStyle,
          })
        }
      }
    },
    [addLayer, setAnalysisResult]
  )

  /* ─── Upload handler ─── */
  const handleUploadSuccess = useCallback(
    async (result: UploadResponse) => {
      if (result.file_type === "vector" && result.bbox) {
        try {
          const geojson = await getUploadGeojson(result.id)
          if (geojson.features?.length > 0) {
            const layerId = `upload-${result.id}`
            const colors = ["#00f2ff", "#00ff41", "#ff5f00", "#8b5cf6", "#ec4899", "#3b82f6"]
            const color = colors[result.id % colors.length]
            const [west, south, east, north] = result.bbox
            const center: [number, number] = [(west + east) / 2, (south + north) / 2]
            const zoom = result.feature_count > 100 ? 10 : result.feature_count > 20 ? 11 : 12
            setAnalysisResult({ center, zoom })
            addLayer({
              id: layerId,
              name: result.original_name,
              type: "vector",
              visible: true,
              opacity: 1,
              group: "reference",
              source: geojson as any,
              style: { color },
            })
          }
        } catch (e) {
          console.error("加载上传数据到地图失败:", e)
        }
      } else if (result.file_type === "raster" && result.bbox) {
        const [west, south, east, north] = result.bbox
        const center: [number, number] = [(west + east) / 2, (south + north) / 2]
        setAnalysisResult({ center, zoom: 10 })
      }
    },
    [addLayer, setAnalysisResult]
  )

  /* ─── Send handler (from Dynamic Island) ─── */
  const handleSend = useCallback(
    async (messageText: string) => {
      if (!messageText || isLoading) return
      const userMessage = {
        id: Date.now().toString(),
        role: "user" as const,
        content: messageText,
        timestamp: new Date(),
      }
      setMessages((prev) => [...prev, userMessage])
      setIsLoading(true)

      const thinkingMessage = {
        id: (Date.now() + 1).toString(),
        role: "assistant" as const,
        content: "",
        timestamp: new Date(),
        isThinking: true,
      }
      setMessages((prev) => [...prev, thinkingMessage])

      try {
        let assistantContent = ""
        setCurrentStep("thinking")

        for await (const event of streamChat(messageText, sessionId)) {
          const { event: eventType, data: dataRaw } = event
          const data = dataRaw as any

          if (["thinking", "planning", "acting", "observing", "done", "tool_error"].includes(eventType)) {
            setCurrentStep(eventType as SSEEventType)
          }

          if (eventType === "session" && data?.session_id) {
            setSessionId(data.session_id as string)
          } else if (eventType === "task_start" && data?.task_id) {
            taskStart(data.task_id as string)
          } else if (eventType === "step_start" && data?.task_id) {
            stepStart(data.task_id as string, data.step_id as string, data.step_index as number, data.tool as string)
          } else if (eventType === "step_result" && data?.task_id) {
            stepResult(data.task_id as string, data.step_id as string, data.tool as string, data.result, data.has_geojson as boolean)
            
            // CRITICAL: Dispatch map commands (BASE_LAYER_CHANGE, etc.) regardless of geojson
            const result = data.result as any
            if (result && result.command) {
              console.log('[Home] Direct command dispatch from tool result:', result.command)
              dispatchAction(result)
            }
            
            if (data.has_geojson && handleToolResult) {
              const toolResult = { ...data.result as object, geojson_ref: data.geojson_ref }
              handleToolResult(data.tool as string, toolResult, (data.session_id || sessionId) as string)
            }
          } else if (eventType === "step_error" && data?.task_id) {
            stepError(data.task_id as string, data.step_id as string, data.error as string)
          } else if (eventType === "task_complete" && data?.task_id) {
            taskComplete(data.task_id as string, data.step_count as number, data.summary as string)
            if (assistantContent.length < 10 && data.summary) {
              assistantContent = data.summary as string
            }
          } else if (eventType === "message" || eventType === "content" || eventType === "token") {
            const chunk = typeof data === "object" ? ((data as any).content || (data as any).text || (data as any).message || "") : String(data)
            assistantContent += chunk
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === thinkingMessage.id ? { ...msg, content: assistantContent, isThinking: false } : msg
              )
            )
          } else if (eventType === "done" || eventType === "end") {
            setCurrentStep("done")
            break
          } else if (eventType === "task_error" || eventType === "tool_error") {
            const errorMsg = typeof data === "object" ? ((data as any).message || (data as any).error || "未知错误") : String(data)
            if (!assistantContent.includes(errorMsg)) {
              assistantContent += `\n\n> ⚠️ **异常**: ${errorMsg}\n`
            }
            setMessages((prev) =>
              prev.map((msg) => (msg.id === thinkingMessage.id ? { ...msg, content: assistantContent, isThinking: false } : msg))
            )
          }
        }

        setMessages((prev) =>
          prev.map((msg) => (msg.id === thinkingMessage.id ? { ...msg, isThinking: false } : msg))
        )
      } catch (_) {
        setCurrentStep("error")
        setMessages((prev) =>
          prev.map((msg) => (msg.id === thinkingMessage.id ? { ...msg, content: "请求失败，请重试。", isThinking: false } : msg))
        )
      } finally {
        setTimeout(() => {
          setCurrentStep(null)
          clearTask()
        }, 1500)
        setIsLoading(false)
      }
    },
    [isLoading, sessionId, taskStart, stepStart, stepResult, stepError, taskComplete, clearTask, handleToolResult]
  )

  /* ─── Status text for Dynamic Island ─── */
  const statusText = currentStep
    ? currentStep === "thinking" ? "思考中..."
    : currentStep === "planning" ? "规划方案..."
    : currentStep === "acting" ? "执行操作..."
    : currentStep === "observing" ? "分析结果..."
    : currentStep === "done" ? "✓ 完成"
    : currentStep === "error" ? "⚠ 出错" : undefined
    : undefined

  return (
    <div className="h-screen w-screen overflow-hidden bg-ds-black relative">
      {/* ═══ Full-Viewport Map Canvas (Z-0) ═══ */}
      <div className="absolute inset-0 z-0">
        <MapPanel
          layers={layers}
          onRemoveLayer={removeLayer}
          onToggleLayer={toggleLayer}
          onEditLayer={() => {}}
          analysisResult={analysisResult}
        />
      </div>

      {/* ═══ HUD Overlay Layer (Z-10+) ═══ */}

      {/* RAG Insight Card — top center */}
      <RagInsightCard />

      {/* Toggle buttons for collapsed panels */}
      {!leftPanelOpen && (
        <button
          onClick={toggleLeftPanel}
          className="absolute top-4 left-4 z-20 hud-btn h-10 w-10 rounded-xl glass-panel"
          title="打开对话面板"
        >
          <PanelLeftOpen className="h-4 w-4 text-hud-cyan/70" />
        </button>
      )}
      {!rightPanelOpen && (
        <button
          onClick={toggleRightPanel}
          className="absolute top-4 right-4 z-20 hud-btn h-10 w-10 rounded-xl glass-panel"
          title="打开任务面板"
        >
          <PanelRightOpen className="h-4 w-4 text-hud-cyan/70" />
        </button>
      )}

      {/* Left Panel — Chat + Data HUD */}
      <HudPanel
        position="left"
        isOpen={leftPanelOpen}
        onClose={toggleLeftPanel}
        title="COMMS"
        icon={<MessageSquare className="h-4 w-4" />}
        width="w-[380px]"
      >
        <ChatHud
          messages={messages}
          isLoading={isLoading}
          onUploadSuccess={handleUploadSuccess}
          sessionId={sessionId}
          showUploadZone={showUploadZone}
          setShowUploadZone={setShowUploadZone}
        />
      </HudPanel>

      {/* Right Panel — Task Flow + Layers HUD */}
      <HudPanel
        position="right"
        isOpen={rightPanelOpen}
        onClose={toggleRightPanel}
        title="OPERATIONS"
        icon={<Activity className="h-4 w-4" />}
        width="w-[360px]"
      >
        <DataHud
          layers={layers}
          onToggleLayer={toggleLayer}
          onRemoveLayer={removeLayer}
          onUpdateLayer={updateLayer}
          onReorderLayers={reorderLayers}
        />
      </HudPanel>

      {/* Dynamic Island — Bottom Center */}
      <DynamicIsland
        onSend={handleSend}
        isLoading={isLoading}
        onUploadClick={() => setShowUploadZone((prev) => !prev)}
        statusText={statusText}
      />

      {/* Grid overlay for depth */}
      <div className="absolute inset-0 pointer-events-none z-[1] opacity-[0.015] bg-grid-hud bg-[size:60px_60px]" />
    </div>
  )
}
