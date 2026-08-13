"use client"
import { useState, useEffect, useRef, Suspense } from "react"
import { useSearchParams } from "next/navigation"
import dynamic from "next/dynamic"

const MapPanel = dynamic(
  () => import('@/components/map/map-panel').then((m) => ({ default: m.MapPanel })),
  { ssr: false, loading: () => <div className="w-full h-full bg-surface-canvas animate-pulse" /> }
)

const StoryMarkdown = dynamic(() => import('@/components/chat/story-markdown'), { ssr: false })

import { devOnly } from "@/lib/utils/logger";
import { useHudStore } from "@/lib/store/useHudStore"
import { apiFetch, isApiError } from "@/lib/api/transport"
import { Play, SkipBack, Share2 } from "lucide-react"

export default function StoryPage() {
  return (
    <Suspense fallback={<div className="flex items-center justify-center h-screen text-ink-muted">Loading...</div>}>
      <StoryPageInner />
    </Suspense>
  )
}

function StoryPageInner() {
  const searchParams = useSearchParams()
  const sessionId = searchParams.get("session_id")

  const [messages, setMessages] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const containerRef = useRef<HTMLDivElement>(null)

  const layers = useHudStore((s) => s.layers)
  const removeLayer = useHudStore((s) => s.removeLayer)
  const toggleLayer = useHudStore((s) => s.toggleLayer)

  useEffect(() => {
    const controller = new AbortController();
    if (sessionId) {
      // 走统一 transport：超时/中止/类型化错误；切换 sessionId 时旧请求被 abort
      // 避免 stale 响应覆盖新会话消息。
      apiFetch<{ messages?: any[] }>(`/api/v1/chat/sessions/${encodeURIComponent(sessionId)}`, {
        signal: controller.signal,
        label: 'Story session error',
      })
        .then((data) => {
          if (data.messages && data.messages.length > 0) {
            setMessages(data.messages);
          }
        })
        .catch((err) => {
          if (!isApiError(err) && err instanceof Error && err.name !== 'AbortError') {
            devOnly.error('Restore session history failed:', err);
          }
        })
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
      setMessages([
        { role: "assistant", content: "# StoryMap 回放模式\n以叙事形式重现 GeoAgent 的分析推演过程。" },
        { role: "assistant", content: "您可以尝试在 URL 中追加 `?session_id=您的会话ID` 来回放之前的分析推演。" },
      ]);
    }
    return () => controller.abort();
  }, [sessionId])

  // ScrollSpy - Parse specific locations from markdown text and fly to them
  const handleScroll = () => {
    // Advanced ScrollSpy logic can be added here to trigger camera flyTo
    // based on visible Markdown headers.
  }

  if (loading) {
    /* E：bg-ds-black / text-hud-cyan / bg-grid-hud 从未在 tailwind.config 或
       任何 CSS 中定义 —— 此前这个 loading 屏与整页都没有背景。改走 V4 语义
       token（surface-canvas 是页面床色，status-info 是叙事 accent），网格
       底纹用已定义的 bg-grid-agent。 */
    return <div className="h-screen w-screen bg-surface-canvas flex items-center justify-center text-status-info font-mono relative">
      <div className="absolute inset-0 z-[1] opacity-[0.015] bg-grid-agent bg-[size:60px_60px]"></div>
      <div className="animate-pulse">Loading StoryMap CNS...</div>
    </div>
  }

  return (
    <div className="h-screen w-screen overflow-hidden bg-surface-canvas relative flex">
      {/* Grid overlay for depth */}
      <div className="absolute inset-0 pointer-events-none z-[1] opacity-[0.015] bg-grid-agent bg-[size:60px_60px]" />

      {/* Narrative Panel (Left) */}
      {/* E：glass-panel / glass-panel-dense 未定义 → 叙事面板此前完全没有背景。
          改用不透明 surface-panel（面板配方）；backdrop-blur-xl 一并去掉 ——
          blur 盖在持续重绘的地图画布上是最贵的那类合成。 */}
      <div 
        ref={containerRef}
        onScroll={handleScroll}
        className="w-[400px] xl:w-[500px] h-full z-20 bg-surface-panel border-r border-edge-subtle overflow-y-auto overflow-x-hidden flex flex-col relative"
      >
        <div className="sticky top-0 p-6 bg-surface-panel border-b border-edge-subtle z-10 flex justify-between items-center">
          <h1 className="text-status-info font-semibold tracking-widest text-heading flex items-center gap-2">
            STORY<span className="text-ink-muted">MAP</span>
          </h1>
          <div className="flex gap-2">
            {/* E：hud-btn 未定义 → 按钮此前无任何样式；改为面板内图标按钮配方。 */}
            <button aria-label="上一个" className="rounded-md p-2 text-ink-muted transition-colors hover:bg-surface-hover hover:text-status-info"><SkipBack className="h-4 w-4" /></button>
            <button aria-label="播放" className="rounded-md p-2 text-ink-muted transition-colors hover:bg-surface-hover hover:text-status-info"><Play className="h-4 w-4" /></button>
            <button aria-label="分享" className="rounded-md p-2 text-ink-muted transition-colors hover:bg-surface-hover hover:text-status-info"><Share2 className="h-4 w-4" /></button>
          </div>
        </div>

        <div className="p-8 pb-32 flex flex-col gap-12 font-sans">
          {messages.map((msg, idx) => (
            <div 
              key={idx} 
              className={`prose prose-agent prose-headings:text-status-info prose-a:text-status-info max-w-none transition-opacity duration-700
                ${msg.role === 'user' ? 'opacity-50 border-l-2 border-edge-subtle pl-4 italic text-body' : 'opacity-100'}`}
            >
              {msg.role === 'user' ? (
                <p className="m-0 font-mono">USER: {msg.content}</p>
              ) : (
                <StoryMarkdown text={msg.content} />
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Map Panel (Right) */}
      <div className="flex-1 h-full relative z-0 relative shadow-[-20px_0_40px_rgba(0,0,0,0.8)]">
        {/* Adds Cinematic Gradient */}
        <div className="absolute inset-y-0 left-0 w-32 bg-gradient-to-r from-surface-canvas to-transparent z-10 pointer-events-none" />
        
        <MapPanel
          layers={layers}
          onRemoveLayer={removeLayer}
          onToggleLayer={toggleLayer}
        />
      </div>
    </div>
  )
}
