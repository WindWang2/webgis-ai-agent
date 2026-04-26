"use client"
import { useState, useEffect, useRef } from "react"
import { useSearchParams } from "next/navigation"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { MapPanel } from "@/components/map/map-panel"
import { API_BASE } from '@/lib/api/config';
import { useHudStore } from "@/lib/store/useHudStore"
import { Play, SkipBack, Share2 } from "lucide-react"

export default function StoryPage() {
  const searchParams = useSearchParams()
  const sessionId = searchParams.get("session_id")

  const [messages, setMessages] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const containerRef = useRef<HTMLDivElement>(null)

  const { layers, addLayer, removeLayer, toggleLayer, setAnalysisResult, analysisResult } = useHudStore()

  useEffect(() => {
    if (sessionId) {
      fetch(`${API_BASE}/api/v1/chat/sessions/${sessionId}`)
        .then((res) => res.json())
        .then((data) => {
          if (data.messages && data.messages.length > 0) {
            setMessages(data.messages)
          }
        })
        .catch((err) => console.error("Restore session history failed:", err))
        .finally(() => setLoading(false))
    } else {
      setLoading(false)
      setMessages([
        { role: "assistant", content: "# 欢迎进入影院级互动演示 (StoryMap)模式\n这是一场地理空间叙事的全新体验。在这里，数据不仅仅是图表，更是一段可以探索的旅程。" },
        { role: "assistant", content: "您可以尝试在 URL 中追加 `?session_id=您的会话ID` 来回放之前的分析推演。" },
      ])
    }
  }, [sessionId])

  // ScrollSpy - Parse specific locations from markdown text and fly to them
  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    // Advanced ScrollSpy logic can be added here to trigger camera flyTo
    // based on visible Markdown headers.
  }

  if (loading) {
    return <div className="h-screen w-screen bg-ds-black flex items-center justify-center text-hud-cyan font-mono relative">
      <div className="absolute inset-0 z-[1] opacity-[0.015] bg-grid-hud bg-[size:60px_60px]"></div>
      <div className="animate-pulse">Loading StoryMap CNS...</div>
    </div>
  }

  return (
    <div className="h-screen w-screen overflow-hidden bg-ds-black relative flex">
      {/* Grid overlay for depth */}
      <div className="absolute inset-0 pointer-events-none z-[1] opacity-[0.015] bg-grid-hud bg-[size:60px_60px]" />

      {/* Narrative Panel (Left) */}
      <div 
        ref={containerRef}
        onScroll={handleScroll}
        className="w-[400px] xl:w-[500px] h-full z-20 glass-panel border-r border-hud-cyan/20 overflow-y-auto overflow-x-hidden flex flex-col relative"
      >
        <div className="sticky top-0 p-6 glass-panel-dense backdrop-blur-xl border-b border-white/5 z-10 flex justify-between items-center">
          <h1 className="text-hud-cyan font-semibold tracking-widest text-lg flex items-center gap-2">
            STORY<span className="text-white/50">MAP</span>
          </h1>
          <div className="flex gap-2">
            <button className="hud-btn p-2 rounded-lg text-white/50 hover:text-hud-cyan"><SkipBack className="h-4 w-4" /></button>
            <button className="hud-btn p-2 rounded-lg text-white/50 hover:text-hud-cyan"><Play className="h-4 w-4" /></button>
            <button className="hud-btn p-2 rounded-lg text-white/50 hover:text-hud-cyan"><Share2 className="h-4 w-4" /></button>
          </div>
        </div>

        <div className="p-8 pb-32 flex flex-col gap-12 font-sans">
          {messages.map((msg, idx) => (
            <div 
              key={idx} 
              className={`prose prose-invert prose-p:text-white/70 prose-headings:text-hud-cyan prose-a:text-hud-cyan prose-strong:text-white max-w-none transition-opacity duration-700
                ${msg.role === 'user' ? 'opacity-50 border-l-2 border-white/10 pl-4 italic text-sm' : 'opacity-100'}`}
            >
              {msg.role === 'user' ? (
                <p className="m-0 font-mono">USER: {msg.content}</p>
              ) : (
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {msg.content}
                </ReactMarkdown>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Map Panel (Right) */}
      <div className="flex-1 h-full relative z-0 relative shadow-[-20px_0_40px_rgba(0,0,0,0.8)]">
        {/* Adds Cinematic Gradient */}
        <div className="absolute inset-y-0 left-0 w-32 bg-gradient-to-r from-ds-black to-transparent z-10 pointer-events-none" />
        
        <MapPanel
          layers={layers}
          onRemoveLayer={removeLayer}
          onToggleLayer={toggleLayer}
          onEditLayer={() => {}}
          analysisResult={analysisResult}
        />
      </div>
    </div>
  )
}
