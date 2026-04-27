"use client"
import { useState, useCallback, useEffect, useRef } from "react"
import { motion, AnimatePresence } from "framer-motion"
import { MapPanel } from "@/components/map/map-panel"
import { HudPanel } from "@/components/hud/hud-panel"
import { DynamicIsland } from "@/components/hud/dynamic-island"
import { RagInsightCard } from "@/components/hud/rag-insight-card"
import { ChatHud } from "@/components/chat/chat-panel"
import { ChatSidebar } from "@/components/chat-sidebar"
import { DataHud } from "@/components/panel/results-panel"
import { SettingsPanel } from "@/components/hud/settings-panel"
import { useHudStore } from "@/lib/store/useHudStore"
import { streamChat, SSEEventType } from "@/lib/api/chat"
import { getSkills } from '@/lib/api/skills'
import { useWebSocket } from "@/lib/hooks/use-websocket"
import type { GeoJSONGeometry, GeoJSONFeature } from "@/lib/types"
import type { ChatSession } from "@/lib/types/chat"
import type { UploadResponse } from "@/lib/api/upload"
import { getUploadGeojson } from "@/lib/api/upload"
import { API_BASE } from '@/lib/api/config';
import { useMapAction } from "@/lib/contexts/map-action-context"
import {
  MessageSquare,
  Activity,
  PanelLeftOpen,
  PanelRightOpen,
  History,
} from "lucide-react"

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
  const abortControllerRef = useRef<AbortController | null>(null)

  /* ─── Session history state ─── */
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [showHistory, setShowHistory] = useState(false)

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

  // Refresh session list when sessionId changes — two passes to catch LLM-generated title
  useEffect(() => {
    if (!sessionId) return
    const t1 = setTimeout(refreshSessions, 2000)
    const t2 = setTimeout(refreshSessions, 6000)
    return () => { clearTimeout(t1); clearTimeout(t2) }
  }, [sessionId, refreshSessions])

  const handleSelectSession = useCallback(async (sid: string) => {
    abortControllerRef.current?.abort()
    // Clear current layers before switching
    useHudStore.getState().clearLayers()
    try {
      // Restore chat messages
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
          content: `📂 已恢复历史会话「${data.title || "未命名"}」— 共 ${data.messages.length} 条记录。可继续提问。`,
          timestamp: new Date(),
        })
        setMessages(restored)
      }
      setSessionId(sid)
      setShowHistory(false)

      // Restore map state for this session
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
  }, [])

  const handleNewSession = useCallback(() => {
    abortControllerRef.current?.abort()
    setSessionId(undefined)
    setMessages([{
      id: "1",
      role: "assistant",
      content: "你好！我是空间智能分析系统。请输入空间分析指令，例如「分析北京市学校分布」或「成都市人口密度热力图」",
      timestamp: new Date(),
    }])
    localStorage.removeItem("webgis_session_id")
    setShowHistory(false)
  }, [])

  const handleDeleteSession = useCallback(async (sid: string) => {
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

  // 1. Restore session from localStorage on mount
  useEffect(() => {
    const savedSessionId = localStorage.getItem("webgis_session_id")
    if (savedSessionId) {
      setSessionId(savedSessionId)
      // Fetch history
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

      // Restore map state (viewport + layers) from backend
      fetch(`${API_BASE}/api/v1/chat/sessions/${savedSessionId}/map-state`)
        .then(res => res.ok ? res.json() : null)
        .then(data => {
          if (!data?.map_state) return
          const state = data.map_state
          const store = useHudStore.getState()

          // Restore viewport
          if (state.viewport) {
            const vp = state.viewport
            store.setViewport(vp.center, vp.zoom, vp.bearing, vp.pitch)
          }

          // Restore base layer
          if (state.base_layer) {
            store.setBaseLayer(state.base_layer)
          }

          // Restore layers — re-fetch GeoJSON data via ref_ids
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

  // 2. Persist session_id when it changes
  useEffect(() => {
    if (sessionId) {
      localStorage.setItem("webgis_session_id", sessionId)
    }
  }, [sessionId])

  // Initialize WebSocket connection
  useWebSocket(sessionId)

  // Abort in-flight SSE stream on unmount
  useEffect(() => {
    return () => { abortControllerRef.current?.abort() }
  }, [])

  /* ─── Tool result handler ─── */
  const handleToolResult = useCallback(
    async (toolName: string, result: any, sid?: string) => {
      let geojson = result.geojson
      let bbox = result.bbox
      let image = result.image

      // Fetch deferred data via reference
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
            _refId: result.geojson_ref || undefined,
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

            // ── Agent Perception: Push upload event via perception buffer ──
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
              content: `📡 已感知新数据源：**${result.original_name}**（${result.feature_count} 个要素）已挂载到地图。`,
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
    [addLayer, setAnalysisResult, sessionId]
  )

  const [showScanEffect, setShowScanEffect] = useState(false)

  /* ─── Send handler (from Dynamic Island) ─── */
  const handleSend = useCallback(
    async (messageText: string) => {
      if (!messageText || isLoading) return

      // Trigger sensory sync visual effect
      setShowScanEffect(true)
      setTimeout(() => setShowScanEffect(false), 2000)

      // Cancel any in-flight SSE stream
      abortControllerRef.current?.abort()
      abortControllerRef.current = new AbortController()
      const currentSignal = abortControllerRef.current.signal

      // Read current state from store at call time (avoids reactive deps)
      const { viewport: currentViewport, layers: currentLayers, baseLayer: currentBaseLayer, is3D: currentIs3D } = useHudStore.getState()
      const mapState = {
        viewport: {
          center: currentViewport.center,
          zoom: currentViewport.zoom,
          bearing: currentViewport.bearing || 0,
          pitch: currentViewport.pitch || 0,
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
        setCurrentStep("thinking")

        let skillName: string | undefined
        const skillMatch = messageText.match(/^使用技能「(.+?)」/)
        if (skillMatch) {
          skillName = skillMatch[1]
        }

        for await (const event of streamChat(messageText, sessionId, mapState, currentSignal, skillName)) {
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
              dispatchAction(result)
            }
            
            if (data.has_geojson && handleToolResult) {
              const toolResult = { ...data.result as object, geojson_ref: data.geojson_ref }
              handleToolResult(data.tool as string, toolResult, (data.session_id || sessionId) as string)
            }
            
            // ─── NDVI / Raster Result Perception ───
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
              
              // Trigger Agent awareness
              useHudStore.getState().setPendingSystemMessage(
                `[系统通知] 植被指数(NDVI)分析已完成并持久化。资产ID: ${result.asset_id}。` +
                `你现在可以告诉用户分析结论（Max: ${result.stats.max.toFixed(2)}, Mean: ${result.stats.mean.toFixed(2)}），` +
                `并向其介绍如何通过右侧“ASSETS”面板管理这份永久资产。`
              );
            }
            // CRITICAL: Append chart data into message
            if (data.tool === "generate_chart" && result && result.chart) {
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === thinkingMessage.id
                    ? { ...msg, charts: [...(msg.charts || []), result.chart] }
                    : msg
                )
              )
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
          } else if (eventType === "tool_call" && data?.tool) {
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === thinkingMessage.id
                  ? { ...msg, content: msg.content + `\n\n> 🔧 **执行工具**: ${data.tool}...` }
                  : msg
              )
            )
          } else if (eventType === "task_plan" && data?.steps) {
            const planSteps = (data.steps as string[]).map((s, i) => `${i + 1}. ${s}`).join("\n")
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === thinkingMessage.id
                  ? { ...msg, content: msg.content + `\n\n📋 **任务计划**:\n${planSteps}` }
                  : msg
              )
            )
          } else if (eventType === "task_cancelled") {
            clearTask()
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === thinkingMessage.id
                  ? { ...msg, content: msg.content + "\n\n> ⏹ 任务已取消" }
                  : msg
              )
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
    [isLoading, sessionId, taskStart, stepStart, stepResult, stepError, taskComplete, clearTask, handleToolResult, dispatchAction]
  )

  const handleActivateSkill = useCallback((skillName: string) => {
    if (isLoading) return
    handleSend(`使用技能「${skillName}」开始分析`)
  }, [isLoading, handleSend])

  /* ─── System Callback Effect ─── */
  const pendingSystemMessage = useHudStore((s: any) => s.pendingSystemMessage);
  const setPendingSystemMessage = useHudStore((s: any) => s.setPendingSystemMessage);

  useEffect(() => {
    if (pendingSystemMessage && !isLoading) {
      handleSend(pendingSystemMessage);
      setPendingSystemMessage(null);
    }
  }, [pendingSystemMessage, isLoading, handleSend, setPendingSystemMessage]);


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
          analysisResult={analysisResult}
        />
        
        {/* Sensory Sync Overlay (Scanning effect) */}
        <AnimatePresence>
          {showScanEffect && (
            <motion.div 
              className="absolute inset-0 z-10 border-2 border-hud-cyan pointer-events-none"
              initial={{ opacity: 0, scale: 0.98 }}
              animate={{ opacity: 0.4, scale: 1 }}
              exit={{ opacity: 0, scale: 1.02 }}
              transition={{ duration: 0.8, ease: "easeOut" }}
            >
              <div className="absolute inset-0 bg-hud-cyan/5" />
              <div className="absolute top-0 left-0 right-0 h-1/3 bg-gradient-to-b from-hud-cyan/20 to-transparent animate-scan-line" />
            </motion.div>
          )}
        </AnimatePresence>

        {/* Cockpit HUD Decorations */}
        <div className="absolute inset-0 pointer-events-none z-10">
          <div className="hud-corner top-8 left-8 border-t-2 border-l-2" />
          <div className="hud-corner top-8 right-8 border-t-2 border-r-2" />
          <div className="hud-corner bottom-24 left-8 border-b-2 border-l-2" />
          <div className="hud-corner bottom-24 right-8 border-b-2 border-r-2" />
        </div>
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

      {/* Left Panel — Chat + History HUD */}
      <HudPanel
        position="left"
        isOpen={leftPanelOpen}
        onClose={toggleLeftPanel}
        title="COMMS"
        icon={<MessageSquare className="h-4 w-4" />}
        width="w-[380px]"
      >
        {/* History / Chat toggle tabs */}
        <div className="flex items-center border-b border-white/[0.06] px-2">
          <button
            onClick={() => setShowHistory(false)}
            className={`flex items-center gap-1.5 px-3 py-2 text-[11px] font-medium transition-colors border-b-2 ${
              !showHistory
                ? 'border-hud-cyan text-hud-cyan'
                : 'border-transparent text-white/30 hover:text-white/50'
            }`}
          >
            <MessageSquare className="h-3 w-3" />
            对话
          </button>
          <button
            onClick={() => setShowHistory(true)}
            className={`flex items-center gap-1.5 px-3 py-2 text-[11px] font-medium transition-colors border-b-2 ${
              showHistory
                ? 'border-hud-cyan text-hud-cyan'
                : 'border-transparent text-white/30 hover:text-white/50'
            }`}
          >
            <History className="h-3 w-3" />
            历史
            {sessions.length > 0 && (
              <span className="ml-1 px-1.5 py-0.5 text-[9px] rounded-full bg-white/[0.06] text-white/40">
                {sessions.length}
              </span>
            )}
          </button>
        </div>

        {/* Content: Chat or History */}
        {showHistory ? (
          <ChatSidebar
            sessions={sessions}
            currentSessionId={sessionId || null}
            onSelectSession={handleSelectSession}
            onNewSession={handleNewSession}
            onDeleteSession={handleDeleteSession}
          />
        ) : (
          <ChatHud
            messages={messages}
            isLoading={isLoading}
            onUploadSuccess={handleUploadSuccess}
            sessionId={sessionId}
            showUploadZone={showUploadZone}
            setShowUploadZone={setShowUploadZone}
          />
        )}
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
          sessionId={sessionId}
          onToggleLayer={(id) => {
            const layer = layers.find((l: any) => l.id === id)
            toggleLayer(id)
            useHudStore.getState().pushPerception('layer_toggled', { layer_id: id, visible: layer ? !layer.visible : undefined })
          }}
          onRemoveLayer={(id) => {
            removeLayer(id)
            useHudStore.getState().pushPerception('layer_removed', { layer_id: id })
          }}
          onUpdateLayer={(id, updates) => {
            updateLayer(id, updates)
            if (updates.opacity !== undefined) {
              useHudStore.getState().pushPerception('layer_opacity_changed', { layer_id: id, opacity: updates.opacity })
            }
          }}
          onReorderLayers={(newLayers) => {
            reorderLayers(newLayers)
            useHudStore.getState().pushPerception('layers_reordered', { order: newLayers.map(l => l.id) })
          }}
        />
      </HudPanel>

      <SettingsPanel />

      {/* Dynamic Island — Bottom Center */}
      <DynamicIsland
        onSend={handleSend}
        isLoading={isLoading}
        onUploadClick={() => setShowUploadZone((prev) => !prev)}
        onActivateSkill={handleActivateSkill}
        statusText={statusText}
      />

      {/* Grid overlay for depth */}
      <div className="absolute inset-0 pointer-events-none z-[1] opacity-[0.015] bg-grid-hud bg-[size:60px_60px]" />
    </div>
  )
}
