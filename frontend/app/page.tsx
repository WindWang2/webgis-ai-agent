"use client"
import { useState, useCallback, useEffect, useRef } from "react"
import dynamic from "next/dynamic"
import { useHudStore } from "@/lib/store/useHudStore"
import { streamChat } from "@/lib/api/chat"
import { useWebSocket } from "@/lib/hooks/use-websocket"
import { useGeolocation } from "@/lib/hooks/use-geolocation"
import type { GeoJSONGeometry, GeoJSONFeature } from "@/lib/types"
import type { ChatSession } from "@/lib/types/chat"
import type { UploadResponse } from "@/lib/api/upload"
import { getUploadGeojson } from "@/lib/api/upload"
import { API_BASE } from '@/lib/api/config';
import { useMapAction } from "@/lib/contexts/map-action-context"

// New layout components
import TopBar from "@/components/layout/top-bar"
import StatusBar from "@/components/layout/status-bar"
import { LeftSidebar } from "@/components/sidebar/left-sidebar"
import MapToolbar from "@/components/map/map-toolbar"
import AITracker from "@/components/map/ai-tracker"
import { HistoryDrawer } from "@/components/drawers/history-drawer"
import { SettingsPanel } from "@/components/settings/settings-panel"

const MapPanel = dynamic(
  () => import("@/components/map/map-panel").then((m) => ({ default: m.MapPanel })),
  {
    ssr: false,
    loading: () => (
      <div className="flex-1 flex items-center justify-center bg-[#dce8f2]">
        <div className="animate-pulse text-slate-300 text-xs font-mono uppercase tracking-widest">
          Loading Map...
        </div>
      </div>
    ),
  }
)

function computeBBoxFromFeatures(features: any[]): [number, number, number, number] | undefined {
  if (!features || features.length === 0) return undefined
  let minLng = Infinity, minLat = Infinity, maxLng = -Infinity, maxLat = -Infinity
  const collect = (coords: number[][]) => {
    for (const c of coords) { minLng = Math.min(minLng, c[0]); maxLng = Math.max(maxLng, c[0]); minLat = Math.min(minLat, c[1]); maxLat = Math.max(maxLat, c[1]) }
  }
  for (const f of features) {
    const g = f.geometry
    if (!g?.coordinates) continue
    switch (g.type) {
      case 'Point': { minLng = Math.min(minLng, g.coordinates[0]); maxLng = Math.max(maxLng, g.coordinates[0]); minLat = Math.min(minLat, g.coordinates[1]); maxLat = Math.max(maxLat, g.coordinates[1]); break }
      case 'MultiPoint': case 'LineString': collect(g.coordinates); break
      case 'MultiLineString': g.coordinates.forEach((r: number[][]) => collect(r)); break
      case 'Polygon': collect(g.coordinates[0] || []); break
      case 'MultiPolygon': g.coordinates.forEach((p: number[][][]) => collect(p[0] || [])); break
    }
  }
  if (minLng === Infinity) return undefined
  return [minLng, minLat, maxLng, maxLat]
}

type ToolCallEntry = {
  id: string;
  tool: string;
  arguments?: string;
  result?: any;
  status: "running" | "completed" | "failed";
  hasGeojson?: boolean;
  error?: string;
  startedAt?: number;
  completedAt?: number;
};

export default function Home() {
  const { dispatchAction, getMapSnapshot } = useMapAction()
  const {
    layers,
    addLayer,
    removeLayer,
    toggleLayer,
    analysisResult,
    setAnalysisResult,
    leftPanelOpen,
    settingsOpen,
    historyOpen,
    setHistoryOpen,
    setAiStatus,
    setSessions: setStoreSessions,
    taskStart,
    stepStart,
    stepResult,
    stepError,
    taskComplete,
    clearTask,
  } = useHudStore()

  /* ─── Chat state ─── */
  const [messages, setMessages] = useState<Array<{ id: string; role: "user" | "assistant"; content: string; timestamp: Date; isThinking?: boolean; charts?: unknown[]; toolCalls?: ToolCallEntry[] }>>([
    {
      id: "1",
      role: "assistant",
      content: "你好！我是 GeoAgent。\n\n我感知地图、分析空间、生成洞察——地图上的一切都是我的一部分。\n\n试着告诉我：\n- 分析北京市学校分布密度\n- 成都市人口热力图\n- 计算各区 POI 覆盖率",
      timestamp: new Date(),
    },
  ])
  const [isLoading, setIsLoading] = useState(false)
  const [sessionId, setSessionId] = useState<string>()
  const abortControllerRef = useRef<AbortController | null>(null)

  /* ─── Session history state ─── */
  const [sessions, setSessions] = useState<ChatSession[]>([])

  // Sync sessions to store for HistoryDrawer
  useEffect(() => {
    setStoreSessions(sessions.map(s => ({
      id: s.id,
      title: s.title || "未命名",
      time: new Date(s.createdAt).toLocaleString('zh') || "",
      msgs: s.messages?.length || 0,
      tags: [],
    })))
  }, [sessions, setStoreSessions])

  // Fetch session list on mount
  useEffect(() => {
    fetch(`${API_BASE}/api/v1/chat/sessions`)
      .then(res => res.json())
      .then(data => {
        if (data.sessions) setSessions(data.sessions)
      })
      .catch(err => console.error("Fetch sessions failed:", err))
  }, [])

  const refreshSessions = useCallback(() => {
    fetch(`${API_BASE}/api/v1/chat/sessions`)
      .then(res => res.json())
      .then(data => {
        if (data.sessions) setSessions(data.sessions)
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (!sessionId) return
    const t1 = setTimeout(refreshSessions, 2000)
    const t2 = setTimeout(refreshSessions, 6000)
    return () => { clearTimeout(t1); clearTimeout(t2) }
  }, [sessionId, refreshSessions])

  const handleSelectSession = useCallback(async (sid: string) => {
    abortControllerRef.current?.abort()
    useHudStore.getState().clearLayers()
    try {
      const res = await fetch(`${API_BASE}/api/v1/chat/sessions/${sid}`)
      const data = await res.json()
      if (data.messages && data.messages.length > 0) {
        const restored = data.messages.map((m: any) => ({
          id: m.id,
          role: m.role,
          content: m.content,
          timestamp: new Date(m.timestamp),
        }))
        restored.push({
          id: `session-switch-${Date.now()}`,
          role: "assistant",
          content: `已恢复历史会话「${data.title || "未命名"}」— 共 ${data.messages.length} 条记录。可继续提问。`,
          timestamp: new Date(),
        })
        setMessages(restored)
      }
      setSessionId(sid)
      setHistoryOpen(false)

      const stateRes = await fetch(`${API_BASE}/api/v1/chat/sessions/${sid}/map-state`)
      if (stateRes.ok) {
        const stateData = await stateRes.json()
        const state = stateData?.map_state
        if (state) {
          const store = useHudStore.getState()
          if (state.viewport) {
            store.setViewport(state.viewport.center, state.viewport.zoom, state.viewport.bearing, state.viewport.pitch)
          }
          if (state.base_layer) store.setBaseLayer(state.base_layer)
          for (const layer of state.layers || []) {
            if (layer._refId && layer._refId.startsWith("ref:")) {
              fetch(`${API_BASE}/api/v1/layers/data/${layer._refId}?session_id=${sid}`)
                .then(r => r.ok ? r.json() : null)
                .then(geojson => {
                  if (geojson && (geojson.type === "FeatureCollection" || geojson.features)) {
                    store.addLayer({ ...layer, source: geojson })
                  }
                })
                .catch(() => {})
            }
          }
        }
      }
    } catch (err) {
      console.error("Load session failed:", err)
    }
  }, [setHistoryOpen])

  const handleNewSession = useCallback(() => {
    abortControllerRef.current?.abort()
    setSessionId(undefined)
    setMessages([{
      id: "1",
      role: "assistant",
      content: "你好！我是 GeoAgent。\n\n我感知地图、分析空间、生成洞察——地图上的一切都是我的一部分。",
      timestamp: new Date(),
    }])
    localStorage.removeItem("webgis_session_id")
    setHistoryOpen(false)
  }, [setHistoryOpen])

  /* reserved for future HistoryDrawer delete support */
  const _handleDeleteSession = useCallback(async (sid: string) => {
    try {
      await fetch(`${API_BASE}/api/v1/chat/sessions/${sid}`, { method: "DELETE" })
      setSessions(prev => prev.filter(s => s.id !== sid))
      if (sessionId === sid) {
        handleNewSession()
      }
    } catch (err) {
      console.error("Delete session failed:", err)
    }
  }, [sessionId, handleNewSession])
  void _handleDeleteSession;

  // Restore session from localStorage on mount
  useEffect(() => {
    const savedSessionId = localStorage.getItem("webgis_session_id")
    if (savedSessionId) {
      setSessionId(savedSessionId)
      fetch(`${API_BASE}/api/v1/chat/sessions/${savedSessionId}`)
        .then(res => res.json())
        .then(data => {
          if (data.messages && data.messages.length > 0) {
            setMessages(data.messages.map((m: any) => ({
              id: m.id,
              role: m.role,
              content: m.content,
              timestamp: new Date(m.timestamp),
            })))
          }
        })
        .catch(err => console.error("Restore session history failed:", err))

      fetch(`${API_BASE}/api/v1/chat/sessions/${savedSessionId}/map-state`)
        .then(res => res.ok ? res.json() : null)
        .then(data => {
          if (!data?.map_state) return
          const state = data.map_state
          const store = useHudStore.getState()
          if (state.viewport) {
            const vp = state.viewport
            store.setViewport(vp.center, vp.zoom, vp.bearing, vp.pitch)
          }
          if (state.base_layer) store.setBaseLayer(state.base_layer)
          const layers = state.layers || []
          for (const layer of layers) {
            if (layer._refId && layer._refId.startsWith("ref:")) {
              fetch(`${API_BASE}/api/v1/layers/data/${layer._refId}?session_id=${savedSessionId}`)
                .then(r => r.ok ? r.json() : null)
                .then(geojson => {
                  if (geojson && (geojson.type === "FeatureCollection" || geojson.features)) {
                    store.addLayer({ ...layer, source: geojson })
                  }
                })
                .catch(() => {})
            } else if (layer.source && typeof layer.source === "object") {
              store.addLayer(layer)
            }
          }
        })
        .catch(() => {})
    }
  }, [])

  useEffect(() => {
    if (sessionId) {
      localStorage.setItem("webgis_session_id", sessionId)
    }
  }, [sessionId])

  useWebSocket(sessionId)
  const { location: userLocation } = useGeolocation()

  useEffect(() => {
    return () => { abortControllerRef.current?.abort() }
  }, [])

  /* ─── Tool result handler ─── */
  const handleToolResult = useCallback(
    async (toolName: string, result: any, sid?: string) => {
      let geojson = result.geojson
      let bbox = result.bbox
      let image = result.image

      if (!geojson && result.geojson_ref && typeof result.geojson_ref === "string" && result.geojson_ref.startsWith("ref:") && sid) {
        try {
          const resp = await fetch(`${API_BASE}/api/v1/layers/data/${result.geojson_ref}?session_id=${sid}`)
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

      if (geojson && geojson.features?.length > 0) {
        const layerId = `${toolName}-${Date.now()}`
        const colors = ["#16a34a", "#2563eb", "#ea580c", "#8b5cf6", "#ec4899", "#dc2626"]
        const color = colors[Math.floor(Math.random() * colors.length)]

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
            _refId: result.geojson_ref || undefined,
          })
        }
      }
    },
    [addLayer, setAnalysisResult]
  )

  /* ─── Upload handler (reserved for future upload zone) ─── */
  const _handleUploadSuccess = useCallback(
    async (result: UploadResponse) => {
      if (result.file_type === "vector" && result.bbox) {
        try {
          const geojson = await getUploadGeojson(result.id)
          if (geojson.features?.length > 0) {
            const layerId = `upload-${result.id}`
            const colors = ["#16a34a", "#2563eb", "#ea580c", "#8b5cf6", "#ec4899", "#dc2626"]
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
            useHudStore.getState().pushPerception('upload_completed', {
              original_name: result.original_name,
              feature_count: result.feature_count,
              layer_id: layerId,
              crs: result.crs,
              file_type: result.file_type,
            })
            setMessages(prev => [...prev, {
              id: `upload-notify-${Date.now()}`,
              role: "assistant",
              content: `已感知新数据源：**${result.original_name}**（${result.feature_count} 个要素）已挂载到地图。`,
              timestamp: new Date(),
            }])
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
  void _handleUploadSuccess;

  /* ─── Send handler ─── */
  const handleSend = useCallback(
    async (messageText: string) => {
      if (!messageText || isLoading) return

      abortControllerRef.current?.abort()
      abortControllerRef.current = new AbortController()
      const currentSignal = abortControllerRef.current.signal

      const { viewport: currentViewport, layers: currentLayers, baseLayer: currentBaseLayer, is3D: currentIs3D } = useHudStore.getState()
      // Get real-time snapshot from the map instance (more accurate than store state)
      const liveSnapshot = getMapSnapshot()
      const mapState = {
        viewport: {
          center: liveSnapshot?.center ?? currentViewport.center,
          zoom: liveSnapshot?.zoom ?? currentViewport.zoom,
          bearing: liveSnapshot?.bearing ?? currentViewport.bearing ?? 0,
          pitch: liveSnapshot?.pitch ?? currentViewport.pitch ?? 0,
          bounds: liveSnapshot?.bounds ?? currentViewport.bounds ?? undefined,
        },
        base_layer: currentBaseLayer,
        is_3d: currentIs3D,
        layers: currentLayers.map((l: any) => ({
          id: l.id,
          name: l.name,
          type: l.type,
          visible: l.visible,
          opacity: l.opacity,
          group: l.group,
          _refId: l._refId,
          featureCount: l.source && typeof l.source === 'object' && 'features' in l.source
            ? (l.source as any).features?.length || 0 : undefined,
          bbox: l.source && typeof l.source === 'object' && 'features' in l.source
            ? computeBBoxFromFeatures((l.source as any).features) : undefined,
          style: l.style,
        })),
        user_location: userLocation ? { lng: userLocation.lng, lat: userLocation.lat, accuracy: userLocation.accuracy } : null,
      }

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
        setAiStatus("thinking")

        let skillName: string | undefined
        const skillMatch = messageText.match(/^使用技能「(.+?)」/)
        if (skillMatch) {
          skillName = skillMatch[1]
        }

        for await (const event of streamChat(messageText, sessionId, mapState, currentSignal, skillName)) {
          const { event: eventType, data: dataRaw } = event
          const data = dataRaw as any

          if (["thinking", "planning"].includes(eventType)) {
            setAiStatus("thinking")
          } else if (["acting", "observing"].includes(eventType)) {
            setAiStatus("acting")
          }

          if (eventType === "session" && data?.session_id) {
            setSessionId(data.session_id as string)
          } else if (eventType === "task_start" && data?.task_id) {
            taskStart(data.task_id as string)
          } else if (eventType === "step_start" && data?.task_id) {
            stepStart(data.task_id as string, data.step_id as string, data.step_index as number, data.tool as string)
            // Add tool call entry to current message
            const tcEntry: ToolCallEntry = {
              id: data.step_id as string,
              tool: data.tool as string,
              status: "running",
              startedAt: Date.now(),
            }
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === thinkingMessage.id
                  ? { ...msg, toolCalls: [...(msg.toolCalls || []), tcEntry] }
                  : msg
              )
            )
          } else if (eventType === "step_result" && data?.task_id) {
            stepResult(data.task_id as string, data.step_id as string, data.tool as string, data.result, data.has_geojson as boolean)

            const result = data.result as any
            if (result && result.command) {
              dispatchAction(result)
            }

            if (data.has_geojson && handleToolResult) {
              const toolResult = { ...data.result as object, geojson_ref: data.geojson_ref }
              handleToolResult(data.tool as string, toolResult, (data.session_id || sessionId) as string)
            }

            if (result && result.type === "ndvi_result" && result.image && result.bbox) {
              dispatchAction({
                command: 'add_raster_layer',
                params: {
                  id: `ndvi-${result.asset_id || Date.now()}`,
                  name: `NDVI分析结果 (${result.filename})`,
                  image: result.image,
                  bbox: result.bbox,
                  opacity: 0.8
                }
              });
              useHudStore.getState().setPendingSystemMessage(
                `[系统通知] 植被指数(NDVI)分析已完成并持久化。资产ID: ${result.asset_id}。`
              );
            }

            if (data.tool === "generate_chart" && result && result.chart) {
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === thinkingMessage.id
                    ? { ...msg, charts: [...(msg.charts || []), result.chart] }
                    : msg
                )
              )
            }

            // Update tool call entry with result
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === thinkingMessage.id
                  ? {
                      ...msg,
                      toolCalls: (msg.toolCalls || []).map((tc) =>
                        tc.id === data.step_id
                          ? { ...tc, status: "completed" as const, result: data.result, hasGeojson: data.has_geojson, completedAt: Date.now() }
                          : tc
                      ),
                    }
                  : msg
              )
            )
          } else if (eventType === "step_error" && data?.task_id) {
            stepError(data.task_id as string, data.step_id as string, data.error as string)
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === thinkingMessage.id
                  ? {
                      ...msg,
                      toolCalls: (msg.toolCalls || []).map((tc) =>
                        tc.id === data.step_id
                          ? { ...tc, status: "failed" as const, error: data.error, completedAt: Date.now() }
                          : tc
                      ),
                    }
                  : msg
              )
            )
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
            break
          } else if (eventType === "task_error" || eventType === "tool_error") {
            const errorMsg = typeof data === "object" ? ((data as any).message || (data as any).error || "未知错误") : String(data)
            if (!assistantContent.includes(errorMsg)) {
              assistantContent += `\n\n> **异常**: ${errorMsg}\n`
            }
            setMessages((prev) =>
              prev.map((msg) => (msg.id === thinkingMessage.id ? { ...msg, content: assistantContent, isThinking: false } : msg))
            )
          } else if (eventType === "tool_call" && data?.name) {
            // Update the most recent running tool call with arguments
            const argsStr = data.arguments as string | undefined
            setMessages((prev) =>
              prev.map((msg) => {
                if (msg.id !== thinkingMessage.id) return msg
                const tcs = msg.toolCalls || []
                // Find the last running entry matching this tool
                const lastRunningIdx = [...tcs].reverse().findIndex((tc) => tc.status === "running" && tc.tool === data.name)
                if (lastRunningIdx === -1) return msg
                const realIdx = tcs.length - 1 - lastRunningIdx
                const updated = [...tcs]
                updated[realIdx] = { ...updated[realIdx], arguments: argsStr }
                return { ...msg, toolCalls: updated }
              })
            )
          } else if (eventType === "task_cancelled") {
            clearTask()
            setMessages((prev) =>
              prev.map((msg) => {
                if (msg.id !== thinkingMessage.id) return msg
                return {
                  ...msg,
                  content: msg.content + "\n\n> 任务已取消",
                  toolCalls: (msg.toolCalls || []).map((tc) =>
                    tc.status === "running" ? { ...tc, status: "failed" as const, error: "任务已取消", completedAt: Date.now() } : tc
                  ),
                }
              })
            )
          }
        }

        setMessages((prev) =>
          prev.map((msg) => (msg.id === thinkingMessage.id ? { ...msg, isThinking: false } : msg))
        )
      } catch {
        setAiStatus("error")
        setMessages((prev) =>
          prev.map((msg) => (msg.id === thinkingMessage.id ? { ...msg, content: "请求失败，请重试。", isThinking: false } : msg))
        )
      } finally {
        setAiStatus("done")
        setTimeout(() => {
          setAiStatus("idle")
          clearTask()
        }, 2000)
        setIsLoading(false)
      }
    },
    [isLoading, sessionId, userLocation, taskStart, stepStart, stepResult, stepError, taskComplete, clearTask, handleToolResult, dispatchAction, setAiStatus]
  )

  /* ─── System Callback Effect ─── */
  const pendingSystemMessage = useHudStore((s: any) => s.pendingSystemMessage);
  const setPendingSystemMessage = useHudStore((s: any) => s.setPendingSystemMessage);

  useEffect(() => {
    if (pendingSystemMessage && !isLoading) {
      handleSend(pendingSystemMessage);
      setPendingSystemMessage(null);
    }
  }, [pendingSystemMessage, isLoading, handleSend, setPendingSystemMessage]);

  // Map SSE event status to aiStatus for the top bar/tracker
  const aiStatus = useHudStore((s) => s.aiStatus)

  // Get current session title for TopBar
  const currentSessionTitle = sessionId
    ? sessions.find(s => s.id === sessionId)?.title || "新会话"
    : "新会话"

  /* ══════════════════════════════════════════
     JSX — All is Agent Layout
     ══════════════════════════════════════════ */
  return (
    <div className="h-screen w-screen flex flex-col overflow-hidden bg-[#dce8f2]">
      {/* TopBar */}
      <TopBar sessionName={currentSessionTitle} onNewSession={handleNewSession} />

      {/* Map Area */}
      <div className="flex-1 relative overflow-hidden">
        {/* Map Canvas */}
        <div className="absolute inset-0">
          <MapPanel
            layers={layers}
            onRemoveLayer={removeLayer}
            onToggleLayer={toggleLayer}
            analysisResult={analysisResult}
          />
        </div>

        {/* Left Sidebar */}
        <LeftSidebar
          open={leftPanelOpen}
          messages={messages}
          aiStatus={aiStatus}
          onSend={handleSend}
          accentColor="#16a34a"
        />

        {/* Map Toolbar */}
        <MapToolbar sidebarOpen={leftPanelOpen} />

        {/* AI Step Tracker */}
        <AITracker />
      </div>

      {/* Status Bar */}
      <StatusBar />

      {/* History Drawer */}
      <HistoryDrawer
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        onSelect={(session) => {
          if (session && session.id) {
            handleSelectSession(session.id)
          } else {
            handleNewSession()
          }
        }}
        accentColor="#16a34a"
      />

      {/* Settings Panel */}
      {settingsOpen && <SettingsPanel />}
    </div>
  )
}
