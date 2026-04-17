"use client"
import { useRef, useEffect, useCallback } from "react"
import { Bot, User, Loader2 } from "lucide-react"
import { UploadZone } from "@/components/upload/upload-zone"

import type { UploadResponse } from "@/lib/api/upload"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { ChartRenderer } from "@/components/chat/chart-renderer"
import { MapActionRenderer } from "@/components/chat/map-action-renderer"
import { TaskTimeline } from "@/components/hud/task-timeline"
import { useHudStore } from "@/lib/store/useHudStore"
import { motion, AnimatePresence } from "framer-motion"

interface Message {
  id: string
  role: "user" | "assistant"
  content: string
  timestamp: Date
  isThinking?: boolean
  charts?: unknown[]
}

interface ChatHudProps {
  messages: Message[]
  isLoading: boolean
  onUploadSuccess?: (result: UploadResponse) => void
  sessionId?: string
  showUploadZone: boolean
  setShowUploadZone: (show: boolean | ((prev: boolean) => boolean)) => void
}

export function ChatHud({
  messages,
  isLoading: _isLoading,
  onUploadSuccess,
  sessionId,
  showUploadZone,
  setShowUploadZone: _setShowUploadZone,
}: ChatHudProps) {
  void _isLoading;
  void _setShowUploadZone;
  const scrollRef = useRef<HTMLDivElement>(null)
  const currentTask = useHudStore((s) => s.currentTask)

  const scrollToBottom = useCallback(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [])

  useEffect(() => {
    // Delay slightly to allow DOM updates
    const timer = setTimeout(scrollToBottom, 100)
    return () => clearTimeout(timer)
  }, [messages, scrollToBottom])

  return (
    <div className="flex flex-col h-full">
      {/* Scrollable Content */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto pb-6"
      >
        {/* Task Progress (inline, if active) */}
        {currentTask && (
          <div className="border-b border-white/[0.04] mb-3">
            <TaskTimeline />
          </div>
        )}

        {/* Messages */}
        <div className="px-4 py-3 space-y-3">
          <AnimatePresence initial={false}>
            {messages.map((message) => (
            <motion.div
              key={message.id}
              className={`flex gap-2.5 ${message.role === "user" ? "flex-row-reverse" : ""}`}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.25 }}
            >
              {/* Avatar */}
              <div
                className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-lg ${
                  message.role === "user"
                    ? "bg-hud-cyan/15 border border-hud-cyan/25"
                    : "bg-white/[0.04] border border-white/[0.06]"
                }`}
              >
                {message.role === "user" ? (
                  <User className="h-3.5 w-3.5 text-hud-cyan" />
                ) : (
                  <Bot className="h-3.5 w-3.5 text-white/50" />
                )}
              </div>

              {/* Bubble */}
              <div
                className={`max-w-[88%] rounded-xl px-3.5 py-2.5 text-[13px] leading-relaxed ${
                  message.role === "user"
                    ? "bg-hud-cyan/10 border border-hud-cyan/20 text-white/90"
                    : "bg-white/[0.03] border border-white/[0.05] text-white/75"
                }`}
              >
                {message.isThinking ? (
                  <div className="flex items-center gap-2.5 py-1">
                    <div className="relative">
                      <Loader2 className="h-3.5 w-3.5 animate-spin text-hud-cyan" />
                      <div className="absolute inset-0 bg-hud-cyan/20 rounded-full blur animate-pulse" />
                    </div>
                    <span className="text-[10px] font-mono uppercase tracking-[0.15em] text-white/30">
                      processing...
                    </span>
                  </div>
                ) : (
                  <div className="prose prose-sm prose-hud max-w-none break-words">
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      components={{
                        // Use div instead of p to avoid "div inside p" hydration errors if content is complex
                        p: ({ children }) => <div className="mb-4 last:mb-0 leading-relaxed text-white/85">{children}</div>,
                        
                        // Strict list handling
                        ul: ({ children }) => <ul className="list-disc list-inside mb-4 space-y-1 text-white/80">{children}</ul>,
                        ol: ({ children }) => <ol className="list-decimal list-inside mb-4 space-y-1 text-white/80">{children}</ol>,
                        li: ({ children }) => <li className="mb-0.5">{children}</li>,

                        img: ({ src, alt }) => {
                          // 严密过滤：只允许真正的 HTTP(S) 或 Data URL，过滤掉本地占位符或 hallucinated paths
                          const isValidUrl = src && (
                            src.startsWith("http://") || 
                            src.startsWith("https://") || 
                            src.startsWith("data:")
                          );
                          
                          // 额外过滤：如果是 localhost 但不是预期的 API 路径，也过滤掉
                          const isSafe = isValidUrl && (!src.includes("localhost") || src.includes("/api/map/view"));
                          
                          if (!isSafe) {
                            return null;
                          }

                          return (
                            <div className="my-2 rounded-lg overflow-hidden border border-white/[0.06]">
                              <img src={src} alt={alt || ""} className="max-w-full" />
                            </div>
                          )
                        },
                        h1: ({ children }) => (
                          <h1 className="text-base font-semibold text-hud-cyan border-b border-hud-cyan/15 pb-1 mb-3">
                            {children}
                          </h1>
                        ),
                        h2: ({ children }) => (
                          <h2 className="text-sm font-medium text-hud-cyan/80 border-l-2 border-hud-cyan/30 pl-2 my-3">
                            {children}
                          </h2>
                        ),
                        table: ({ children }) => (
                          <div className="overflow-x-auto my-2 rounded-lg border border-white/[0.06]">
                            <table className="w-full text-[11px]">{children}</table>
                          </div>
                        ),
                        th: ({ children }) => (
                          <th className="px-2 py-1.5 bg-white/[0.03] text-left text-white/50 font-medium border-b border-white/[0.04]">
                            {children}
                          </th>
                        ),
                        td: ({ children }) => (
                          <td className="px-2 py-1.5 border-b border-white/[0.03] text-white/60">{children}</td>
                        ),
                        code: ({ children, className }) => {
                          const inline = !className?.includes("language-")
                          return inline ? (
                            <code className="bg-white/10 px-1 rounded text-hud-cyan text-[12px]">{children}</code>
                          ) : (
                            <pre className="p-3 bg-black/30 rounded-lg overflow-x-auto my-3 border border-white/5 font-mono text-[11px] text-white/90">
                              <code>{children}</code>
                            </pre>
                          )
                        },
                      }}
                    >
                      {message.content}
                    </ReactMarkdown>
                    {message.role === "assistant" && message.content && (
                      <div className="mt-3 pt-3 border-t border-white/[0.04]">
                        <MapActionRenderer content={message.content} />
                      </div>
                    )}
                    {(message.charts as any[])?.map((chart: any, idx: number) => (
                      <div
                        key={`chart-${message.id}-${idx}`}
                        className="mt-3 p-3 rounded-lg bg-white/[0.02] border border-white/[0.04]"
                      >
                        <ChartRenderer chart={chart} />
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </motion.div>
          ))}
        </AnimatePresence>
        </div>
      </div>

      {/* Upload zone (toggle) */}
      <AnimatePresence>
        {showUploadZone && (
          <motion.div
            className="px-4 pb-3 border-t border-white/[0.04]"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
          >
            <div className="pt-3">
              <UploadZone sessionId={sessionId} onUploadSuccess={onUploadSuccess!} compact={true} />
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

/* Re-export for backward compat */
export { ChatHud as ChatPanel }
